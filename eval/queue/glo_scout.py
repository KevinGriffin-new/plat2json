#!/usr/bin/env python3
"""Scout BLM GLO Records (glorecords.blm.gov) by dm_id.

usage: glo_scout.py START END [PILOT_N=5]

For each dm_id: fetch the survey details page (1.5 s spacing), record what it
is (mineral survey or not, image name, fieldnote link) to results/glo_index.tsv.
For the first PILOT_N mineral surveys: run the full conversion+download path
(getImage.ashx -> ConvertedImages PDF) and stage tiles like acquire_ms.py.
Also attempts up to 2 FIELDNOTE conversions and logs the raw outcome — this is
the open question from research (curl got empty bodies; a browser worked).

US-federal source: public domain, images committable. Venv python.
"""
import html
import json
import os
import re
import subprocess
import sys
import time

import fitz
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
START, END = int(sys.argv[1]), int(sys.argv[2])
PILOT_N = int(sys.argv[3]) if len(sys.argv) > 3 else 5
BASE = "https://glorecords.blm.gov"
UA = "Mozilla/5.0 (plat2json eval harness)"
HARNESS = os.path.expanduser("~/plat2json/eval/harness")
OUT = os.path.expanduser("~/plat-queue/results/glo_index.tsv")
new_index = not os.path.exists(OUT)


def curl(url, out=None, referer=None):
    cmd = ["curl", "-fsSL", "-A", UA, "--max-time", "90", url]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    if out:
        cmd += ["-o", out]
    r = subprocess.run(cmd, capture_output=True, text=out is None)
    return (r.returncode == 0, r.stdout if out is None else "")


def convert_image(dl_url, referer, fmt="pdf"):
    """getImage.ashx -> poll until READY -> imageFileLink."""
    q = f"{dl_url}&sheetNr=1&backOfPlat=&imageFormat={fmt}"
    for _ in range(6):
        ok, body = curl(q, referer=referer)
        if ok and body.strip():
            try:
                j = json.loads(body)
            except ValueError:
                return None, f"non-json ({body[:80]!r})"
            if j.get("conversionStatus") == "READY":
                return j.get("imageFileLink"), "READY"
            time.sleep(5)
        else:
            return None, f"empty/err (ok={ok}, {len(body)}B)"
    return None, "never READY"


pilots = fn_tries = 0
with open(OUT, "a", encoding="utf-8") as idx:
    if new_index:
        idx.write("dm_id\tmineral\timage\tfieldnote_dm\ttitle\n")
    for dm in range(START, END + 1):
        time.sleep(1.5)
        url = f"{BASE}/details/survey/default.aspx?dm_id={dm}"
        ok, page = curl(url)
        if not ok or "downloadImageURL" not in page:
            continue
        m = re.search(r"downloadImageURL = '([^']+)'", page)
        dl = html.unescape(m.group(1)) if m else ""
        img = re.search(r"currentImageFileName = '([^']*)'", page)
        img = img.group(1) if img else ""
        mineral = "Mineral Survey" in page
        fn = re.search(r"details/fieldnote/default\.aspx\?dm_id=(\d+)", page)
        fn_dm = fn.group(1) if fn else ""
        title = re.search(r"<title>([^<]*)</title>", page)
        title = (title.group(1).strip() if title else "")[:80]
        idx.write(f"{dm}\t{int(mineral)}\t{img}\t{fn_dm}\t{title}\n")
        idx.flush()
        print(f"dm_id={dm} mineral={int(mineral)} img={img} fn={fn_dm}")

        if mineral and pilots < PILOT_N and dl:
            slug = f"glo_dm{dm}"
            dst = os.path.join(HARNESS, "_sources", slug)
            os.makedirs(dst, exist_ok=True)
            link, status = convert_image(dl, url)
            print(f"  [pilot {slug}] convert: {status} -> {link}")
            if link:
                pdf = os.path.join(dst, "plat.pdf")
                if curl(link, out=pdf)[0]:
                    try:
                        doc = fitz.open(pdf)
                        bi, bp = 0, -1
                        for i, p in enumerate(doc):
                            for im in p.get_images():
                                if im[2] * im[3] > bp:
                                    bp, bi = im[2] * im[3], i
                        pix = doc[bi].get_pixmap(dpi=300, colorspace=fitz.csGRAY)
                        png = os.path.join(dst, "plat.png")
                        Image.frombytes("L", [pix.width, pix.height],
                                        pix.samples).save(png)
                        subprocess.run(
                            [sys.executable, os.path.join(HARNESS, "prep_plan.py"),
                             png, slug, "--scale", "4800", "--tile", "1100",
                             "--overlap", "200", "--tiles-only"],
                            check=True, capture_output=True, text=True)
                        with open(os.path.join(dst, "_plan_plat2json.json"),
                                  "w", encoding="utf-8") as f:
                            json.dump({"lines": [], "arcs": [], "circles": [],
                                       "texts": []}, f)
                        pilots += 1
                        print(f"  [pilot {slug}] staged {pix.width}x{pix.height}")
                    except Exception as e:  # noqa: BLE001
                        print(f"  [pilot {slug}] stage FAILED: {e}")

        if fn_dm and fn_tries < 2:
            fn_tries += 1
            fn_url = f"{BASE}/details/fieldnote/default.aspx?dm_id={fn_dm}&s_dm_id={dm}"
            okf, fpage = curl(fn_url)
            fm = re.search(r"downloadImageURL = '([^']+)'", fpage or "")
            if fm:
                flink, fstatus = convert_image(html.unescape(fm.group(1)), fn_url)
                print(f"  [FIELDNOTE probe dm={fn_dm}] {fstatus} -> {flink}")
            else:
                print(f"  [FIELDNOTE probe dm={fn_dm}] no downloadImageURL "
                      f"(page ok={okf}, {len(fpage or '')}B)")
print(f"glo scout done: pilots={pilots}, fieldnote probes={fn_tries}")

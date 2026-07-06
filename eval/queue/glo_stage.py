#!/usr/bin/env python3
"""Stage one BLM GLO Records survey plat (public domain) for the VLM read.

usage: glo_stage.py <dm_id>

Fetches the details page, runs the getImage.ashx conversion, downloads the
ConvertedImages PDF, and renders at the EMBEDDED IMAGE's native resolution
(zoom matrix, not fixed dpi -- GLO page boxes are huge and a 300 dpi page
render is a gigapixel decompression bomb; see glo_dm147002 in the wave-5 log).
Then tiles at 1100/200 and writes an empty geometry json for the scorer.
Re-staging is safe: cleans a dir whose tiles/ is missing or empty.
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time

import fitz
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
dm = int(sys.argv[1])
BASE = "https://glorecords.blm.gov"
UA = "Mozilla/5.0 (plat2json eval harness)"
HARNESS = os.path.expanduser("~/plat2json/eval/harness")
slug = f"glo_dm{dm}"
dst = os.path.join(HARNESS, "_sources", slug)
tiles = os.path.join(dst, "tiles")

if os.path.isdir(tiles) and os.listdir(tiles):
    print(f"[{slug}] already staged")
    sys.exit(0)
if os.path.isdir(dst) and not (os.path.isdir(tiles) and os.listdir(tiles)):
    for f in ("plat.png",):  # drop a bomb-sized render from a failed attempt
        p = os.path.join(dst, f)
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(tiles, ignore_errors=True)
os.makedirs(dst, exist_ok=True)


def curl(url, out=None, referer=None):
    cmd = ["curl", "-fsSL", "-A", UA, "--max-time", "180", url]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    if out:
        cmd += ["-o", out]
    r = subprocess.run(cmd, capture_output=True, text=out is None)
    return (r.returncode == 0, r.stdout if out is None else "")


url = f"{BASE}/details/survey/default.aspx?dm_id={dm}"
ok, page = curl(url)
m = re.search(r"downloadImageURL = '([^']+)'", page or "")
if not (ok and m):
    print(f"[{slug}] no downloadImageURL")
    sys.exit(1)
dl = html.unescape(m.group(1))

pdf = os.path.join(dst, "plat.pdf")
if not os.path.exists(pdf):
    link = None
    for _ in range(6):
        okc, body = curl(f"{dl}&sheetNr=1&backOfPlat=&imageFormat=pdf", referer=url)
        if okc and body.strip():
            j = json.loads(body)
            if j.get("conversionStatus") == "READY":
                link = j.get("imageFileLink")
                break
        time.sleep(5)
    if not link or not curl(link, out=pdf)[0]:
        print(f"[{slug}] conversion/download failed")
        sys.exit(1)

doc = fitz.open(pdf)
bi, bw, bp = 0, 0, -1
for i, p in enumerate(doc):
    for im in p.get_images():
        if im[2] * im[3] > bp:
            bp, bi, bw = im[2] * im[3], i, im[2]
pg = doc[bi]
z = (bw / pg.rect.width) if bw else 1.0        # match native scan resolution
z = min(z, 12000 / max(pg.rect.width, pg.rect.height))  # hard cap
pix = pg.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csGRAY)
png = os.path.join(dst, "plat.png")
Image.frombytes("L", [pix.width, pix.height], pix.samples).save(png)
print(f"[{slug}] p{bi} {pix.width}x{pix.height} (z={z:.2f})")

subprocess.run([sys.executable, os.path.join(HARNESS, "prep_plan.py"), png, slug,
                "--scale", "4800", "--tile", "1100", "--overlap", "200",
                "--tiles-only"], check=True, capture_output=True, text=True)
with open(os.path.join(dst, "_plan_plat2json.json"), "w", encoding="utf-8") as f:
    json.dump({"lines": [], "arcs": [], "circles": [], "texts": []}, f)
print(f"[{slug}] staged")

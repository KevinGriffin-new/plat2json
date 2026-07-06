#!/usr/bin/env python3
"""Harvest NCDOT letting-plan Right-of-Way sheets into the eval corpus.

Crawls https://xfer.services.ncdot.gov/dsplan/ (plain IIS listings,
2 s/request), finds per-project "Right of Way" PDFs, downloads up to CAP,
and runs vector_golden.py on each: text-layer golden -> eval/goldens/,
blind 1100 px tiles -> eval/harness/_sources/ncdot_<slug>/.

usage: ncdot_harvest.py [CAP=20] [/dsplan/<year>%20highway%20letting/ ...]
NC public record: PDFs stay local (URL-only source); only numeric goldens
are committable. Run with the plat2json venv python.
"""
import os
import re
import subprocess
import sys
import time
import urllib.parse

BASE = "https://xfer.services.ncdot.gov"
YEARS = sys.argv[2:] or ["/dsplan/2024%20highway%20letting/",
                         "/dsplan/2023%20highway%20letting/"]
CAP = int(sys.argv[1]) if len(sys.argv) > 1 else 20
HARNESS = os.path.expanduser("~/plat2json/eval/harness")
GOLDENS = os.path.expanduser("~/plat2json/eval/goldens")
VG = os.path.join(HARNESS, "vector_golden.py")
DL = os.path.expanduser("~/plat-queue/results/ncdot_pdfs")
UA = "Mozilla/5.0 (plat2json eval harness)"
os.makedirs(DL, exist_ok=True)


def fetch(path):
    time.sleep(2)
    r = subprocess.run(["curl", "-fsSL", "-A", UA, "--max-time", "60", BASE + path],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def kids(path):
    """Child hrefs strictly under path (raw, URL-encoded)."""
    got = re.findall(r'<A HREF="([^"]+)"', fetch(path), re.I)
    return [h for h in got if h.startswith(path) and h != path]


def find_row_pdf(proj):
    """First 'Right of Way' PDF within a project dir (<=2 levels deep)."""
    stack = [(proj, 0)]
    while stack:
        p, depth = stack.pop()
        for h in kids(p):
            plain = urllib.parse.unquote(h)
            if re.search(r"right of way[^/]*\.pdf$", plain, re.I):
                return h
            if h.endswith("/") and depth < 2:
                stack.append((h, depth + 1))
    return None


found = 0
for yr in YEARS:
    if found >= CAP:
        break
    for date in kids(yr):
        if found >= CAP:
            break
        pp = [d for d in kids(date) if re.search(r"plans.*proposals", d, re.I)]
        for ppd in pp:
            for proj in kids(ppd):
                if found >= CAP:
                    break
                if not proj.endswith("/"):
                    continue
                name = urllib.parse.unquote(proj.rstrip("/").rsplit("/", 1)[-1])
                slug = "ncdot_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]
                import glob as _g
                if _g.glob(os.path.join(GOLDENS, slug + ".key_p*.json")):
                    print(f"[{slug}] already harvested, skip")
                    continue
                row = find_row_pdf(proj)
                if not row:
                    print(f"[{slug}] no ROW pdf, skip")
                    continue
                pdf = os.path.join(DL, slug + ".pdf")
                if not os.path.exists(pdf):
                    time.sleep(2)
                    r = subprocess.run(["curl", "-fsSL", "-A", UA, "--max-time", "300",
                                        "-o", pdf, BASE + row])
                    if r.returncode:
                        print(f"[{slug}] download FAILED")
                        continue
                try:
                    subprocess.run([sys.executable, VG, pdf, slug],
                                   check=True, timeout=1200)
                    found += 1
                    print(f"[{slug}] harvested ({found}/{CAP})  <- {urllib.parse.unquote(row)}")
                except Exception as e:  # noqa: BLE001 - scan-era/low-text sheets are expected
                    print(f"[{slug}] vector_golden rejected ({e}); skip")
print(f"ncdot harvest done: {found} new sheets")

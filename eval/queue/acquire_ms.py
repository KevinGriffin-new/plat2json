#!/usr/bin/env python3
"""Stage one BLM WY mineral-survey plat for the VLM read.

usage: acquire_ms.py <range>/<msN[sfx]>      e.g. acquire_ms.py 050-099/ms52a

Creates eval/harness/_sources/blm_<msN>/:
  <ms>.pdf, fieldnotes.pdf (when published), plat.png (300 dpi grey),
  tiles/ (1100 px full-res tiles, overlap 200 -- the validated quality config),
  plat_lo.png + _plan_plat2json.json (2200 px geometry for the self-check scorer).
Run with the plat2json venv python.
"""
import json
import os
import subprocess
import sys

import fitz
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

rng, ms = sys.argv[1].split("/")
HERE = os.path.expanduser("~/plat2json/eval/harness")
BASE = "https://www.wy.blm.gov/cadastral/mineralsurvey"
UA = "Mozilla/5.0 (plat2json eval harness)"
slug = f"blm_{ms}"
out = os.path.join(HERE, "_sources", slug)
os.makedirs(out, exist_ok=True)

pdf = os.path.join(out, f"{ms}.pdf")
if not os.path.exists(pdf):
    subprocess.run(["curl", "-fsSL", "-A", UA, "-o", pdf, f"{BASE}/{rng}/{ms}.pdf"],
                   check=True)

fn = os.path.join(out, "fieldnotes.pdf")
if not os.path.exists(fn):
    r = subprocess.run(["curl", "-fsSL", "-A", UA, "-o", fn,
                        f"{BASE}/{rng}/fieldnotes/{ms}_fn.pdf"])
    if r.returncode and os.path.exists(fn):
        os.remove(fn)

doc = fitz.open(pdf)
best_i, best_px = 0, -1
for i, p in enumerate(doc):
    for im in p.get_images():
        if im[2] * im[3] > best_px:
            best_px, best_i = im[2] * im[3], i
pix = doc[best_i].get_pixmap(dpi=300, colorspace=fitz.csGRAY)
png = os.path.join(out, "plat.png")
Image.frombytes("L", [pix.width, pix.height], pix.samples).save(png)
print(f"[{slug}] {doc.page_count}pp plat=p{best_i} {pix.width}x{pix.height} "
      f"fieldnotes={'yes' if os.path.exists(fn) else 'no'}")

subprocess.run([sys.executable, os.path.join(HERE, "prep_plan.py"), png, slug,
                "--scale", "4800", "--tile", "1100", "--overlap", "200",
                "--tiles-only"], check=True, capture_output=True, text=True)

lo = Image.open(png)
lo.thumbnail((2200, 2200))
lopng = os.path.join(out, "plat_lo.png")
lo.save(lopng)
pj = os.path.join(out, "_plan_plat2json.json")
try:
    subprocess.run([sys.executable, os.path.expanduser("~/plat2json/plat2json.py"),
                    lopng, pj, "--plot-scale", "4800"],
                   check=True, capture_output=True, text=True, timeout=600)
except Exception as e:  # noqa: BLE001  - keep the sheet; self-check just degrades
    print(f"[{slug}] plat2json failed ({e}); writing empty geometry")
    with open(pj, "w", encoding="utf-8") as f:
        json.dump({"lines": [], "arcs": [], "circles": [], "texts": []}, f)
print(f"[{slug}] staged")

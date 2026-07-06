#!/usr/bin/env python3
"""Stage one PDF page as a read variant (dense-sheet rescue experiments).

usage: dense_stage.py <pdf> <page0idx> <slug> <dpi> <tile> <overlap>
Renders the page at <dpi>, tiles at <tile>/<overlap>, writes empty geometry
json so score_run works. Skips if tiles already exist. Venv python.
"""
import json
import os
import subprocess
import sys

import fitz
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
pdf, page, slug = sys.argv[1], int(sys.argv[2]), sys.argv[3]
dpi, tile, ov = sys.argv[4], sys.argv[5], sys.argv[6]
HARNESS = os.path.expanduser("~/plat2json/eval/harness")
dst = os.path.join(HARNESS, "_sources", slug)
tiles = os.path.join(dst, "tiles")
if os.path.isdir(tiles) and os.listdir(tiles):
    print(f"[{slug}] already staged")
    sys.exit(0)
os.makedirs(dst, exist_ok=True)
doc = fitz.open(pdf)
pix = doc[page].get_pixmap(dpi=int(dpi), colorspace=fitz.csGRAY)
png = os.path.join(dst, "plat.png")
Image.frombytes("L", [pix.width, pix.height], pix.samples).save(png)
subprocess.run([sys.executable, os.path.join(HARNESS, "prep_plan.py"), png, slug,
                "--scale", "4800", "--tile", tile, "--overlap", ov,
                "--tiles-only"], check=True, capture_output=True, text=True)
with open(os.path.join(dst, "_plan_plat2json.json"), "w", encoding="utf-8") as f:
    json.dump({"lines": [], "arcs": [], "circles": [], "texts": []}, f)
print(f"[{slug}] staged p{page} @{dpi}dpi {pix.width}x{pix.height} tile={tile}")

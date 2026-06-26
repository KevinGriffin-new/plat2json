#!/usr/bin/env python3
"""batch_acquire.py - acquire + prep a batch of BLM Wyoming township plats.

For each "county/txxnryyw" arg: download the PDF, auto-detect the PLAT page
(the page whose largest embedded image has the most pixels; field-note pages
are smaller scans), render it to a PNG, and run prep_plan.py (tiles + plat2json).

    python batch_acquire.py albany/t16nr73w carbon/t20nr85w ...

Outputs _sources/glo_<county>_<twp>/ per plat. Run with SYSTEM python.
PD source: wy.blm.gov/cadastral/countyplats/<county>/<twp>.pdf
"""
import subprocess, sys, os
import fitz
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://www.wy.blm.gov/cadastral/countyplats"
PLAT2JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plat2json.py")


def plat_page(doc):
    """Index of the page whose largest image has the most pixels (the plat)."""
    best_i, best_px = 0, -1
    for i, p in enumerate(doc):
        for im in p.get_images():
            px = im[2] * im[3]
            if px > best_px:
                best_px, best_i = px, i
    return best_i


for arg in sys.argv[1:]:
    county, twp = arg.split("/")
    slug = f"glo_{county}_{twp}"
    out = os.path.join(HERE, "_sources", slug)
    os.makedirs(out, exist_ok=True)
    pdf = os.path.join(out, f"{twp}.pdf")
    try:
        if not os.path.exists(pdf):
            subprocess.run(["curl", "-sSL", "-A", "Mozilla/5.0", "-o", pdf,
                            f"{BASE}/{county}/{twp}.pdf"], check=True)
        doc = fitz.open(pdf)
        pi = plat_page(doc)
        pix = doc[pi].get_pixmap(dpi=300, colorspace=fitz.csGRAY)
        png = os.path.join(out, "plat.png")
        img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
        img.save(png)
        print(f"[{slug}] {doc.page_count}pp, plat=p{pi}, {pix.width}x{pix.height}")
        # hi-res tiles for the VLM read
        subprocess.run([sys.executable, os.path.join(HERE, "prep_plan.py"),
                        png, slug, "--scale", "4800", "--tiles-only"], check=True,
                       capture_output=True, text=True)
        # downsampled copy for plat2json (full-res skeletonize is pathologically slow)
        lo = img.copy(); lo.thumbnail((2200, 2200))
        lopng = os.path.join(out, "plat_lo.png"); lo.save(lopng)
        pj = os.path.join(out, "_plan_plat2json.json")
        subprocess.run([sys.executable, PLAT2JSON, lopng, pj, "--plot-scale", "4800"],
                       check=True, capture_output=True, text=True, timeout=300)
        print(f"   prepped -> _sources/{slug}/ (tiles + plat2json@{lo.size[0]}px)")
    except Exception as e:  # noqa: BLE001
        print(f"[{slug}] FAILED: {e}")
print("batch done")

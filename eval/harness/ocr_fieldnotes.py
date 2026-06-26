#!/usr/bin/env python3
"""ocr_fieldnotes.py - OCR BLM field notes into an authoritative recall key.

BLM field notes are typewritten narrative ("N. 0deg 17' W., ... CHAINS ...") and
OCR cleanly with tesseract. This extracts the published bearings (-> azimuth)
and chain distances as a license-free answer key to score a plat read against
(true recall, vs the geometry self-check). Independent of the plat-image read,
so agreement between the two cross-validates both (see golden-value skill).

    python ocr_fieldnotes.py <fieldnotes.pdf> <out_key.json> [--pages 5-24]

Run with SYSTEM python (tesseract 5.x + pytesseract).
"""
import argparse, json, re
import fitz, pytesseract
from PIL import Image

DEG = r"[deg°\*� oO0]?"   # the degree mark OCRs as many glyphs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("out")
    ap.add_argument("--pages", default=None,
                    help="e.g. 5-24 or 56-59,74-85 (0-based, inclusive)")
    ap.add_argument("--dpi", type=int, default=300)
    a = ap.parse_args()
    d = fitz.open(a.pdf)
    if a.pages:
        rng = []
        for part in a.pages.split(","):
            lo, hi = (int(x) for x in part.split("-"))
            rng += list(range(lo, hi + 1))
    else:
        rng = range(d.page_count)
    txt = []
    for i in rng:
        if i >= d.page_count:
            break
        pix = d[i].get_pixmap(dpi=a.dpi, colorspace=fitz.csGRAY)
        im = Image.frombytes("L", [pix.width, pix.height], pix.samples)
        txt.append(pytesseract.image_to_string(im))
    txt = "\n".join(txt)

    # bearing-tree / monument calls reuse the N..E format but are NOT line
    # courses ("A pine ... bears S. 81 E., 23 lks") - exclude those lines.
    MON = re.compile(r"bears|lks\.|ins\.|diam|\bBT\b|pine|fir|spruce|aspen|"
                     r"cottonwood|willow|Form|OFFICE|mkd")
    bpat = re.compile(r"([NS])\.?\s*(\d{1,3})" + DEG + r"\s*(\d{1,2})\D{0,3}([EW])")
    bset, dset = set(), set()
    for line in txt.splitlines():
        if MON.search(line):
            continue
        for m in bpat.finditer(line):
            ns, dd, mm, ew = m[1], int(m[2]), int(m[3]), m[4]
            if dd > 90 or mm >= 60:
                continue
            ang = dd + mm / 60
            bset.add(round({("N", "E"): ang, ("S", "E"): 180 - ang,
                            ("S", "W"): 180 + ang, ("N", "W"): 360 - ang}[(ns, ew)], 4))
        for m in re.finditer(r"(?<![\d.])(\d{1,2}\.\d{2})(?![\d])", line):
            v = float(m.group(1))
            if 0.05 <= v <= 85:
                dset.add(round(v, 2))
    json.dump({"bearings_az": sorted(bset), "distances_m": sorted(dset),
               "note": f"OCR of {a.pdf} pages {a.pages or 'all'}; distances=2dp chain "
                       "candidates (narrative chainages, may include stray numbers)"},
              open(a.out, "w"), indent=1)
    print(f"-> {a.out}: {len(bset)} bearings, {len(dset)} distances")


if __name__ == "__main__":
    main()

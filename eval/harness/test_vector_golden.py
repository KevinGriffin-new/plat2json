#!/usr/bin/env python3
"""test_vector_golden.py - self-contained regression test for vector_golden.py.

Generates a synthetic vector plat PDF whose text layer carries a known set of
courses PLUS adversarial decoys (curve R=/L=/A=/Delta params, areas, lot/plan/
year integers, a scale), then asserts vector_golden harvests exactly the planted
bearings and straight-leg distances and rejects every decoy.

No network, no copyrighted source - committable as a deterministic regression
net. Run with the venv python (needs fitz):

    python test_vector_golden.py
"""
import os
import sys
import tempfile

import fitz  # PyMuPDF

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vector_golden as vg

PLANTED_BEARINGS = [
    "N12°34'56\"E", "S05°00'00\"W", "N89°59'59\"W",
    "S45°30'15\"E", "N0°38'32\"E", "N7°15'51\"W",
]
PLANTED_DISTANCES = [7.23, 25.0, 43.44, 100.0, 152.31, 1319.61]
# Each decoy is a single contiguous token so the curve-prefix / area-suffix
# context tests fire the way they will on a real sheet.
DECOYS = [
    "R=15.500", "L=12.778", "A=29.047", "Δ=14.400",   # curve table
    "Area = 929.9 m2", "12345.6 SF",                    # areas
    "Lot 12", "Plan EPP12345", "2018", "Scale 1:250",   # integers / scale
]
DECOY_VALUES = {15.5, 12.778, 29.047, 14.4, 929.9, 12345.6}


def make_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=792, height=612)
    y = 60
    # courses: bearing + distance pairs, a couple rotated like real line labels
    for i, (b, d) in enumerate(zip(PLANTED_BEARINGS, PLANTED_DISTANCES)):
        rot = (0, 90, 0, 270, 0, 0)[i]
        page.insert_text((60, y), f"{b}   {d}", fontsize=9, rotate=rot)
        y += 40
    page.insert_text((400, 60), "DISTANCES IN FEET", fontsize=9)
    y = 120
    for dec in DECOYS:
        page.insert_text((400, y), dec, fontsize=9)
        y += 28
    doc.save(path)
    doc.close()


def main():
    with tempfile.TemporaryDirectory() as td:
        pdf = os.path.join(td, "synthetic_plat.pdf")
        make_pdf(pdf)
        text = fitz.open(pdf)[0].get_text()

        bearings = vg.harvest_bearings(text)
        distances = vg.harvest_distances(text)
        unit = vg.guess_unit(text)

        fails = []
        if set(bearings) != set(PLANTED_BEARINGS):
            fails.append(f"bearings: got {sorted(bearings)}\n"
                         f"          want {sorted(PLANTED_BEARINGS)}")
        if set(distances) != set(PLANTED_DISTANCES):
            fails.append(f"distances: got {distances}\n"
                         f"           want {sorted(PLANTED_DISTANCES)}")
        leaked = DECOY_VALUES & set(distances)
        if leaked:
            fails.append(f"decoys leaked into distances: {sorted(leaked)}")
        if unit != "ft":
            fails.append(f"unit: got {unit!r}, want 'ft'")

        if fails:
            print("FAIL")
            for f in fails:
                print(" -", f)
            sys.exit(1)
        print(f"PASS  {len(bearings)} bearings, {len(distances)} distances, "
              f"unit={unit}, all {len(DECOYS)} decoys rejected")


if __name__ == "__main__":
    main()

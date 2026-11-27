#!/usr/bin/env python3
"""Generate docs/samples/sample-vector-plat.pdf, a tiny SYNTHETIC vector plat.

Made-up geometry and labels (no real survey), so it is license-free and safe
to commit. It has what the vector-only path reads: straight lot lines, a
cul-de-sac drawn as true circular Bezier arcs, monument circles, a sheet
frame, and a text layer with bearings and distances.

    python docs/samples/make_sample.py [OUT.pdf]
"""
import math
import os
import sys

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))


def arc_bezier(shape, cx, cy, r, a0, a1):
    """Draw a circular arc (degrees, PDF y-down) as cubic Beziers <= 90 deg."""
    n = max(1, math.ceil(abs(a1 - a0) / 90.0))
    step = (a1 - a0) / n
    for i in range(n):
        t0 = math.radians(a0 + i * step)
        t1 = math.radians(a0 + (i + 1) * step)
        k = 4.0 / 3.0 * math.tan((t1 - t0) / 4.0)
        p0 = (cx + r * math.cos(t0), cy + r * math.sin(t0))
        p3 = (cx + r * math.cos(t1), cy + r * math.sin(t1))
        c1 = (p0[0] - k * r * math.sin(t0), p0[1] + k * r * math.cos(t0))
        c2 = (p3[0] + k * r * math.sin(t1), p3[1] - k * r * math.cos(t1))
        shape.draw_bezier(p0, c1, c2, p3)


def make(path):
    doc = pymupdf.open()
    page = doc.new_page(width=792, height=612)  # US letter, landscape
    sh = page.new_shape()
    sh.draw_rect(pymupdf.Rect(18, 18, 774, 594))  # sheet frame
    sh.finish(color=(0, 0, 0), width=1.2)
    sh.commit()

    # Block: west line x=120, east x=600, north y=120, south street y=420.
    sh = page.new_shape()
    sh.draw_polyline([(120, 420), (120, 120), (600, 120), (600, 420)])
    sh.finish(color=(0, 0, 0), width=1.0, closePath=False)
    for x in (240, 360, 480):                       # lot lines
        sh.draw_line((x, 120), (x, 300))
    sh.draw_line((120, 300), (600, 300))            # rear line of the front lots
    sh.finish(color=(0, 0, 0), width=0.8, closePath=False)
    sh.commit()

    # South frontage with a cul-de-sac bulb (true circular arc, r=60pt).
    sh = page.new_shape()
    sh.draw_line((120, 420), (300, 420))
    arc_bezier(sh, 360, 420, 60, 180, 0)            # bulb dips south (y-down)
    sh.draw_line((420, 420), (600, 420))
    sh.finish(color=(0, 0, 0), width=1.0, closePath=False)
    sh.commit()

    for x, y in ((120, 120), (600, 120), (600, 420), (120, 420)):
        c = page.new_shape()
        c.draw_circle((x, y), 2.5)
        c.finish(color=(0, 0, 0), fill=(0, 0, 0), width=0.5)
        c.commit()

    labels = [
        ((300, 112), "N89°58'12\"E  146.30", 0),
        ((610, 260), "S00°01'48\"E  91.44", 90),
        ((112, 300), "N00°01'48\"W  91.44", 90),
        ((170, 412), "N89°58'12\"E  54.86", 0),
        ((330, 500), "R=18.29  L=57.46", 0),
        ((250, 210), "LOT 1", 0), ((370, 210), "LOT 2", 0), ((490, 210), "LOT 3", 0),
        ((40, 570), "SYNTHETIC SAMPLE PLAT - NOT A REAL SURVEY.  SCALE 1:1200  DISTANCES IN METRES", 0),
    ]
    for (x, y), s, rot in labels:
        page.insert_text((x, y), s, fontsize=8, rotate=rot)
    doc.save(path, garbage=4, deflate=True)
    doc.close()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "sample-vector-plat.pdf")
    make(out)
    print(f"{os.path.getsize(out)} bytes -> {out}")

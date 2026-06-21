#!/usr/bin/env python3
"""plat2json — extract drawable geometry from a vector survey-plat PDF.

Reads a vector PDF plat (LTSA-style: linework flattened to line segments, text
as stroked glyphs, no text layer, no arc primitives), isolates the parcel
linework, vectorizes it, and writes a plan-JSON of world-coordinate geometry
that Open CAD Studio's LandSurvey plugin (LS_IMPORTPLAN) — or any consumer —
can draw. One extraction, many sinks.

STATUS: experimental. Output is a rough geometry skeleton, NOT survey-grade:
  * geometry comes out as many short Hough segments (fragment-soup, ~100+
    segments for ~10 real edges) — needs skeleton path-tracing to clean up;
  * arcs are NOT fitted here (see fit_arcs.py / arc_refine.py);
  * labels (bearings, curve r=/a=) are NOT read (unsolved OCR — see STATUS.md).

Pipeline: render -> binarize (Otsu) -> keep long connected components as
linework (drop the page border) -> largest = the parcel -> skeletonize ->
HoughLinesP -> map paper-points to ground metres at the plot scale, Y flipped
north-up.

Usage:
    python plat2json.py INPUT.pdf OUTPUT.json [--dpi 300] [--plot-scale 250]
"""
import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser(description="Vector survey-plat PDF -> plan-JSON geometry.")
    ap.add_argument("pdf", help="input vector plat PDF")
    ap.add_argument("out", help="output plan-JSON path")
    ap.add_argument("--dpi", type=int, default=300, help="render DPI (default 300)")
    ap.add_argument("--plot-scale", type=float, default=250.0,
                    help="plot scale denominator, e.g. 250 for 1:250 (default 250)")
    ap.add_argument("--layer", default="PROPERTY_LINE", help="layer name for output lines")
    ap.add_argument("--page", type=int, default=0, help="page index (default 0)")
    args = ap.parse_args()

    try:
        import fitz  # PyMuPDF
        import cv2
        import numpy as np
        from skimage.morphology import skeletonize
    except ImportError as e:
        sys.exit(f"missing dependency ({e}). Run: pip install -r requirements.txt")

    sc = args.dpi / 72.0
    # 1 paper-point = 1/72 in = 25.4/72 mm; at 1:scale that's *scale ground-mm.
    pt2m = 25.4 / 72 / 1000 * args.plot_scale

    page = fitz.open(args.pdf)[args.page]
    h_pt = page.rect.height
    pix = page.get_pixmap(dpi=args.dpi, colorspace=fitz.csGRAY)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    H, W = g.shape
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # long connected components = linework; short ones = stroked glyphs (dropped).
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    line_px = int(90 * args.dpi / 400)
    geom = np.zeros_like(bw)
    boxes = []
    for i in range(1, n):
        x, y, w, hh, area = stats[i]
        if max(w, hh) > line_px and area >= 6:
            if w > 0.9 * W and hh > 0.9 * H:
                continue  # the page border rectangle
            geom[lab == i] = 255
            boxes.append((w * hh, x, y, w, hh))
    if not boxes:
        sys.exit("no linework found — is this a vector plat?")

    boxes.sort(reverse=True)
    _, px, py, pw, ph = boxes[0]  # largest non-border linework CC = the parcel
    m = int(0.04 * max(pw, ph))
    x0, y0 = max(0, px - m), max(0, py - m)
    x1, y1 = min(W, px + pw + m), min(H, py + ph + m)

    skel = skeletonize(geom[y0:y1, x0:x1] > 0).astype(np.uint8) * 255
    segs = cv2.HoughLinesP(skel, 1, np.pi / 360, threshold=22, minLineLength=14, maxLineGap=6)
    segs = [] if segs is None else segs[:, 0, :]

    def tf(xpx, ypx):
        xpt, ypt = (xpx + x0) / sc, (ypx + y0) / sc
        return [round(xpt * pt2m, 4), round((h_pt - ypt) * pt2m, 4)]  # metres, north-up

    lines = []
    for sx0, sy0, sx1, sy1 in segs:
        a, b = tf(sx0, sy0), tf(sx1, sy1)
        lines.append([a[0], a[1], b[0], b[1], args.layer])

    json.dump({"lines": lines, "arcs": [], "circles": [], "texts": []}, open(args.out, "w"))

    if lines:
        xs = [c for L in lines for c in (L[0], L[2])]
        ys = [c for L in lines for c in (L[1], L[3])]
        ext = f"{max(xs) - min(xs):.1f} x {max(ys) - min(ys):.1f} m"
    else:
        ext = "empty"
    print(f"{len(lines)} segments -> {args.out}  (extent {ext})")
    print("NOTE: geometry only (no arcs/labels). Run fit_arcs.py for arcs; see STATUS.md.")


if __name__ == "__main__":
    main()

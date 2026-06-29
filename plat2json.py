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


def trace_polylines(skel, eps, min_len):
    """Walk a 1-px skeleton into ordered polylines, replacing Hough's fragment-
    soup (one curve -> many short stray segments) with one ordered polyline per
    edge. Split the skeleton graph at endpoints/junctions (degree != 2), trace
    each degree-2 chain between them, then seed any remaining closed loops (a
    boundary ring has no endpoints); drop chains shorter than min_len px (skeleton
    spurs, tick marks, stroked-glyph debris); Douglas-Peucker each survivor down
    to its vertices. Returns a list of polylines, each a list of (x, y) px points."""
    import numpy as np
    import cv2
    ys, xs = np.where(skel > 0)
    pts = set(zip(ys.tolist(), xs.tolist()))
    N8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    def nbrs(p):
        r, c = p
        return [q for q in ((r + dr, c + dc) for dr, dc in N8) if q in pts]

    deg = {p: len(nbrs(p)) for p in pts}
    used = set()  # consumed edges, as frozenset({a, b})

    def walk(start, nxt):
        path = [start, nxt]
        used.add(frozenset((start, nxt)))
        prev, cur = start, nxt
        while deg.get(cur, 0) == 2:  # follow the chain until a node or dead end
            ahead = [q for q in nbrs(cur)
                     if q != prev and frozenset((cur, q)) not in used]
            if not ahead:
                break
            q = ahead[0]
            used.add(frozenset((cur, q)))
            path.append(q)
            prev, cur = cur, q
        return path

    chains = []
    for p in pts:                      # branches anchored at endpoints/junctions
        if deg[p] != 2:
            for q in nbrs(p):
                if frozenset((p, q)) not in used:
                    chains.append(walk(p, q))
    for p in pts:                      # leftover closed loops (all degree-2)
        if deg[p] == 2:
            for q in nbrs(p):
                if frozenset((p, q)) not in used:
                    chains.append(walk(p, q))

    def plen(a):  # polyline pixel length
        return float(np.hypot(np.diff(a[:, 0]), np.diff(a[:, 1])).sum()) if len(a) > 1 else 0.0

    out = []
    for ch in chains:
        a = np.array([[c, r] for r, c in ch], dtype=np.float64)  # (x, y) = (col, row)
        if plen(a) < min_len:
            continue  # spur / tick / glyph debris
        if len(a) >= 3:
            s = cv2.approxPolyDP(a.astype(np.int32).reshape(-1, 1, 2), eps, False)
            a = s.reshape(-1, 2).astype(np.float64)
        out.append([(float(x), float(y)) for x, y in a])
    return out


def main():
    ap = argparse.ArgumentParser(description="Vector survey-plat PDF -> plan-JSON geometry.")
    ap.add_argument("pdf", help="input vector plat PDF")
    ap.add_argument("out", help="output plan-JSON path")
    ap.add_argument("--dpi", type=int, default=300, help="render DPI (default 300)")
    ap.add_argument("--plot-scale", type=float, default=250.0,
                    help="plot scale denominator, e.g. 250 for 1:250 (default 250)")
    ap.add_argument("--layer", default="PROPERTY_LINE", help="layer name for output lines")
    ap.add_argument("--page", type=int, default=0, help="page index (default 0)")
    ap.add_argument("--vectorize", choices=["trace", "hough"], default="trace",
                    help="skeleton -> geometry: 'trace' = ordered polylines "
                         "(default, clean); 'hough' = legacy fragment segments")
    ap.add_argument("--simplify-eps", type=float, default=2.0,
                    help="Douglas-Peucker epsilon (px) for --vectorize trace (default 2.0)")
    ap.add_argument("--min-len", type=float, default=0.0,
                    help="drop traced polylines shorter than N px (default 0 = 3x the "
                         "linework threshold; filters spurs + stroked-glyph debris. "
                         "Raise on text-dense sheets, lower to keep short lot lines)")
    ap.add_argument("--mask-text", action=argparse.BooleanOptionalAction, default=True,
                    help="erase the PDF text layer's word boxes from the raster before "
                         "skeletonizing, so labels don't pollute the linework (default on; "
                         "no-ops on scans / stroked-glyph plats with no text layer)")
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

    # Erase the text layer's word boxes so published labels don't pollute the
    # linework skeleton (the big noise source on text-dense vector plats). Exact
    # boxes from the PDF, so no fragile text/line heuristic; no-ops when the page
    # has no text layer (scans, stroked-glyph plats). Small gaps where a label sat
    # on a line are harmless to tracing (the chain just splits and re-traces).
    nmask = 0
    if args.mask_text:
        ox, oy = page.rect.x0, page.rect.y0
        pad = 2
        for wx0, wy0, wx1, wy1, *_ in page.get_text("words"):
            X0, Y0 = max(0, int((wx0 - ox) * sc) - pad), max(0, int((wy0 - oy) * sc) - pad)
            X1, Y1 = min(W, int((wx1 - ox) * sc) + pad), min(H, int((wy1 - oy) * sc) + pad)
            if X1 > X0 and Y1 > Y0:
                bw[Y0:Y1, X0:X1] = 0
                nmask += 1

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

    def tf(xpx, ypx):
        xpt, ypt = (xpx + x0) / sc, (ypx + y0) / sc
        return [round(xpt * pt2m, 4), round((h_pt - ypt) * pt2m, 4)]  # metres, north-up

    lines, polylines, npoly = [], [], 0
    if args.vectorize == "trace":
        polys = trace_polylines(skel, args.simplify_eps, args.min_len or line_px * 3)
        npoly = len(polys)
        for poly in polys:
            world = [tf(x, y) for x, y in poly]
            polylines.append([[p[0], p[1]] for p in world] + [args.layer])
            for i in range(len(world) - 1):
                a, b = world[i], world[i + 1]
                if a != b:
                    lines.append([a[0], a[1], b[0], b[1], args.layer])
    else:
        segs = cv2.HoughLinesP(skel, 1, np.pi / 360, threshold=22, minLineLength=14, maxLineGap=6)
        segs = [] if segs is None else segs[:, 0, :]
        for sx0, sy0, sx1, sy1 in segs:
            a, b = tf(sx0, sy0), tf(sx1, sy1)
            lines.append([a[0], a[1], b[0], b[1], args.layer])

    out = {"lines": lines, "arcs": [], "circles": [], "texts": []}
    if polylines:  # ordered chains: clean input for arc-fitting (fit_arcs.py)
        out["polylines"] = polylines
    json.dump(out, open(args.out, "w"))

    if lines:
        xs = [c for L in lines for c in (L[0], L[2])]
        ys = [c for L in lines for c in (L[1], L[3])]
        ext = f"{max(xs) - min(xs):.1f} x {max(ys) - min(ys):.1f} m"
    else:
        ext = "empty"
    how = f"{npoly} ordered polylines" if args.vectorize == "trace" else "hough fragments"
    masked = f", masked {nmask} text boxes" if nmask else ""
    print(f"{len(lines)} segments ({how}{masked}) -> {args.out}  (extent {ext})")
    print("NOTE: geometry only (no arcs/labels). Run fit_arcs.py for arcs; see STATUS.md.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""raster_lots.py - lot-fabric geometry from a RASTER plat (no vector layer).

The parcel/lot pipeline (assoc_study --crop-edges -> cogo_assemble) is built on
VECTOR segments from page.get_drawings(); on a scanned plat that is empty. This
module produces the ONE missing input - clean straight-line segments in page
POINTS - from the raster itself, so the existing planarize()/extract_faces()/
close_faces() in cogo_assemble run unchanged and yield lot faces.

pipeline: render -> ROI to the drawing -> binarize -> keep long components as
linework (drop small text glyphs + the page border) -> skeletonize -> trace
ordered polylines -> Douglas-Peucker vertex-pair segments -> page points ->
CONNECTIVITY REPAIR (morphological close + snap dangling ends onto the nearest
line + overshoot corners) -> planarize -> faces = lots.

The repair matters because a traced raster plat is a FOREST: lot side-lines
stop short of the road frontage and the ROW curve is chopped into disconnected
arcs, so no cycles exist. `--snap` (stitch a dangling end onto the nearest
line) + `--extend` (overshoot so near-miss corners cross) turn that forest into
a planar graph whose faces are lots.

KNOWN LIMIT: only STRAIGHT-frontage lots close. Cul-de-sac / loop-road lots and
any tract inside the loop have ARC frontages that straight-line faces cannot
close - those need arc-aware closing (curve-table C-tags), not endpoint repair.

    python raster_lots.py INPUT.pdf --page 1 [--dpi 250] [--roi x0,y0,x1,y1 frac]
                          [--close 9] [--snap 12] [--extend 6] [--min-face-area 2000]
                          [--out segs.json] [--viz faces.png]
"""
import argparse, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plat2json as P2J
import cogo_assemble as CA


def raster_segments(pdf, page_idx, dpi, roi, min_len_pt, simplify_eps_px, close_px):
    import fitz, cv2, numpy as np
    from skimage.morphology import skeletonize
    page = fitz.open(pdf)[page_idx]
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    H, W = g.shape
    x0, y0, x1, y1 = roi
    rx0, ry0, rx1, ry1 = int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)
    sub = g[ry0:ry1, rx0:rx1]
    _, bw = cv2.threshold(sub, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    sh, sw = bw.shape

    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    line_px = int(90 * dpi / 400)             # same linework threshold as plat2json
    geom = np.zeros_like(bw)
    for i in range(1, n):
        x, y, w, hh, area = stats[i]
        if max(w, hh) > line_px and area >= 6:
            if w > 0.95 * sw and hh > 0.95 * sh:
                continue                       # page border
            geom[lab == i] = 255               # KEEP ALL long components (not just largest)

    if close_px:
        # bridge label-broken and dash gaps so corners meet (raster-space fix)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px))
        geom = cv2.morphologyEx(geom, cv2.MORPH_CLOSE, k)
    skel = skeletonize(geom > 0).astype(np.uint8) * 255
    polys = P2J.trace_polylines(skel, simplify_eps_px, min_len_pt * dpi / 72.0)

    # px (ROI-local) -> page points (full page, y-down, matching get_drawings)
    sc = dpi / 72.0
    def to_pt(px, py):
        return ((px + rx0) / sc, (py + ry0) / sc)
    segs = []
    for poly in polys:
        pts = [to_pt(x, y) for x, y in poly]
        for a, b in zip(pts, pts[1:]):
            if math.hypot(b[0] - a[0], b[1] - a[1]) >= min_len_pt * 0.25:
                segs.append((a[0], a[1], b[0], b[1]))
    return segs, (W, H)


def face_area(cyc, nodes, edges):
    s = 0.0
    for ei, fwd in cyc:
        e = edges[ei]
        ax, ay = nodes[e["a"] if fwd else e["b"]]
        bx, by = nodes[e["b"] if fwd else e["a"]]
        s += ax * by - bx * ay
    return abs(s) / 2.0


def _nearest_on_seg(px, py, s):
    x0, y0, x1, y1 = s
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    if L2 < 1e-9:
        return None
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L2))
    qx, qy = x0 + t * dx, y0 + t * dy
    return qx, qy, math.hypot(px - qx, py - qy)


def snap_endpoints(segs, R, iters=2):
    """Pull each segment endpoint onto the nearest OTHER segment within R pt.
    This attaches lot side-lines that stop short of the road frontage onto the
    ROW line, and stitches the road's fragmented arc pieces to each other -
    the dominant source of dangling ends on a traced raster plat."""
    S = [list(s) for s in segs]
    for _ in range(iters):
        for i, s in enumerate(S):
            for e in (0, 2):
                px, py = s[e], s[e + 1]
                best, bestd = None, R
                for j, t in enumerate(S):
                    if j == i:
                        continue
                    r = _nearest_on_seg(px, py, t)
                    if r and r[2] < bestd:
                        bestd, best = r[2], (r[0], r[1])
                if best:
                    s[e], s[e + 1] = best
    return [tuple(s) for s in S
            if math.hypot(s[2] - s[0], s[3] - s[1]) >= 1.0]  # drop collapsed segs


def viz(nodes, edges, faces, out, mark_dangles=True):
    import numpy as np, cv2
    xs = [n[0] for n in nodes]; ys = [n[1] for n in nodes]
    if not xs:
        return
    mnx, mxx, mny, mxy = min(xs), max(xs), min(ys), max(ys)
    scale = 1500.0 / max(mxx - mnx, mxy - mny)
    Wp = int((mxx - mnx) * scale) + 40; Hp = int((mxy - mny) * scale) + 40
    img = np.full((Hp, Wp, 3), 255, np.uint8)
    def T(x, y):
        return (int((x - mnx) * scale) + 20, int((y - mny) * scale) + 20)  # y-down
    rng = np.random.default_rng(1)
    for cyc in faces:                          # fill faces
        ring = [T(*nodes[edges[ei]["a"] if fwd else edges[ei]["b"]]) for ei, fwd in cyc]
        c = tuple(int(v) for v in rng.integers(60, 230, 3))
        cv2.fillPoly(img, [np.array(ring, np.int32)], c)
    for e in edges:                            # edges on top
        cv2.line(img, T(*nodes[e["a"]]), T(*nodes[e["b"]]), (40, 40, 40), 1, cv2.LINE_AA)
    if mark_dangles:                           # degree-1 nodes = broken connectivity
        deg = {}
        for e in edges:
            deg[e["a"]] = deg.get(e["a"], 0) + 1
            deg[e["b"]] = deg.get(e["b"], 0) + 1
        for ni, (x, y) in enumerate(nodes):
            if deg.get(ni, 0) == 1:
                cv2.circle(img, T(x, y), 5, (0, 0, 255), -1)   # red = dangling end
    cv2.imwrite(out, img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--dpi", type=int, default=250)
    ap.add_argument("--roi", default="0.02,0.13,0.63,0.90",
                    help="drawing ROI as x0,y0,x1,y1 fractions (drop title/tables)")
    ap.add_argument("--min-len", type=float, default=12.0, help="min segment (pt)")
    ap.add_argument("--eps", type=float, default=2.0, help="Douglas-Peucker px")
    # defaults tuned for a ~250 dpi render of a busy subdivision sheet; retune per plat
    ap.add_argument("--close", type=int, default=9, help="morphological close kernel px (gap bridge)")
    ap.add_argument("--tol", type=float, default=4.0, help="planarize node/junction tol (pt)")
    ap.add_argument("--extend", type=float, default=6.0, help="overshoot each segment end (pt) so near-miss corners cross")
    ap.add_argument("--min-face-area", type=float, default=2000.0, help="drop faces below N pt^2 (sliver filter)")
    ap.add_argument("--snap", type=float, default=12.0, help="snap dangling endpoints onto nearest segment within N pt (road-frontage stitch)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--viz", default=None)
    a = ap.parse_args()
    roi = tuple(float(v) for v in a.roi.split(","))
    segs, (W, H) = raster_segments(a.pdf, a.page, a.dpi, roi, a.min_len, a.eps, a.close)
    if a.snap:
        segs = snap_endpoints(segs, a.snap)
    if a.extend:
        ext = []                              # overshoot each end so near-miss corners cross
        for x0, y0, x1, y1 in segs:
            L = math.hypot(x1 - x0, y1 - y0)
            if L < 1e-6:
                continue
            ux, uy = (x1 - x0) / L, (y1 - y0) / L
            ext.append((x0 - ux * a.extend, y0 - uy * a.extend,
                        x1 + ux * a.extend, y1 + uy * a.extend))
        segs = ext
    nodes, edges = CA.planarize(segs, tol=a.tol)
    faces = CA.extract_faces(nodes, edges)
    areas = sorted((face_area(f, nodes, edges) for f in faces), reverse=True)
    real = [f for f in faces if face_area(f, nodes, edges) >= a.min_face_area]
    deg = {}
    for e in edges:
        deg[e["a"]] = deg.get(e["a"], 0) + 1
        deg[e["b"]] = deg.get(e["b"], 0) + 1
    dangles = sum(1 for ni in range(len(nodes)) if deg.get(ni, 0) == 1)
    print(f"segments (raster)      : {len(segs)}")
    print(f"planar nodes / edges   : {len(nodes)} / {len(edges)}   dangling ends: {dangles}")
    print(f"faces total            : {len(faces)}")
    print(f"faces >= {a.min_face_area:g} pt^2      : {len(real)}  (real-lot candidates)")
    print(f"top face areas (pt^2)  : {[int(x) for x in areas[:16]]}")
    faces = real if a.min_face_area else faces
    if a.out:
        json.dump({"lines": [[*s, "PROPERTY_LINE"] for s in segs],
                   "planar": {"nodes": [[round(x, 2), round(y, 2)] for x, y in nodes],
                              "edges": edges},
                   "faces": [[[ei, fwd] for ei, fwd in f] for f in faces]},
                  open(a.out, "w"))
        print("->", a.out)
    if a.viz:
        viz(nodes, edges, faces, a.viz)
        print("->", a.viz)


if __name__ == "__main__":
    main()

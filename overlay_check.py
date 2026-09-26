#!/usr/bin/env python3
"""overlay_check.py — visual + numeric QA: does the reconstruction lie on the ink?

Renders the reconstructed geometry back into the source plat page's pixel space
and diffs it against the printed linework. Two inputs are accepted for the
"reconstruction" side:

  --plan PLAN.json   a plan-JSON (plat2json.py / fit_arcs.py / cogo_assemble.py:
                     "lines" [x1,y1,x2,y2,layer], "arcs" [cx,cy,r,a0deg,a1deg,layer],
                     "circles" [cx,cy,r,...], "polylines" [[x,y]|[x,y,bulge],...,layer])
  --plot PLOT.pdf    a plotted VECTOR PDF (e.g. Open CAD Studio's output after
                     LS_IMPORTPLAN) — exact paths are pulled with fitz, never
                     re-rasterized/re-traced.

Plan-JSONs live in different coordinate spaces depending on the producer
(cogo_assemble: page points, y-down; plat2json.py: ground metres, north-up), and
a plotted PDF is in its own plot space entirely. So registration is FITTED, not
assumed: a similarity transform (scale, rotation, translation) x {y-flip, no
flip} is seeded from cheap candidates (pt-space identity; bbox match against the
parcel linework) and refined by minimizing the mean distance-transform residual
of the transformed geometry against the page ink (Nelder-Mead). --no-fit skips
refinement and uses the exact pt-space mapping (dpi/72, y-down) for
cogo_assemble-style plans.

Outputs:
  * overlay PNG — source ink in RED, reconstruction in CYAN, agreement BLACK,
    background white: disagreement screams in color, agreement reads as dark.
  * metrics — recon->ink chamfer (mean/p95, in px and in the geometry's own
    units, i.e. ground units for world-space plans), recon_on_ink_pct (% of
    reconstruction within --tol-px of ink), linework_covered_pct (% of the
    page's long-component linework skeleton within --tol-px of the
    reconstruction; text/labels are excluded by the component-size filter, but
    dashed/short strokes may be excluded with them — treat as a floor).

Usage:
    python overlay_check.py PLAT.pdf --plan plan.json  [--page 1] [--dpi 250]
    python overlay_check.py PLAT.pdf --plot plotted.pdf [--page 1]
    ... [--roi fx0,fy0,fx1,fy1] [--tol-px 3] [--out overlay.png]
        [--report report.json] [--fail-under PCT] [--no-fit]

Run with the system/venv python (needs fitz + cv2 + scipy, same as the pipeline).
"""
import argparse
import json
import math
import os
import sys

import numpy as np


# ---------------------------------------------------------------- source page

def page_gray(pdf, page, dpi):
    import fitz
    pix = fitz.open(pdf)[page].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)


def ink_masks(gray, dpi, roi=None, text_filter=True):
    """(ink, linework): ink = every dark pixel on the FULL page (the chamfer /
    registration target — never ROI-cropped, or the fitter is blinded to ink
    the geometry legitimately covers and squeezes the transform to fit the
    crop); linework = long connected components only, page border dropped
    (same size heuristic as plat2json.py, so text and stroked glyphs don't
    count as 'lines we missed'), optionally ROI-cropped — the ROI scopes the
    coverage DENOMINATOR to the drawing area, excluding title block / notes /
    vicinity-map furniture that no reconstruction should chase.

    text_filter drops TEXT-LIKE survivors of the size filter (a tall bearing
    string or a stamp passes 'long component' easily): estimate each
    component's stroke length as area / stroke-width (stroke-width = 2x mean
    in-ink distance-transform) and drop it when that length exceeds ~2.2x the
    bbox extent — a line or gentle arc carries about 1-1.5x its extent in
    stroke, a glyph cluster several times that. Components spanning >8% of
    the page are exempt (the parcel network is one giant CC and must stay)."""
    import cv2
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    line_px = int(90 * dpi / 400)
    H, W = bw.shape
    keep = np.zeros(n, bool)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if max(w, h) > line_px and area >= 6 and not (w > 0.9*W and h > 0.9*H):
            keep[i] = True
    if text_filter and keep.any():
        dtin = cv2.distanceTransform(bw, cv2.DIST_L2, 5)
        sums = np.bincount(lab.ravel(), weights=dtin.ravel(), minlength=n)
        big = 0.08 * max(W, H)
        for i in np.nonzero(keep)[0]:
            x, y, w, h, area = stats[i]
            if max(w, h) >= big:
                continue
            sw = max(2.0 * sums[i] / area, 1.0)
            if (area / sw) / max(w, h) > 2.2:
                keep[i] = False
    linework = keep[lab]
    if roi:
        m = np.zeros_like(linework)
        m[int(roi[1]*H):int(roi[3]*H), int(roi[0]*W):int(roi[2]*W)] = True
        linework &= m
    return bw > 0, linework


# ------------------------------------------------------------ geometry source

def _arc_pts(cx, cy, r, a0, a1, step=2.0):
    if a1 < a0:
        a1 += 360.0
    n = max(2, int(math.ceil((a1 - a0) / step)) + 1)
    a = np.radians(np.linspace(a0, a1, n))
    return np.column_stack([cx + r*np.cos(a), cy + r*np.sin(a)])


def _bulge_pts(p0, p1, b, step=2.0):
    """Sample the arc from p0 to p1 with bulge b = tan(delta/4). b>0 = CCW."""
    (x0, y0), (x1, y1) = p0, p1
    delta = 4.0 * math.atan(b)
    c = math.hypot(x1-x0, y1-y0)
    if c < 1e-12 or abs(delta) < 1e-9:
        return np.array([[x0, y0], [x1, y1]])
    mx, my = (x0+x1)/2, (y0+y1)/2
    h = (c/2) / math.tan(delta/2)                      # signed center offset
    nx, ny = -(y1-y0)/c, (x1-x0)/c                     # left normal of chord
    cx, cy = mx + nx*h, my + ny*h
    r = abs((c/2) / math.sin(delta/2))
    a0 = math.atan2(y0-cy, x0-cx)
    n = max(2, int(math.ceil(abs(math.degrees(delta)) / step)) + 1)
    a = a0 + np.linspace(0.0, delta, n)
    return np.column_stack([cx + r*np.cos(a), cy + r*np.sin(a)])


def plan_polylines(plan):
    """Every drawable in the plan as an Nx2 polyline. Duplicates between
    'lines'/'arcs' and their 'polylines' chain forms are harmless here — same
    ink, drawn twice."""
    out = []
    for L in plan.get("lines", []):
        x1, y1, x2, y2 = (float(v) for v in L[:4])
        out.append(np.array([[x1, y1], [x2, y2]]))
    for A in plan.get("arcs", []):
        cx, cy, r, a0, a1 = (float(v) for v in A[:5])
        out.append(_arc_pts(cx, cy, r, a0, a1))
    for C in plan.get("circles", []):
        cx, cy, r = (float(v) for v in C[:3])
        out.append(_arc_pts(cx, cy, r, 0.0, 360.0))
    for P in plan.get("polylines", []):
        pts = P[:-1] if P and isinstance(P[-1], str) else P
        for i in range(len(pts) - 1):
            p0, p1 = pts[i], pts[i+1]
            if len(p0) >= 3 and p0[2]:
                out.append(_bulge_pts(p0[:2], p1[:2], float(p0[2])))
            else:
                out.append(np.array([p0[:2], p1[:2]], float))
    return [p for p in out if len(p) >= 2]


def plot_polylines(pdf, page):
    """Exact stroke geometry out of a plotted vector PDF (never re-traced).
    Bezier curves are flattened by uniform t-sampling — plenty for QA overlay."""
    import fitz
    out = []
    pg = fitz.open(pdf)[page]
    for d in pg.get_drawings():
        for it in d["items"]:
            k = it[0]
            if k == "l":
                a, b = it[1], it[2]
                out.append(np.array([[a.x, a.y], [b.x, b.y]]))
            elif k == "c":
                p0, p1, p2, p3 = it[1:5]
                t = np.linspace(0, 1, 16)[:, None]
                pts = ((1-t)**3 * np.array([[p0.x, p0.y]]) +
                       3*(1-t)**2*t * np.array([[p1.x, p1.y]]) +
                       3*(1-t)*t**2 * np.array([[p2.x, p2.y]]) +
                       t**3 * np.array([[p3.x, p3.y]]))
                out.append(pts)
            elif k == "re":
                r = it[1]
                out.append(np.array([[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1],
                                     [r.x0, r.y1], [r.x0, r.y0]]))
            elif k == "qu":
                q = it[1]
                out.append(np.array([[q.ul.x, q.ul.y], [q.ur.x, q.ur.y],
                                     [q.lr.x, q.lr.y], [q.ll.x, q.ll.y],
                                     [q.ul.x, q.ul.y]]))
    return out


# -------------------------------------------------------- transform + fitting

def densify(polys, spacing):
    """Points every `spacing` geometry-units along every polyline."""
    pts = []
    for p in polys:
        for i in range(len(p) - 1):
            a, b = p[i], p[i+1]
            L = float(np.hypot(*(b - a)))
            n = max(2, int(L / spacing) + 1)
            t = np.linspace(0, 1, n)[:, None]
            pts.append(a + t * (b - a))
    return np.vstack(pts) if pts else np.zeros((0, 2))


def apply_T(pts, s, th, tx, ty, flip):
    c, sn = math.cos(th), math.sin(th)
    x, y = pts[:, 0], pts[:, 1] * flip
    return np.column_stack([s*(c*x - sn*y) + tx, s*(sn*x + c*y) + ty])


def _residual(dt, q, clip):
    H, W = dt.shape
    xi = np.clip(np.round(q[:, 0]).astype(int), 0, W-1)
    yi = np.clip(np.round(q[:, 1]).astype(int), 0, H-1)
    r = dt[yi, xi].astype(float)
    oob = (q[:, 0] < 0) | (q[:, 0] >= W) | (q[:, 1] < 0) | (q[:, 1] >= H)
    r[oob] = clip
    return r


def _bbox_seed(gx0, gy0, gx1, gy1, bx0, by0, bx1, by1, flip):
    s = min((bx1-bx0) / max(gx1-gx0, 1e-9), (by1-by0) / max(gy1-gy0, 1e-9))
    gcx, gcy = (gx0+gx1)/2, ((gy0+gy1)/2) * flip
    return (s, 0.0, (bx0+bx1)/2 - s*gcx, (by0+by1)/2 - s*gcy, flip)


def _nm(cost, x0, steps, args):
    """Nelder-Mead with an EXPLICIT initial simplex. The default simplex
    perturbs each coordinate by 5% of its value — for a translation seeded at
    0 that is ~nothing, and the fit silently never explores translation (bug
    observed: a registration 19 px off scored 46% on-ink while the true
    optimum was 100%). Explicit per-parameter steps make the basin reachable."""
    from scipy.optimize import minimize
    sim = [np.asarray(x0, float)]
    for i, st in enumerate(steps):
        v = np.asarray(x0, float).copy(); v[i] += st; sim.append(v)
    return minimize(cost, x0, args=args, method="Nelder-Mead",
                    options={"initial_simplex": np.array(sim), "adaptive": True,
                             "xatol": 1e-3, "fatol": 1e-4, "maxiter": 800})


def fit_transform(sample, dt, dpi, no_fit=False):
    """Best similarity transform geometry->pixels: exhaustive cheap seeding,
    then coarse-to-fine Nelder-Mead.

    A single bbox-center seed is NOT enough: when the geometry's aspect ratio
    differs from the target box (e.g. a whole-page trace that fills the page
    height but only part of its width), center-matching starts >1000 px off in
    translation — outside any local basin. So seed the cross product of
    {ink bbox, page bbox} x {y-flip, no flip} x {scale-by-width, -height, min}
    x {center + 4 corner anchors}, plus the exact pt-space y-down mapping at
    dpi/72 (cogo_assemble plans register identically under it). Each seed is
    one vectorized residual eval — ~60 evals costs nothing — then survivors
    are refined on 8x / 4x / 1x downsampled distance fields."""
    H, W = dt.shape
    ys, xs = np.nonzero(dt == 0)
    gx0, gy0 = sample.min(0); gx1, gy1 = sample.max(0)
    boxes = [(xs.min(), ys.min(), xs.max(), ys.max()), (0, 0, W, H)]
    seeds = [(dpi/72.0, 0.0, 0.0, 0.0, +1.0)]
    if no_fit:
        return seeds[0]
    for flip in (+1.0, -1.0):
        for bx0, by0, bx1, by1 in boxes:
            sw = (bx1-bx0) / max(gx1-gx0, 1e-9)
            sh = (by1-by0) / max(gy1-gy0, 1e-9)
            for s in {sw, sh, min(sw, sh)}:
                X0, X1 = s*gx0, s*gx1
                Y0, Y1 = sorted((s*flip*gy0, s*flip*gy1))
                for tx, ty in [((bx0+bx1)/2-(X0+X1)/2, (by0+by1)/2-(Y0+Y1)/2),
                               (bx0-X0, by0-Y0), (bx1-X1, by0-Y0),
                               (bx0-X0, by1-Y1), (bx1-X1, by1-Y1)]:
                    seeds.append((s, 0.0, tx, ty, flip))

    pts = sample[np.random.default_rng(0).choice(len(sample), 3000, replace=False)] \
        if len(sample) > 3000 else sample

    def cost(v, flip, dtk, k, clip):
        s, th, tx, ty = math.exp(v[0]), v[1], v[2], v[3]
        r = _residual(dtk, apply_T(pts, s, th, tx, ty, flip) / k, clip)
        return float(np.minimum(r, clip).mean())

    # rank all seeds at 8x (distance values stay full-res px)
    dt8, dt4 = dt[::8, ::8], dt[::4, ::4]
    ranked = sorted(seeds, key=lambda c: cost(
        (math.log(c[0]), c[1], c[2], c[3]), c[4], dt8, 8, 200.0))

    survivors = []
    for s, th, tx, ty, flip in ranked[:6]:
        r = _nm(cost, [math.log(s), th, tx, ty], [0.08, 0.03, 200.0, 200.0], (flip, dt8, 8, 200.0))
        survivors.append((r.fun, r.x, flip))
    survivors.sort(key=lambda t: t[0])

    mid = []
    for _, x, flip in survivors[:3]:
        r = _nm(cost, list(x), [0.03, 0.01, 40.0, 40.0], (flip, dt4, 4, 80.0))
        mid.append((r.fun, r.x, flip))
    mid.sort(key=lambda t: t[0])

    best, best_c = None, None
    for _, x, flip in mid[:2]:
        r = _nm(cost, list(x), [0.008, 0.003, 6.0, 6.0], (flip, dt, 1, 25.0))
        if best_c is None or r.fun < best_c:
            best_c, best = r.fun, (math.exp(r.x[0]), r.x[1], r.x[2], r.x[3], flip)
    return best


# --------------------------------------------------------------------- output

def render_recon(polys, T, shape, thick=2):
    import cv2
    m = np.zeros(shape, np.uint8)
    for p in polys:
        q = np.round(apply_T(p, *T)).astype(np.int32)
        cv2.polylines(m, [q.reshape(-1, 1, 2)], False, 255, thick)
    return m > 0


def main():
    import cv2
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf", help="source plat PDF")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--plan", help="plan-JSON reconstruction")
    src.add_argument("--plot", help="plotted vector PDF reconstruction (e.g. OCS output)")
    ap.add_argument("--page", type=int, default=0, help="source page index")
    ap.add_argument("--plot-page", type=int, default=0, help="page index in --plot PDF")
    ap.add_argument("--dpi", type=int, default=250)
    ap.add_argument("--roi", default=None, help="fx0,fy0,fx1,fy1 fractional crop of source ink")
    ap.add_argument("--tol-px", type=float, default=3.0, help="on-ink tolerance, pixels")
    ap.add_argument("--no-fit", action="store_true",
                    help="skip refinement; exact pt-space y-down mapping (cogo_assemble plans)")
    ap.add_argument("--no-text-filter", action="store_true",
                    help="keep text-like components in the coverage denominator")
    ap.add_argument("--out", default=None, help="overlay PNG path")
    ap.add_argument("--miss", default=None,
                    help="write a miss-map PNG (covered linework teal, missed red, recon "
                         "gray) and print the least-covered large ink components — the "
                         "work-list for improving linework_covered_pct")
    ap.add_argument("--report", default=None, help="write metrics JSON here")
    ap.add_argument("--fail-under", type=float, default=None,
                    help="exit 2 if recon_on_ink_pct is below this")
    a = ap.parse_args()

    gray = page_gray(a.pdf, a.page, a.dpi)
    roi = tuple(float(v) for v in a.roi.split(",")) if a.roi else None
    ink, linework = ink_masks(gray, a.dpi, roi, text_filter=not a.no_text_filter)

    if a.plan:
        polys = plan_polylines(json.load(open(a.plan)))
        recon_name = os.path.basename(a.plan)
    else:
        polys = plot_polylines(a.plot, a.plot_page)
        recon_name = os.path.basename(a.plot)
    if not polys:
        sys.exit(f"no geometry in {recon_name}")

    # distance-to-ink field: input 0 at ink -> DT = distance to nearest ink px
    dt = cv2.distanceTransform((~ink).astype(np.uint8), cv2.DIST_L2, 5)

    ext = max(float(np.ptp(np.vstack(polys)[:, 0])), float(np.ptp(np.vstack(polys)[:, 1])), 1e-9)
    sample = densify(polys, spacing=ext / 3000.0)      # ~3k pts along the geometry
    T = fit_transform(sample, dt, a.dpi, no_fit=a.no_fit)
    s, th, tx, ty, flip = T

    q = apply_T(sample, *T)
    res = _residual(dt, q, clip=1e9)
    on_ink = float((res <= a.tol_px).mean() * 100)
    ch_mean, ch_p95 = float(res.mean()), float(np.percentile(res, 95))

    recon = render_recon(polys, T, gray.shape)
    dt_r = cv2.distanceTransform((~recon).astype(np.uint8), cv2.DIST_L2, 5)
    try:
        from skimage.morphology import skeletonize
        lw = skeletonize(linework)                     # 1px centerline: unbiased denominator
    except ImportError:
        lw = linework
    ly, lx = np.nonzero(lw)
    cov = dt_r[ly, lx] <= a.tol_px if len(ly) else np.zeros(0, bool)
    covered = float(cov.mean() * 100) if len(ly) else float("nan")

    if a.miss:
        # covered skeleton teal, missed red (dilated for visibility), recon gray
        mm = np.full((*gray.shape, 3), 255, np.uint8)
        mm[recon] = (185, 185, 185)
        for idx, color in ((cov, (0, 150, 150)), (~cov, (230, 0, 0))):
            m = np.zeros(gray.shape, np.uint8)
            m[ly[idx], lx[idx]] = 255
            mm[cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=2) > 0] = color
        cv2.imwrite(a.miss, mm[:, :, ::-1])
        # attribution: coverage per ink component -> the concrete work-list
        n, lab, stats, _ = cv2.connectedComponentsWithStats(linework.astype(np.uint8), 8)
        cc = lab[ly, lx]
        rows = sorted(((float(cov[cc == i].mean() * 100), int((cc == i).sum()), stats[i])
                       for i in range(1, n) if (cc == i).sum() >= 200), key=lambda r: r[0])
        print(f"  miss map -> {a.miss}; least-covered large ink components:")
        for pc, tot, st in rows[:10]:
            print(f"    {pc:5.1f}% covered | {tot:6d} skel px | bbox x={st[0]} y={st[1]} {st[2]}x{st[3]}")

    # overlay: ink kills G+B (-> red), recon kills R (-> cyan), both -> black
    out_img = np.full((*gray.shape, 3), 255, np.uint8)
    out_img[ink, 1] = 0; out_img[ink, 2] = 0
    out_img[recon, 0] = 0
    out_png = a.out or (os.path.splitext(a.plan or a.plot)[0] + ".overlay.png")
    cv2.imwrite(out_png, out_img[:, :, ::-1])          # RGB -> BGR for cv2

    rep = {
        "source": {"pdf": os.path.basename(a.pdf), "page": a.page, "dpi": a.dpi},
        "recon": recon_name,
        "transform": {"scale_px_per_unit": round(s, 6), "rotation_deg": round(math.degrees(th), 4),
                      "tx": round(tx, 2), "ty": round(ty, 2), "y_flip": flip < 0,
                      "fitted": not a.no_fit},
        "chamfer_px": {"mean": round(ch_mean, 3), "p95": round(ch_p95, 3)},
        "chamfer_units": {"mean": round(ch_mean / s, 4), "p95": round(ch_p95 / s, 4),
                          "note": "geometry units (ground units for world-space plans; "
                                  "page points for cogo_assemble plans)"},
        "recon_on_ink_pct": round(on_ink, 2),
        "linework_covered_pct": round(covered, 2),
        "tol_px": a.tol_px,
        "overlay_png": out_png,
    }
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1)
    print(f"[{recon_name}] vs {os.path.basename(a.pdf)} p{a.page} @ {a.dpi}dpi")
    print(f"  transform: scale {s:.4f} px/unit, rot {math.degrees(th):.3f} deg, "
          f"flip {'Y' if flip < 0 else 'N'}{' (no-fit)' if a.no_fit else ''}")
    print(f"  recon->ink chamfer: mean {ch_mean:.2f} px ({ch_mean/s:.3f} units), "
          f"p95 {ch_p95:.2f} px ({ch_p95/s:.3f} units)")
    print(f"  recon on ink (<= {a.tol_px:g} px): {on_ink:.1f}%")
    print(f"  page linework covered by recon:   {covered:.1f}%")
    print(f"  -> {out_png}")
    if a.fail_under is not None and on_ink < a.fail_under:
        sys.exit(2)


if __name__ == "__main__":
    main()

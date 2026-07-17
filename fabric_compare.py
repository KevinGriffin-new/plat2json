"""Fit reconstructed plan faces onto a county parcel fabric: identity + position.

The fabric (assessor tax parcels, ArcGIS FeatureServer snapshot, see
eval/goldens/area482.fabric.utm26912.json) is a COARSE oracle on area
(~0.1-1%: below the closure/printed-area gates) but AUTHORITATIVE on
absolute position, which the internal oracles cannot see at all. Position
resolves the equal-area-class identity ambiguity face_check flags
("area-class label, not identity"): on 482.pdf it corrected 7/16 labels
and independently confirmed the open set {LOT 1, LOT 3}.

Method (all validated on 482.pdf, consensus RMS 0.25 m over 16 lots):
  1. plan-JSON -> planarize/dedupe/stitch/extract_faces (face_check's path).
  2. RANSAC scale fit of face areas onto printed areas (one area-unit/unit^2;
     units follow the fabric snapshot: sqft for Wyoming, m2 for ParcelMap BC).
  3. Similarity fit plan->fabric. Do NOT anchor naively on unique-printed-
     area matches - near-equal-value mislabels poison the fit (RMS 76 m).
     RANSAC over anchor triples, scored by consensus over ALL
     area-compatible (face, parcel) centroid pairs, then refit on the
     consensus set. Sanity check: recovered rotation should approximate
     the UTM grid convergence at the site (1.24 deg at 482's longitude).
  4. Greedy 1:1 nearest-centroid assignment -> per-lot identity, centroid
     residual, area deltas vs fabric and printed.

Usage:
    python fabric_compare.py PLAN.json FABRIC.json PRINTED.json
        [--out FACES.geojson]   # faces in the fabric CRS, for QGIS overlay
"""
import argparse, json, math, sys
from collections import Counter
from itertools import combinations

import cogo_assemble as CA
import raster_lots as RL
import face_check as FC

FT2_PER_M2 = 1 / 0.09290341161  # sq ft per sq m (US survey foot)


def poly_centroid(pts):
    a = cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        a += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    a /= 2.0
    return (cx / (6 * a), cy / (6 * a))


def ring_area(ring):
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def umeyama(src, dst, mirror):
    """Least-squares similarity transform src->dst (optionally x-mirrored)."""
    n = len(src)
    sx = [(-x if mirror else x, y) for x, y in src]
    mx = sum(p[0] for p in sx) / n
    my = sum(p[1] for p in sx) / n
    ux = sum(p[0] for p in dst) / n
    uy = sum(p[1] for p in dst) / n
    sxx = sxy = syx = syy = var = 0.0
    for (ax, ay), (bx, by) in zip(sx, dst):
        ax -= mx; ay -= my; bx -= ux; by -= uy
        sxx += ax * bx; sxy += ax * by
        syx += ay * bx; syy += ay * by
        var += ax * ax + ay * ay
    if var <= 0:  # coincident sources: 1 distinct anchor -> nan fit (observed)
        raise ZeroDivisionError("degenerate umeyama: zero source variance")
    theta = math.atan2(sxy - syx, sxx + syy)
    s = ((sxx + syy) * math.cos(theta) + (sxy - syx) * math.sin(theta)) / var
    c, sn = math.cos(theta), math.sin(theta)
    tx = ux - s * (c * mx - sn * my)
    ty = uy - s * (sn * mx + c * my)

    def apply(p):
        x, y = (-p[0] if mirror else p[0], p[1])
        return (s * (c * x - sn * y) + tx, s * (sn * x + c * y) + ty)

    rms = math.sqrt(sum((apply(a)[0] - b[0]) ** 2 + (apply(a)[1] - b[1]) ** 2
                        for a, b in zip(src, dst)) / n)
    return apply, s, math.degrees(theta), rms


def sample_ring(ring, step):
    """Points every `step` along a closed ring's edges (ring units)."""
    r = [tuple(p) for p in ring]
    if r[0] != r[-1]:
        r.append(r[0])
    pts = []
    for (x1, y1), (x2, y2) in zip(r, r[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        n = max(1, int(L / step))
        for t in range(n):
            f = t / n
            pts.append((x1 + f * (x2 - x1), y1 + f * (y2 - y1)))
    return pts


def shape_anchor_fit(face_rings, fabric_rings, s0, trim=0.7, step_m=2.0,
                     ctx_rings=None, rot_prior=None, mirrors=(False, True)):
    """SHAPE anchoring: trimmed similarity ICP between edge-sampled point
    clouds — reconstructed face rings (plan units) onto fabric parcel rings
    (projected metres). No area-value correspondence at all, which makes it
    (a) the correct anchor in the DRIFT regime, where proposal areas differ
    from registered areas by construction, and (b) the fallback when
    unique-value anchoring starves in the extraction regime (observed on
    EPP46435: a capture improvement shifted the match set and the value
    anchors landed on the still-broken lots).

    Seeded by the printed-area scale fit (s0, m/unit) and a coarse 5-degree
    rotation sweep with mirror test; scale is refined but clamped to
    +-15% of s0 so a partial overlap cannot collapse the cloud.
    Returns (apply, scale_m_per_unit, rot_deg, mirrored, trimmed_rms_m, n)."""
    import numpy as np
    from scipy.spatial import cKDTree

    src = np.array([p for ring in face_rings
                    for p in sample_ring(ring, step_m / s0)], float)
    dst = np.array([p for ring in fabric_rings
                    for p in sample_ring(ring, step_m)], float)

    def cap(A, n):
        return A[np.linspace(0, len(A) - 1, n).astype(int)] if len(A) > n else A

    src, dst = cap(src, 4000), cap(dst, 6000)
    if ctx_rings:
        # context parcels join the registration target under their own cap
        # and a coarser step, so a big neighbourhood cannot dilute the
        # subject block out of the cloud (observed: 520 context parcels
        # under one shared cap starved the subject to ~500 pts and every
        # variant's ICP degraded)
        ctx = np.array([p for ring in ctx_rings
                        for p in sample_ring(ring, 1.5 * step_m)], float)
        dst = np.concatenate([dst, cap(ctx, 4000)])
    tree = cKDTree(dst)
    dc = dst.mean(0)

    def icp(mirror, theta0, t0=None, s_init=None):
        s_init = s0 if s_init is None else s_init
        S = src * [-1.0, 1.0] if mirror else src.copy()
        sc = S.mean(0)
        th, s = theta0, s_init
        t = None if t0 is None else np.asarray(t0, float)
        k = max(3, int(len(S) * trim))
        for _ in range(60):
            c, sn = math.cos(th), math.sin(th)
            R = np.array([[c, -sn], [sn, c]])
            if t is None:
                t = dc - s * (R @ sc)
            P = S @ (s * R).T + t
            d, j = tree.query(P)
            keep = np.argsort(d)[:k]
            A, B = S[keep], dst[j[keep]]
            ca, cb = A.mean(0), B.mean(0)
            A0, B0 = A - ca, B - cb
            Sxx = float(A0[:, 0] @ B0[:, 0]); Sxy = float(A0[:, 0] @ B0[:, 1])
            Syx = float(A0[:, 1] @ B0[:, 0]); Syy = float(A0[:, 1] @ B0[:, 1])
            var = float((A0 ** 2).sum())
            if var <= 0:
                break
            th_new = math.atan2(Sxy - Syx, Sxx + Syy)
            s_new = ((Sxx + Syy) * math.cos(th_new)
                     + (Sxy - Syx) * math.sin(th_new)) / var
            s_new = min(max(s_new, 0.85 * s_init), 1.15 * s_init)
            c, sn = math.cos(th_new), math.sin(th_new)
            R = np.array([[c, -sn], [sn, c]])
            t_new = cb - s_new * (R @ ca)
            moved = abs(s_new - s) * 100 + abs(th_new - th) * 100 + \
                float(np.hypot(*(t_new - t)))
            th, s, t = th_new, s_new, t_new
            if moved < 1e-4:
                break
        c, sn = math.cos(th), math.sin(th)
        P = S @ np.array([[s * c, s * sn], [-s * sn, s * c]]) + t
        d, _ = tree.query(P)
        rms = float(np.sqrt((np.sort(d)[:k] ** 2).mean()))
        return rms, th, s, (float(t[0]), float(t[1]))

    # coarse seed: exhaustive (mirror x scale x 5-deg rotation) sweep scored
    # by RASTER CROSS-CORRELATION. The correlation peak supplies both the
    # translation and an unbiased score in one shot: counting coincident
    # occupied cells rewards neither scale collapse (a shrunken cloud
    # overlaps fewer cells) nor is it fooled by centroid skew (title-block
    # faces pull the plan centroid off the block, which made every
    # NN-scored rot-0 seed rank below the impostors on the Surrey pilot —
    # the true basin was never ICP'd). Scale brackets around the area-fit
    # s0; a radius-ratio estimate was tried and removed (meaningless under
    # partial overlap).
    from scipy.signal import correlate
    scales = [s0 * m for m in (0.88, 1.0, 1.14)]
    cell = 3.0
    do = dst.min(0)
    gd = np.zeros((int((dst[:, 1].max() - do[1]) / cell) + 2,
                   int((dst[:, 0].max() - do[0]) / cell) + 2))
    for (x, y) in dst:
        gd[int((y - do[1]) / cell), int((x - do[0]) / cell)] = 1.0

    def corr_seeds(mirror, th, si, npeaks=2):
        """Top translation peaks + normalized scores for one pose."""
        S = src * [-1.0, 1.0] if mirror else src
        c, sn = math.cos(th), math.sin(th)
        S2 = S @ np.array([[si * c, si * sn], [-si * sn, si * c]])
        so = S2.min(0)
        gs = np.zeros((int((S2[:, 1].max() - so[1]) / cell) + 2,
                       int((S2[:, 0].max() - so[0]) / cell) + 2))
        for (x, y) in S2:
            gs[int((y - so[1]) / cell), int((x - so[0]) / cell)] = 1.0
        C = correlate(gd, gs, mode="full", method="fft")
        norm = math.sqrt(float(gs.sum()))
        flat = np.argsort(C.ravel())[::-1]
        out, taken = [], []
        for idx in flat:
            iy, ix = divmod(int(idx), C.shape[1])
            if any(abs(iy - jy) < 3 and abs(ix - jx) < 3 for jy, jx in taken):
                continue
            taken.append((iy, ix))
            ty = do[1] + (iy - (gs.shape[0] - 1)) * cell - so[1]
            tx = do[0] + (ix - (gs.shape[1] - 1)) * cell - so[0]
            out.append((float(C[iy, ix]) / max(norm, 1e-9), (tx, ty)))
            if len(out) >= npeaks:
                break
        return out

    if rot_prior is not None:
        p0, ptol = rot_prior
        degs = [p0 + d / 2.0 for d in range(int(-2 * ptol), int(2 * ptol) + 1, 5)]
    else:
        degs = list(range(0, 360, 5))
    seeds = []
    for mirror in mirrors:
        for si in scales:
            for deg in degs:
                th = math.radians(deg)
                for score, t in corr_seeds(mirror, th, si, npeaks=1):
                    seeds.append((-score, mirror, th, si, t))
    seeds.sort(key=lambda s: s[0])
    sep = math.radians(3 if rot_prior is not None else 20)
    picked = []
    for score, mirror, th0, si, t in seeds:
        if len(picked) >= 8:
            break
        if any(m == mirror and abs((th0 - t2 + math.pi) % (2 * math.pi)
                                   - math.pi) < sep
               for _, m, t2, _s, _t in picked):
            continue
        picked.append((score, mirror, th0, si, t))
    cands, saturated = [], []
    for _, mirror, th0, si, t_peak in picked:
        inits = [t_peak] + [t for _, t in corr_seeds(mirror, th0, si, npeaks=3)[1:]]
        # the RAW correlation poses are candidates too, un-refined: in
        # cluttered partial-overlap scenes the ICP objective itself can walk
        # a correct seed away (its NN correspondences are dominated by
        # neighbourhood ink), while the caller's consensus-umeyama polish
        # refines from structural lot pairings instead — give it the chance
        c0, sn0 = math.cos(th0), math.sin(th0)
        for (tx0, ty0) in inits:
            def apply_raw(p, s=si, c=c0, sn=sn0, tx=tx0, ty=ty0, mirror=mirror):
                x, y = (-p[0] if mirror else p[0]), p[1]
                return (s * (c * x - sn * y) + tx, s * (sn * x + c * y) + ty)
            cands.append((apply_raw, si, math.degrees(th0), mirror, 9.9,
                          len(src)))
        for t0 in inits:
            rms, th, s, (tx, ty) = icp(mirror, th0, t0, si)
            c, sn = math.cos(th), math.sin(th)

            def apply(p, s=s, c=c, sn=sn, tx=tx, ty=ty, mirror=mirror):
                x, y = (-p[0] if mirror else p[0]), p[1]
                return (s * (c * x - sn * y) + tx, s * (sn * x + c * y) + ty)

            # a final scale ON the clamp boundary is a collapse artifact
            # (shrinking the cloud always lowers trimmed NN distance),
            # not a converged pose — quarantine unless nothing else exists
            bucket = (saturated if min(abs(s - 0.85 * si), abs(s - 1.15 * si))
                      / si < 0.01 else cands)
            bucket.append((apply, s, math.degrees(th), mirror, rms, len(src)))
    cands.sort(key=lambda t: t[4])
    return cands or saturated


def ring_iou(A, B, cells=64):
    """Raster IoU of two rings sharing a coordinate frame. Cheap (one small
    local grid) and shape-aware: a 90-deg-rotated elongated lot over its
    parcel scores ~0.3 where an aligned-but-drifted one scores ~0.8."""
    import numpy as np
    from skimage.draw import polygon as sk_polygon
    ax = [p[0] for p in A] + [p[0] for p in B]
    ay = [p[1] for p in A] + [p[1] for p in B]
    x0, x1, y0, y1 = min(ax), max(ax), min(ay), max(ay)
    span = max(x1 - x0, y1 - y0, 1e-9)
    cell = span / cells
    W = int((x1 - x0) / cell) + 2
    H = int((y1 - y0) / cell) + 2
    ga = np.zeros((H, W), bool)
    gb = np.zeros((H, W), bool)
    for ring, g in ((A, ga), (B, gb)):
        rr, cc = sk_polygon([(p[1] - y0) / cell for p in ring],
                            [(p[0] - x0) / cell for p in ring], g.shape)
        g[rr, cc] = True
    inter = float((ga & gb).sum())
    union = float((ga | gb).sum())
    return inter / union if union else 0.0


def lot_id(legal):
    """'AREA THIRTY 3 EST LOT 12' -> 'LOT 12'; '... TR A' -> 'TRACT A'."""
    if legal.endswith("TR A"):
        return "TRACT A"
    return "LOT " + legal.rsplit("LOT", 1)[-1].strip()


def load_fabric(path):
    """Load either a fabric_fetch normalized snapshot ({units, parcels:[...]})
    or a legacy esri-JSON Wyoming snapshot ({features:[{attributes,...}]}).
    Returns (G, units, gis_area_factor) where gis_area_factor converts a
    shoelace area in projected m^2 into the snapshot's area units."""
    d = json.load(open(path))
    G = {}
    if "parcels" in d:  # normalized (fabric_fetch.py)
        units = d.get("units", "m2")
        factor = FT2_PER_M2 if units == "sqft" else 1.0
        for p in d["parcels"]:
            lid = p["label"]
            while lid in G:  # context dupes (e.g. several 'Road' parcels)
                lid += "+"
            G[lid] = {"centroid": poly_centroid(p["ring"]),
                      "ring": p["ring"],
                      "gis_area": ring_area(p["ring"]) * factor,
                      "printed": p.get("area"),
                      "context": bool(p.get("context"))}
        return G, units, factor
    units, factor = "sqft", FT2_PER_M2  # legacy esri snapshot (Wyoming)
    for f in d["features"]:
        lid = lot_id(f["attributes"]["legal"])
        ring = f["geometry"]["rings"][0]
        G[lid] = {"centroid": poly_centroid(ring), "ring": ring,
                  "gis_area": ring_area(ring) * factor,
                  "printed": f["attributes"].get("landgrosss"),
                  "context": False}
    return G, units, factor


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan", help="plan-JSON from plat2json.py")
    ap.add_argument("fabric", help="esri-JSON parcel snapshot (projected CRS)")
    ap.add_argument("printed", help='printed-areas golden ({parcels:[{id,sqft|area}]})')
    ap.add_argument("--tol", type=float, default=0.25)
    ap.add_argument("--min-area", type=float, default=25.0)
    ap.add_argument("--area-tol", type=float, default=0.08,
                    help="rel. tol for the printed-area scale fit")
    ap.add_argument("--pair-tol", type=float, default=0.03,
                    help="rel. area tol for a (face,parcel) RANSAC pair")
    ap.add_argument("--inlier-m", type=float, default=12.0,
                    help="centroid distance (m) for a RANSAC inlier")
    ap.add_argument("--rot-prior", default=None, metavar="DEG[,TOL]",
                    help="restrict the shape-anchor rotation sweep to "
                         "DEG +- TOL degrees (default TOL 8). Supply when "
                         "the drawing's orientation is known — jurisdiction "
                         "street grid, plan north arrow, VLM-read street "
                         "names. On dense self-similar orthogonal grids the "
                         "unconstrained sweep is genuinely ambiguous (90-deg "
                         "rotations, mirrors and lattice shifts of a Surrey "
                         "block all tie); the prior collapses the symmetry "
                         "group. Mirror is still tested both ways.")
    ap.add_argument("--no-mirror", action="store_true",
                    help="assert the document is not mirrored. Born-digital "
                         "drawings (municipal PDF site plans) are plan views "
                         "— mirroring only enters via scanning artifacts. On "
                         "near-symmetric blocks under relayout drift the "
                         "mirror twin can FIT BETTER than the truth (the "
                         "drift regime's disagreement-is-signal, at pose "
                         "level), so when the document class rules mirroring "
                         "out, say so.")
    ap.add_argument("--anchor", choices=["auto", "shape"], default="auto",
                    help="'auto' = label/unique-value anchors with SHAPE "
                         "(boundary-ICP) fallback when they starve; 'shape' "
                         "forces boundary ICP (the drift-regime anchor: "
                         "proposal areas differ from registered by design, "
                         "so only geometry can anchor)")
    ap.add_argument("--out", default=None, help="write faces GeoJSON here")
    ap.add_argument("--dump-transform", default=None,
                    help="write the fitted plan->fabric affine (JSON) here")
    ap.add_argument("--corridor-out", default=None,
                    help="write a rescue-corridor JSON (plan units) for "
                         "plat2json --rescue-corridor")
    ap.add_argument("--corridor-lots", default=None,
                    help="comma-separated fabric lot ids (default: the "
                         "face-less lots)")
    ap.add_argument("--corridor-halfwidth-m", type=float, default=2.0,
                    help="corridor half-width in ground metres (default 2.0 "
                         "~ fabric vertex grade)")
    a = ap.parse_args()

    # ---- faces (face_check's exact path) ----
    plan = json.load(open(a.plan))
    segs = [tuple(L[:4]) for L in plan.get("lines", [])
            if math.hypot(L[2] - L[0], L[3] - L[1]) > 1e-9]
    if not segs:
        sys.exit("plan has no lines")
    nodes, edges = CA.planarize(segs, tol=a.tol)
    seen, uniq = set(), []
    for e in edges:
        key = (min(e["a"], e["b"]), max(e["a"], e["b"]))
        if e["a"] == e["b"] or key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    nodes, edges, n_joins, n_welds = FC.stitch_graph(nodes, uniq)
    faces = CA.extract_faces(nodes, edges)

    def face_poly(cyc):
        return [tuple(nodes[edges[ei]["a" if fwd else "b"]]) for ei, fwd in cyc]

    F = [(face_poly(f), RL.face_area(f, nodes, edges)) for f in faces]
    F = [(p, ar) for p, ar in F if ar >= a.min_area]
    print(f"faces >={a.min_area:g} u^2: {len(F)} "
          f"({n_joins} stub-joins, {n_welds} welds)")

    # ---- fabric parcels (normalized snapshot or legacy esri) ----
    # Gsub = the SUBJECT plan's parcels: anchoring, consensus, coverage,
    # assignment, and the open set all run against these. Context parcels
    # (fabric_fetch --context-buffer) extend only the registration target —
    # the ICP cloud and road detection — because the drawing draws the
    # neighbourhood, and without it symmetric plats tie.
    G, units, _factor = load_fabric(a.fabric)
    Gsub = {lid: g for lid, g in G.items() if not g.get("context")}
    n_ctx = len(G) - len(Gsub)
    print(f"fabric: {len(Gsub)} subject parcels"
          + (f" + {n_ctx} context" if n_ctx else "") + f", areas in {units}")

    # ---- printed-area scale fit (face_check's RANSAC) ----
    P = [(p["id"], float(p.get("sqft", p.get("area", 0))))
         for p in json.load(open(a.printed))["parcels"]]
    areas = sorted((ar for _, ar in F), reverse=True)
    best = None
    for fa0 in areas:
        for _, pv0 in P:
            r = pv0 / fa0
            used, matches, err = set(), {}, 0.0
            for fi, (_, fa) in enumerate(F):
                cand = [(abs(fa * r - pv) / pv, i, pid, pv)
                        for i, (pid, pv) in enumerate(P)
                        if i not in used and abs(fa * r - pv) / pv <= a.area_tol]
                if cand:
                    e_, i, pid, pv = min(cand)
                    used.add(i)
                    matches[fi] = (pid, pv, e_)
                    err += e_
            score = (len(matches), -err)
            if best is None or score > best[0]:
                best = (score, r, matches)
    (_, _), r, matches = best
    print(f"scale fit: {r:.2f} {units}/unit^2, {len(matches)}/{len(P)} printed matched")
    dup_vals = {pv for pv, c in Counter(p for _, p in P).items() if c > 1}

    # ---- RANSAC similarity fit ----
    # anchor pairing is label-first, unique-value fallback: where the fabric
    # carries lot numbers (Wyoming legal descriptions) the printed id keys it
    # directly; where labels are PIDs (ParcelMap BC) a printed value unique in
    # the golden that matches exactly one fabric parcel's geometry area within
    # 1.5% anchors instead. Value-only anchoring is NOT enough for fabrics
    # whose area grade (~1%) can't separate the lot-size bands — that is why
    # label equality stays primary.
    anchors = []
    for fi, (pid, pv, _) in matches.items():
        if pv in dup_vals:
            continue
        if pid in Gsub:
            anchors.append((poly_centroid(F[fi][0]), Gsub[pid]["centroid"], pid))
            continue
        cand = sorted((abs(g["gis_area"] - pv) / pv, lid)
                      for lid, g in Gsub.items() if g["gis_area"])
        if cand and cand[0][0] <= 0.015 and (len(cand) == 1
                                             or cand[1][0] > 0.02):
            anchors.append((poly_centroid(F[fi][0]),
                            G[cand[0][1]]["centroid"], f"{pid}~{cand[0][1]}",
                            cand[0][0], cand[0][1]))
    # under proposal->registration drift several printed values can all
    # "uniquely" claim the SAME fabric parcel — degenerate anchors make the
    # RANSAC triples collapse (observed: 6 anchors on one parcel, rot -34deg
    # nonsense fit). Keep only the closest claim per fabric parcel and
    # require 3 distinct targets.
    label_anchors = [an for an in anchors if len(an) == 3]
    value_anchors = {}
    for an in (an for an in anchors if len(an) == 5):
        lid = an[4]
        if lid not in value_anchors or an[3] < value_anchors[lid][3]:
            value_anchors[lid] = an
    anchors = label_anchors + [an[:3] for an in value_anchors.values()]
    shape_mode = a.anchor == "shape"
    if not shape_mode:
        dst_distinct = {tuple(an[1]) for an in anchors}
        if len(dst_distinct) < 2:
            print(f"anchors resolve to only {len(dst_distinct)} distinct "
                  f"fabric parcel(s) — area anchoring is not identifiable "
                  f"(drift regime?); falling back to SHAPE anchoring")
            shape_mode = True
    fcent = {fi: poly_centroid(p) for fi, (p, _) in enumerate(F)}
    pairs_ok = [(fi, lid) for fi, (p, ar) in enumerate(F) for lid, g in Gsub.items()
                if g["gis_area"] and abs(ar * r - g["gis_area"]) / g["gis_area"] <= a.pair_tol]
    icp_fit = None

    def collect_inl(apply_t):
        inl = []
        for fi, lid in pairs_ok:
            c = apply_t(fcent[fi])
            d = math.hypot(c[0] - G[lid]["centroid"][0],
                           c[1] - G[lid]["centroid"][1])
            if d < a.inlier_m:
                inl.append((fi, lid, d))
        return inl

    def greedy_pairs(inl):
        inl = sorted(inl, key=lambda t: t[2])
        uf, ul, out = set(), set(), []
        for fi, lid, d in inl:
            if fi in uf or lid in ul:
                continue
            uf.add(fi)
            ul.add(lid)
            out.append((fi, lid, d))
        return out

    def run_shape():
        # two source-cloud variants, candidates pooled: matched faces only
        # (clean lots, but skews the cloud centroid when un-matched lots
        # cluster on one side — on EPP46435 the correct pose fell out of the
        # seed list) and ALL faces (road frame + block outline = the most
        # drift-stable geometry, but frame/legend junk can drown ICP — on
        # 482 the all-faces cloud lost the pose the matched cloud finds).
        # The arbiter across all candidates is the REVERSE trimmed RMS:
        # distance from every FABRIC sample to the nearest transformed plan
        # point. The fabric is a subset of what the plan draws (a site plan
        # covers the neighbourhood; the fabric covers one block), so forward
        # metrics reward collapse and consensus counts saturate under loose
        # tolerances — but a correct pose must COVER the fabric. Consensus
        # is only a polish step afterwards, kept when it does not degrade
        # the reverse score.
        import numpy as np
        from scipy.spatial import cKDTree
        s0 = math.sqrt(r / _factor)
        mirrors = (False,) if a.no_mirror else (False, True)
        rot_prior = None
        if a.rot_prior:
            parts = [float(v) for v in a.rot_prior.split(",")]
            rot_prior = (parts[0], parts[1] if len(parts) > 1 else 8.0)
        # ICP registration target = SUBJECT rings at full density, plus
        # CONTEXT rings (fabric_fetch --context-buffer) under their own cap:
        # the drawing draws the neighbourhood, and the context frontages
        # give the street frame a counterpart, breaking mirror/lattice ties
        fabric_rings = [g["ring"] for g in Gsub.values()]
        ctx_rings = [g["ring"] for g in G.values() if g.get("context")] or None
        variants = [[p for p, _ in F]]
        m_rings = [F[fi][0] for fi in matches]
        if len(m_rings) >= 5:
            variants.append(m_rings)
        cands = []
        for src_rings in variants:
            cands += shape_anchor_fit(src_rings, fabric_rings, s0,
                                      ctx_rings=ctx_rings,
                                      rot_prior=rot_prior, mirrors=mirrors)
        # road-locked variant: a SUBJECT road/common parcel (>=2.5x the
        # median subject parcel area) is drift-immune (dedications don't
        # move between proposal and registration) and asymmetric (bulbs,
        # corners) — ICP of the road FACE onto the road RING alone has none
        # of the lot-grid's mirror/lattice ambiguity. Subject-only: a
        # neighbourhood snapshot contains dozens of large context parcels
        # (parks, schools, roads) and firing per parcel explodes the
        # candidate pool (observed: 3243 candidates).
        gareas = sorted(g["gis_area"] for g in Gsub.values() if g["gis_area"])
        med = gareas[len(gareas) // 2] if gareas else 0
        for lid, g in Gsub.items():
            if not med or not g["gis_area"] or g["gis_area"] < 2.5 * med:
                continue
            rf = [p for p, ar in F
                  if abs(ar * r - g["gis_area"]) / g["gis_area"] <= 0.35]
            if rf:
                cands += shape_anchor_fit(rf, [g["ring"]], s0,
                                          rot_prior=rot_prior,
                                          mirrors=mirrors)
        plan_cloud = [p for ring, _ in F for p in sample_ring(ring, 1.0 / s0)]
        # coverage stays SUBJECT-only: context parcels' back edges are not
        # drawn, so scoring them would punish the true pose
        fab_pts = np.array([p for g in Gsub.values()
                            for p in sample_ring(g["ring"], 2.0)])
        kf = max(3, int(len(fab_pts) * 0.9))

        def rev_rms(apply_t):
            P = np.array([apply_t(p) for p in plan_cloud])
            d, _ = cKDTree(P).query(fab_pts)
            return float(np.sqrt((np.sort(d)[:kf] ** 2).mean()))


        # two complementary arbiters, combined lexicographically:
        # 1. re-estimated CONSENSUS count (structural: lot centroids must
        #    land on area-compatible parcels) — reverse coverage alone is
        #    blind on cluttered site plans where ink sits near every fabric
        #    point under almost any pose (Surrey pilot picked a mirrored
        #    178-deg impostor by coverage);
        # 2. reverse-RMS as the tie-break and the polish acceptance gate —
        #    consensus alone saturates under loose tolerances.
        scored = []
        for apply_icp, s_i, rot_i, mir_i, rms_i, n_i in cands:
            rev = rev_rms(apply_icp)
            fit = (apply_icp, s_i, rot_i, mir_i, rms_i)
            inl = collect_inl(apply_icp)
            cons = greedy_pairs(inl)
            if len(cons) >= 4:
                try:
                    apply_1, s_1, rot_1, rms_1 = umeyama(
                        [fcent[fi] for fi, _, _ in cons],
                        [G[lid]["centroid"] for _, lid, _ in cons], mir_i)
                    rev_1 = rev_rms(apply_1)
                    if rev_1 <= rev * 1.15:
                        inl_1 = collect_inl(apply_1)
                        cons_1 = greedy_pairs(inl_1)
                        if len(cons_1) >= len(cons):
                            fit = (apply_1, s_1, rot_1, mir_i, rms_1)
                            rev, inl, cons = rev_1, inl_1, cons_1
                except ZeroDivisionError:
                    pass
            # the deciding score: SUM of per-pair ring IoU over the greedy
            # consensus. Centroid counts, coverage, and crossing counts all
            # saturate under the symmetry group of an orthogonal suburban
            # grid (90-deg rotations, mirrors, lattice shifts each kept 7-8
            # centroid-consensus lots on the Surrey pilot) — but a rotated
            # elongated lot only OVERLAPS its parcel ~0.3 IoU where an
            # aligned-but-drifted one scores ~0.8. Shape, not position,
            # breaks the tie; the pairs ARE the per-lot correspondence.
            siou = sum(ring_iou([fit[0](q) for q in F[fi][0]],
                                G[lid]["ring"])
                       for fi, lid, _ in cons)
            scored.append(((siou, -rev), inl, fit, rev, len(cons)))
        scored.sort(key=lambda t: t[0], reverse=True)
        (siou, _), inl, fit, rev, ncons = scored[0]
        print(f"shape anchor (boundary ICP): {len(cands)} pose candidate(s) "
              f"from {len(variants)} source cloud(s); best by consensus "
              f"shape-overlap (sum-IoU {siou:.2f} over {ncons} pairs) + "
              f"coverage ({rev:.2f} m): scale={fit[1]:.5f} m/unit, "
              f"rot={fit[2]:.2f} deg, mirrored={fit[3]}")
        return inl, fit[3], fit

    if shape_mode:
        best_inl, best_m, icp_fit = run_shape()
    else:
        pair_seeded = len(dst_distinct) < 3
        if pair_seeded:
            print(f"only {len(dst_distinct)} distinct anchor targets — falling "
                  f"back to PAIR-seeded RANSAC (2-point similarity is exact; "
                  f"acceptance rests entirely on consensus size)")
        print(f"anchor candidates (unique-area lots): {sorted(x[2] for x in anchors)}")
        if len(anchors) < (2 if pair_seeded else 3):
            sys.exit("not enough unique-area anchors")
        best_score, best_inl, best_m = (-1, float("inf")), None, False
        seeds = (list(combinations(anchors, 2)) if pair_seeded
                 else list(combinations(anchors, 3)))
        for tri in seeds:
            for m in (False, True):
                try:
                    apply_t = umeyama([x[0] for x in tri], [x[1] for x in tri], m)[0]
                except ZeroDivisionError:
                    continue
                inl, err = [], 0.0
                for fi, lid in pairs_ok:
                    c = apply_t(fcent[fi])
                    d = math.hypot(c[0] - G[lid]["centroid"][0],
                                   c[1] - G[lid]["centroid"][1])
                    if d < a.inlier_m:
                        inl.append((fi, lid, d))
                        err += d
                if (len(inl), -err) > best_score:
                    best_score, best_inl, best_m = (len(inl), -err), inl, m
    def greedy_cons(inl):
        inl.sort(key=lambda t: t[2])
        uf, ul, cons = set(), set(), []
        for fi, lid, d in inl:
            if fi in uf or lid in ul:
                continue
            uf.add(fi)
            ul.add(lid)
            cons.append((fcent[fi], G[lid]["centroid"]))
        return cons

    cons = greedy_cons(best_inl)
    if icp_fit is None and len(cons) < 4:
        # anchors existed but the RANSAC consensus is degenerate — the
        # observed mode: a capture change shifts the match set and the
        # unique-value anchors land on broken faces. Geometry still anchors.
        print(f"anchor RANSAC consensus only {len(cons)} lot(s) — falling "
              f"back to SHAPE anchoring")
        best_inl, best_m, icp_fit = run_shape()
        cons = greedy_cons(best_inl)
    if icp_fit:
        # the shape path already picked (and possibly consensus-polished) its
        # transform under the reverse-coverage check — use it as-is
        apply_T, scale, rot, rms = icp_fit[0], icp_fit[1], icp_fit[2], icp_fit[4]
        print(f"similarity fit: {len(cons)} consensus lots (shape-anchored), "
              f"scale={scale:.5f} m/unit, rot={rot:.2f} deg (expect ~grid "
              f"convergence), mirrored={best_m}, RMS={rms:.2f} m")
    else:
        apply_T, scale, rot, rms = umeyama([c[0] for c in cons],
                                           [c[1] for c in cons], best_m)
        print(f"similarity fit: {len(cons)} consensus lots, scale={scale:.5f} m/unit, "
              f"rot={rot:.2f} deg (expect ~grid convergence), mirrored={best_m}, "
              f"RMS={rms:.2f} m")
    if a.dump_transform:
        o, ex, ey = apply_T((0, 0)), apply_T((1, 0)), apply_T((0, 1))
        json.dump({"plan_to_fabric": [[ex[0] - o[0], ey[0] - o[0], o[0]],
                                      [ex[1] - o[1], ey[1] - o[1], o[1]]],
                   "scale_m_per_unit": scale, "rot_deg": rot,
                   "mirrored": best_m, "rms_m": rms, "n_consensus": len(cons)},
                  open(a.dump_transform, "w"), indent=1)
        print(f"wrote transform -> {a.dump_transform}")

    # ---- final 1:1 assignment + report ----
    cands = sorted((math.hypot(apply_T(fcent[fi])[0] - g["centroid"][0],
                               apply_T(fcent[fi])[1] - g["centroid"][1]), fi, lid)
                   for fi in range(len(F)) for lid, g in Gsub.items())
    uf, ul, assign = set(), set(), {}
    for d, fi, lid in cands:
        if fi in uf or lid in ul or d > 5 * max(1.0, rms) + 2:
            continue
        uf.add(fi)
        ul.add(lid)
        assign[fi] = (lid, d)
    print(f"\n{'face':>4} {'fabric lot':>10} {'cdist_m':>7} {'face_sqft':>9} "
          f"{'gis_sqft':>9} {'d_gis':>7} {'d_print':>7}  area-match label")
    for fi in sorted(assign, key=lambda i: -F[i][1]):
        lid, d = assign[fi]
        fa = F[fi][1] * r
        g = G[lid]
        am = matches.get(fi, ("-",))[0]
        flag = "" if am == lid else f"  <-- was labeled {am}"
        dp = (100 * (fa - g["printed"]) / g["printed"]
              if g["printed"] else float("nan"))
        print(f"{fi:>4} {lid:>10} {d:>7.2f} {fa:>9.0f} {g['gis_area']:>9.0f} "
              f"{100 * (fa - g['gis_area']) / g['gis_area']:>+6.2f}% "
              f"{dp:>+6.2f}%  {am}{flag}")
    open_lots = [l for l in Gsub if l not in ul]
    print(f"\nfabric lots with NO face: {open_lots}")

    if a.corridor_out:
        # fabric rings -> plan units via the INVERSE fit: a positional prior
        # telling plat2json where missing boundary ink must run. The corridor
        # may GUIDE capture; closure + printed-area gates still validate.
        o, ex, ey = apply_T((0, 0)), apply_T((1, 0)), apply_T((0, 1))
        ma, mb, mc = ex[0] - o[0], ey[0] - o[0], o[0]
        md, me, mf = ex[1] - o[1], ey[1] - o[1], o[1]
        det = ma * me - mb * md

        def inv_T(p):
            dx, dy = p[0] - mc, p[1] - mf
            return [(me * dx - mb * dy) / det, (-md * dx + ma * dy) / det]

        lots = ([s.strip() for s in a.corridor_lots.split(",")]
                if a.corridor_lots else open_lots)
        cor = {"halfwidth": a.corridor_halfwidth_m / scale,
               "lots": lots,
               "polylines": [[inv_T(p) for p in G[l]["ring"]] for l in lots]}
        json.dump(cor, open(a.corridor_out, "w"), indent=1)
        print(f"wrote corridor ({', '.join(lots)}, halfwidth "
              f"{cor['halfwidth']:.3f} u) -> {a.corridor_out}")

    if a.out:
        feats = []
        for fi, (poly, ar) in enumerate(F):
            ring = [list(apply_T(p)) for p in poly]
            ring.append(ring[0])
            lid, d = assign.get(fi, (None, None))
            feats.append({"type": "Feature",
                          "properties": {"face": fi, "lot": lid,
                                         "area": round(ar * r), "units": units,
                                         "cdist_m": d and round(d, 2)},
                          "geometry": {"type": "Polygon", "coordinates": [ring]}})
        json.dump({"type": "FeatureCollection", "features": feats},
                  open(a.out, "w"))
        print(f"wrote {a.out} ({len(feats)} faces, fabric CRS)")


if __name__ == "__main__":
    main()

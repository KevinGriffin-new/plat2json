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
            G[p["label"]] = {"centroid": poly_centroid(p["ring"]),
                             "ring": p["ring"],
                             "gis_area": ring_area(p["ring"]) * factor,
                             "printed": p.get("area")}
        return G, units, factor
    units, factor = "sqft", FT2_PER_M2  # legacy esri snapshot (Wyoming)
    for f in d["features"]:
        lid = lot_id(f["attributes"]["legal"])
        ring = f["geometry"]["rings"][0]
        G[lid] = {"centroid": poly_centroid(ring), "ring": ring,
                  "gis_area": ring_area(ring) * factor,
                  "printed": f["attributes"].get("landgrosss")}
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
    G, units, _factor = load_fabric(a.fabric)
    print(f"fabric: {len(G)} parcels, areas in {units}")

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
        if pid in G:
            anchors.append((poly_centroid(F[fi][0]), G[pid]["centroid"], pid))
            continue
        cand = sorted((abs(g["gis_area"] - pv) / pv, lid)
                      for lid, g in G.items() if g["gis_area"])
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
    dst_distinct = {tuple(an[1]) for an in anchors}
    if len(dst_distinct) < 2:
        print(f"anchors resolve to only {len(dst_distinct)} distinct fabric "
              f"parcel(s) — area anchoring is not identifiable (drift "
              f"regime?); aborting fit")
        sys.exit(3)
    pair_seeded = len(dst_distinct) < 3
    if pair_seeded:
        print(f"only {len(dst_distinct)} distinct anchor targets — falling "
              f"back to PAIR-seeded RANSAC (2-point similarity is exact; "
              f"acceptance rests entirely on consensus size)")
    print(f"anchor candidates (unique-area lots): {sorted(x[2] for x in anchors)}")
    if len(anchors) < (2 if pair_seeded else 3):
        sys.exit("not enough unique-area anchors")
    fcent = {fi: poly_centroid(p) for fi, (p, _) in enumerate(F)}
    pairs_ok = [(fi, lid) for fi, (p, ar) in enumerate(F) for lid, g in G.items()
                if abs(ar * r - g["gis_area"]) / g["gis_area"] <= a.pair_tol]
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
    best_inl.sort(key=lambda t: t[2])
    uf, ul, cons = set(), set(), []
    for fi, lid, d in best_inl:
        if fi in uf or lid in ul:
            continue
        uf.add(fi)
        ul.add(lid)
        cons.append((fcent[fi], G[lid]["centroid"]))
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
                   for fi in range(len(F)) for lid, g in G.items())
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
    open_lots = [l for l in G if l not in ul]
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

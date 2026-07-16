#!/usr/bin/env python3
"""face_check.py — structure scoreboard: do the plan's lines close into lots?

Coverage (overlay_check.py) says the ink was captured; THIS says the topology
survived: planarize the plan-JSON's lines and count the faces the fabric
closes into. Complements linework_covered_pct — coverage can be high while
zero lots close (measured on 482.pdf), and a recall metric alone is gameable.

Probe recipe (each step was measured, not assumed — see STATUS.md iter 15/16):
  * NO endpoint extension: extending every segment shatters chain fabric
    (consecutive polyline vertices pull apart beyond the cluster tolerance;
    662 degree-1 stubs appeared on 482.pdf).
  * NO snap_endpoints: it collapses chain fabric built from ordered polylines
    (halved the edge count on 482.pdf); it is for atomic Hough-style segs.
  * planarize directly, default tol 0.25 (world units).
  * report ALL face areas — pick the lot band from the printed areas and the
    plan's scale, not a hardcoded "big" cutoff (a wrong 500-unit cutoff hid
    every real ~264-unit lot on 482.pdf).

Usage:
    python face_check.py PLAN.json [--tol 0.25] [--min-area 25]
        [--lot-band LO,HI]      # highlight faces in this area range
        [--expect N]            # exit 2 if faces in the lot band < N
"""
import argparse
import json
import math
import sys

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
sys.path.insert(0, HERE)


def stitch_graph(nodes, edges, join_r=1.2, weld_r=2.0):
    """Close the residual micro-holes at graph level, where degrees are known:
    (1) STUB-STUB: two degree-1 nodes within join_r whose incident edges
    continue each other (anti-parallel within ~35 deg) get a connecting edge —
    the label/dash holes that chain-level repair leaves behind; (2) STUB-EDGE:
    a remaining degree-1 node within weld_r of a non-incident edge welds to
    the projection (T-shortfall). Units are plan units (~0.03 units/px at
    300 dpi / plot-scale 250). Returns (nodes, edges, n_joins, n_welds)."""
    import numpy as np
    nodes = [tuple(n) for n in nodes]
    edges = [dict(e) for e in edges]

    def degree():
        d = {}
        for e in edges:
            d[e["a"]] = d.get(e["a"], 0) + 1
            d[e["b"]] = d.get(e["b"], 0) + 1
        return d

    def stub_dir(v):
        e = next(e for e in edges if v in (e["a"], e["b"]))
        w = e["b"] if e["a"] == v else e["a"]
        d = np.asarray(nodes[v], float) - np.asarray(nodes[w], float)
        n = float(np.hypot(*d))
        return d / n if n > 0 else None

    def crs(a, b):
        return float(a[0] * b[1] - a[1] * b[0])

    deg = degree()
    stubs = [v for v, d in deg.items() if d == 1]
    n_joins = 0
    used = set()
    cands = []
    def crosses_edge(v, w):
        """True if segment v-w properly crosses any existing edge — the
        graph-level corridor test: a genuine label void has nothing in it,
        while a collinear pair straddling the road must cross the curbs
        (one un-gated long join did exactly that and cut the closed road
        face in half)."""
        p1 = np.asarray(nodes[v], float); p2 = np.asarray(nodes[w], float)
        def side(a, b, c):
            return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
        for e in edges:
            if v in (e["a"], e["b"]) or w in (e["a"], e["b"]):
                continue
            p3 = np.asarray(nodes[e["a"]], float); p4 = np.asarray(nodes[e["b"]], float)
            d1, d2 = side(p1, p2, p3), side(p1, p2, p4)
            d3, d4 = side(p3, p4, p1), side(p3, p4, p2)
            if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
                return True
        return False
    long_r = 5.5    # curb-arc label breaks span ~4-5.5 units; staggered lot
    # dividers are never collinear across the road, so with the lateral gate
    # below a long stub-join cannot jump the right-of-way
    for i, v in enumerate(stubs):
        dv = stub_dir(v)
        if dv is None:
            continue
        for w in stubs[i+1:]:
            u = np.asarray(nodes[w], float) - np.asarray(nodes[v], float)
            gap = float(np.hypot(*u))
            if gap > 15.0 or gap < 1e-9:
                continue
            dw = stub_dir(w)
            if dw is None:
                continue
            if gap <= join_r:
                if float(-(dv @ dw)) < 0.82:
                    continue
            elif gap <= long_r:
                # long join, SIGN-AGNOSTIC: post-planarize stub directions
                # flip unpredictably in overlap/stagger configurations (a
                # single atomic edge decides them), so trust only alignment
                # MAGNITUDE + mutual collinearity. Lateral 0.7 admits tight
                # cul-de-sac curb arcs (R ~ 3 units: sagitta ~0.5 over a
                # label break) while rejecting distinct parallel lines.
                uh = u / gap
                if abs(float(dv @ uh)) < 0.90 or abs(float(dw @ uh)) < 0.90 or \
                   abs(crs(dv, uh)) * gap > 0.7 or abs(crs(dw, uh)) * gap > 0.7:
                    continue
            else:
                # EXTRA-long join (boundary inline labels span up to ~13
                # units): near-perfect mutual collinearity AND an empty
                # corridor (no existing edge crossed by the connector).
                uh = u / gap
                if abs(float(dv @ uh)) < 0.985 or abs(float(dw @ uh)) < 0.985 or \
                   abs(crs(dv, uh)) * gap > 0.35 or abs(crs(dw, uh)) * gap > 0.35:
                    continue
                if crosses_edge(v, w):
                    continue
            cands.append((gap, v, w))
    for _, v, w in sorted(cands):
        if v in used or w in used:
            continue
        used.update((v, w))
        edges.append({**edges[0], "a": v, "b": w})
        n_joins += 1

    # ---- corner joins: boundary monuments (X / triangle symbols) eat the
    # vertex where two legs meet at an ANGLE — no collinear tier can span
    # that. Two free stubs whose outward rays intersect ahead of both within
    # ~3.5 units join through the intersection, corridor-checked. ----
    n_corners = 0
    deg = degree()
    stubs2 = [v for v, d in deg.items() if d == 1 and v not in used]
    ccands = []
    for i, v in enumerate(stubs2):
        dv = stub_dir(v)
        if dv is None:
            continue
        for w in stubs2[i+1:]:
            pv = np.asarray(nodes[v], float); pw = np.asarray(nodes[w], float)
            if float(np.hypot(*(pw - pv))) > 7.0:
                continue
            dw = stub_dir(w)
            if dw is None:
                continue
            den = crs(dv, dw)
            if abs(den) < 0.30:                  # near-parallel: joins' business
                continue
            ti = crs(pw - pv, dw) / den
            tj = crs(pw - pv, dv) / den
            if not (0.0 <= ti <= 3.5 and 0.0 <= tj <= 3.5) or ti + tj < 0.3:
                continue
            ccands.append((ti + tj, v, w, tuple(pv + ti * dv)))
    for _, v, w, X in sorted(ccands, key=lambda t: t[0]):
        if v in used or w in used:
            continue
        nodes.append(X)
        xi = len(nodes) - 1
        if crosses_edge(v, xi) or crosses_edge(w, xi):
            nodes.pop()
            continue
        used.update((v, w))
        edges.append({**edges[0], "a": v, "b": xi})
        edges.append({**edges[0], "a": w, "b": xi})
        n_corners += 1
    n_joins += n_corners

    def pt_seg(p, a, b):
        a, b, p = np.asarray(a, float), np.asarray(b, float), np.asarray(p, float)
        ab = b - a
        t = max(0.0, min(1.0, float((p - a) @ ab) / max(float(ab @ ab), 1e-12)))
        q = a + t * ab
        return float(np.hypot(*(q - p))), t, tuple(q)

    deg = degree()
    n_welds = 0
    for v in [v for v, d in deg.items() if d == 1]:
        p = nodes[v]
        best = None
        for ei, e in enumerate(edges):
            if v in (e["a"], e["b"]):
                continue
            dist, t, q = pt_seg(p, nodes[e["a"]], nodes[e["b"]])
            if best is None or dist < best[0]:
                best = (dist, ei, t, q)
        if best and best[0] <= weld_r:
            dist, ei, t, q = best
            # directional sanity for the longer welds: the target must sit
            # roughly AHEAD of the stub (a divider stopping short of a curb),
            # not broadside — broadside long welds fuse parallel neighbors
            if dist > 0.5:
                dv = stub_dir(v)
                u = np.asarray(q, float) - np.asarray(p, float)
                n = float(np.hypot(*u))
                if dv is None or n < 1e-9 or abs(float(dv @ (u / n))) < 0.5:
                    continue
            e = edges[ei]
            if t < 0.05:
                w = e["a"]
            elif t > 0.95:
                w = e["b"]
            else:
                nodes.append(q)
                w = len(nodes) - 1
                b_old = e["b"]
                e["b"] = w
                edges.append({**e, "a": w, "b": b_old})
            if w != v:
                edges.append({**edges[ei], "a": v, "b": w})
                n_welds += 1
    return nodes, edges, n_joins, n_welds


def main():
    import cogo_assemble as CA
    import raster_lots as RL
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan")
    ap.add_argument("--tol", type=float, default=0.25, help="planarize cluster tol (plan units)")
    ap.add_argument("--min-area", type=float, default=25.0, help="ignore faces smaller than this")
    ap.add_argument("--lot-band", default=None, help="LO,HI area range that counts as a lot")
    ap.add_argument("--printed-sqft", default=None,
                    help="printed-areas golden JSON ({parcels:[{id,sqft}]}): RANSAC-fit "
                         "the sqft-per-unit^2 ratio, validate faces against printed "
                         "areas, and derive the lot band automatically")
    ap.add_argument("--area-tol", type=float, default=0.08,
                    help="relative tolerance for a face area to match a printed area")
    ap.add_argument("--no-stitch", action="store_true",
                    help="skip graph-level stub stitching (ablation)")
    ap.add_argument("--expect", type=int, default=None,
                    help="exit 2 if lot-band face count is below this")
    a = ap.parse_args()

    plan = json.load(open(a.plan))
    segs = [tuple(L[:4]) for L in plan.get("lines", [])
            if math.hypot(L[2] - L[0], L[3] - L[1]) > 1e-9]
    if not segs:
        sys.exit("plan has no lines")
    nodes, edges = CA.planarize(segs, tol=a.tol)
    # dedupe: repair-merged chains can overlap, leaving several edges between
    # the same node pair (all straight => geometrically identical); duplicates
    # and self-loops distort the angular face walk
    seen_pairs, uniq = set(), []
    for e in edges:
        key = (min(e["a"], e["b"]), max(e["a"], e["b"]))
        if e["a"] == e["b"] or key in seen_pairs:
            continue
        seen_pairs.add(key)
        uniq.append(e)
    n_dupes, edges = len(edges) - len(uniq), uniq
    n_joins = n_welds = 0
    if not a.no_stitch:
        nodes, edges, n_joins, n_welds = stitch_graph(nodes, edges)
    faces = CA.extract_faces(nodes, edges)
    areas = sorted((float(RL.face_area(f, nodes, edges)) for f in faces), reverse=True)
    areas = [x for x in areas if x >= a.min_area]

    print(f"[{a.plan.rsplit(chr(92), 1)[-1].rsplit('/', 1)[-1]}] "
          f"{len(segs)} segs -> {len(edges)} edges ({n_dupes} dupes dropped, "
          f"{n_joins} stub-joins, {n_welds} welds) "
          f"-> {len(faces)} faces ({len(areas)} >= {a.min_area:g})")
    print("  areas:", [round(x, 1) for x in areas[:30]])
    n_lots = None

    if a.printed_sqft:
        # RANSAC over pairwise ratios: the sheet has ONE sqft-per-unit^2 scale;
        # the ratio that lets the most printed areas find a face wins. Greedy
        # 1:1 matching (printed values repeat, e.g. nine 1.5-acre lots).
        parcels = json.load(open(a.printed_sqft))["parcels"]
        P = [(p["id"], float(p["sqft"])) for p in parcels]
        best = None
        for f in areas:
            for _, p in P:
                r = p / f
                used, matches, err = set(), [], 0.0
                for fi, fa in enumerate(areas):
                    cand = [(abs(fa * r - pv) / pv, i, pid, pv)
                            for i, (pid, pv) in enumerate(P)
                            if i not in used and abs(fa * r - pv) / pv <= a.area_tol]
                    if cand:
                        e_, i, pid, pv = min(cand)
                        used.add(i)
                        matches.append((fa, pid, pv, e_))
                        err += e_
                score = (len(matches), -err)
                if best is None or score > best[0]:
                    best = (score, r, matches)
        (n_match, _), r, matches = best
        print(f"  scale fit: {r:.2f} sqft/unit^2 -> {n_match}/{len(P)} printed "
              f"areas matched (tol {a.area_tol:.0%})")
        # several lots often share one printed area (e.g. nine 1.5-acre lots):
        # the COUNT per area-class is meaningful, the specific lot LABEL is not
        from collections import Counter
        dup = {pv for pv, c in Counter(p for _, p in P).items() if c > 1}
        for fa, pid, pv, e_ in sorted(matches, key=lambda m: -m[2]):
            tag = "  [area-class label, not identity]" if pv in dup else ""
            print(f"    {pid:>8}: printed {pv:>8.0f} sqft ~ face {fa*r:>8.0f}  ({e_:.1%}){tag}")
        n_lots = n_match
        if not a.lot_band:
            lo = min(p for _, p in P) / r * (1 - 2 * a.area_tol)
            hi = max(p for _, p in P) / r * (1 + 2 * a.area_tol)
            print(f"  auto lot-band: [{lo:.0f}, {hi:.0f}] plan-units^2")

    if a.lot_band:
        lo, hi = (float(v) for v in a.lot_band.split(","))
        lots = [x for x in areas if lo <= x <= hi]
        n_lots = len(lots)
        print(f"  lot-band [{lo:g}, {hi:g}]: {n_lots} faces ->",
              [round(x, 1) for x in lots])
    if a.expect is not None:
        if n_lots is None:
            sys.exit("--expect requires --lot-band")
        if n_lots < a.expect:
            sys.exit(2)


if __name__ == "__main__":
    main()

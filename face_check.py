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
    faces = CA.extract_faces(nodes, edges)
    areas = sorted((RL.face_area(f, nodes, edges) for f in faces), reverse=True)
    areas = [x for x in areas if x >= a.min_area]

    print(f"[{a.plan.rsplit(chr(92), 1)[-1].rsplit('/', 1)[-1]}] "
          f"{len(segs)} segs -> {len(edges)} edges ({n_dupes} dupes dropped) "
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
        for fa, pid, pv, e_ in sorted(matches, key=lambda m: -m[2]):
            print(f"    {pid:>8}: printed {pv:>8.0f} sqft ~ face {fa*r:>8.0f}  ({e_:.1%})")
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

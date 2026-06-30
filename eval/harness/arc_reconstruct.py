#!/usr/bin/env python3
"""Known-radius arc confirmation — use the published curve table (curve_table.py)
as a prior to find arcs the blind geometry misses, and report, per published
curve, whether it is present in the traced linework.

Why a prior is needed: a gentle large-radius arc fragment is nearly straight, so a
FREE circle-fit gives a wild radius+centre and blind arc-fitting drops it
entirely (the R=63 ft curve was invisible to fit_arcs). But FIXED-RADIUS centre
fitting is well-posed: given a published radius, the centre is well-determined,
and a long fragment that genuinely lies on that circle is unmistakable.

Per published curve (id, R, L): collect chains that fit radius R with low
fixed-radius residual AND real curvature (sag >= 5*residual - a straight chord
only musters sag ~ 3.3*residual, so this rejects lot lines); take the longest
co-centred cluster; report captured arc-length vs the published L (= R*Delta):
FULL if it matches, PARTIAL if only part of the sweep was traced, else NOT FOUND.

The win this unlocks: gentle large arcs blind geometry can't see. On adams_prc24_12
the R=63 ft (19.2 m) boundary curve - invisible to fit_arcs - is recovered at
residual 0.00 (71 deg of its 90 deg traced). On Boise the R=50 ft curve recovers
at 76 deg.

LIMITATIONS (honest): this CONFIRMS radii and recovers centres; it is not a
perfect full-arc reconstructor on dense sheets. (a) radii within trace resolution
(36/37.5/38 ft, or 2944/2914 ft) are indistinguishable - the FULL (L) match is the
only disambiguator; (b) only the part of a curve actually traced is captured, so
most results are PARTIAL; (c) the longest fragment at a radius may be a different
feature of that radius - a FULL L-match is the confident signal, PARTIAL means
"an arc of this radius is present" not necessarily this exact published curve.

    python arc_reconstruct.py PLAN.json PLAT.pdf PAGE [--unit ft] [--out arcs.json]
"""
import argparse, json, math, os, sys
import numpy as np

FT2M = 0.3048


def chains_of(plan):
    return [np.array(pl[:-1], float) for pl in plan.get("polylines", []) if len(pl) - 1 >= 3]


def plen(P):
    return float(np.hypot(np.diff(P[:, 0]), np.diff(P[:, 1])).sum())


def fixed_radius_center(P, r, init, iters=80):
    c = init.astype(float).copy()
    for _ in range(iters):
        diff = c - P
        dist = np.maximum(np.hypot(diff[:, 0], diff[:, 1]), 1e-6)
        c_new = (P + r * diff / dist[:, None]).mean(0)
        if math.hypot(c_new[0] - c[0], c_new[1] - c[1]) < 1e-5:
            c = c_new; break
        c = c_new
    diff = P - c
    # RMS deviation from the TARGET radius r (NOT std: std subtracts the mean, so
    # a circle of the wrong radius - all points equidistant at some other r' -
    # would score ~0; we need distance == r, not just "all equal").
    return c, float(np.sqrt(np.mean((np.hypot(diff[:, 0], diff[:, 1]) - r) ** 2)))


def best_center(P, r):
    mid = P.mean(0)
    d = P[-1] - P[0]
    perp = np.array([-d[1], d[0]]); n = np.hypot(*perp)
    perp = perp / n if n > 1e-6 else np.array([1.0, 0.0])
    best = None
    for s in (+1, -1):
        c, resid = fixed_radius_center(P, r, mid + s * r * perp)
        if best is None or resid < best[1]:
            best = (c, resid)
    return best


def chord_sweep(P, c, r):
    """Sweep (deg) from the cluster's angular extent; robust via the extreme
    points' angle around the centre (chains are short arcs of a known circle)."""
    ang = np.degrees(np.arctan2(P[:, 1] - c[1], P[:, 0] - c[0])) % 360
    ang = np.sort(ang)
    gaps = np.diff(np.concatenate([ang, [ang[0] + 360]]))
    return 360 - gaps[gaps.argmax()]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan"); ap.add_argument("pdf"); ap.add_argument("page", type=int)
    ap.add_argument("--unit", choices=["ft", "m"], default="ft")
    ap.add_argument("--resid", type=float, default=0.2)
    ap.add_argument("--sag-k", type=float, default=5.0)
    ap.add_argument("--min-frag", type=float, default=4.0)
    ap.add_argument("--l-tol", type=float, default=1.5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    import fitz
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from curve_table import harvest_curves

    plan = json.load(open(a.plan))
    chains = [P for P in chains_of(plan) if plen(P) >= a.min_frag]
    curves = harvest_curves(fitz.open(a.pdf)[a.page].get_text())
    conv = FT2M if a.unit == "ft" else 1.0

    def fragments_for(r_m):
        """chains confirmed (resid + sag) to lie on a radius-r_m circle."""
        out = []
        for P in chains:
            c, resid = best_center(P, r_m)
            if resid > a.resid:
                continue
            sweep = chord_sweep(P, c, r_m)
            sag = r_m * (1 - math.cos(math.radians(min(sweep, 180) / 2)))
            if sag >= max(0.6, a.sag_k * resid):
                out.append((P, c, resid, plen(P)))
        return out

    # For each distinct published radius, report the single best CONFIRMING
    # fragment (longest chain that lies on that radius). No clustering - merging
    # fragments across this dense a sheet conflates distinct curves; the single
    # longest confirmed fragment is the honest, unambiguous evidence.
    rows, out_arcs, seen = [], [], set()
    for cv in sorted(curves, key=lambda c: -(c["radius"] or 0)):
        r_ft = round(cv["radius"], 3)
        if r_ft in seen:
            continue
        seen.add(r_ft)
        r_m = r_ft * conv
        Ls = sorted(c["length"] * conv for c in curves
                    if abs(c["radius"] - r_ft) < 1e-3 and c["length"])
        frags = fragments_for(r_m)
        if not frags:
            rows.append((r_ft, "NOT FOUND", None)); continue
        # best = longest confirmed fragment
        P, c, resid, L_chain = max(frags, key=lambda f: f[3])
        sweep = chord_sweep(P, c, r_m)
        L = r_m * math.radians(sweep)
        match = min(Ls, key=lambda x: abs(x - L)) if Ls else None
        full = match is not None and abs(match - L) <= a.l_tol
        status = "FULL" if full else "PARTIAL"
        out_arcs.append({"radius_unit": r_ft, "center": [round(c[0], 2), round(c[1], 2)],
                         "sweep_deg": round(sweep), "arc_len_unit": round(L / conv, 2),
                         "published_L_unit": round(match / conv, 2) if match else None,
                         "match": status.lower(), "resid": round(resid, 3),
                         "n_confirming": len(frags)})
        rows.append((r_ft, status, (sweep, L, match, resid, len(frags))))

    print(f"{len(chains)} chains; {len(curves)} published curves; {len(seen)} distinct radii\n")
    print(f" RADIUS   status    best confirmed fragment")
    for r_ft, status, best in sorted(rows, key=lambda z: -z[0]):
        cap = ""
        if best:
            sweep, L, match, resid, nf = best
            cap = (f"sweep {sweep:.0f}°  L={L/conv:.1f}{a.unit}"
                   + (f" vs pub {match/conv:.1f}" if match else "")
                   + f"  resid={resid:.2f}  ({nf} fits)")
        print(f" {r_ft:6.2f}{a.unit}  {status:9s} {cap}")
    nfull = sum(1 for r in rows if r[1] == "FULL")
    npart = sum(1 for r in rows if r[1] == "PARTIAL")
    nnone = sum(1 for r in rows if r[1] == "NOT FOUND")
    print(f"\nrecovery: {nfull} FULL, {npart} PARTIAL, {nnone} NOT FOUND  "
          f"(of {len(rows)} distinct published radii)")
    if a.out:
        json.dump({"arcs": out_arcs, "unit": a.unit}, open(a.out, "w"), indent=1)
        print(f"-> {a.out}")


if __name__ == "__main__":
    main()

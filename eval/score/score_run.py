#!/usr/bin/env python3
"""score_run.py - score one prepped plan run (shapes + labels).

License-free + self-validating: with NO external answer key (the GLO/NARA case)
it cross-checks the VLM-read labels against the plat2json geometry - a printed
distance should match a recovered segment length (after ratio-vote scale), and a
printed bearing should match a segment azimuth (mod 180). That agreement IS the
precision signal without a golden. If a key is given, it also reports recall.

    python score_run.py <slug> [--gt <key.json>]
      reads _sources/<slug>/{_plan_plat2json.json,_vlm_reads.json}
      key.json (optional): {"bearings_dms":[...], "distances_m":[...]} OR
                           {"gt_legs_m":[...]} (a DXF-derived key)

Run with any python (stdlib only).
"""
import argparse, json, math, os, re, statistics

HERE = os.path.dirname(os.path.abspath(__file__))


def dms(s):
    """Tolerant bearing -> AZIMUTH degrees (0=N, cw). Requires a degree mark.
    Handles quadrant bearings (N.88°52'W. -> 271.13) AND raw azimuths (98°15')
    AND degrees-only (90°). Quadrant is applied when a leading N/S and trailing
    E/W are present. Returns None if not a bearing."""
    s = str(s)
    if "°" not in s:
        return None
    g = re.findall(r"\d+", s)
    if not g:
        return None
    ang = int(g[0]) + (int(g[1]) / 60 if len(g) > 1 else 0) + \
        (int(g[2]) / 3600 if len(g) > 2 else 0)
    u = s.upper()
    ns = "N" if u.lstrip(". ").startswith("N") else ("S" if u.lstrip(". ").startswith("S") else None)
    ew = "E" if "E" in u[1:] else ("W" if "W" in u[1:] else None)
    if ns and ew:  # quadrant bearing -> azimuth
        if ns == "N" and ew == "E":
            return ang
        if ns == "S" and ew == "E":
            return 180 - ang
        if ns == "S" and ew == "W":
            return 180 + ang
        return 360 - ang  # N..W
    return ang  # raw azimuth or degrees-only


def num(s):
    """Tolerant distance -> float. Handles feet/inch marks, integers, and a
    leading cardinal word ('East 237.16'). Skips illegible ('?') and curve
    params (R=, L.C.) which are not straight-leg lengths."""
    s = str(s)
    if "?" in s or re.search(r"R\s*=|L\.?\s*C|M\.?\s*S", s):
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", s.replace("'", " ").replace('"', " "))
    return float(nums[-1]) if nums else None


def seglens(lines):
    return [math.hypot(L[2] - L[0], L[3] - L[1]) for L in lines]


def segaz(lines):
    return [math.degrees(math.atan2(L[2] - L[0], L[3] - L[1])) % 180 for L in lines]


def greedy(reads, truth, tol, mod=False):
    pool, hit = list(truth), 0
    for r in reads:
        bi, bd = None, tol
        for i, g in enumerate(pool):
            d = abs(g - r)
            if mod:
                d = min(d % 180, 180 - d % 180)
            if d <= bd:
                bd, bi = d, i
        if bi is not None:
            hit += 1; pool.pop(bi)
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug"); ap.add_argument("--gt", default=None)
    a = ap.parse_args()
    base = os.path.join(HERE, "_sources", a.slug)
    plat = json.load(open(os.path.join(base, "_plan_plat2json.json"), encoding="utf-8"))
    lines = plat.get("lines", [])
    reads = json.load(open(os.path.join(base, "_vlm_reads.json"), encoding="utf-8"))

    # gate by 'kind' when present so a bearing's digits aren't read as a distance
    def is_b(x):
        return x.get("kind", "") == "bearing" or ("kind" not in x and dms(x["raw"]) is not None)

    def is_d(x):
        return x.get("kind", "") == "distance" or ("kind" not in x and num(x["raw"]) is not None)
    vb = sorted({round(dms(x["raw"]), 5) for x in reads if is_b(x) and dms(x["raw"]) is not None})
    vd = sorted({round(num(x["raw"]), 3) for x in reads if is_d(x) and num(x["raw"]) is not None})
    frag = [x["raw"] for x in reads
            if (is_b(x) and dms(x["raw"]) is None) or (is_d(x) and num(x["raw"]) is None)]
    print(f"[{a.slug}] VLM: {len(vb)} complete bearings, {len(vd)} distances, "
          f"{len(frag)} fragments  | plat2json {len(lines)} segments")

    # ---- self-check: labels vs geometry (no key needed) ----
    gaz, gd = segaz(lines), sorted(seglens(lines), reverse=True)
    if vd and gd:
        K = min(8, len(vd), len(gd))
        scale = statistics.median(sorted(vd, reverse=True)[k] / gd[k] for k in range(K))
        gd_s = [d * scale for d in gd]
        bh = greedy(vb, gaz, 0.05, mod=True)
        dh = greedy(vd, gd_s, 0.2)
        print(f"  self-check vs geometry (scale x{scale:.4f}): "
              f"bearings {bh}/{len(vb)} match a segment azimuth (<=3'); "
              f"distances {dh}/{len(vd)} match a segment length (<=0.2m)")

    # ---- recall vs external key (optional) ----
    if a.gt and os.path.exists(a.gt):
        key = json.load(open(a.gt, encoding="utf-8"))
        # key bearings may be quadrant strings ("bearings_dms") or already
        # azimuths ("bearings_az", as ocr_fieldnotes.py emits)
        gtb = sorted({round(dms(s) or 0, 5) for s in key.get("bearings_dms", []) if dms(s)})
        if not gtb and key.get("bearings_az"):
            gtb = sorted({round(float(x), 5) for x in key["bearings_az"]})
        gtd = sorted({round(float(d), 3) for d in
                      (key.get("distances_m") or key.get("gt_legs_m") or [])})
        if gtb:
            print(f"  bearing recall: {greedy(gtb, vb, 0.05, mod=True)}/{len(gtb)}")
        if gtd:
            print(f"  distance recall: {greedy(gtd, vd, 0.2)}/{len(gtd)}")


if __name__ == "__main__":
    main()

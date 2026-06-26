#!/usr/bin/env python3
"""merge_lines.py - consolidate plat2json Hough fragment-soup into real legs.

plat2json emits many short Hough segments per real edge (endpoints scatter, so
shared-node merging fails). This clusters segments by their INFINITE-LINE params
(theta, rho) and, within a cluster, splits into runs separated by large gaps,
emitting one merged segment per run = the real edge with its true length. Lifts
the distance side of the self-check (lengths, not just azimuths).

    python merge_lines.py <plat2json.json> <out_merged.json>
"""
import argparse, json, math


def line_params(s):
    x0, y0, x1, y1 = s[:4]
    th = math.atan2(y1 - y0, x1 - x0) % math.pi          # 0..pi
    rho = x0 * math.sin(th) - y0 * math.cos(th)          # signed perp offset
    return th, rho


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp"); ap.add_argument("out")
    a = ap.parse_args()
    L = json.load(open(a.inp))["lines"]
    xs = [c for s in L for c in (s[0], s[2])]
    ys = [c for s in L for c in (s[1], s[3])]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    dtheta = math.radians(1.5)          # collinearity angle tol
    drho = span * 0.004                 # perpendicular tol (~0.4% of plan span)
    gap = span * 0.01                   # max along-line gap to still be one edge

    # bucket by (theta, rho) then split each bucket into gap-separated runs
    buckets = []  # each: (th, rho, [segs])
    for s in L:
        th, rho = line_params(s)
        placed = False
        for b in buckets:
            dt = min(abs(th - b[0]), math.pi - abs(th - b[0]))
            if dt < dtheta and abs(rho - b[1]) < drho:
                b[2].append(s); placed = True; break
        if not placed:
            buckets.append((th, rho, [s]))

    merged = []
    for th, rho, segs in buckets:
        ux, uy = math.cos(th), math.sin(th)              # line direction
        pts = []
        for s in segs:
            for (x, y) in ((s[0], s[1]), (s[2], s[3])):
                pts.append((x * ux + y * uy, x, y))      # (param along line, x, y)
        pts.sort()
        run = [pts[0]]
        for p in pts[1:]:
            if p[0] - run[-1][0] > gap:
                _emit(run, merged); run = [p]
            run.append(p)
        _emit(run, merged)

    json.dump({"lines": merged, "arcs": [], "circles": [], "texts": []}, open(a.out, "w"))
    print(f"{len(L)} fragments -> {len(merged)} merged legs  (theta<=1.5deg, "
          f"rho<={drho:.2f}, gap<={gap:.2f})")


def _emit(run, merged):
    if len(run) < 2:
        return
    a, b = run[0], run[-1]
    if math.hypot(b[1] - a[1], b[2] - a[2]) > 1e-9:
        merged.append([a[1], a[2], b[1], b[2], "MERGED"])


if __name__ == "__main__":
    main()

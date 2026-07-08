#!/usr/bin/env python3
"""cogo_assemble.py - assemble ASSOCIATED reads into a validated plan JSON.

The production step after the wave-8 association study: consume a sheet's
segment geometry (_assoc_key.json) and its per-segment VLM reads
(_assoc_reads.json - crop name IS the association) and emit plan-JSON v2:
COGO courses (bearing + distance bound to a segment), a sheet-consistency
report, and the traverse-closure self-check.

Validation layers (each one catches a different lie):
  1. rotation fit  - every read bearing implies a sheet-north rotation vs its
     segment's axis; a single theta must explain them all. Outliers = a misread
     bearing OR a wrong association; either way, flagged.
  2. scale fit     - on single-course chains the printed distance / chain
     length gives the sheet scale; a robust median must explain them all.
  3. ring closure  - where full courses chain end-to-end into a closed ring,
     the courses are summed as vectors: linear misclosure / perimeter is the
     surveyor's precision ratio. Beyond tolerance -> the sheet is flagged for
     a human read. Closure is what turns high recall into a drawing you can
     trust - provably right or explicitly flagged.

Output is consumed by import_plan_json (TeeJay-Survey-MCP / civil3d-mcp) and,
via the back-compat "lines" block, by Open CAD Studio's LS_IMPORTPLAN.

    python cogo_assemble.py --key <_assoc_key.json> --reads <_assoc_reads.json>
        [--unit ft] [--crop-w 1100] [--crop-overlap 200]
        [--bearing-tol 0.5] [--dist-tol 0.05] [--out plan.json]

Stdlib only.
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "eval", "score"))
import score_run  # dms / dms_cands / num - the same matching rules as the eval


def wrap180(a):
    return (a + 90) % 180 - 90


def wrap360(a):
    return (a + 180) % 360 - 180


def seg_axis_az(x0, y0, x1, y1):
    """Segment axis as a survey azimuth mod 180 (page pts, y-down, north=up)."""
    return math.degrees(math.atan2(x1 - x0, -(y1 - y0))) % 180


def collect_courses(key, reads, crop_w, crop_ov):
    """Per-segment reads -> deduped bearings/distances with window positions."""
    per_seg = {}
    step = max(1, crop_w - crop_ov)
    for name, items in reads.items():
        try:
            _, seg_s, w_s = name.split("_")
            seg, win = int(seg_s), int(w_s.lstrip("w"))
        except ValueError:
            continue
        for it in items:
            raw = it.get("raw", "")
            if it.get("kind") == "bearing":
                az = score_run.dms(raw)
                if az is not None:
                    per_seg.setdefault(seg, {"b": [], "d": []})["b"].append(
                        (win * step, raw, az))
            elif it.get("kind") == "distance":
                v = score_run.num(raw)
                if v is not None:
                    per_seg.setdefault(seg, {"b": [], "d": []})["d"].append(
                        (win * step, raw, v))
    # dedupe values repeated across overlapping windows
    for seg, bd in per_seg.items():
        for k, tol in (("b", 0.01), ("d", 0.005)):
            uniq = []
            for pos, raw, v in sorted(bd[k]):
                if not any(abs(v - u[2]) <= tol for u in uniq):
                    uniq.append((pos, raw, v))
            bd[k] = uniq
    return per_seg


def fit_rotation(per_seg, segs, inlier_tol=1.0):
    """One sheet rotation theta (mod 180) that explains bearing-vs-axis
    residuals; RANSAC-lite: best inlier count, then median of inliers."""
    res = []
    for seg, bd in per_seg.items():
        ax = seg_axis_az(*segs[seg])
        for _, raw, az in bd["b"]:
            res.append(wrap180(az - ax))
    if not res:
        return 0.0, 0, 0
    best_theta, best_n = 0.0, -1
    for cand in res:
        n = sum(1 for r in res if abs(wrap180(r - cand)) <= inlier_tol)
        if n > best_n:
            best_n, best_theta = n, cand
    inl = sorted(wrap180(r - best_theta) for r in res
                 if abs(wrap180(r - best_theta)) <= inlier_tol)
    theta = wrap180(best_theta + inl[len(inl) // 2]) if inl else best_theta
    return theta, best_n, len(res)


def fit_scale(per_seg, segs):
    """ft-per-pt from single-course chains (printed distance / chain length)."""
    cands = []
    for seg, bd in per_seg.items():
        if len(bd["d"]) == 1:
            x0, y0, x1, y1 = segs[seg]
            L = math.hypot(x1 - x0, y1 - y0)
            if L > 1:
                cands.append(bd["d"][0][2] / L)
    if not cands:
        return None, 0
    cands.sort()
    return cands[len(cands) // 2], len(cands)


def build_courses(per_seg, segs, theta, scale, b_tol, d_tol):
    """Pair each segment's bearings/distances by window order into courses;
    resolve travel direction from the bearing; flag residual outliers."""
    courses = []
    for seg, bd in sorted(per_seg.items()):
        x0, y0, x1, y1 = segs[seg]
        L = math.hypot(x1 - x0, y1 - y0)
        # pair bearings with distances by WINDOW POSITION along the chain (a
        # course's two labels stack at the same spot), not by list index
        bs, ds = list(bd["b"]), list(bd["d"])
        pairs, used = [], set()
        for b in bs:
            best_j, best_gap = None, float("inf")
            for j, d in enumerate(ds):
                if j in used:
                    continue
                gap = abs(b[0] - d[0])
                if gap < best_gap:
                    best_gap, best_j = gap, j
            if best_j is not None:
                used.add(best_j)
                pairs.append((b, ds[best_j]))
            else:
                pairs.append((b, None))
        pairs.extend((None, d) for j, d in enumerate(ds) if j not in used)
        for b, d in pairs:
            c = {"seg": seg, "px": [round(v, 2) for v in segs[seg]], "flags": []}
            if b:
                _, raw, az = b
                c["bearing"] = raw
                fwd = math.degrees(math.atan2(x1 - x0, -(y1 - y0))) % 360
                cands = [(abs(wrap360(az - ((fwd + theta) % 360))), +1),
                         (abs(wrap360(az - ((fwd + 180 + theta) % 360))), -1)]
                resid, sense = min(cands)
                c["azimuth"] = round(az, 4)
                c["sense"] = sense
                if resid > b_tol:
                    c["flags"].append(f"bearing_residual_{resid:.2f}deg")
            if d:
                _, raw, v = d
                c["distance"] = v
                c["distance_raw"] = raw
                if scale and len(bd["d"]) == 1:
                    err = abs(v - scale * L) / max(v, 1e-9)
                    if err > d_tol:
                        c["flags"].append(f"distance_vs_length_{err*100:.1f}pct")
            if not b or not d:
                c["flags"].append("partial_course")
            courses.append(c)
    return courses


def _circ_median_180(residuals, inlier_tol=1.5):
    """RANSAC-lite circular median mod 180 (same trick as fit_rotation)."""
    if not residuals:
        return 0.0
    best, best_n = residuals[0], -1
    for cand in residuals:
        n = sum(1 for r in residuals if abs(wrap180(r - cand)) <= inlier_tol)
        if n > best_n:
            best_n, best = n, cand
    inl = sorted(wrap180(r - best) for r in residuals
                 if abs(wrap180(r - best)) <= inlier_tol)
    return wrap180(best + inl[len(inl) // 2]) if inl else best


def close_rings(courses, segs, snap=12.0):
    """Chain full courses end-to-end by endpoint snap; for each closed ring sum
    the course vectors -> linear misclosure and the surveyor's precision ratio.

    CLOSURE IS THE ARBITER: courses flagged only for a bearing/distance
    residual still participate (the global rotation fit that flagged them can
    itself be wrong on a mixed sheet); a ring that closes vindicates its
    members, and its residual flags are cleared. Each leg's travel direction
    is resolved against the ring's OWN walk (per-ring rotation fit), not the
    global theta."""
    full = [c for c in courses
            if "azimuth" in c and "distance" in c
            and not any(f.startswith("partial") for f in c["flags"])]
    by_seg = {}
    for c in full:
        by_seg.setdefault(c["seg"], []).append(c)
    # ring walking uses one course per segment; multi-course chains (several
    # lot edges merged into one collinear chain) can't ring-walk anyway
    seg_ids = sorted(s for s, cs in by_seg.items() if len(cs) == 1)
    ends = {}
    for s in seg_ids:
        x0, y0, x1, y1 = segs[s]
        ends[s] = ((x0, y0), (x1, y1))

    def near(p, q):
        return math.hypot(p[0] - q[0], p[1] - q[1]) <= snap

    def walk_dir(s, fwd):
        """Segment's travel direction as a page azimuth (y-down, north=up)."""
        (x0, y0), (x1, y1) = ends[s] if fwd else (ends[s][1], ends[s][0])
        return math.degrees(math.atan2(x1 - x0, -(y1 - y0))) % 360

    rings, used = [], set()
    for start in seg_ids:
        if start in used:
            continue
        path, node = [(start, True)], ends[start][1]
        origin = ends[start][0]
        while True:
            nxt = None
            in_path = {s for s, _ in path}
            for s in seg_ids:
                if s in used or s in in_path:
                    continue
                if near(ends[s][0], node):
                    nxt, node = (s, True), ends[s][1]
                elif near(ends[s][1], node):
                    nxt, node = (s, False), ends[s][0]
                if nxt:
                    break
            if not nxt:
                break
            path.append(nxt)
            if near(node, origin) and len(path) >= 3:
                # per-ring rotation: how the sheet's bearings sit relative to
                # THIS ring's geometric walk (robust to a bad global fit)
                res = [wrap180(by_seg[s][0]["azimuth"] - walk_dir(s, fwd))
                       for s, fwd in path]
                theta_ring = _circ_median_180(res)
                dx = dy = per = 0.0
                senses = {}
                for s, fwd in path:
                    c = by_seg[s][0]
                    want = (walk_dir(s, fwd) + theta_ring) % 360
                    az = c["azimuth"]
                    if abs(wrap360(az - want)) > 90:
                        az = (az + 180) % 360
                    senses[s] = 1 if abs(az - c["azimuth"]) < 1e-9 else -1
                    a = math.radians(az)
                    dx += c["distance"] * math.sin(a)
                    dy += c["distance"] * math.cos(a)
                    per += c["distance"]
                mis = math.hypot(dx, dy)
                ring = {
                    "segs": [s for s, _ in path], "legs": len(path),
                    "perimeter": round(per, 2),
                    "misclosure": round(mis, 3),
                    "theta_ring": round(theta_ring, 4),
                    "precision": f"1:{int(per / mis)}" if mis > 1e-6 else "exact",
                }
                rings.append(ring)
                used.update(s for s, _ in path)
                # a PASSING ring vindicates its members: bake the resolved
                # senses and clear residual flags so consumers draw it as-is
                ratio = mis / per if per else 1.0
                if ratio <= 1e-4 or mis <= 0.05:
                    for s, _ in path:
                        c = by_seg[s][0]
                        c["sense"] = senses[s]
                        c["flags"] = [f for f in c["flags"]
                                      if not (f.startswith("bearing_residual")
                                              or f.startswith("distance_vs_length"))]
                break
    return rings


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", required=True, help="_assoc_key.json (geometry)")
    ap.add_argument("--reads", required=True, help="_assoc_reads.json (VLM)")
    ap.add_argument("--unit", default="ft")
    ap.add_argument("--crop-w", type=int, default=1100)
    ap.add_argument("--crop-overlap", type=int, default=200)
    ap.add_argument("--bearing-tol", type=float, default=0.5,
                    help="flag course when |bearing - (axis+theta)| exceeds (deg)")
    ap.add_argument("--dist-tol", type=float, default=0.05,
                    help="flag course when |dist - scale*len|/dist exceeds")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    key = json.load(open(a.key, encoding="utf-8"))
    reads = json.load(open(a.reads, encoding="utf-8"))
    segs = [tuple(s) for s in key["segments"]]
    px_per_pt = key.get("dpi", 400) / 72.0
    # crop coords are px at dpi; window step must land in pt like the segments
    per_seg = collect_courses(key, reads, a.crop_w / px_per_pt,
                              a.crop_overlap / px_per_pt)

    theta, n_in, n_b = fit_rotation(per_seg, segs)
    scale, n_s = fit_scale(per_seg, segs)
    courses = build_courses(per_seg, segs, theta, scale,
                            a.bearing_tol, a.dist_tol)
    rings = close_rings(courses, segs)

    flagged = [c for c in courses if c["flags"]]
    clean = [c for c in courses if not c["flags"]]
    bad_rings = [r for r in rings
                 if r["misclosure"] > 0.02 * math.sqrt(max(r["legs"], 1))
                 and (r["misclosure"] / max(r["perimeter"], 1e-9)) > 1e-4]

    plan = {
        "schema": "plan2/assoc-v1",
        "unit": a.unit,
        "source": {"key": os.path.basename(a.key),
                   "reads": os.path.basename(a.reads),
                   "pdf": key.get("pdf"), "page": key.get("page")},
        "rotation_deg": round(theta, 4),
        "rotation_inliers": f"{n_in}/{n_b}",
        "scale_unit_per_pt": round(scale, 6) if scale else None,
        "scale_samples": n_s,
        "courses": courses,
        "rings": rings,
        "lines": [list(s) for s in segs],   # back-compat: LS_IMPORTPLAN geometry
        "report": {
            "segments": len(segs),
            "courses": len(courses),
            "courses_clean": len(clean),
            "courses_flagged": len(flagged),
            "rings_closed": len(rings),
            "rings_beyond_tolerance": len(bad_rings),
            "needs_human": bool(bad_rings) or (n_b > 0 and n_in / n_b < 0.7),
        },
    }
    out = a.out or a.reads.replace("_assoc_reads", "_plan_assoc")\
                          .replace(".assoc.json", ".plan_assoc.json")
    json.dump(plan, open(out, "w", encoding="utf-8"), indent=1,
              ensure_ascii=False)
    r = plan["report"]
    print(f"[{os.path.basename(out)}] theta={theta:.3f}deg "
          f"(inliers {n_in}/{n_b}), scale={scale and round(scale,4)} "
          f"{a.unit}/pt ({n_s} samples)")
    print(f"  courses: {r['courses']} ({r['courses_clean']} clean, "
          f"{r['courses_flagged']} flagged); rings closed: {r['rings_closed']}"
          f" ({r['rings_beyond_tolerance']} beyond tolerance)")
    for ring in rings:
        print(f"    ring {ring['legs']} legs, perimeter {ring['perimeter']} "
              f"{a.unit}, misclosure {ring['misclosure']} -> {ring['precision']}")
    print(f"  needs_human: {r['needs_human']}")


if __name__ == "__main__":
    main()

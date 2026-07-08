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


def pair_bd(bd):
    """Pair a chain's bearings with its distances by WINDOW POSITION (a
    course's two labels stack at the same spot), not by list index."""
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
    return pairs


def assign_courses_to_edges(per_chain, edges, scale, halfwin):
    """Bind courses to atomic edges by MONOTONE 1:1 alignment per chain.

    Label order along a chain equals edge order, so this is a sequence
    alignment, not independent nearest-matching — which mattered: subdivision
    lots repeat identical printed widths (50.00' × N), so independent
    best-length matching resolved its ties by iteration order and stacked
    several courses on one edge while its look-alike neighbours starved
    (38/54 quad faces with zero courses despite full crop coverage).

    Edit-distance DP per chain: each course is either assigned to the next
    unused edge in order (cost = position offset + printed-distance vs
    scale×edge-length mismatch), or dropped at fixed cost; edges may be
    skipped freely. At most one course lands on any edge."""
    edges_by_chain = {}
    for ei, e in enumerate(edges):
        edges_by_chain.setdefault(e["chain"], []).append(ei)
    per_edge = {}
    DROP = 3.0
    for chain, bd in per_chain.items():
        eis = sorted(edges_by_chain.get(chain, []),
                     key=lambda ei: edges[ei]["t0"])
        if not eis:
            continue
        pairs = sorted(pair_bd(bd), key=lambda p: (p[0] or p[1])[0])
        n, m = len(pairs), len(eis)
        if not n:
            continue

        def cost(p, ei):
            b, d = p
            pos = (b or d)[0] + halfwin      # window start -> label center
            e = edges[ei]
            mid = (e["t0"] + e["t1"]) / 2
            elen = e["t1"] - e["t0"]
            c = min(abs(pos - mid) / max(halfwin, 1.0), 2.5)
            if d is not None and scale and elen > 0:
                rel = abs(d[2] - scale * elen) / max(d[2], 1e-9)
                c += min(rel * 10.0, 3.0)    # 10% length mismatch = +1.0
            return c

        INF = float("inf")
        g = [[INF] * (m + 1) for _ in range(n + 1)]
        bk = [[None] * (m + 1) for _ in range(n + 1)]
        for j in range(m + 1):
            g[0][j] = 0.0
        for i in range(1, n + 1):
            for j in range(m + 1):
                if j > 0 and g[i][j - 1] < g[i][j]:
                    g[i][j] = g[i][j - 1]
                    bk[i][j] = "skip"
                if g[i - 1][j] + DROP < g[i][j]:
                    g[i][j] = g[i - 1][j] + DROP
                    bk[i][j] = "drop"
                if j > 0:
                    c = g[i - 1][j - 1] + cost(pairs[i - 1], eis[j - 1])
                    if c < g[i][j]:
                        g[i][j] = c
                        bk[i][j] = "take"
        i, j = n, m
        while i > 0:
            move = bk[i][j]
            if move == "skip":
                j -= 1
            elif move == "drop":
                i -= 1
            else:
                per_edge.setdefault(eis[j - 1], []).append(pairs[i - 1])
                i -= 1
                j -= 1
    return per_edge


def build_courses(per_edge, edges, nodes, theta, scale, b_tol, d_tol):
    """Edge-bound course dicts: resolve travel direction from the bearing
    against the EDGE axis; flag residual outliers. `seg` indexes plan lines
    (= planarized edges), keeping the consumer contract unchanged."""
    courses = []
    for ei in sorted(per_edge):
        e = edges[ei]
        ax, ay = nodes[e["a"]]
        bx, by = nodes[e["b"]]
        L = math.hypot(bx - ax, by - ay)
        for b, d in per_edge[ei]:
            c = {"seg": ei,
                 "px": [round(ax, 2), round(ay, 2), round(bx, 2), round(by, 2)],
                 "flags": []}
            if b:
                _, raw, az = b
                c["bearing"] = raw
                fwd = math.degrees(math.atan2(bx - ax, -(by - ay))) % 360
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
                if scale and L > 0:
                    err = abs(v - scale * L) / max(v, 1e-9)
                    if err > d_tol:
                        c["flags"].append(f"distance_vs_length_{err*100:.1f}pct")
            if not b or not d:
                c["flags"].append("partial_course")
            courses.append(c)
    return courses


def planarize(segs, tol=4.0):
    """Split the chained segments at X-crossings and T-junctions into the
    atomic edges of a planar arrangement. Lot corners on a plat are mostly
    T-nodes (one lot's side ends against the interior of a neighbour's merged
    collinear chain), so whole-chain endpoints never meet and no polygon can
    be walked — this is the step that makes faces (= lots) exist at all.

    Returns (nodes, edges): nodes = [(x, y)] cluster centers; edges = dicts
    {a, b (node ids), chain (parent seg index), t0, t1 (pt along the chain)}.
    """
    cuts = []
    geo = []
    for x0, y0, x1, y1 in segs:
        L = math.hypot(x1 - x0, y1 - y0)
        geo.append((x0, y0, x1, y1, L))
        cuts.append({0.0, L})

    for i in range(len(segs)):
        xi0, yi0, xi1, yi1, Li = geo[i]
        rix, riy = xi1 - xi0, yi1 - yi0
        for j in range(i + 1, len(segs)):
            xj0, yj0, xj1, yj1, Lj = geo[j]
            sjx, sjy = xj1 - xj0, yj1 - yj0
            den = rix * sjy - riy * sjx
            if abs(den) > 1e-9:
                # X-crossing (or endpoint touch) via the line-line solve
                qpx, qpy = xj0 - xi0, yj0 - yi0
                t = (qpx * sjy - qpy * sjx) / den   # fraction along i
                u = (qpx * riy - qpy * rix) / den   # fraction along j
                if (-tol / Li <= t <= 1 + tol / Li
                        and -tol / Lj <= u <= 1 + tol / Lj):
                    cuts[i].add(min(max(t * Li, 0.0), Li))
                    cuts[j].add(min(max(u * Lj, 0.0), Lj))
                    continue
            # T-junction with a gap: an endpoint of one chain stopping just
            # short of the other's interior (dash gaps / stroke width)
            for (px, py), (ax0, ay0, rx, ry, La, k) in (
                ((xj0, yj0), (xi0, yi0, rix, riy, Li, i)),
                ((xj1, yj1), (xi0, yi0, rix, riy, Li, i)),
                ((xi0, yi0), (xj0, yj0, sjx, sjy, Lj, j)),
                ((xi1, yi1), (xj0, yj0, sjx, sjy, Lj, j)),
            ):
                tt = ((px - ax0) * rx + (py - ay0) * ry) / (La * La)
                if 0.0 < tt < 1.0:
                    qx, qy = ax0 + tt * rx, ay0 + tt * ry
                    if math.hypot(px - qx, py - qy) <= tol:
                        cuts[k].add(tt * La)

    # cluster all cut points into nodes (grid hash, tol radius)
    nodes, grid = [], {}

    def node_id(x, y):
        cx, cy = int(x // tol), int(y // tol)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for ni in grid.get((gx, gy), ()):
                    nx, ny = nodes[ni]
                    if math.hypot(x - nx, y - ny) <= tol:
                        return ni
        nodes.append((x, y))
        grid.setdefault((cx, cy), []).append(len(nodes) - 1)
        return len(nodes) - 1

    edges = []
    for i, (x0, y0, x1, y1, L) in enumerate(geo):
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        ts = sorted(cuts[i])
        merged = [ts[0]]
        for t in ts[1:]:
            if t - merged[-1] > tol:
                merged.append(t)
        for t0, t1 in zip(merged, merged[1:]):
            a = node_id(x0 + t0 * ux, y0 + t0 * uy)
            b = node_id(x0 + t1 * ux, y0 + t1 * uy)
            if a != b:
                edges.append({"a": a, "b": b, "chain": i,
                              "t0": t0, "t1": t1})
    return nodes, edges


def extract_faces(nodes, edges):
    """Faces of the planar graph via the angular (next-clockwise) walk.
    Returns faces as lists of (edge_idx, forward_bool); the unbounded outer
    face (largest |area|) and degenerate slivers are dropped."""
    out = {}
    for ei, e in enumerate(edges):
        ax, ay = nodes[e["a"]]
        bx, by = nodes[e["b"]]
        ang_ab = math.atan2(by - ay, bx - ax)
        ang_ba = math.atan2(ay - by, ax - bx)
        out.setdefault(e["a"], []).append((ang_ab, ei, True))
        out.setdefault(e["b"], []).append((ang_ba, ei, False))
    for v in out.values():
        v.sort()

    def next_he(ei, fwd):
        e = edges[ei]
        v = e["b"] if fwd else e["a"]
        back = (ei, not fwd)
        ring = out[v]
        k = next(i for i, (_, e2, f2) in enumerate(ring)
                 if (e2, f2) == back)
        _, ne, nf = ring[(k - 1) % len(ring)]   # next clockwise
        return ne, nf

    faces, seen = [], set()
    for ei in range(len(edges)):
        for fwd in (True, False):
            if (ei, fwd) in seen:
                continue
            cyc, cur = [], (ei, fwd)
            while cur not in seen:
                seen.add(cur)
                cyc.append(cur)
                cur = next_he(*cur)
            if cur == (ei, fwd) and len(cyc) >= 3:
                faces.append(cyc)

    def area(cyc):
        s = 0.0
        for e2, f2 in cyc:
            e = edges[e2]
            (ax, ay) = nodes[e["a"] if f2 else e["b"]]
            (bx, by) = nodes[e["b"] if f2 else e["a"]]
            s += ax * by - bx * ay
        return s / 2.0

    scored = [(area(c), c) for c in faces if abs(area(c)) > 25.0]  # no slivers
    if not scored:
        return []
    # Interior and outer faces get opposite orientation signs under a fixed
    # traversal rule — and every component's outer face shares the same sign,
    # so sign-filtering also handles disconnected linework correctly.
    outer_sign = math.copysign(1.0, max(scored, key=lambda sc: abs(sc[0]))[0])
    return [c for a, c in scored if math.copysign(1.0, a) != outer_sign]


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


def close_faces(faces, edges, nodes, courses):
    """Traverse-closure per FACE of the planar arrangement (faces = lots).

    CLOSURE IS THE ARBITER: courses flagged only for a bearing/distance
    residual still participate (the global rotation fit that flagged them can
    itself be wrong on a mixed sheet); a face that closes vindicates its
    members, and its residual flags are cleared. Each leg's travel direction
    is resolved against the face's OWN walk (per-ring rotation fit), not the
    global theta. Returns (rings, faces_fully_coursed)."""
    by_edge = {}
    for c in courses:
        if "azimuth" in c and "distance" in c \
                and not any(f.startswith("partial") for f in c["flags"]):
            by_edge.setdefault(c["seg"], c)

    def walk_dir(ei, fwd):
        e = edges[ei]
        (x0, y0) = nodes[e["a"] if fwd else e["b"]]
        (x1, y1) = nodes[e["b"] if fwd else e["a"]]
        return math.degrees(math.atan2(x1 - x0, -(y1 - y0))) % 360

    rings, n_full = [], 0
    for face in faces:
        if any(ei not in by_edge for ei, _ in face):
            continue                     # a leg has no readable course
        n_full += 1
        # consumers anchor the ring at its first edge's `a` node and walk
        # forward — rotate the cycle so leg 0 is traversed forward
        k = next((i for i, (_, fwd) in enumerate(face) if fwd), 0)
        face = face[k:] + face[:k]
        res = [wrap180(by_edge[ei]["azimuth"] - walk_dir(ei, fwd))
               for ei, fwd in face]
        theta_ring = _circ_median_180(res)
        dx = dy = per = 0.0
        senses = {}
        for ei, fwd in face:
            c = by_edge[ei]
            want = (walk_dir(ei, fwd) + theta_ring) % 360
            az = c["azimuth"]
            if abs(wrap360(az - want)) > 90:
                az = (az + 180) % 360
            senses[ei] = 1 if abs(az - c["azimuth"]) < 1e-9 else -1
            a = math.radians(az)
            dx += c["distance"] * math.sin(a)
            dy += c["distance"] * math.cos(a)
            per += c["distance"]
        mis = math.hypot(dx, dy)
        rings.append({
            "segs": [ei for ei, _ in face], "legs": len(face),
            "perimeter": round(per, 2),
            "misclosure": round(mis, 3),
            "theta_ring": round(theta_ring, 4),
            "precision": f"1:{int(per / mis)}" if mis > 1e-6 else "exact",
        })
        # a PASSING face vindicates its members: bake the resolved senses and
        # clear residual flags so consumers draw it as-is
        ratio = mis / per if per else 1.0
        if ratio <= 1e-4 or mis <= 0.05:
            for ei, _ in face:
                c = by_edge[ei]
                c["sense"] = senses[ei]
                c["flags"] = [f for f in c["flags"]
                              if not (f.startswith("bearing_residual")
                                      or f.startswith("distance_vs_length"))]
    return rings, n_full


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

    planar = key.get("planar")
    if planar:
        # EDGE-CROP mode: the stage phase already planarized and cropped one
        # strip per atomic edge, so the crop index IS the edge id — no
        # assignment step, association is true by construction.
        nodes = [tuple(p) for p in planar["nodes"]]
        edges = planar["edges"]
        geom = [(*nodes[e["a"]], *nodes[e["b"]]) for e in edges]
        theta, n_in, n_b = fit_rotation(per_seg, geom)
        scale, n_s = fit_scale(per_seg, geom)
        per_edge = {ei: pair_bd(bd) for ei, bd in per_seg.items()
                    if ei < len(edges)}
    else:
        theta, n_in, n_b = fit_rotation(per_seg, segs)
        scale, n_s = fit_scale(per_seg, segs)
        # planarize here: chains -> atomic edges at X/T junctions
        nodes, edges = planarize(segs)
        halfwin = (a.crop_w / px_per_pt) / 2.0
        per_edge = assign_courses_to_edges(per_seg, edges, scale, halfwin)
    faces = extract_faces(nodes, edges)
    courses = build_courses(per_edge, edges, nodes, theta, scale,
                            a.bearing_tol, a.dist_tol)
    rings, faces_full = close_faces(faces, edges, nodes, courses)
    elines = [[round(v, 2) for v in (*nodes[e["a"]], *nodes[e["b"]])]
              for e in edges]

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
        # lines = the PLANARIZED edges (same ink as the chains, split at X/T
        # junctions); course.seg and ring.segs index into this list, so every
        # consumer (LS_IMPORTPLAN, both MCPs) keeps working unchanged
        "lines": elines,
        "report": {
            "segments": len(segs),
            "edges": len(edges),
            "faces": len(faces),
            "faces_fully_coursed": faces_full,
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
    print(f"  {r['segments']} chains -> {r['edges']} edges, {r['faces']} faces "
          f"({r['faces_fully_coursed']} fully coursed)")
    print(f"  courses: {r['courses']} ({r['courses_clean']} clean, "
          f"{r['courses_flagged']} flagged); rings closed: {r['rings_closed']}"
          f" ({r['rings_beyond_tolerance']} beyond tolerance)")
    for ring in rings:
        print(f"    ring {ring['legs']} legs, perimeter {ring['perimeter']} "
              f"{a.unit}, misclosure {ring['misclosure']} -> {ring['precision']}")
    print(f"  needs_human: {r['needs_human']}")


if __name__ == "__main__":
    main()

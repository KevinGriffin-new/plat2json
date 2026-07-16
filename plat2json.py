#!/usr/bin/env python3
"""plat2json — extract drawable geometry from a vector survey-plat PDF.

Reads a vector PDF plat (LTSA-style: linework flattened to line segments, text
as stroked glyphs, no text layer, no arc primitives), isolates the parcel
linework, vectorizes it, and writes a plan-JSON of world-coordinate geometry
that Open CAD Studio's LandSurvey plugin (LS_IMPORTPLAN) — or any consumer —
can draw. One extraction, many sinks.

STATUS: experimental. Output is a rough geometry skeleton, NOT survey-grade:
  * geometry comes out as many short Hough segments (fragment-soup, ~100+
    segments for ~10 real edges) — needs skeleton path-tracing to clean up;
  * arcs are NOT fitted here (see fit_arcs.py / arc_refine.py);
  * labels (bearings, distances, curve r=/a=) are NOT read HERE — label reading
    is solved separately by the VLM reader (per-sheet median bearing recall 0.95
    on the 100-sheet NCDOT corpus — see eval/results/RESULTS.md); the open work
    is label→segment association, i.e. joining that reader's values to this
    script's geometry.

Pipeline: render -> binarize (Otsu) -> keep long connected components as
linework (drop the page border) -> largest = the parcel -> skeletonize ->
HoughLinesP -> map paper-points to ground metres at the plot scale, Y flipped
north-up.

Usage:
    python plat2json.py INPUT.pdf OUTPUT.json [--dpi 300] [--plot-scale 250]
"""
import argparse
import json
import sys


def merge_collinear(chains, ang_tol_deg=28.0, max_gap=5.0, tail=8):
    """Good-continuation merge: re-join raw skeleton chains across junctions.

    Every tick mark, crossing, or label touch splits a real drawn line into
    junction-to-junction fragments; judging min_len against the FRAGMENTS is
    what silently discarded most of the lot fabric (measured by
    overlay_check.py: 34.5% -> ~70% linework coverage on 482.pdf when the cut
    no longer sees fragments). At each junction, pair chain ends that continue
    each other (endpoints within max_gap px, outward directions within
    ang_tol of anti-parallel), greedily by best angle; union the pairings into
    merged point runs. Ticks leave a junction at a steep angle and never pair;
    glyph debris has no long continuation, so length (applied AFTER merging)
    stays the discriminator the NOTE below argues for."""
    import numpy as np
    ends = []  # (chain_idx, end, point(r,c), outward unit dir)
    for ci, ch in enumerate(chains):
        if len(ch) < 2:
            continue
        a = np.asarray(ch, dtype=float)
        for end, (p, q) in enumerate(((a[0], a[min(tail, len(a)-1)]),
                                      (a[-1], a[max(-1-tail, -len(a))]))):
            d = p - q
            n = np.hypot(*d)
            if n > 0:
                ends.append((ci, end, p, d / n))
    # candidate pairs from a coarse grid (junction ends sit within a few px)
    cell = max(int(max_gap), 1)
    grid = {}
    for k, (_, _, p, _) in enumerate(ends):
        grid.setdefault((int(p[0]) // cell, int(p[1]) // cell), []).append(k)
    cos_tol = np.cos(np.radians(ang_tol_deg))
    cands = []
    for (gr, gc), ks in grid.items():
        near = [k for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                for k in grid.get((gr + dr, gc + dc), [])]
        for k in ks:
            ci, e, p, d = ends[k]
            for j in near:
                if j <= k or ends[j][0] == ci:
                    continue
                cj, ej, pj, dj = ends[j]
                if np.hypot(*(p - pj)) > max_gap:
                    continue
                c = float(-(d @ dj))          # continuation: outward dirs anti-parallel
                if c >= cos_tol:
                    cands.append((c, k, j))
    links, taken = {}, set()
    for _, k, j in sorted(cands, reverse=True):  # best continuation first
        if k in taken or j in taken:
            continue
        taken.update((k, j))
        a, b = (ends[k][0], ends[k][1]), (ends[j][0], ends[j][1])
        links[a], links[b] = b, a
    merged, seen = [], set()

    def run(ci, enter):
        seq, cur, ent = [], ci, enter
        while cur not in seen:
            seen.add(cur)
            p = list(chains[cur]) if ent == 0 else list(chains[cur])[::-1]
            seq.extend(p if not seq else p[1:] if seq[-1] == p[0] else p)
            nxt = links.get((cur, 1 - ent))
            if not nxt:
                break
            cur, ent = nxt
        return seq

    for ci in range(len(chains)):
        if ci in seen:
            continue
        if (ci, 0) not in links:
            merged.append(run(ci, 0))
        elif (ci, 1) not in links:
            merged.append(run(ci, 1))
    for ci in range(len(chains)):                # closed cycles of linked chains
        if ci not in seen:
            merged.append(run(ci, 0))
    return merged


def repair_topology(polys, max_gap=60.0, ang_tol_deg=25.0, tail_len=25.0,
                    ink=None, long_max=0.0, min_anchor=100.0, absorb=True):
    """Close the breaks that block face formation. Label text printed on a
    line, scan dropouts, and corner monument symbols leave dangling ends —
    planarize+extract_faces formed ~0 lot faces at ANY snap tolerance until
    these were repaired (measured on 482.pdf via a dangling-ends map). Three
    join types, in priority order:

      A) collinear BRIDGE — two ends continue each other through a gap
         (label/dropout break): anti-parallel within ang_tol, mutually on each
         other's direction line (lateral tolerance grows with the gap — end
         directions are least-squares fits, still noisy).
      B) CORNER join — two ends' direction lines intersect ahead of both
         within ~max_gap (the shared vertex sat inside a dropped monument
         symbol): connect through the intersection.
      C) T-EXTEND — a free end whose forward ray meets another polyline
         within ~max_gap: extend to just past the hit so planarize splits.

    max_gap must stay well under the narrowest road width on the sheet, or
    radial lot lines bridge straight across the right-of-way (60 ft =
    ~180 px at 300 dpi / 1\"=100 ft).

    LONG bridges (`long_max` > max_gap, needs `ink`): a boundary line broken
    by an INLINE label ("N 00°06'51\"W — 1258.98'" written in a gap of the
    line itself) can gap several hundred px — beyond any safe blind range.
    The discriminator is the corridor: an inline-label gap contains NO
    linework ink (the label glyphs were dropped by the component filter),
    while a false bridge across a road always crosses curb/centerline ink.
    So beyond max_gap, a bridge must pass a strict collinearity gate AND an
    ink-free-corridor test against `ink` (the linework mask in the same
    pixel space as the polylines).

    `min_anchor`: corner joins (B) and T-extends (C) only fire from chains at
    least this long — ticks and small arc fragments must not manufacture
    corners. Collinear bridges (A) accept shorter chains: the middle pieces
    of a twice-broken boundary line are themselves short, and A's mutual-
    collinearity gate is what protects it.

    Operates on simplified (x, y) vertex arrays; returns the repaired list."""
    import numpy as np
    polys = [np.asarray(p, dtype=np.float64) for p in polys]

    def end_dir(a, end):
        """Outward unit direction at an end, least-squares over the trailing
        tail_len of arc length (robust to skeleton hooks at break points)."""
        pts = a if end == 1 else a[::-1]        # make the end the LAST point
        acc, i = 0.0, len(pts) - 1
        while i > 0 and acc < tail_len:
            acc += float(np.hypot(*(pts[i] - pts[i-1])))
            i -= 1
        seg = pts[i:]
        if len(seg) < 2:
            return None
        c = seg - seg.mean(0)
        d = np.linalg.svd(c, full_matrices=False)[2][0]
        if float(d @ (seg[-1] - seg[0])) < 0:
            d = -d
        return d

    ends = []                                    # (poly, end, point, dir)
    for ci, a in enumerate(polys):
        if len(a) < 2 or np.allclose(a[0], a[-1]):
            continue                              # degenerate, or a closed ring
        if float(np.hypot(*np.diff(a, axis=0).T).sum()) < 6.0:
            continue                              # sub-6 px specks: pure noise

        for end in (0, 1):
            d = end_dir(a, end)
            if d is not None:
                ends.append((ci, end, a[0] if end == 0 else a[-1], d))

    plen_of = [float(np.hypot(*np.diff(a, axis=0).T).sum()) if len(a) > 1 else 0.0
               for a in polys]
    cos_tol = np.cos(np.radians(ang_tol_deg))
    cos_long = np.cos(np.radians(14.0))          # long bridges: strict heading, but
                                                 # loose enough for a curb ARC gap
                                                 # (~10 deg drift over a label break
                                                 # at R~23 units); the ink-free
                                                 # corridor is the real guard
    links, taken = {}, set()                     # (ci,end) -> ((cj,endj), X|None)

    def claim(k, j, X):
        taken.update((k, j))
        a = (ends[k][0], ends[k][1])
        b = (ends[j][0], ends[j][1])
        links[a], links[b] = (b, X), (a, X)

    def corridor_free(p, q):
        """No linework ink strictly between p and q (6 px end margins)."""
        v = q - p
        L = float(np.hypot(*v))
        if L <= 12.0:
            return True
        u = v / L
        H, W = ink.shape
        for t in np.arange(6.0, L - 6.0, 2.0):
            x, y = p + t * u
            xi, yi = int(round(x)), int(round(y))
            if 0 <= yi < H and 0 <= xi < W and ink[max(0, yi-1):yi+2, max(0, xi-1):xi+2].any():
                return False
        return True

    # ---- A: collinear bridges (shortest first) ----
    hard_max = max(max_gap, long_max if (long_max and ink is not None) else 0.0)
    cands = []
    for k in range(len(ends)):
        ci, e, p, d = ends[k]
        for j in range(k + 1, len(ends)):
            cj, ej, pj, dj = ends[j]
            if cj == ci:
                continue
            v = pj - p
            gap = float(np.hypot(*v))
            if gap > hard_max:
                continue
            # SHORT chains (< 30 px) may only be ABSORBED, not chain freely:
            # a real fragment of a twice-broken line sits between two long
            # pieces and is strictly collinear with them; easement DASHES are
            # short on BOTH sides (dash-to-dash rejected here, or the dashed
            # layer completes itself and subdivides every lot face — measured:
            # a 5000-bridge storm that cost two validated lots), and glyph
            # debris fails collinearity.
            if plen_of[ci] < 30.0 or plen_of[cj] < 30.0:
                if not absorb:
                    continue
                if min(plen_of[ci], plen_of[cj]) < 30.0 and \
                   max(plen_of[ci], plen_of[cj]) < min_anchor:
                    continue
                if float(-(d @ dj)) < cos_long:
                    continue
                if abs(float(np.cross(d, v))) > 4.0 or abs(float(np.cross(dj, v))) > 4.0:
                    continue
                if float(d @ v) > 0:
                    cands.append((gap, k, j, None))
                continue
            proj = float(d @ v)
            if proj < 0:
                # OVERLAP WELD: two traces of the same line passing each other
                # (ends point apart). planarize cannot join collinear overlaps
                # (the parallel line-line solve is degenerate), so weld here:
                # anti-parallel, laterally tight, backtrack <= 25 px, joined
                # through the midpoint so node clustering absorbs the fold.
                if proj < -25.0 or float(-(d @ dj)) < cos_tol:
                    continue
                if abs(float(np.cross(d, v))) > 6.0 or abs(float(np.cross(dj, v))) > 6.0:
                    continue
                cands.append((abs(proj), k, j, (p + pj) / 2.0))
                continue
            if gap < 1e-9:
                continue
            if gap <= max_gap:
                if float(-(d @ dj)) < cos_tol:
                    continue
                # 6 px lateral floor: DP eps + skeleton jitter + dash offset
                # stack to ~5 px; parallel easement dashes sit far beyond it
                lat = max(6.0, 0.20 * gap)
                if abs(float(np.cross(d, v))) > lat or abs(float(np.cross(dj, v))) > lat:
                    continue                      # not mutually collinear
            else:                                 # long bridge: strict + empty corridor
                if float(-(d @ dj)) < cos_long:
                    continue
                if abs(float(np.cross(d, v))) > 6.0 or abs(float(np.cross(dj, v))) > 6.0:
                    continue
                if not corridor_free(p, pj):
                    continue
            cands.append((gap, k, j, None))
    nA = nL = 0
    for gap, k, j, X in sorted(cands, key=lambda t: t[0]):
        if k not in taken and j not in taken:
            claim(k, j, X)
            if gap > max_gap:
                nL += 1
            else:
                nA += 1

    # ---- B: corner joins through the direction-line intersection ----
    cands = []
    for k in range(len(ends)):
        if k in taken or plen_of[ends[k][0]] < min_anchor:
            continue
        ci, e, p, d = ends[k]
        for j in range(k + 1, len(ends)):
            if j in taken or ends[j][0] == ci or plen_of[ends[j][0]] < min_anchor:
                continue
            cj, ej, pj, dj = ends[j]
            den = float(np.cross(d, dj))
            if abs(den) < 0.17:                  # near-parallel: A's business
                continue
            v = pj - p
            ti = float(np.cross(v, dj)) / den    # p + ti*d = intersection
            tj = float(np.cross(v, d)) / den     # pj + tj*dj = same point
            if 0.0 <= ti <= max_gap and 0.0 <= tj <= max_gap and ti + tj > 1.0:
                cands.append((ti + tj, k, j, p + ti * d))
    nB = 0
    for _, k, j, X in sorted(cands, key=lambda t: t[0]):
        if k not in taken and j not in taken:
            claim(k, j, X)
            nB += 1

    # ---- B2: junction reconstruction — several free ends clustered around
    # an EATEN crossing (a corner/monument dot at an X junction leaves 3-4
    # line ends around a hole; pairwise corner joins cannot rebuild a 4-way).
    # Weld every free anchor-length end in the cluster to the common point;
    # planarize's node clustering then makes it one junction. ----
    nB2 = 0
    free = [k for k in range(len(ends))
            if k not in taken and plen_of[ends[k][0]] >= min_anchor]
    for k in free:
        if k in taken:
            continue
        grp = [j for j in free if j not in taken
               and float(np.hypot(*(ends[j][2] - ends[k][2]))) <= 30.0]
        if len(grp) < 2:
            continue
        X = np.mean([ends[j][2] for j in grp], axis=0)
        # every member's outward ray must POINT AT the weld point (ahead,
        # within ~35 deg) — otherwise two unrelated stubs that merely pass
        # near each other get fused into a chord that cuts a real face
        # (measured: -1 validated lot with unconditional centroid welding)
        ok = []
        for j in grp:
            w = X - ends[j][2]
            L = float(np.hypot(*w))
            if L < 3.0 or float(ends[j][3] @ (w / L)) >= 0.82:
                ok.append(j)
        if len(ok) < 3:
            continue          # 2-end joins are phase A/B's business — a pair
                              # weld here just fuses stubs that happen to pass
                              # near each other; B2 exists for eaten MULTI-way
                              # junctions only
        for j in ok:
            cj, ej = ends[j][0], ends[j][1]
            if ej == 0:
                polys[cj] = np.vstack([X[None, :], polys[cj]])
            else:
                polys[cj] = np.vstack([polys[cj], X[None, :]])
            taken.add(j)
        nB2 += 1

    # ---- C: T-extend remaining free ends onto crossing linework ----
    nC = 0
    dense, owner = [], []
    for ci, a in enumerate(polys):
        for s in range(len(a) - 1):
            L = float(np.hypot(*(a[s+1] - a[s])))
            n = max(2, int(L / 2.0) + 1)
            t = np.linspace(0, 1, n)[:, None]
            dense.append(a[s] + t * (a[s+1] - a[s]))
            owner.extend([ci] * n)
    dense = np.vstack(dense); owner = np.asarray(owner)
    try:
        from scipy.spatial import cKDTree
        tree = cKDTree(dense)
    except ImportError:
        tree = None
    if tree is not None:
        for k, (ci, e, p, d) in enumerate(ends):
            if k in taken or plen_of[ci] < min_anchor:
                continue
            for t in np.arange(3.0, max_gap, 2.0):
                q = p + t * d
                dd, jj = tree.query(q, k=8)
                hit = [x for x, di in zip(np.atleast_1d(jj), np.atleast_1d(dd))
                       if di <= 2.5 and owner[x] != ci]
                if hit:
                    ext = p + (t + 3.0) * d      # overshoot so planarize splits
                    if e == 0:
                        polys[ci] = np.vstack([ext[None, :], polys[ci]])
                    else:
                        polys[ci] = np.vstack([polys[ci], ext[None, :]])
                    nC += 1
                    break
    print(f"  [repair] joins: {nA} bridge + {nL} long-bridge + {nB} corner "
          f"+ {nB2} junction + {nC} T-extend", file=sys.stderr)

    # ---- assemble A/B links into merged polylines ----
    merged, seen = [], set()

    def run(ci, enter):
        seq, cur, ent = [], ci, enter
        while cur not in seen:
            seen.add(cur)
            a = polys[cur] if ent == 0 else polys[cur][::-1]
            seq.append(a)
            nxt = links.get((cur, 1 - ent))
            if not nxt:
                break
            (cur, ent), X = nxt
            if X is not None:
                seq.append(np.asarray(X, dtype=np.float64)[None, :])
        return np.vstack(seq)

    for ci in range(len(polys)):
        if ci in seen:
            continue
        if (ci, 0) not in links:
            merged.append(run(ci, 0))
        elif (ci, 1) not in links:
            merged.append(run(ci, 1))
    for ci in range(len(polys)):
        if ci not in seen:
            merged.append(run(ci, 0))
    return merged


def trace_polylines(skel, eps, min_len, merge=True, bridge_px=0.0, ink=None,
                    absorb=True):
    """Walk a 1-px skeleton into ordered polylines, replacing Hough's fragment-
    soup (one curve -> many short stray segments) with one ordered polyline per
    edge. Split the skeleton graph at endpoints/junctions (degree != 2), trace
    each degree-2 chain between them, then seed any remaining closed loops (a
    boundary ring has no endpoints); re-join collinear chains across junctions
    (merge_collinear) so a real line chopped by ticks/crossings is judged
    whole; drop merged chains shorter than min_len px (spurs, ticks, mesh
    detail, stroked-glyph debris); Douglas-Peucker each survivor down to its
    vertices. Returns a list of polylines, each a list of (x, y) px pts."""
    import numpy as np
    import cv2
    ys, xs = np.where(skel > 0)
    pts = set(zip(ys.tolist(), xs.tolist()))
    N8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    def nbrs(p):
        r, c = p
        return [q for q in ((r + dr, c + dc) for dr, dc in N8) if q in pts]

    deg = {p: len(nbrs(p)) for p in pts}
    used = set()  # consumed edges, as frozenset({a, b})

    def walk(start, nxt):
        path = [start, nxt]
        used.add(frozenset((start, nxt)))
        prev, cur = start, nxt
        while deg.get(cur, 0) == 2:  # follow the chain until a node or dead end
            ahead = [q for q in nbrs(cur)
                     if q != prev and frozenset((cur, q)) not in used]
            if not ahead:
                break
            q = ahead[0]
            used.add(frozenset((cur, q)))
            path.append(q)
            prev, cur = cur, q
        return path

    chains = []
    for p in pts:                      # branches anchored at endpoints/junctions
        if deg[p] != 2:
            for q in nbrs(p):
                if frozenset((p, q)) not in used:
                    chains.append(walk(p, q))
    for p in pts:                      # leftover closed loops (all degree-2)
        if deg[p] == 2:
            for q in nbrs(p):
                if frozenset((p, q)) not in used:
                    chains.append(walk(p, q))

    def plen(a):  # polyline pixel length
        return float(np.hypot(np.diff(a[:, 0]), np.diff(a[:, 1])).sum()) if len(a) > 1 else 0.0

    # NOTE: a topology-aware "spur prune" (drop only chains that dead-end at a
    # degree-1 pixel) was tried and reverted - busy-sheet noise is a connected
    # MESH of short junction-to-junction segments (hatching, dimension structure,
    # fine detail), not dead-end spurs, so length is the robust discriminator.
    # merge_collinear keeps that stance: length still decides, but only after
    # fragments of the same drawn line are re-joined across junctions.
    if merge:
        chains = merge_collinear(chains)
    # simplify FIRST: repair_topology fits end directions from vertices, and
    # DP vertices are far less noisy than raw skeleton pixels (hooks at breaks)
    simp = []
    for ch in chains:
        a = np.array([[c, r] for r, c in ch], dtype=np.float64)  # (x, y) = (col, row)
        if len(a) >= 3:
            s = cv2.approxPolyDP(a.astype(np.int32).reshape(-1, 1, 2), eps, False)
            a = s.reshape(-1, 2).astype(np.float64)
        simp.append(a)
    if bridge_px > 0:
        # repair breaks AFTER junction merging. EVERY chain participates in
        # phase A — a length cutoff here recreates the unreachable-island bug
        # one level down (a 15 px fragment between two label breaks gets
        # excluded, its neighbors' direct bridge is corridor-blocked by the
        # fragment's own ink, and min_len later deletes it, leaving a hole).
        # Corner/T/junction phases gate on min_anchor internally so ticks and
        # debris can't manufacture structure.
        simp = repair_topology(simp, max_gap=bridge_px, ink=ink,
                               long_max=4.0 * bridge_px,
                               min_anchor=min_len * 0.5, absorb=absorb)
    return [[(float(x), float(y)) for x, y in a] for a in simp
            if plen(a) >= min_len]  # spur / tick / mesh-detail / glyph debris


def main():
    ap = argparse.ArgumentParser(description="Vector survey-plat PDF -> plan-JSON geometry.")
    ap.add_argument("pdf", help="input vector plat PDF")
    ap.add_argument("out", help="output plan-JSON path")
    ap.add_argument("--dpi", type=int, default=300, help="render DPI (default 300)")
    ap.add_argument("--plot-scale", type=float, default=250.0,
                    help="plot scale denominator, e.g. 250 for 1:250 (default 250)")
    ap.add_argument("--layer", default="PROPERTY_LINE", help="layer name for output lines")
    ap.add_argument("--page", type=int, default=0, help="page index (default 0)")
    ap.add_argument("--vectorize", choices=["trace", "hough"], default="trace",
                    help="skeleton -> geometry: 'trace' = ordered polylines "
                         "(default, clean); 'hough' = legacy fragment segments")
    ap.add_argument("--simplify-eps", type=float, default=2.0,
                    help="Douglas-Peucker epsilon (px) for --vectorize trace (default 2.0)")
    ap.add_argument("--min-len", type=float, default=0.0,
                    help="drop traced polylines shorter than N px (default 0 = 3x the "
                         "linework threshold; filters spurs/ticks/mesh-detail/glyphs. "
                         "Raise on detail-dense sheets, lower to keep short lot lines)")
    ap.add_argument("--mask-text", action=argparse.BooleanOptionalAction, default=True,
                    help="erase the PDF text layer's word boxes from the raster before "
                         "skeletonizing, so labels don't pollute the linework (default on; "
                         "no-ops on scans / stroked-glyph plats with no text layer)")
    ap.add_argument("--merge-collinear", action=argparse.BooleanOptionalAction, default=True,
                    help="re-join collinear skeleton chains across junctions before the "
                         "min-len cut, so lines chopped by ticks/crossings are judged "
                         "whole instead of discarded as fragments (default on)")
    ap.add_argument("--absorb-fragments", action=argparse.BooleanOptionalAction, default=True,
                    help="let sub-30 px fragments be absorbed into anchor chains during "
                         "repair (strict collinearity; fixes island holes between double "
                         "label breaks). --no-absorb-fragments for ablation.")
    ap.add_argument("--bridge-gaps", type=float, default=0.2,
                    help="bridge collinear dangling ends across label/dropout breaks, "
                         "as a fraction of an inch at the render dpi (default 0.2 = "
                         "60 px at 300 dpi; MUST stay under the narrowest road width "
                         "or radial lot lines bridge across the right-of-way; 0 = off)")
    args = ap.parse_args()

    try:
        import fitz  # PyMuPDF
        import cv2
        import numpy as np
        from skimage.morphology import skeletonize
    except ImportError as e:
        sys.exit(f"missing dependency ({e}). Run: pip install -r requirements.txt")

    sc = args.dpi / 72.0
    # 1 paper-point = 1/72 in = 25.4/72 mm; at 1:scale that's *scale ground-mm.
    pt2m = 25.4 / 72 / 1000 * args.plot_scale

    page = fitz.open(args.pdf)[args.page]
    h_pt = page.rect.height
    pix = page.get_pixmap(dpi=args.dpi, colorspace=fitz.csGRAY)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    H, W = g.shape
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Erase the text layer's word boxes so published labels don't pollute the
    # linework skeleton (the big noise source on text-dense vector plats). Exact
    # boxes from the PDF, so no fragile text/line heuristic; no-ops when the page
    # has no text layer (scans, stroked-glyph plats). Small gaps where a label sat
    # on a line are harmless to tracing (the chain just splits and re-traces).
    nmask = 0
    if args.mask_text:
        ox, oy = page.rect.x0, page.rect.y0
        pad = 2
        for wx0, wy0, wx1, wy1, *_ in page.get_text("words"):
            X0, Y0 = max(0, int((wx0 - ox) * sc) - pad), max(0, int((wy0 - oy) * sc) - pad)
            X1, Y1 = min(W, int((wx1 - ox) * sc) + pad), min(H, int((wy1 - oy) * sc) + pad)
            if X1 > X0 and Y1 > Y0:
                bw[Y0:Y1, X0:X1] = 0
                nmask += 1

    # long connected components = linework; short ones = stroked glyphs (dropped).
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    line_px = int(90 * args.dpi / 400)
    geom = np.zeros_like(bw)
    boxes = []
    for i in range(1, n):
        x, y, w, hh, area = stats[i]
        if max(w, hh) > line_px and area >= 6:
            if w > 0.9 * W and hh > 0.9 * H:
                continue  # the page border rectangle
            geom[lab == i] = 255
            boxes.append((w * hh, x, y, w, hh))
    if not boxes:
        sys.exit("no linework found — is this a vector plat?")

    boxes.sort(reverse=True)
    _, px, py, pw, ph = boxes[0]  # largest non-border linework CC = the parcel
    m = int(0.04 * max(pw, ph))
    x0, y0 = max(0, px - m), max(0, py - m)
    x1, y1 = min(W, px + pw + m), min(H, py + ph + m)

    skel = skeletonize(geom[y0:y1, x0:x1] > 0).astype(np.uint8) * 255

    def tf(xpx, ypx):
        xpt, ypt = (xpx + x0) / sc, (ypx + y0) / sc
        return [round(xpt * pt2m, 4), round((h_pt - ypt) * pt2m, 4)]  # metres, north-up

    lines, polylines, npoly = [], [], 0
    if args.vectorize == "trace":
        polys = trace_polylines(skel, args.simplify_eps, args.min_len or line_px * 3,
                                merge=args.merge_collinear,
                                bridge_px=args.bridge_gaps * args.dpi,
                                ink=geom[y0:y1, x0:x1] > 0,
                                absorb=args.absorb_fragments)
        npoly = len(polys)
        for poly in polys:
            world = [tf(x, y) for x, y in poly]
            polylines.append([[p[0], p[1]] for p in world] + [args.layer])
            for i in range(len(world) - 1):
                a, b = world[i], world[i + 1]
                if a != b:
                    lines.append([a[0], a[1], b[0], b[1], args.layer])
    else:
        segs = cv2.HoughLinesP(skel, 1, np.pi / 360, threshold=22, minLineLength=14, maxLineGap=6)
        segs = [] if segs is None else segs[:, 0, :]
        for sx0, sy0, sx1, sy1 in segs:
            a, b = tf(sx0, sy0), tf(sx1, sy1)
            lines.append([a[0], a[1], b[0], b[1], args.layer])

    out = {"lines": lines, "arcs": [], "circles": [], "texts": []}
    if polylines:  # ordered chains: clean input for arc-fitting (fit_arcs.py)
        out["polylines"] = polylines
    json.dump(out, open(args.out, "w"))

    if lines:
        xs = [c for L in lines for c in (L[0], L[2])]
        ys = [c for L in lines for c in (L[1], L[3])]
        ext = f"{max(xs) - min(xs):.1f} x {max(ys) - min(ys):.1f} m"
    else:
        ext = "empty"
    how = f"{npoly} ordered polylines" if args.vectorize == "trace" else "hough fragments"
    masked = f", masked {nmask} text boxes" if nmask else ""
    print(f"{len(lines)} segments ({how}{masked}) -> {args.out}  (extent {ext})")
    print("NOTE: geometry only (no arcs/labels). Run fit_arcs.py for arcs; see STATUS.md.")


if __name__ == "__main__":
    main()

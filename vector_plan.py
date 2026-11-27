#!/usr/bin/env python3
"""vector_plan.py - plan-JSON straight from a vector PDF's own paths + text layer.

The vector-only path: no rendering, no OpenCV, no VLM. It reads the page's
drawing operators (PyMuPDF ``get_drawings``) and its text layer, and writes a
v1 plan-JSON in the same world frame as ``plat2json.py`` (metres at the plot
scale, north-up). It is what the in-browser sample (docs/try.html) runs under
Pyodide, and it works the same on the desktop:

    python vector_plan.py PLAT.pdf OUT.json [--page 1] [--plot-scale 250] [--dxf OUT.dxf]

What it does:
  * straight path segments and rectangles -> ordered ``polylines`` (restated as
    ``lines`` per the duplicate contract);
  * Bezier curves that are circular arcs -> bulge vertices (restated as
    ``arcs``); other curves are flattened to short segments;
  * closed all-arc paths (monument symbols) -> ``circles``;
  * the page frame and tiny glyph-sized paths are dropped;
  * the text layer -> ``texts``, and its bearings / distances are harvested
    with the same rules as eval/harness/vector_golden.py into ``labels``.

Scanned / image-only pages have no usable vectors; ``looks_scanned`` detects
them so a caller can route them to the vision reader instead of returning an
empty drawing. Output is a rough geometry skeleton, NOT survey-grade.
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_LAYER = "PROPERTY_LINE"
# Thresholds in PDF points (1/72 in).
MIN_FEATURE_PT = 4.0       # paths smaller than this are glyph strokes / ticks
MIN_CIRCLE_R_PT = 0.75     # keep monument circles down to this radius
FRAME_FRAC = 0.85          # rect spanning this much of the page = the frame
ARC_TOL_REL = 0.01         # Bezier-is-a-circular-arc tolerance (of radius)
ARC_TOL_ABS_PT = 0.05
FLATTEN_STEPS = 8

# Scanned-page heuristics (see looks_scanned).
SCAN_IMAGE_COVER = 0.5
SCAN_MAX_VECTOR_ITEMS = 50
EMPTY_MAX_VECTOR_ITEMS = 10
EMPTY_MAX_TEXT_CHARS = 20


def _pymupdf():
    try:
        import pymupdf
    except ImportError:  # older installs only expose the fitz name
        import fitz as pymupdf
    return pymupdf


def _harvesters():
    """vector_golden's bearing/distance rules, or None if not importable."""
    path = os.path.join(HERE, "eval", "harness")
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        import vector_golden as vg
    except Exception:
        return None
    return vg


# --------------------------------------------------------------------------- #
# Scanned / image-only detection
# --------------------------------------------------------------------------- #

def page_profile(page):
    """Cheap per-page stats: raw vector items, text chars, image coverage."""
    rect = page.rect
    area = max(rect.width * rect.height, 1e-9)
    n_items = sum(len(d.get("items") or []) for d in page.get_drawings())
    text_chars = sum(1 for ch in page.get_text() if not ch.isspace())
    cover = 0.0
    for info in page.get_image_info():
        bbox = info.get("bbox")
        if not bbox:
            continue
        x0, y0 = max(bbox[0], rect.x0), max(bbox[1], rect.y0)
        x1, y1 = min(bbox[2], rect.x1), min(bbox[3], rect.y1)
        if x1 > x0 and y1 > y0:
            cover += (x1 - x0) * (y1 - y0) / area
    return {
        "vector_items": n_items,
        "text_chars": text_chars,
        "image_cover": round(min(cover, 1.0), 3),
        "width_pt": round(rect.width, 2),
        "height_pt": round(rect.height, 2),
    }


def looks_scanned(profile):
    """(scanned, reason) from a page_profile dict.

    Scanned = an image covers most of the page and there is next to no
    linework (a searchable scan's OCR text does not count as linework), or the
    page has neither meaningful vector paths nor text.
    """
    items = int(profile.get("vector_items") or 0)
    chars = int(profile.get("text_chars") or 0)
    cover = float(profile.get("image_cover") or 0.0)
    if cover >= SCAN_IMAGE_COVER and items < SCAN_MAX_VECTOR_ITEMS:
        return True, "an image covers most of the page and there is almost no vector linework"
    if items < EMPTY_MAX_VECTOR_ITEMS and chars < EMPTY_MAX_TEXT_CHARS:
        return True, "the page has no meaningful vector paths or text"
    return False, ""


# --------------------------------------------------------------------------- #
# Geometry helpers (world coordinates)
# --------------------------------------------------------------------------- #

def _bez(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
            u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1])


def _circle3(a, b, c):
    d = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d
    uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d
    return ux, uy, math.hypot(a[0] - ux, a[1] - uy)


def bezier_as_arc(p0, p1, p2, p3, scale=1.0):
    """(cx, cy, r, sweep_rad) if the cubic is a circular arc, else None.

    ``sweep`` is signed in the frame of the given points: positive = CCW
    (y up). ``scale`` converts the absolute tolerance from points.
    """
    mid = _bez(p0, p1, p2, p3, 0.5)
    circ = _circle3(p0, mid, p3)
    if circ is None:
        return None
    cx, cy, r = circ
    tol = max(ARC_TOL_REL * r, ARC_TOL_ABS_PT * scale)
    for t in (0.25, 0.75):
        q = _bez(p0, p1, p2, p3, t)
        if abs(math.hypot(q[0] - cx, q[1] - cy) - r) > tol:
            return None
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    am = math.atan2(mid[1] - cy, mid[0] - cx)
    a1 = math.atan2(p3[1] - cy, p3[0] - cx)
    two_pi = 2.0 * math.pi
    ccw = (a1 - a0) % two_pi
    if ccw < 1e-12:
        return None
    sweep = ccw if (am - a0) % two_pi < ccw else -(two_pi - ccw)
    return cx, cy, r, sweep


def _close(a, b, tol):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

def extract_page(page, plot_scale=250.0, layer=DEFAULT_LAYER):
    """v1 plan-JSON dict from one vector page (see module docstring)."""
    rect = page.rect
    pt2m = 25.4 / 72.0 / 1000.0 * float(plot_scale)
    h_pt = rect.height

    def tf(p):
        return ((p[0] - rect.x0) * pt2m, (h_pt - (p[1] - rect.y0)) * pt2m)

    def rnd(p):
        return [round(p[0], 4), round(p[1], 4)]

    tol_w = 0.01 * pt2m  # joint tolerance: 0.01 pt in world units
    polylines, circles = [], []
    seen = set()
    stats = {"paths": 0, "dropped_small": 0, "dropped_frame": 0,
             "dropped_fill": 0, "curves_arc": 0, "curves_flat": 0}

    for d in page.get_drawings():
        items = d.get("items") or []
        if not items:
            continue
        stats["paths"] += 1
        bbox = d.get("rect")
        bw, bh = (bbox.width, bbox.height) if bbox is not None else (0.0, 0.0)

        # Closed all-arc path = a circle (monument symbol), stroked or filled.
        if all(it[0] == "c" for it in items):
            arcs = [bezier_as_arc(*(tf(q) for q in it[1:5]), scale=pt2m) for it in items]
            if all(arcs):
                tot = sum(a[3] for a in arcs)
                c0 = arcs[0]
                same = all(math.hypot(a[0] - c0[0], a[1] - c0[1]) <= max(0.02 * c0[2], tol_w)
                           and abs(a[2] - c0[2]) <= max(0.02 * c0[2], tol_w) for a in arcs)
                if same and abs(abs(tot) - 2 * math.pi) < 0.05 \
                        and c0[2] >= MIN_CIRCLE_R_PT * pt2m:
                    key = ("c", round(c0[0], 3), round(c0[1], 3), round(c0[2], 3))
                    if key not in seen:
                        seen.add(key)
                        circles.append([round(c0[0], 4), round(c0[1], 4),
                                        round(c0[2], 4), layer])
                    continue

        if d.get("type") == "f":          # fill-only: glyphs, solid symbols
            stats["dropped_fill"] += 1
            continue
        if max(bw, bh) < MIN_FEATURE_PT:  # glyph strokes, ticks
            stats["dropped_small"] += 1
            continue
        if bw >= FRAME_FRAC * rect.width and bh >= FRAME_FRAC * rect.height \
                and any(it[0] in ("re", "qu") for it in items):
            stats["dropped_frame"] += 1   # the sheet border
            continue

        chains, cur = [], None

        def flush():
            nonlocal cur
            if cur and len(cur) >= 2:
                chains.append(cur)
            cur = None

        for it in items:
            kind = it[0]
            if kind in ("re", "qu"):
                flush()
                if kind == "re":
                    r = it[1]
                    pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                else:
                    q = it[1]
                    pts = [q.ul, q.ur, q.lr, q.ll]
                w = [tf(p) for p in pts]
                chains.append([[*w[0], 0.0], [*w[1], 0.0], [*w[2], 0.0],
                               [*w[3], 0.0], [*w[0], 0.0]])
                continue
            if kind == "l":
                a, b = tf(it[1]), tf(it[2])
                if cur is None or not _close(cur[-1][:2], a, tol_w):
                    flush()
                    cur = [[*a, 0.0]]
                cur.append([*b, 0.0])
            elif kind == "c":
                p = [tf(q) for q in it[1:5]]
                if cur is None or not _close(cur[-1][:2], p[0], tol_w):
                    flush()
                    cur = [[*p[0], 0.0]]
                arc = bezier_as_arc(*p, scale=pt2m)
                if arc:
                    cur[-1][2] = math.tan(arc[3] / 4.0)
                    cur.append([*p[3], 0.0])
                    stats["curves_arc"] += 1
                else:
                    for k in range(1, FLATTEN_STEPS + 1):
                        cur.append([*_bez(*p, k / FLATTEN_STEPS), 0.0])
                    stats["curves_flat"] += 1
        flush()
        if d.get("closePath") and chains:
            last = chains[-1]
            if not _close(last[0][:2], last[-1][:2], tol_w):
                last.append([last[0][0], last[0][1], 0.0])

        for ch in chains:
            verts = []
            for x, y, b in ch:
                v = rnd((x, y))
                if verts and verts[-1][:2] == v:
                    if abs(b) > 0:
                        verts[-1] = v + [round(b, 6)]
                    continue
                verts.append(v + [round(b, 6)] if abs(b) > 1e-9 else v)
            if len(verts) < 2:
                continue
            key = tuple(tuple(v[:2]) for v in verts)
            rkey = tuple(reversed(key))
            if key in seen or rkey in seen:
                continue
            seen.add(key)
            polylines.append(verts + [layer])

    lines, arcs = [], []
    for pl in polylines:  # duplicate contract: restate as lines / arcs
        verts = pl[:-1]
        for a, b in zip(verts, verts[1:]):
            bulge = a[2] if len(a) > 2 else 0.0
            if not bulge:
                lines.append([a[0], a[1], b[0], b[1], layer])
                continue
            sweep = 4.0 * math.atan(bulge)
            chord = math.hypot(b[0] - a[0], b[1] - a[1])
            r = chord / (2.0 * abs(math.sin(sweep / 2.0)))
            mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
            ux, uy = (b[0] - a[0]) / chord, (b[1] - a[1]) / chord
            s = (chord / 2.0) / math.tan(sweep / 2.0)  # signed, + = left of a->b
            cx, cy = mx - uy * s, my + ux * s
            sa = math.degrees(math.atan2(a[1] - cy, a[0] - cx))
            ea = math.degrees(math.atan2(b[1] - cy, b[0] - cx))
            if sweep < 0:   # DXF/LS_IMPORTPLAN arcs run CCW start -> end
                sa, ea = ea, sa
            if ea < sa:
                ea += 360.0
            arcs.append([round(cx, 4), round(cy, 4), round(r, 4),
                         round(sa, 6), round(ea, 6), layer])

    texts = []
    full_text = page.get_text()
    for block in page.get_text("dict").get("blocks", []):
        for ln in block.get("lines", []):
            spans = ln.get("spans") or []
            s = "".join(sp.get("text", "") for sp in spans).strip()
            if not s:
                continue
            ox, oy = spans[0].get("origin", (0.0, 0.0))
            w = tf((ox, oy))
            texts.append([round(w[0], 4), round(w[1], 4), s, "STANDARD"])

    labels = {"bearings": [], "distances": [], "unit_guess": None}
    vg = _harvesters()
    if vg is not None and full_text.strip():
        labels = {"bearings": vg.harvest_bearings(full_text),
                  "distances": vg.harvest_distances(full_text),
                  "unit_guess": vg.guess_unit(full_text)}

    return {
        "lines": lines,
        "arcs": arcs,
        "circles": circles,
        "texts": texts,
        "polylines": polylines,
        "labels": labels,
        "source": {"kind": "vector-pdf", "page": page.number + 1,
                   "plot_scale": float(plot_scale), "extract": stats},
    }


def plan_counts(plan):
    return {k: len(plan.get(k) or []) for k in
            ("polylines", "lines", "arcs", "circles", "texts")} | {
        "bearings": len((plan.get("labels") or {}).get("bearings") or []),
        "distances": len((plan.get("labels") or {}).get("distances") or [])}


def analyze_pdf(data, page_number=1, plot_scale=250.0):
    """Browser/CLI entry: bytes -> {page_count, page, profile, scanned, plan?}.

    ``page_number`` is 1-based. Only that one page is read.
    """
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(stream=bytes(data), filetype="pdf")
    except Exception as e:
        raise ValueError(f"not a readable PDF ({e})") from e
    try:
        n = doc.page_count
        if n < 1:
            raise ValueError("the PDF has no pages")
        if not 1 <= int(page_number) <= n:
            raise ValueError(f"page {page_number} is out of range (1-{n})")
        page = doc[int(page_number) - 1]
        prof = page_profile(page)
        scanned, reason = looks_scanned(prof)
        out = {"page_count": n, "page": int(page_number), "profile": prof,
               "scanned": scanned, "reason": reason}
        if not scanned:
            plan = extract_page(page, plot_scale)
            out["plan"] = plan
            out["counts"] = plan_counts(plan)
        return out
    finally:
        doc.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Vector plat PDF -> plan-JSON (no raster, no VLM).")
    ap.add_argument("pdf")
    ap.add_argument("out", help="output plan-JSON")
    ap.add_argument("--page", type=int, default=1, help="1-based page (default 1)")
    ap.add_argument("--plot-scale", type=float, default=250.0,
                    help="plot scale denominator, e.g. 250 for 1:250 (default 250)")
    ap.add_argument("--dxf", default=None, help="also write a DXF via plan2dxf.py")
    a = ap.parse_args(argv)
    with open(a.pdf, "rb") as f:
        res = analyze_pdf(f.read(), a.page, a.plot_scale)
    if res["scanned"]:
        sys.exit(f"page {res['page']} looks scanned: {res['reason']}. "
                 "Scanned plats need the vision reader (eval/harness/vlm_read.py).")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res["plan"], f, ensure_ascii=False)
    print(f"{res['counts']} -> {a.out}")
    if a.dxf:
        sys.path.insert(0, HERE)
        import plan2dxf
        plan2dxf.write_dxf(res["plan"], a.dxf)
        print(f"DXF -> {a.dxf}")
    print("NOTE: rough geometry skeleton, not survey-grade.")


if __name__ == "__main__":
    main()

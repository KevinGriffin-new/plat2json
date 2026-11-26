#!/usr/bin/env python3
"""plan2dxf — write a plan-JSON drawing to DXF.

Converts plat2json v1 geometry and v2 ``plan2/assoc-v1`` assemblies into an
AutoCAD-compatible DXF (R2013). Coordinates are written as-is in the plan's
world system (v1: metres, north-up, matching LS_IMPORTPLAN). This is a
drawing of the recovered skeleton, NOT survey-grade output.

Usage:
    python plan2dxf.py IN.json OUT.dxf
    python plan2dxf.py IN.json OUT.dxf --no-prefer-polylines

Arc angle convention (from fit_arcs.py, the form LS_IMPORTPLAN expects):
    ``start_deg`` / ``end_deg`` are mathematical degrees, 0° = +X (east),
    increasing counterclockwise. The drawn sweep is CCW from start to end.
    ``fit_arcs.py`` unwraps then swaps so start ≤ end; values may fall
    outside [0, 360) (e.g. 350 → 380 for an arc that crosses due east).
    DXF ARC uses the same 0°=+X, CCW-from-start convention, so angles are
    passed through without wrapping.

Duplicate geometry:
    v1 plans restate polyline chains as ``lines`` (straight edges) and
    ``arcs`` (bulged edges) using the same rounded values, so importers
    without polyline support still have something to draw. This writer is
    polyline-aware: when ``polylines`` are present it prefers them and
    skips matching ``lines`` / ``arcs``. Pass ``--no-prefer-polylines``
    (or ``prefer_polylines=False``) to emit every list.

Layer mapping:
    JSON layer names become DXF layer names. ``DEFAULT_LAYER_TABLE`` only
    assigns ACI colors (easy to edit); there is no client layer-standard
    remapping. Unknown layers are created with color 7. v2 extras:
      ANNOTATION  course labels (bearing + distance), aligned to the segment
      FLAGS       courses that carry flags; rings that fail the same
                  misclosure test as cogo_assemble.py when report.needs_human
"""
from __future__ import annotations

import argparse
import json
import math
import sys

try:
    import ezdxf
    from ezdxf import units as ez_units
    from ezdxf.enums import TextEntityAlignment
except ImportError as e:  # pragma: no cover - exercised only when deps missing
    ezdxf = None
    _EZDXF_IMPORT_ERROR = e
else:
    _EZDXF_IMPORT_ERROR = None


# --------------------------------------------------------------------------- #
# Layer table (colors only; names pass through). Edit here or pass layer_table.
# ACI: 1 red, 3 green, 7 white/black.
# --------------------------------------------------------------------------- #
DEFAULT_LAYER_TABLE = {
    "PROPERTY_LINE": 7,  # parcel / lot linework
    "ANNOTATION": 3,     # v2 course labels
    "FLAGS": 1,          # flagged courses / rings that need a human check
}

ANNOTATION_LAYER = "ANNOTATION"
FLAGS_LAYER = "FLAGS"
DEFAULT_GEOM_LAYER = "PROPERTY_LINE"
DXF_VERSION = "R2013"

# Match points / segments restated at 2–4 decimal places in plan-JSON.
_COORD_TOL = 1e-3
_DEG_ZERO_BULGE = 1e-12


class PlanError(ValueError):
    """Invalid plan-JSON: malformed, not a plan object, or a bad entity."""


def _require_ezdxf():
    if ezdxf is None:
        raise PlanError(
            f"missing dependency ({_EZDXF_IMPORT_ERROR}). "
            "Run: pip install -r requirements.txt"
        )


# --------------------------------------------------------------------------- #
# Parsing / validation
# --------------------------------------------------------------------------- #

def load_plan(path):
    """Load a plan-JSON object from ``path``. Raises PlanError on bad JSON."""
    try:
        with open(path, encoding="utf-8") as f:
            plan = json.load(f)
    except json.JSONDecodeError as e:
        raise PlanError(f"invalid JSON in {path}: {e}") from e
    if not isinstance(plan, dict):
        raise PlanError(
            f"{path}: plan-JSON must be an object, got {type(plan).__name__}"
        )
    return plan


def _num(v, where):
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        raise PlanError(f"{where}: expected a number, got {v!r}") from e


def _layer_name(name, default=DEFAULT_GEOM_LAYER):
    if name is None:
        return default
    text = str(name).strip()
    return text or default


def parse_lines(raw):
    """Return a list of (x1, y1, x2, y2, layer). Missing/empty → []."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PlanError("'lines' must be a list of [x1, y1, x2, y2, LAYER]")
    out = []
    for i, item in enumerate(raw):
        where = f"lines[{i}]"
        if not isinstance(item, (list, tuple)) or len(item) < 4:
            raise PlanError(f"{where}: expected [x1, y1, x2, y2, LAYER]")
        x1, y1, x2, y2 = (_num(item[0], where), _num(item[1], where),
                          _num(item[2], where), _num(item[3], where))
        layer = DEFAULT_GEOM_LAYER
        if len(item) >= 5:
            if isinstance(item[4], str) or item[4] is None:
                layer = _layer_name(item[4])
            else:
                raise PlanError(f"{where}: LAYER must be a string, got {item[4]!r}")
        out.append((x1, y1, x2, y2, layer))
    return out


def parse_arcs(raw):
    """Return a list of (cx, cy, r, start_deg, end_deg, layer)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PlanError(
            "'arcs' must be a list of [cx, cy, r, start_deg, end_deg, LAYER]"
        )
    out = []
    for i, item in enumerate(raw):
        where = f"arcs[{i}]"
        if not isinstance(item, (list, tuple)) or len(item) < 5:
            raise PlanError(
                f"{where}: expected [cx, cy, r, start_deg, end_deg, LAYER]"
            )
        cx, cy, r = _num(item[0], where), _num(item[1], where), _num(item[2], where)
        sd, ed = _num(item[3], where), _num(item[4], where)
        if r <= 0:
            raise PlanError(f"{where}: radius must be positive, got {r}")
        layer = DEFAULT_GEOM_LAYER
        if len(item) >= 6:
            if isinstance(item[5], str) or item[5] is None:
                layer = _layer_name(item[5])
            else:
                raise PlanError(f"{where}: LAYER must be a string, got {item[5]!r}")
        out.append((cx, cy, r, sd, ed, layer))
    return out


def parse_circles(raw):
    """Return a list of (cx, cy, r, layer)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PlanError("'circles' must be a list of [cx, cy, r, LAYER]")
    out = []
    for i, item in enumerate(raw):
        where = f"circles[{i}]"
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            raise PlanError(f"{where}: expected [cx, cy, r, LAYER]")
        cx, cy, r = _num(item[0], where), _num(item[1], where), _num(item[2], where)
        if r <= 0:
            raise PlanError(f"{where}: radius must be positive, got {r}")
        layer = DEFAULT_GEOM_LAYER
        if len(item) >= 4:
            if isinstance(item[3], str) or item[3] is None:
                layer = _layer_name(item[3])
            else:
                raise PlanError(f"{where}: LAYER must be a string, got {item[3]!r}")
        out.append((cx, cy, r, layer))
    return out


def parse_texts(raw):
    """Return a list of (x, y, string, style)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PlanError("'texts' must be a list of [x, y, string, STYLE]")
    out = []
    for i, item in enumerate(raw):
        where = f"texts[{i}]"
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            raise PlanError(f"{where}: expected [x, y, string, STYLE]")
        x, y = _num(item[0], where), _num(item[1], where)
        text = "" if item[2] is None else str(item[2])
        style = "STANDARD"
        if len(item) >= 4 and item[3]:
            style = str(item[3])
        out.append((x, y, text, style))
    return out


def parse_polylines(raw):
    """Return a list of (vertices, layer, closed).

    Each vertex is ``(x, y, bulge)`` with bulge defaulting to 0. The last
    element of a plan polyline is the layer name when it is a string.
    If the first and last vertices coincide the polyline is closed and the
    duplicate last vertex is dropped (its bulge is unused).
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PlanError("'polylines' must be a list of [vertex, ..., LAYER]")
    out = []
    for i, item in enumerate(raw):
        where = f"polylines[{i}]"
        if not isinstance(item, (list, tuple)) or not item:
            raise PlanError(f"{where}: expected [vertex, ..., LAYER]")
        layer = DEFAULT_GEOM_LAYER
        verts_raw = list(item)
        if isinstance(verts_raw[-1], str) or verts_raw[-1] is None:
            layer = _layer_name(verts_raw[-1])
            verts_raw = verts_raw[:-1]
        verts = []
        for j, v in enumerate(verts_raw):
            vwhere = f"{where} vertex {j}"
            if not isinstance(v, (list, tuple)) or len(v) < 2:
                raise PlanError(f"{vwhere}: expected [x, y] or [x, y, bulge]")
            x, y = _num(v[0], vwhere), _num(v[1], vwhere)
            bulge = _num(v[2], vwhere) if len(v) >= 3 else 0.0
            verts.append((x, y, bulge))
        if len(verts) < 2:
            raise PlanError(f"{where}: need at least two vertices")
        closed = False
        if len(verts) >= 3 and _pts_close(verts[0], verts[-1]):
            closed = True
            verts = verts[:-1]
            if len(verts) < 2:
                raise PlanError(f"{where}: closed polyline needs at least two unique vertices")
        out.append((verts, layer, closed))
    return out


def _pts_close(a, b, tol=_COORD_TOL):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def _seg_key(x1, y1, x2, y2):
    a = (round(x1, 4), round(y1, 4))
    b = (round(x2, 4), round(y2, 4))
    return (a, b) if a <= b else (b, a)


def _arc_endpoints(cx, cy, r, start_deg, end_deg):
    sr, er = math.radians(start_deg), math.radians(end_deg)
    return ((cx + r * math.cos(sr), cy + r * math.sin(sr)),
            (cx + r * math.cos(er), cy + r * math.sin(er)))


def _polyline_restatements(polylines):
    """Straight-edge keys and bulged segments restated by the polylines."""
    line_keys = set()
    bulge_segs = []
    for verts, _layer, closed in polylines:
        n = len(verts)
        count = n if closed else n - 1
        for k in range(count):
            x1, y1, bulge = verts[k]
            x2, y2, _ = verts[(k + 1) % n]
            if abs(bulge) <= _DEG_ZERO_BULGE:
                line_keys.add(_seg_key(x1, y1, x2, y2))
            else:
                bulge_segs.append((x1, y1, x2, y2, bulge))
    return line_keys, bulge_segs


def _arc_matches_bulge(cx, cy, r, sd, ed, bulge_segs, tol=_COORD_TOL):
    s, e = _arc_endpoints(cx, cy, r, sd, ed)
    for x1, y1, x2, y2, bulge in bulge_segs:
        if not ((_pts_close(s, (x1, y1), tol) and _pts_close(e, (x2, y2), tol))
                or (_pts_close(s, (x2, y2), tol) and _pts_close(e, (x1, y1), tol))):
            continue
        chord = math.hypot(x2 - x1, y2 - y1)
        ang = 4.0 * math.atan(bulge)
        if abs(math.sin(ang / 2.0)) < 1e-15:
            continue
        rr = abs(chord / (2.0 * math.sin(ang / 2.0)))
        if abs(rr - r) <= tol:
            return True
    return False


# --------------------------------------------------------------------------- #
# Units, layers, v2 annotations
# --------------------------------------------------------------------------- #

def insunits_for(plan):
    """DXF $INSUNITS value. v1 is metres; v2 may declare ``unit``."""
    unit = str(plan.get("unit") or "m").strip().lower()
    if unit in ("ft", "foot", "feet"):
        return ez_units.FT
    if unit in ("in", "inch", "inches"):
        return ez_units.IN
    if unit in ("mm", "millimeter", "millimetre", "millimeters", "millimetres"):
        return ez_units.MM
    return ez_units.M


def _ensure_layer(doc, name, layer_table):
    if name in doc.layers:
        return
    color = layer_table.get(name, 7)
    doc.layers.add(name, color=color)


def _is_v2(plan):
    return plan.get("schema") == "plan2/assoc-v1" or isinstance(plan.get("courses"), list)


def _course_segment(course, lines):
    px = course.get("px")
    if isinstance(px, (list, tuple)) and len(px) >= 4:
        try:
            return [float(px[0]), float(px[1]), float(px[2]), float(px[3])]
        except (TypeError, ValueError):
            pass
    seg = course.get("seg")
    if isinstance(seg, int) and 0 <= seg < len(lines):
        L = lines[seg]
        return [L[0], L[1], L[2], L[3]]
    return None


def _course_label(course):
    parts = []
    if course.get("bearing"):
        parts.append(str(course["bearing"]))
    elif course.get("azimuth") is not None:
        try:
            parts.append(f"az {float(course['azimuth']):g}")
        except (TypeError, ValueError):
            parts.append(f"az {course['azimuth']}")
    if course.get("distance_raw"):
        parts.append(str(course["distance_raw"]))
    elif course.get("distance") is not None:
        try:
            parts.append(f"{float(course['distance']):g}")
        except (TypeError, ValueError):
            parts.append(str(course["distance"]))
    return "  ".join(parts)


def _label_rotation(x1, y1, x2, y2):
    """Degrees CCW from +X, flipped so the text stays upright."""
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
    if ang > 90:
        ang -= 180
    elif ang <= -90:
        ang += 180
    return ang


def _text_height(lines, courses):
    xs, ys = [], []
    for L in lines:
        xs.extend((L[0], L[2]))
        ys.extend((L[1], L[3]))
    for c in courses or []:
        if not isinstance(c, dict):
            continue
        px = c.get("px") or []
        if isinstance(px, (list, tuple)) and len(px) >= 4:
            try:
                xs.extend((float(px[0]), float(px[2])))
                ys.extend((float(px[1]), float(px[3])))
            except (TypeError, ValueError):
                pass
    if len(xs) < 2:
        return 1.0
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    if diag <= 0:
        return 1.0
    return max(0.2, min(diag * 0.015, 10.0))


def _ring_beyond_tolerance(ring):
    """Same test cogo_assemble.py uses for report.rings_beyond_tolerance."""
    try:
        mis = float(ring.get("misclosure") or 0.0)
        per = float(ring.get("perimeter") or 0.0)
        legs = int(ring.get("legs") or len(ring.get("segs") or []) or 1)
    except (TypeError, ValueError):
        return False
    return mis > 0.02 * math.sqrt(max(legs, 1)) and (mis / max(per, 1e-9)) > 1e-4


def _ring_points(segs, lines, tol=_COORD_TOL):
    segments = []
    for ei in segs:
        if not isinstance(ei, int) or ei < 0 or ei >= len(lines):
            continue
        L = lines[ei]
        segments.append(((L[0], L[1]), (L[2], L[3])))
    if not segments:
        return []
    pts = [segments[0][0], segments[0][1]]
    used = {0}
    while len(used) < len(segments):
        end = pts[-1]
        found = False
        for i, (a, b) in enumerate(segments):
            if i in used:
                continue
            if math.hypot(a[0] - end[0], a[1] - end[1]) <= tol:
                pts.append(b)
                used.add(i)
                found = True
                break
            if math.hypot(b[0] - end[0], b[1] - end[1]) <= tol:
                pts.append(a)
                used.add(i)
                found = True
                break
        if not found:
            break
    return pts


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #

def plan_to_dxf(plan, *, prefer_polylines=True, layer_table=None,
                dxfversion=DXF_VERSION):
    """Build an ezdxf document from a plan-JSON dict. Does not write a file."""
    _require_ezdxf()
    if not isinstance(plan, dict):
        raise PlanError(f"plan-JSON must be an object, got {type(plan).__name__}")

    lines = parse_lines(plan.get("lines"))
    arcs = parse_arcs(plan.get("arcs"))
    circles = parse_circles(plan.get("circles"))
    texts = parse_texts(plan.get("texts"))
    polylines = parse_polylines(plan.get("polylines"))

    table = dict(DEFAULT_LAYER_TABLE)
    if layer_table:
        table.update(layer_table)

    skip_lines, skip_arcs = set(), False
    if prefer_polylines and polylines:
        skip_lines, bulge_segs = _polyline_restatements(polylines)
        skip_arcs = True  # gate on bulge_segs per arc below
    else:
        bulge_segs = []

    doc = ezdxf.new(dxfversion, setup=True)
    doc.units = insunits_for(plan)
    msp = doc.modelspace()

    def layer(name):
        _ensure_layer(doc, name, table)
        return name

    drawn_lines = drawn_arcs = drawn_pl = 0

    for verts, name, closed in polylines:
        pts = [(v[0], v[1], v[2]) for v in verts]
        msp.add_lwpolyline(
            pts, format="xyb", close=closed,
            dxfattribs={"layer": layer(name)},
        )
        drawn_pl += 1

    for x1, y1, x2, y2, name in lines:
        if skip_lines and _seg_key(x1, y1, x2, y2) in skip_lines:
            continue
        msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer(name)})
        drawn_lines += 1

    for cx, cy, r, sd, ed, name in arcs:
        if skip_arcs and _arc_matches_bulge(cx, cy, r, sd, ed, bulge_segs):
            continue
        msp.add_arc(
            center=(cx, cy), radius=r,
            start_angle=sd, end_angle=ed,
            dxfattribs={"layer": layer(name)},
        )
        drawn_arcs += 1

    for cx, cy, r, name in circles:
        msp.add_circle((cx, cy), r, dxfattribs={"layer": layer(name)})

    height = _text_height(lines, plan.get("courses") if _is_v2(plan) else None)
    for x, y, text, style in texts:
        # Ensure the style exists so the DXF stays valid with unknown STYLE names.
        if style and style not in doc.styles:
            try:
                doc.styles.add(style)
            except Exception:
                style = "STANDARD"
        # Plan texts carry a STYLE, not a layer; put them on 0.
        entity = msp.add_text(
            text,
            dxfattribs={
                "layer": "0",
                "style": style or "STANDARD",
                "height": height,
            },
        )
        entity.set_placement((x, y), align=TextEntityAlignment.LEFT)

    stats = {
        "lines": drawn_lines,
        "arcs": drawn_arcs,
        "circles": len(circles),
        "texts": len(texts),
        "polylines": drawn_pl,
        "labels": 0,
        "flags": 0,
    }

    if _is_v2(plan):
        _add_v2_annotations(doc, msp, plan, lines, layer, table, height, stats)

    doc.plan2dxf_stats = stats
    return doc


def _add_v2_annotations(_doc, msp, plan, lines, layer, _table, height, stats):
    courses = plan.get("courses") or []
    if not isinstance(courses, list):
        raise PlanError("'courses' must be a list of course objects")
    rings = plan.get("rings") or []
    if rings is not None and not isinstance(rings, list):
        raise PlanError("'rings' must be a list")
    report = plan.get("report") or {}
    if report and not isinstance(report, dict):
        raise PlanError("'report' must be an object")
    needs_human = bool(report.get("needs_human")) if isinstance(report, dict) else False

    layer(ANNOTATION_LAYER)
    layer(FLAGS_LAYER)

    for i, course in enumerate(courses):
        if not isinstance(course, dict):
            raise PlanError(f"courses[{i}]: expected an object")
        seg = _course_segment(course, lines)
        if not seg:
            continue
        x1, y1, x2, y2 = seg
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        label = _course_label(course)
        if label:
            rot = _label_rotation(x1, y1, x2, y2)
            L = math.hypot(x2 - x1, y2 - y1) or 1.0
            # Offset to the left of the upright reading direction so the
            # label sits off the ink.
            rad = math.radians(rot)
            ox, oy = -math.sin(rad) * height * 0.7, math.cos(rad) * height * 0.7
            entity = msp.add_text(
                label,
                dxfattribs={
                    "layer": ANNOTATION_LAYER,
                    "style": "STANDARD",
                    "height": height,
                    "rotation": rot,
                },
            )
            entity.set_placement(
                (mx + ox, my + oy),
                align=TextEntityAlignment.BOTTOM_CENTER,
            )
            stats["labels"] += 1

        flags = course.get("flags") or []
        if flags:
            msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": FLAGS_LAYER})
            msp.add_circle((mx, my), max(height * 0.8, 0.15),
                           dxfattribs={"layer": FLAGS_LAYER})
            stats["flags"] += 1

    if needs_human and isinstance(rings, list):
        for ring in rings:
            if not isinstance(ring, dict) or not _ring_beyond_tolerance(ring):
                continue
            segs = ring.get("segs") or []
            pts = _ring_points(segs, lines)
            if len(pts) >= 2:
                if _pts_close(pts[0], pts[-1]) and len(pts) >= 3:
                    pts = pts[:-1]
                msp.add_lwpolyline(
                    pts, close=True,
                    dxfattribs={"layer": FLAGS_LAYER},
                )
                stats["flags"] += 1


def write_dxf(plan, outfile, *, prefer_polylines=True, layer_table=None,
              dxfversion=DXF_VERSION):
    """Write ``plan`` to ``outfile`` (path or text stream). Returns the document."""
    doc = plan_to_dxf(
        plan,
        prefer_polylines=prefer_polylines,
        layer_table=layer_table,
        dxfversion=dxfversion,
    )
    if hasattr(outfile, "write"):
        doc.write(outfile)
    else:
        doc.saveas(str(outfile))
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="plan-JSON -> DXF (a drawing of the recovered skeleton, "
                    "not survey-grade output).",
    )
    ap.add_argument("inp", help="input plan-JSON")
    ap.add_argument("out", help="output .dxf path")
    ap.add_argument(
        "--prefer-polylines",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="when polylines are present, skip restated lines/arcs (default on)",
    )
    args = ap.parse_args(argv)
    try:
        _require_ezdxf()
        plan = load_plan(args.inp)
        doc = write_dxf(plan, args.out, prefer_polylines=args.prefer_polylines)
    except PlanError as e:
        sys.exit(f"plan2dxf: {e}")
    except OSError as e:
        sys.exit(f"plan2dxf: {e}")
    s = getattr(doc, "plan2dxf_stats", {})
    bits = [f"{s.get(k, 0)} {k}" for k in
            ("polylines", "lines", "arcs", "circles", "texts", "labels")
            if s.get(k)]
    print(f"{', '.join(bits) or 'empty drawing'} -> {args.out}")
    print("NOTE: this is a drawing, not survey-grade output.")


if __name__ == "__main__":
    main()

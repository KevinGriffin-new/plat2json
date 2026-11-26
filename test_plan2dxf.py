#!/usr/bin/env python3
"""Offline tests for plan2dxf.py. No GPU, no VLM, no network.

    pytest test_plan2dxf.py
"""
import io
import json
import math

import ezdxf
import pytest

import plan2dxf
from plan2dxf import PlanError, insunits_for, plan_to_dxf, write_dxf


# A quarter-circle: center (0, 0), r=10, 0° -> 90° (0° = +X, CCW).
# Start (10, 0), end (0, 10). bulge = tan(90°/4) = √2 - 1.
ARC_CX, ARC_CY, ARC_R = 0.0, 0.0, 10.0
ARC_START, ARC_END = 0.0, 90.0
ARC_BULGE = math.tan(math.radians(ARC_END - ARC_START) / 4.0)


def v1_plan():
    """Synthetic v1 plan: unique line, arc, circle, text, and bulge polyline."""
    return {
        "lines": [[0.0, 0.0, 20.0, 0.0, "PROPERTY_LINE"]],
        "arcs": [[ARC_CX, ARC_CY, ARC_R, ARC_START, ARC_END, "PROPERTY_LINE"]],
        "circles": [[50.0, 50.0, 2.5, "MONUMENT"]],
        "texts": [[50.0, 54.0, "LOT 5", "STANDARD"]],
        "polylines": [
            # independent bulge polyline (not a restatement of the arc above)
            [[20.0, 0.0, ARC_BULGE], [20.0, 10.0], "CURVE"],
        ],
    }


def v1_with_duplicates():
    """v1 duplicate contract: the polyline restates the line and the arc."""
    # Straight (0,0)-(20,0), then a CCW quarter-arc about (10,0) r=10
    # from (20,0) at 0° to (10,10) at 90° — the form fit_arcs.py emits.
    cx, cy, r, sd, ed = 10.0, 0.0, 10.0, 0.0, 90.0
    bulge = math.tan(math.radians(ed - sd) / 4.0)
    sx = round(cx + r * math.cos(math.radians(sd)), 4)
    sy = round(cy + r * math.sin(math.radians(sd)), 4)
    ex = round(cx + r * math.cos(math.radians(ed)), 4)
    ey = round(cy + r * math.sin(math.radians(ed)), 4)
    return {
        "lines": [[0.0, 0.0, sx, sy, "PROPERTY_LINE"]],
        "arcs": [[cx, cy, r, sd, ed, "PROPERTY_LINE"]],
        "circles": [],
        "texts": [],
        "polylines": [
            [[0.0, 0.0], [sx, sy, bulge], [ex, ey], "PROPERTY_LINE"],
        ],
    }


def v2_plan():
    """Small square traverse: one flagged course, one ring beyond tolerance."""
    # 10 m square, north-up. Course 2 is flagged. Ring misclosure is large
    # enough to fail cogo_assemble's needs_human test.
    return {
        "schema": "plan2/assoc-v1",
        "unit": "m",
        "lines": [
            [0.0, 0.0, 10.0, 0.0, "PROPERTY_LINE"],
            [10.0, 0.0, 10.0, 10.0, "PROPERTY_LINE"],
            [10.0, 10.0, 0.0, 10.0, "PROPERTY_LINE"],
            [0.0, 10.0, 0.0, 0.0, "PROPERTY_LINE"],
        ],
        "courses": [
            {"seg": 0, "px": [0.0, 0.0, 10.0, 0.0],
             "bearing": "N90°00'00\"E", "azimuth": 90.0,
             "distance": 10.0, "distance_raw": "10.00", "flags": []},
            {"seg": 1, "px": [10.0, 0.0, 10.0, 10.0],
             "bearing": "N0°00'00\"E", "azimuth": 0.0,
             "distance": 10.0, "distance_raw": "10.00", "flags": []},
            {"seg": 2, "px": [10.0, 10.0, 0.0, 10.0],
             "bearing": "N89°00'00\"W", "azimuth": 271.0,
             "distance": 10.5, "distance_raw": "10.50",
             "flags": ["bearing_residual_1.00deg", "distance_vs_length_5.0pct"]},
            {"seg": 3, "px": [0.0, 10.0, 0.0, 0.0],
             "bearing": "S0°00'00\"W", "azimuth": 180.0,
             "distance": 10.0, "distance_raw": "10.00", "flags": []},
        ],
        "rings": [{
            "segs": [0, 1, 2, 3], "legs": 4,
            "perimeter": 40.5, "misclosure": 0.8,
            "precision": "1:50",
        }],
        "links": [],
        "report": {
            "courses": 4, "courses_flagged": 1, "rings_closed": 1,
            "rings_beyond_tolerance": 1, "needs_human": True,
        },
    }


def _roundtrip(plan, tmp_path, name="out.dxf", **kwargs):
    path = tmp_path / name
    write_dxf(plan, path, **kwargs)
    return ezdxf.readfile(path)


def _xy(entity):
    """Start/end or insert as plain floats."""
    if entity.dxftype() == "LINE":
        s, e = entity.dxf.start, entity.dxf.end
        return (float(s.x), float(s.y), float(e.x), float(e.y))
    if entity.dxftype() == "CIRCLE":
        c = entity.dxf.center
        return (float(c.x), float(c.y), float(entity.dxf.radius))
    if entity.dxftype() == "ARC":
        c = entity.dxf.center
        return (float(c.x), float(c.y), float(entity.dxf.radius),
                float(entity.dxf.start_angle), float(entity.dxf.end_angle))
    if entity.dxftype() == "TEXT":
        i = entity.dxf.insert
        return (float(i.x), float(i.y), entity.dxf.text)
    return None


# --------------------------------------------------------------------------- #
# v1
# --------------------------------------------------------------------------- #

def test_v1_entities_layers_coords_angles_bulges_units(tmp_path):
    doc = _roundtrip(v1_plan(), tmp_path, "v1.dxf")
    msp = doc.modelspace()

    assert doc.header["$INSUNITS"] == 6  # metres
    assert doc.dxfversion == "AC1027"  # R2013

    lines = list(msp.query("LINE"))
    arcs = list(msp.query("ARC"))
    circles = list(msp.query("CIRCLE"))
    texts = list(msp.query("TEXT"))
    plines = list(msp.query("LWPOLYLINE"))

    assert len(lines) == 1
    assert len(arcs) == 1
    assert len(circles) == 1
    assert len(texts) == 1
    assert len(plines) == 1

    x1, y1, x2, y2 = _xy(lines[0])
    assert lines[0].dxf.layer == "PROPERTY_LINE"
    assert (x1, y1, x2, y2) == pytest.approx((0.0, 0.0, 20.0, 0.0))

    cx, cy, r, sd, ed = _xy(arcs[0])
    assert arcs[0].dxf.layer == "PROPERTY_LINE"
    assert (cx, cy, r) == pytest.approx((ARC_CX, ARC_CY, ARC_R))
    assert (sd, ed) == pytest.approx((ARC_START, ARC_END))

    cx, cy, r = _xy(circles[0])
    assert circles[0].dxf.layer == "MONUMENT"
    assert "MONUMENT" in doc.layers
    assert (cx, cy, r) == pytest.approx((50.0, 50.0, 2.5))

    x, y, s = _xy(texts[0])
    assert s == "LOT 5"
    assert (x, y) == pytest.approx((50.0, 54.0))

    pl = plines[0]
    assert pl.dxf.layer == "CURVE"
    pts = [(float(x), float(y), float(b)) for x, y, b in pl.get_points("xyb")]
    assert len(pts) == 2
    assert pts[0][:2] == pytest.approx((20.0, 0.0))
    assert pts[1][:2] == pytest.approx((20.0, 10.0))
    assert pts[0][2] == pytest.approx(ARC_BULGE, rel=1e-9)
    assert pts[1][2] == pytest.approx(0.0)


def test_prefer_polylines_skips_restated_lines_and_arcs(tmp_path):
    plan = v1_with_duplicates()
    doc = _roundtrip(plan, tmp_path, "pref.dxf", prefer_polylines=True)
    msp = doc.modelspace()
    assert len(list(msp.query("LWPOLYLINE"))) == 1
    assert len(list(msp.query("LINE"))) == 0
    assert len(list(msp.query("ARC"))) == 0

    # The polyline carries the straight edge and the bulge.
    pl = list(msp.query("LWPOLYLINE"))[0]
    pts = [(float(x), float(y), float(b)) for x, y, b in pl.get_points("xyb")]
    assert len(pts) == 3
    assert pts[0][:2] == pytest.approx((0.0, 0.0))
    assert pts[1][:2] == pytest.approx((20.0, 0.0))
    assert pts[2][:2] == pytest.approx((10.0, 10.0))
    assert pts[0][2] == pytest.approx(0.0)
    assert pts[1][2] == pytest.approx(math.tan(math.radians(90) / 4))


def test_no_prefer_polylines_draws_everything(tmp_path):
    plan = v1_with_duplicates()
    doc = _roundtrip(plan, tmp_path, "dup.dxf", prefer_polylines=False)
    msp = doc.modelspace()
    assert len(list(msp.query("LWPOLYLINE"))) == 1
    assert len(list(msp.query("LINE"))) == 1
    assert len(list(msp.query("ARC"))) == 1


def test_arc_angles_outside_0_360_are_passed_through(tmp_path):
    # fit_arcs.py may emit unwrapped angles when the sweep crosses +X.
    plan = {
        "lines": [],
        "arcs": [[0.0, 0.0, 5.0, 350.0, 380.0, "PROPERTY_LINE"]],
        "circles": [],
        "texts": [],
    }
    doc = _roundtrip(plan, tmp_path, "unwrap.dxf")
    arc = list(doc.modelspace().query("ARC"))[0]
    assert float(arc.dxf.start_angle) == pytest.approx(350.0)
    assert float(arc.dxf.end_angle) == pytest.approx(380.0)


def test_closed_polyline_drops_duplicate_and_sets_closed(tmp_path):
    plan = {
        "polylines": [[
            [0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0], [0.0, 0.0],
            "PROPERTY_LINE",
        ]],
    }
    doc = _roundtrip(plan, tmp_path, "closed.dxf")
    pl = list(doc.modelspace().query("LWPOLYLINE"))[0]
    pts = list(pl.get_points("xy"))
    assert len(pts) == 4
    assert pl.closed is True


# --------------------------------------------------------------------------- #
# v2
# --------------------------------------------------------------------------- #

def test_v2_labels_flags_units(tmp_path):
    plan = v2_plan()
    doc = _roundtrip(plan, tmp_path, "v2.dxf")
    msp = doc.modelspace()

    assert doc.header["$INSUNITS"] == 6
    assert "ANNOTATION" in doc.layers
    assert "FLAGS" in doc.layers
    assert "PROPERTY_LINE" in doc.layers

    lines = list(msp.query("LINE"))
    # 4 property lines + 1 overlay of the flagged course on FLAGS
    prop = [e for e in lines if e.dxf.layer == "PROPERTY_LINE"]
    flag_lines = [e for e in lines if e.dxf.layer == "FLAGS"]
    assert len(prop) == 4
    assert len(flag_lines) == 1
    assert _xy(flag_lines[0]) == pytest.approx((10.0, 10.0, 0.0, 10.0))

    labels = [e for e in msp.query("TEXT") if e.dxf.layer == "ANNOTATION"]
    assert len(labels) == 4
    texts = {e.dxf.text for e in labels}
    assert "N90°00'00\"E  10.00" in texts
    assert "N89°00'00\"W  10.50" in texts

    # Label on the east side (vertical segment) is aligned to the segment.
    east = next(e for e in labels if "N0°00'00\"E" in e.dxf.text)
    # Upright-corrected rotation of a +Y segment is +90°.
    assert float(east.dxf.rotation) == pytest.approx(90.0)
    # Insertion is near the midpoint (10, 5), offset off the ink.
    ins = east.dxf.insert
    assert float(ins.x) == pytest.approx(10.0, abs=1.0)
    assert float(ins.y) == pytest.approx(5.0, abs=1.0)

    # Flagged course also gets a marker circle; the ring is an LWPOLYLINE.
    flag_circles = [e for e in msp.query("CIRCLE") if e.dxf.layer == "FLAGS"]
    assert len(flag_circles) == 1
    cx, cy, _r = _xy(flag_circles[0])
    assert (cx, cy) == pytest.approx((5.0, 10.0))

    flag_pl = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer == "FLAGS"]
    assert len(flag_pl) == 1
    assert flag_pl[0].closed is True


def test_v2_feet_sets_insunits_feet(tmp_path):
    plan = v2_plan()
    plan["unit"] = "ft"
    doc = _roundtrip(plan, tmp_path, "v2ft.dxf")
    assert doc.header["$INSUNITS"] == 2  # feet
    assert insunits_for(plan) == 2


def test_v2_clean_ring_is_not_flagged_without_needs_human(tmp_path):
    plan = v2_plan()
    plan["report"]["needs_human"] = False
    plan["courses"][2]["flags"] = []
    plan["rings"][0]["misclosure"] = 0.0
    plan["rings"][0]["precision"] = "exact"
    doc = _roundtrip(plan, tmp_path, "v2clean.dxf")
    msp = doc.modelspace()
    assert list(msp.query("LINE").filter(lambda e: e.dxf.layer == "FLAGS")) == [] \
        or all(e.dxf.layer != "FLAGS" for e in msp.query("LINE"))
    assert not list(msp.query('LWPOLYLINE[layer=="FLAGS"]'))
    assert not list(msp.query('CIRCLE[layer=="FLAGS"]'))
    # Labels still drawn.
    assert len(list(msp.query('TEXT[layer=="ANNOTATION"]'))) == 4


# --------------------------------------------------------------------------- #
# Errors and tolerance
# --------------------------------------------------------------------------- #

def test_unknown_fields_tolerated(tmp_path):
    plan = v1_plan()
    plan["schema_note"] = "future"
    plan["unexpected"] = {"x": 1}
    plan["lines"].append([1.0, 1.0, 2.0, 2.0])  # missing layer is ok
    doc = _roundtrip(plan, tmp_path, "extra.dxf")
    assert len(list(doc.modelspace().query("LINE"))) == 2


def test_empty_plan_writes_valid_dxf(tmp_path):
    doc = _roundtrip({}, tmp_path, "empty.dxf")
    assert doc.header["$INSUNITS"] == 6
    assert len(list(doc.modelspace())) == 0


def test_invalid_json_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PlanError, match="invalid JSON"):
        plan2dxf.load_plan(path)


def test_non_object_plan():
    with pytest.raises(PlanError, match="must be an object"):
        plan_to_dxf([1, 2, 3])


def test_bad_line_raises():
    with pytest.raises(PlanError, match="lines\\[0\\]"):
        plan_to_dxf({"lines": [[0, 1, 2]]})


def test_bad_arc_radius_raises():
    with pytest.raises(PlanError, match="radius must be positive"):
        plan_to_dxf({"arcs": [[0, 0, 0, 0, 90, "PROPERTY_LINE"]]})


def test_bad_polyline_vertex_raises():
    with pytest.raises(PlanError, match="vertex"):
        plan_to_dxf({"polylines": [["not-a-vertex", "LAYER"]]})


def test_cli_writes_and_reports(tmp_path, capsys):
    src = tmp_path / "in.json"
    dst = tmp_path / "out.dxf"
    src.write_text(json.dumps(v1_plan()), encoding="utf-8")
    plan2dxf.main([str(src), str(dst)])
    assert dst.is_file()
    out = capsys.readouterr().out
    assert "out.dxf" in out
    assert "not survey-grade" in out
    doc = ezdxf.readfile(dst)
    assert len(list(doc.modelspace().query("ARC"))) == 1


def test_cli_invalid_input_exits(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"lines": [[1, 2]]}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        plan2dxf.main([str(src), str(tmp_path / "out.dxf")])
    assert "plan2dxf:" in str(exc.value)


def test_write_to_stream():
    buf = io.StringIO()
    write_dxf(v1_plan(), buf)
    buf.seek(0)
    doc = ezdxf.read(buf)
    assert len(list(doc.modelspace().query("CIRCLE"))) == 1

#!/usr/bin/env python3
"""Offline tests for vector_plan.py (the browser sample's extractor).

    pytest test_vector_plan.py
"""
import importlib.util
import io
import math
import os

import ezdxf
import pytest

import plan2dxf
import vector_plan
from vector_plan import analyze_pdf, looks_scanned

pymupdf = vector_plan._pymupdf()
HERE = os.path.dirname(os.path.abspath(__file__))


def _sample_bytes(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "make_sample", os.path.join(HERE, "docs", "samples", "make_sample.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "sample.pdf"
    mod.make(str(out))
    return out.read_bytes()


def _raster_only_bytes(src_bytes):
    src = pymupdf.open(stream=src_bytes, filetype="pdf")
    pix = src[0].get_pixmap(dpi=72)
    doc = pymupdf.open()
    page = doc.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.insert_image(page.rect, pixmap=pix)
    return doc.tobytes()


# --------------------------------------------------------------------------- #
# scanned detection (pure)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("profile,expected", [
    ({"vector_items": 0, "text_chars": 0, "image_cover": 1.0}, True),      # bare scan
    ({"vector_items": 3, "text_chars": 900, "image_cover": 0.98}, True),   # searchable scan
    ({"vector_items": 2, "text_chars": 5, "image_cover": 0.0}, True),      # blank page
    ({"vector_items": 6000, "text_chars": 8000, "image_cover": 0.0}, False),
    ({"vector_items": 400, "text_chars": 50, "image_cover": 0.9}, False),  # vector over a basemap
    ({"vector_items": 4, "text_chars": 1000, "image_cover": 0.0}, False),  # text-only page
])
def test_looks_scanned(profile, expected):
    scanned, reason = looks_scanned(profile)
    assert scanned is expected
    assert bool(reason) is expected


def test_looks_scanned_tolerates_missing_keys():
    assert looks_scanned({})[0] is True


# --------------------------------------------------------------------------- #
# extraction on the synthetic sample
# --------------------------------------------------------------------------- #

def test_sample_extracts_linework_arcs_monuments_and_labels(tmp_path):
    res = analyze_pdf(_sample_bytes(tmp_path), 1, 1200)
    assert res["page_count"] == 1 and res["scanned"] is False
    c = res["counts"]
    assert c["polylines"] == 6       # block, 3 lot lines, rear line, frontage
    assert c["arcs"] == 2            # the cul-de-sac as two quarter arcs
    assert c["circles"] == 4         # monuments
    assert c["bearings"] == 3 and c["distances"] == 3
    plan = res["plan"]
    assert plan["source"]["extract"]["dropped_frame"] == 1
    assert "R=18.29  L=57.46" in [t[2] for t in plan["texts"]]
    assert 18.29 not in plan["labels"]["distances"]  # curve params are not legs

    # 1:1200 -> 1 pt = 25.4/72 mm * 1200 = 0.42333 m; the west line x=120pt.
    assert any(abs(v[0] - 50.8) < 1e-3 for pl in plan["polylines"] for v in pl[:-1])


def test_cul_de_sac_bulges_dip_south(tmp_path):
    plan = analyze_pdf(_sample_bytes(tmp_path), 1, 1200)["plan"]
    front = next(pl for pl in plan["polylines"] if any(len(v) == 3 for v in pl[:-1]))
    bulged = [(a, b) for a, b in zip(front[:-1], front[1:-1]) if len(a) == 3]
    assert len(bulged) == 2
    for a, b in bulged:
        assert a[2] == pytest.approx(math.tan(math.radians(90) / 4), rel=1e-4)
    # every arc's midpoint lies below (south of) the street line
    doc = plan2dxf.plan_to_dxf(plan)
    street_y = front[0][1]
    for pl in doc.modelspace().query("LWPOLYLINE"):
        for e in pl.virtual_entities():
            if e.dxftype() == "ARC":
                s, t = e.dxf.start_angle, e.dxf.end_angle
                m = math.radians((s + (t if t > s else t + 360)) / 2)
                assert e.dxf.center.y + e.dxf.radius * math.sin(m) < street_y


def test_restated_arcs_are_skipped_by_plan2dxf(tmp_path):
    plan = analyze_pdf(_sample_bytes(tmp_path), 1, 1200)["plan"]
    buf = io.StringIO()
    plan2dxf.write_dxf(plan, buf)
    buf.seek(0)
    msp = ezdxf.read(buf).modelspace()
    assert len(msp.query("LWPOLYLINE")) == 6
    assert len(msp.query("LINE")) == 0 and len(msp.query("ARC")) == 0
    assert len(msp.query("CIRCLE")) == 4


def test_raster_only_pdf_is_detected_not_rejected(tmp_path):
    res = analyze_pdf(_raster_only_bytes(_sample_bytes(tmp_path)), 1, 250)
    assert res["scanned"] is True
    assert res["profile"]["image_cover"] >= 0.99
    assert "plan" not in res and res["page_count"] == 1


def test_page_selection_and_range(tmp_path):
    vec = pymupdf.open(stream=_sample_bytes(tmp_path), filetype="pdf")
    doc = pymupdf.open()
    doc.new_page()                     # blank page 1
    doc.insert_pdf(vec)                # vector page 2
    data = doc.tobytes()
    assert analyze_pdf(data, 1)["scanned"] is True
    two = analyze_pdf(data, 2, 1200)
    assert two["scanned"] is False and two["page"] == 2 and two["page_count"] == 2
    with pytest.raises(ValueError, match="out of range"):
        analyze_pdf(data, 3)


def test_not_a_pdf():
    with pytest.raises(ValueError, match="not a readable PDF"):
        analyze_pdf(b"hello", 1)

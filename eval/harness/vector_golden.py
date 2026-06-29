#!/usr/bin/env python3
"""vector_golden.py - build a license-free golden from a vector plat's text layer.

Modern county-recorder final plats are vector PDFs whose *text layer is the
surveyor's exact published bearings and distances* - a perfect answer key with
NO OCR and NO scope guessing. This script harvests that key, then stages the
SAME page as a blind raster read (tiles via prep_plan.py) so vlm_read.py +
score_run.py can measure recall against it.

The reader stays BLIND: the text layer is parsed here, separately, only to write
the golden. prep_plan.py renders from pixels, so the read never sees the layer.

    python vector_golden.py PLAN.pdf <slug> [--page N] [--unit ft|m]
      -> eval/goldens/<slug>.key_p<N>.json   (the facts; committable)
      -> eval/harness/_sources/<slug>/        (blind tiles; gitignored)
    python vlm_read.py <slug> --workers 1 --max-side 1536 \
        --prompt-file eval/read_prompt_local.txt
    python ../score/score_run.py <slug> --gt eval/goldens/<slug>.key_p<N>.json

Run with the SYSTEM/venv python (needs fitz). prep step also needs cv2/skimage.

What is committable (facts, not expression): the numeric golden this writes.
NEVER the source PDF/render unless the source is US-federal public domain - see
eval/manifest.json's redistribution_rule.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
GOLDENS = os.path.join(REPO, "eval", "goldens")
PREP_PLAN = os.path.join(HERE, "prep_plan.py")

# Quadrant DMS bearing: leading N/S, degrees, minutes, optional seconds, trailing
# E/W. Tolerant of whitespace and unicode prime marks; seconds optional (some
# plats publish deg-min only). We keep the digits AS PRINTED (no zero-padding) so
# the golden mirrors the sheet - score_run.dms() normalizes to azimuth for recall.
BEARING_RE = re.compile(
    r"([NS])\s*(\d{1,3})\s*°\s*(\d{1,2})\s*['′]"
    r"(?:\s*(\d{1,2}(?:\.\d+)?)\s*[\"″])?\s*([EW])",
    re.IGNORECASE,
)

# A decimal number = a distance candidate. Integers are dropped on purpose: lot
# numbers, plan numbers, years, and "1:250" scales are integers, while published
# leg lengths carry a decimal (the sheet writes 50.00, 25.0, 237.16).
DECIMAL_RE = re.compile(r"\d{1,6}\.\d{1,3}")

# Curve-table params and bookkeeping that are decimals but NOT straight-leg
# lengths. Reject signals on the text immediately BEFORE a number:
#  - '=' or ':' RIGHT before it (<=1 space) -> a LABELED value (R=, L=, Δ=,
#    Area =, coordinate N:), never a bare leg length. The adjacency matters:
#    record courses read "(R1: <bearing> 1319.61')" - after the bearing is
#    erased the colon is several chars back, so the distance is still kept;
#  - an unlabeled curve word (RADIUS/ARC/CHORD/DELTA/TANGENT/CH).
LABEL_PREFIX_RE = re.compile(r"[=:]\s?$")
CURVE_WORD_RE = re.compile(
    r"(?:RAD(?:IUS)?|ARC|CHORD|DELTA|TAN(?:GENT)?|CH)\s*$", re.IGNORECASE)
# A number trailed by an inch mark is a monument cap SIZE (FOUND 3.25" CAP),
# not a leg length - legs are feet (') or metres, never inches (").
INCH_SUFFIX_RE = re.compile(r'^\s*["″]')
# Area / non-length units that follow a number -> not a distance.
AREA_SUFFIX_RE = re.compile(
    r"^\s*(?:SF|SQ|S\.F\.|AC|ACRES?|HA|HECTARES?|M2|M²|SQ\.?\s*FT|SQ\.?\s*M)",
    re.IGNORECASE,
)

DIST_MIN, DIST_MAX = 0.1, 100000.0


def norm_bearing(m):
    """Canonical as-printed bearing string, e.g. N00°18'47\"E -> 'N00°18\\'47\"E'."""
    ns, deg, mn, sec, ew = m.groups()
    out = f"{ns.upper()}{deg}°{mn}'"
    if sec is not None:
        out += f"{sec}\""
    return out + ew.upper()


def harvest_bearings(text):
    seen, out = set(), []
    for m in BEARING_RE.finditer(text):
        b = norm_bearing(m)
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def harvest_distances(text):
    """Decimal numbers that are straight-leg lengths: not inside a bearing, not a
    curve param, not an area, within a sane range."""
    # Erase bearing matches first so their deg/min/sec digits can't leak in.
    clean = BEARING_RE.sub(" ", text)
    vals = set()
    for m in DECIMAL_RE.finditer(clean):
        pre = clean[max(0, m.start() - 8):m.start()]
        post = clean[m.end():m.end() + 8]
        if LABEL_PREFIX_RE.search(pre) or CURVE_WORD_RE.search(pre):
            continue
        if AREA_SUFFIX_RE.match(post) or INCH_SUFFIX_RE.match(post):
            continue
        v = float(m.group())
        if DIST_MIN <= v <= DIST_MAX:
            vals.add(round(v, 3))
    return sorted(vals)


def pick_page(doc, want):
    """Page index with the most bearing matches (the course sheet). Falls back to
    the most-text page when nothing parses anywhere."""
    if want is not None:
        return want
    best_i, best_b, best_chars, fallback = None, -1, -1, 0
    for i, p in enumerate(doc):
        t = p.get_text()
        nb = len(BEARING_RE.findall(t))
        if nb > best_b:
            best_b, best_i = nb, i
        if len(t) > best_chars:
            best_chars, fallback = len(t), i
    return best_i if best_b > 0 else fallback


def guess_unit(text):
    """Imperial-vs-metric sniff from strong unit tokens; --unit overrides. Bare
    'M' is deliberately excluded - it matches 'B.M.' (Base Meridian) and the like
    far more often than metres. Ties resolve to feet (the US county-plat case)."""
    u = text.upper()
    imp = len(re.findall(r"\bFEET\b|\bFT\b|\(FT\)|SQ\.?\s*FT|\bACRES?\b", u))
    met = len(re.findall(r"\bMETRES?\b|\bMETERS?\b|\bHECTARES?\b|\bHA\b|M²|\bM2\b", u))
    return "m" if met > imp else "ft"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", help="vector plat PDF (must have a real text layer)")
    ap.add_argument("slug", help="corpus slug, e.g. county_test")
    ap.add_argument("--page", type=int, default=None,
                    help="page index (default: auto = most bearings)")
    ap.add_argument("--unit", choices=["ft", "m"], default=None,
                    help="distance unit as printed (default: auto-sniff)")
    ap.add_argument("--out", default=None, help="golden path (default: goldens/<slug>.key_p<N>.json)")
    ap.add_argument("--min-bearings", type=int, default=3,
                    help="refuse if fewer bearings parse (likely a stroked-glyph plat)")
    ap.add_argument("--no-prep", action="store_true",
                    help="only write the golden; skip staging blind tiles")
    ap.add_argument("--tiles-only", action="store_true",
                    help="when prepping, skip the slow plat2json geometry pass")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--scale", type=float, default=250.0)
    a = ap.parse_args()

    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        sys.exit(f"missing dependency ({e}). Run: pip install -r requirements.txt")

    doc = fitz.open(a.pdf)
    page = pick_page(doc, a.page)
    text = doc[page].get_text()
    bearings = harvest_bearings(text)
    distances = harvest_distances(text)
    unit = a.unit or guess_unit(text)

    if len(bearings) < a.min_bearings:
        sys.exit(
            f"only {len(bearings)} bearings parsed on page {page} "
            f"({len(text)} chars of text). This looks like a stroked-glyph plat "
            f"with no usable text layer - use the blind VLM read path, not a "
            f"vector golden. (Override with --min-bearings 0.)")

    # Golden schema: bearings as printed (score_run.dms() -> azimuth); distances
    # under the scorer's key ('distances_m') but tagged with the true 'unit'.
    # Reader transcribes printed numbers too, so printed-vs-printed recall is
    # unit-agnostic; 'unit' keeps the facts honest. See README redistribution rule.
    golden = {
        "bearings_dms": bearings,
        "distances_m": distances,
        "unit": unit,
        "page": page,
        "note": f"page-{page} vector text-layer key, slug={a.slug}; "
                f"distances are as-printed in {unit} (key name is the scorer's)",
    }
    out = a.out or os.path.join(GOLDENS, f"{a.slug}.key_p{page}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(golden, fh, indent=1, ensure_ascii=True)
        fh.write("\n")
    print(f"[{a.slug}] page {page}: {len(bearings)} bearings, "
          f"{len(distances)} distances ({unit}) -> {out}")

    if a.no_prep:
        print("  (--no-prep) golden only; stage tiles later with prep_plan.py")
        return

    cmd = [sys.executable, PREP_PLAN, os.path.abspath(a.pdf), a.slug,
           "--page", str(page), "--dpi", str(a.dpi), "--scale", str(a.scale)]
    if a.tiles_only:
        cmd.append("--tiles-only")
    print(f"  staging blind tiles: prep_plan.py {a.slug} --page {page}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    tail = (r.stdout or r.stderr).strip().splitlines()
    for line in tail[-4:]:
        print("   ", line)
    if r.returncode != 0:
        sys.exit(f"prep_plan failed (rc={r.returncode}); golden was still written")

    print(f"\n  next: python eval/harness/vlm_read.py {a.slug} --workers 1 "
          f"--max-side 1536 --prompt-file eval/read_prompt_local.txt")
    print(f"        python eval/score/score_run.py {a.slug} --gt {out}")


if __name__ == "__main__":
    main()

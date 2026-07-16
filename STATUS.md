# Plan-label OCR — pipeline status (EPP12345 sample)

> **SUPERSEDED (2026-07): the "unsolved OCR" conclusion below is stale.**
> Label reading — including rotated DMS bearings — is solved by the local VLM
> reader: per-sheet **median bearing recall 0.95** (mean 0.87) on the 100-sheet
> NCDOT corpus, quadrant-tolerant scoring; see
> [`eval/results/RESULTS.md`](eval/results/RESULTS.md) (cite the macro,
> per-sheet figures). Four dense sheets remain as scoped reader work.
> This file stays as the record of the classical-OCR iterations 1–14 and why
> they failed. The open problem is no longer reading; it is
> **label→segment association** (joining read values to extracted geometry)
> and downstream COGO reconstruction with a traverse-closure self-check.

Goal: read the *authoritative* labels (bearings, distances, curve `r=`/`a=`,
areas, lot/plan IDs) off an LTSA vector plat, so geometry can be **COGO-
reconstructed from the published numbers** (exact) rather than traced from
flattened linework (approximate). Validated downstream against licensed-tool
ground truth — never against student artifacts.

## Source characterization (`E_SUBDIVISION_PLAN_S_67_LTA_2018-07-17.pdf`)
- **Pure vector, no raster** (0 images). 167 KB.
- **25,199 line segments, 0 curves** — every arc is flattened to short segments.
- **94 chars of real text** — all labels are *stroked glyphs*, not a text layer.
- Implication: edge detection is moot (we already have exact edges); the work is
  semantic reconstruction + reading stroked text.

## Iterations & findings
1. **Tesseract, horizontal** — prose/title excellent; `°'"` → replacement chars;
   rotated labels missed. (`plat_ocr.py`)
2. **Vector stroke-clustering + PCA deskew** — failed: fragments labels (char
   spacing varies, linework interleaved). (`deskew_labels.py`)
3. **cv2 morphology text-detection** — detects text regions, but fragments at
   word/sub-word level; line-overlap hurts. (`detect_cv.py`)
4. **EasyOCR / CRAFT, full render** — prose now whole strings; rotated geometry
   labels still fragmented (`r=65`, `a=14.` as `0=14.`). (`run_easyocr.py`)
5. **Vector linework removal → EasyOCR** — CURRENT BEST. Erase boundary/road/arc
   lines via the exact vectors, then OCR the clean raster:
   distances/areas/arc-lengths read reliably; rotated DMS bearings still partial.
   (`linefree_easyocr.py`, image `_plat_linefree.png`)

## What reads reliably now
Prose notes, title/plan IDs, **distances** (`152.306`, `43.443`, …), **areas**
(`929.9 m2`), **curve arc-lengths** (`a=12.778`).

## Remaining hard piece
**Rotated degree-minute-second bearings** (`57°15'51"`): steep arbitrary angles,
multi-token (deg/min/sec spread along the line), `°'"` symbols. Learned-detector
rotation handling (90/180/270 + CRAFT) does not rectify arbitrary angles.

## Iteration 6 — geometry-guided per-segment deskew (`geo_deskew.py`)
Drove deskew from long vector segments (raster CCs can't isolate edges — the
boundary is one connected blob). Result: the **deskew mechanism is proven** (the
straight title-box border lines rectify to perfect horizontal in
`_plat_strips.png`), BUT a wide band along each line grabs *neighboring* labels
at other angles — so the property-line bearings stay jumbled. The unsolved core
is **label-to-segment association** on a dense sheet, not the rotation math.

## Iteration 7–13 — geometry export + arc fitting + curve-label OCR
- **Geometry export WORKS** (`epp12345_plan.json`): skeletonize the linework-free
  raster → Hough → vector segments → real-meter, north-up plan JSON drawn by
  `LS_IMPORTPLAN`. The parcel (boundary, roads, cul-de-sac) reconstructs cleanly.
- **Arc fitting WORKS** (`fit_arcs.py` → `epp12345_plan_arcs.json`): chain segments,
  circle-fit → the cul-de-sac becomes true arcs with radii (~15.4 m) matching the
  published `r=15.500`. Big gentle road curve stays polyline (low curvature).
- **Curve-label OCR does NOT converge** (`curve_ocr.py`): arc geometry targets the
  crop (the `r=`/`a=` label IS in it), but fragmented arcs give an off deskew
  angle and neighbor-label clutter defeats reading; angle-sweep got 1 mangled read.

## Honest conclusion
Geometry extraction + drawing + arc fitting is a real, usable result. Automated
label OCR (bearings AND curve `r=`/`a=`) is a genuine document-AI research problem
that resisted ~6 approaches; per-iteration returns have gone flat. Recommend
**banking the geometry pipeline** and treating label OCR as a separate scoped
effort (fine-tuned detector, or vector-glyph matching against the LTSA strokefont,
or a human-in-the-loop read of the ~3 distinct curve radii to snap/refine arcs).

## Iteration 14 — arc refine with human-read curve table (`arc_refine.py`)
Curve table (human-read): r=15.000/a=12.778, 15.000/13.468, 6.000/7.234,
15.500/20.915, 65.500/14.400, 65.500/29.047. Snapped fitted radii to these and
**validated computed arc length vs published a=**. Result: lengths off 3–18 m,
9 arcs instead of 6 — the curved regions fragment into multiple Hough chains, so
no chain holds a whole arc. The validation harness works (it numerically catches
the bad reconstruction); the blocker is upstream curve extraction.

## FINAL STATE / where to resume
Bankable & working: LandSurvey OpenCAD plugin; PDF->CAD **geometry** pipeline
(parcel drawn in CAD); cul-de-sac as approximate arcs; the curve table; a
length-validation harness. Blocked (scoped future work, NOT quick tweaks):
1. **Clean arc reconstruction** — needs skeleton path-tracing (ordered per-curve
   polylines) so whole arcs fit + validate against the curve table.
2. **Automated label OCR** — separate document-AI effort (fine-tuned detector or
   vector-glyph matching vs the LTSA strokefont).
Artifacts: epp12345_plan.json (lines), _plan_arcs.json (approx arcs),
_plan_final.json (snapped, not yet validated). Draw any via LS_IMPORTPLAN.

## (superseded) earlier next-step note — detector-box → deskew → Tesseract (hybrid)
Sidestep line-association: use CRAFT/EasyOCR *detection* to get one oriented box
per label (with the label's own angle), crop that box from the **linework-free**
raster, rotate by the box's own angle, and recognize with **Tesseract** (best on
clean horizontal text). The label's own orientation is the deskew angle — no line
needed. Then classify (DMS/decimal/`r=`/`a=`) + `°'"` post-processing, associate
to the nearest parallel segment for COGO, and traverse-close to validate.

## Validation
COGO-close the traverse from the read bearings/distances (self-check on
misclosure, like the FARM-A fixture); confirm against MicroSurvey/Civil 3D.

## Environment
`fitz` (PyMuPDF), `opencv-python` 4.13, `pytesseract` 0.3.13 + Tesseract 5.5.0,
`easyocr` 1.7.2 + `torch` 2.12 (CPU). Scripts + `_plat_*.png` artifacts here.

## Iteration 15 — overlay harness + collinear chain merging (2026-07-15)
`overlay_check.py` registers any plan-JSON (or an OCS-plotted vector PDF) back
onto the source page and scores it: recon->ink chamfer, `recon_on_ink_pct`,
`linework_covered_pct` (ROI-scoped, text-filtered denominator), `--miss` map +
per-component attribution. Verified to 0.07 px against the known plot-scale
transform on 482.pdf sheet 2.

First harness-driven finding: the front-end's dominant loss was NOT
Otsu/skeleton fragmentation — it was `trace_polylines` min_len (~202 px)
discarding chains that junctions/ticks chopped short. Fix: `merge_collinear`
(good-continuation re-join across junctions, then the length cut judges the
reconstructed line). Scoreboard, 482.pdf sheet 2, ROI 0.10,0.18,0.72,0.88:

| front-end                    | polylines | segments | covered | chamfer  | representation |
|------------------------------|-----------|----------|---------|----------|----------------|
| default trace (pre-merge)    | 119       | 412      | 38.4%   | 0.07 px  | centerlines    |
| blunt `--min-len 30`         | 924       | 2 001    | 74.5%   | 0.05 px  | + glyph debris |
| **merge-collinear (now default)** | **132** | 1 166   | **67.5%** | 0.07 px | ordered centerlines |
| vtracer 0.6 binary/polygon   | 11 095    | 147 415  | 93.7%   | 0.70 px  | OUTLINES (double contours, glyphs incl.) |

vtracer note (the banked experiment): shipped vtracer has no centerline mode;
binary/polygon traces ink OUTLINES — coverage looks superb but every stroke is
a double contour, useless for association without outline->centerline
post-processing. A fairer retry would feed it the linework-only mask. The
coverage metric alone is gameable — always read it next to polyline/segment
counts.

Next bottleneck (measured, not guessed): FACE CLOSURE. Even at 67.5% coverage,
planarize+extract_faces forms ~0 faces from the full-page world trace at any
snap/extend/tol — the skeleton still has gaps where labels sit on lines and
where the scan drops out. Work item: label-aware gap bridging (mask detected
text boxes, then close small gaps along collinear continuations) before
face extraction; then re-run the association study on the merged front-end.

## Iteration 16 — topology repair: first automated lot faces from the raster trace
`repair_topology` (plat2json.py, runs after merge_collinear, `--bridge-gaps`):
three join types measured off overlay_check's dangling-ends map — A) collinear
BRIDGE across label/dropout breaks, B) CORNER join through the direction-line
intersection (the shared vertex sits inside a dropped monument symbol),
C) T-EXTEND a free end onto crossing linework (with overshoot so planarize
splits). Dangling ends 162 -> 99; keep max_gap under the narrowest road width
(~180 px at 300 dpi) or radial lot lines bridge across the right-of-way.

Face probe corrections that cost a day of false "0 faces" (now baked into
face_check.py — use IT, not ad-hoc planarize calls):
  * do NOT extend segment endpoints (shatters chain fabric: 662 stubs);
  * do NOT snap_endpoints on polyline-derived plans (halves the edge count);
  * area cutoffs must come from the plan's scale — at the default plot-scale
    250 a 65,340 sqft lot is ~264 units^2, and a hardcoded ">=500" hid every
    real lot while the rings were closed all along.

Ladder (face_check.py --tol 0.25, lot band 100-600 units^2, ~19 lots printed):
  merge only               1 lot face
  + repair  60 px          4
  + repair 150 px          6   (+ a 565 = two lots sharing an unclosed divider)

Next blockers, in order of expected yield: (1) the OUTER BOUNDARY ring — label
runs + monument-symbol corners > 150 px still break west/north/east edges, so
every perimeter lot leaks into the outer face; fix is erasing text-like ink
before skeletonizing (reuse overlay_check's stroke-length/extent classifier on
the binary) rather than ever-larger bridges; (2) duplicate overlapping edges
from repair-merged chains (46 node-pairs) — dedupe in planarize consumption;
(3) auto-pick the lot band from the printed lot areas once the label reader
is wired in.

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

## Iteration 17 — area-validated lot faces: 8/18 parcels match printed areas
Targets from iter 16, all landed (this + prior commit):
1. **Corridor-gated long bridges + overlap welds** (repair_topology): beyond
   the road-width cap, a bridge needs strict collinearity AND an ink-free
   corridor (inline-label gaps are empty; a false road crossing always has
   curb ink). New overlap weld joins two traces of the same line that pass
   each other (ends point apart — planarize's parallel solve can never merge
   these). Lateral floor raised 4->6 px (DP eps + skeleton jitter + dash
   offset stack to ~5). Repair now logs its join counts to stderr.
2. **Duplicate-edge dedupe** in face_check (repair-merged chains overlap;
   same-node-pair straight edges are identical; dupes distort the face walk).
3. **Printed-areas oracle**: eval/goldens/area482.printed_areas.json (18
   parcels, self-checked vs printed acreage x 43560; LOT 5/11 cross-checked
   vs the COGO golden). face_check --printed-sqft RANSAC-fits the single
   sqft-per-unit^2 scale and validates faces: fitted 247.63 vs 248.0
   theoretical for 1"=100 ft traced at assumed 1:250.

**Scoreboard (482.pdf sheet 2, --bridge-gaps 0.5): 8/18 parcels closed AND
area-validated <= 0.3% (LOTs 1, 2, 4, 7, 9, 10, 12, 17)**; 11 faces total;
coverage 77.0%; on-ink 95.3% (the 4.7% is repair segments crossing blank
label gaps — by design). Session arc: 0 -> 4 -> 6 -> 8 validated lots.

Still open: LOTs 3, 5, 6, 8, 11, 13-16, TRACT A. Known blockers: the
563-unit face = two lots sharing an unclosed divider; cul-de-sac lots (5, 6,
11, TRACT A) need the curb arcs' junctions with radials to close; the outer
boundary ring west/north corners at monument symbols (long bridges fired
only once — corner-adjacent geometry, not collinear; needs corner-aware
long joins or symbol-aware vertex reconstruction). LOT 8 is the big
7-acre remainder bounded mostly by the outer ring.

## Iteration 18 — 13/18 parcels + the road ROW closed; open set correctly identified
Stitching grew into three gated tiers (face_check.py): short stub-stub joins;
long joins to 5.5 units (SIGN-AGNOSTIC — post-planarize stub directions flip
in overlap/stagger configs, trust alignment magnitude + collinearity; lateral
0.7 admits tight bulb-curb arcs); extra-long joins to 15 units for boundary
inline-label voids, gated by near-perfect collinearity AND a crossing-edge
test (the graph-level corridor check — an un-gated long join fused two lines
straddling the road and cut the closed road face in half). Stub-edge welds to
2.0 units with a directional gate. Iterating stitch+replanarize converges
after one round (tested to 3).

**Closed and area-validated (<=0.5%): TRACT A, the road ROW (252k sqft), LOTs
2, 4, 5, 6, 10, 11, 12, 13, 15, 16, 17. Open: LOTs 1, 3, 7, 8, 9.**

Two lessons burned in:
* The greedy area-match LABELS are arbitrary within an equal-area class
  (eight 65,340 lots) — probe-point identification against the actual plat
  showed several "matched LOT n" labels pointing at other same-size lots.
  face_check now flags these matches; per-class COUNTS are the metric.
  True identity needs the label reader (lot numbers) — planned.
* The open lots all ring the outer boundary / lot-8 block. Capture there is
  GOOD (boundary + lot-9 lines fully traced — verified by polyline-over-ink
  renders), so the breaks are joinery at corner monuments and long label
  voids on BIG rings: each of these lots has a much longer ring than the
  interior lots, accumulating more break-chances. Next session: per-ring
  autopsy with the graph-zoom + containing-cycle probes (both in scratch
  tooling now), likely needing corner-aware joins at boundary monuments
  (the X/triangle symbols eat the vertex and the two boundary legs meet at
  an angle, so no collinear tier can span them).

## Iteration 19 — corner joins close LOT 8: 16/18 parcels; the last two forensically mapped
Graph-level CORNER JOINS in face_check.stitch_graph (two free stubs whose
outward rays intersect ahead of both within ~3.5 units, corridor-checked)
rebuilt the boundary-monument corners: **16/18 parcels closed and
area-validated (<=0.7%), including LOT 8's 7-acre ring at 0.7%.** Open: LOTs
1 and 3 (physically — the printed "LOT 1 matched" line is a near-equal-value
mislabel: 65,523 vs 65,340 are cross-compatible at 8% tol; the face map is
the identity authority).

New capture mechanism: plat2json dash_trains — reconstructs DASHED lines from
sub-line_px components (elongation >= 1.7; axis test only on strongly
elongated members — thick dashes have unstable PCA axes; RANSAC-trim against
parallel-line hopping; uniform-spacing gate vs text) with an easement SHADOW
filter (a train parallel to a traced line within ~70 px at >50% of its length
is an easement; verified visually: all 48 easement trains dropped, no false
keeps).

## Iteration 20 — county parcel fabric comparator: identity adjudicated, open set confirmed
Geolocated the plat from its banked text keys in ONE query (Wyoming statewide
parcels FeatureServer, `legal LIKE '%AREA THIRTY 3 EST%'` -> all 18 parcels,
Sweetwater Co.). New `fabric_compare.py` + oracle snapshot
`eval/goldens/area482.fabric.utm26912.json`: faces via face_check's exact
graph path, printed-area scale fit, then a RANSAC-consensus similarity fit
onto the fabric (naive anchoring on unique-printed-area matches FAILS at
RMS 76 m — near-equal-value mislabels poison it; triple-sampled RANSAC scored
over all area-compatible pairs lands **RMS 0.25 m over 16 lots**; recovered
rotation 1.24 deg == UTM grid convergence, a free physical sanity check).

The fabric is a coarse AREA oracle (assessor geometry re-shoelaces to
0.1–1% of printed; its `landgrosss` equals the printed areas 18/18 exactly —
plat-derived, so a transcription check only) but AUTHORITATIVE on POSITION,
the axis our internal oracles can't see. Consequences for iteration 19:
* **Identity solved without the label reader** (for fabric-covered plats):
  position corrected 7/16 area-class labels — the "LOT 1 matched" face is
  actually LOT 15, confirming iteration 19's mislabel suspicion. Fabric
  position is the interim identity authority; the label reader remains the
  path for plats with no fabric (new/BLM/GLO).
* **Open set independently confirmed:** the two fabric lots with no face are
  exactly LOTs 1 and 3 — the joinery diagnosis stands, from an evidence
  channel outside the plan.
* **New tool for the 1/3 frontage work:** inverse-transform the fabric's
  LOT 1/3 polygons into plan units as search corridors for the dead
  100-200 px frontage chains (fabric may GUIDE capture; closure +
  printed-area gates still do the validating — keep the oracle out of the
  validation loop it feeds).

## Iteration 22 — first BC run (Example-plan / EPP46435): 31/36 day one, fingerprint retrieval validated
The GEOM-2031 Example-plan (36 metric lots, Fort St. John, raster scan,
placeholder plan number EPP99999) went through the full pipeline:
* **Fingerprint retrieval worked.** EPP99999 → 0 fabric hits (placeholder
  confirmed — reported as a finding). Parent text key EPP19291 is real; a
  BBOX + PARCEL_CLASS='Subdivision' sweep (233 parcels) matched the printed-
  areas golden 36/36 within 1.5% for **EPP46435** only (next best 20/36) —
  the anonymized plat's real identity, and a per-lot oracle, from areas alone.
* **Gates on a raster scan:** 31/36 printed areas matched first pass (0.2–6%
  deltas — looser than 482's vector-derived scan). fabric_compare: 27
  consensus lots, RMS 0.68 m, rotation −0.05 deg == the plan's grid bearings
  (ISA 50, UTM 10) — the physical sanity check again. Identity face→PID
  adjudicated (BC fabric has no lot numbers; anchor pairing is label-first,
  unique-VALUE fallback — value-only anchoring cannot separate Wyoming's
  ~1%-grade area bands, which is why label stays primary).
* **Ablation finding:** corner-monument erasure (482's +2 lots) REGRESSED
  this plat 31→28 — its corners are small iron posts shared with closed
  neighbours; discs merged faces (60→52). Now opt-in
  (--rescue-erase-corners). One plat-class's cure is another's poison;
  the corridor loop without erasure is neutral here (open set unchanged).
* Open set (by PID): 029-457-{653,661,971,637,963} — next: per-ring autopsy
  (obs #169 recipe: ring-vs-traced gap scan, bitmap check, LOOK).
Goldens banked: example_plan.printed_areas.json (36 lots, m2, self-checked
against printed dimensions), example_plan.fabric.utm26910.json (EPP46435
snapshot + identification provenance).

## Iteration 21 — corridor-guided capture closes 18/18
The fabric corridor (fabric_compare --corridor-out, iteration 20) now drives
three capture mechanisms in plat2json, ALL inert without --rescue-corridor:
1. **Corridor crop extension.** The real LOT 1 blocker was never min_len:
   the largest-CC crop CLIPPED its entire east frontage (solid 265.54' line
   + corner monument were separate CCs beyond the bbox+4% margin — the
   traced drive line ended 3 px from the crop edge). Corridor bbox extends
   the crop; the title block can never be pulled in. This alone closed
   LOT 1 (17/18).
2. **Corridor-gated sub-min_len rescue** (--rescue-floor, default 0.4x).
   Transitional here: the final 18/18 run rescues 0 chains — and one
   intermediate run showed rescue re-admitting a monument-symbol OUTLINE,
   which glued LOT 3's corner into a stub-less squiggle blob no stitch tier
   could use. Lesson: at symbol corners, REMOVAL beats addition.
3. **Corner-monument disc erasure.** LOT 3's north corner is a triangle+dot
   monument the boundary lines end AT; its traced outline dead-loops the
   graph. Discs (corridor halfwidth + 22 px) erased at fabric-ring corner
   vertices (>20 deg direction change) leave clean stubs that repair
   bridges + face_check corner joins rebuild. This closed LOT 3.

**Scoreboard: 18/18 parcels closed and printed-area matched.** 17 lots
within 0.9% (LOT 1 0.0%, LOT 3 0.0%); the LOT-7-position face is the
outlier at 1.24% and LOT 6 drifted to 0.76% (corner erasure bruises
adjacent geometry mildly — acceptable, flagged). fabric_compare: 18
consensus lots, RMS 0.30 m, no face-less fabric lots. Oracle hygiene:
the corridor GUIDED capture; area validation is against the printed
golden the capture never saw, so LOT 3 at +0.00% is independent.

Reproduce:
    python fabric_compare.py PLAN.json eval/goldens/area482.fabric.utm26912.json \
        eval/goldens/area482.printed_areas.json --corridor-out COR.json \
        --corridor-lots "LOT 1,LOT 3" --corridor-halfwidth-m 3.0
    python plat2json.py 482.pdf PLAN18.json --page 1 --bridge-gaps 0.5 \
        --rescue-corridor COR.json --rescue-floor 0.35

LOTs 1/3 forensics (superseded by iteration 21; kept for the record — note
the min_len hypothesis was WRONG for LOT 1, it was the crop): their shared
Yellowstone frontage's
LOWER half is traced (poly ~12); the UPPER half is solid ink, in the skeleton,
but its 100-200 px junction-chopped chains die at the min_len cut — at
--min-len 100 LOT 3 closes (0.7%) but debris re-admission splits the road
face and degrades everything (LOT 11 3.0%, LOT 8 1.2%): the global knob is
the wrong tool. An "extension rescue" of sub-min_len chains collinear off
accepted ends was tried and REVERTED (-1 parcel: a rescued chain cut LOT 8's
ring without closing 1/3). Next candidates: understand why repair's phase A
does not merge those 100-200 px frontage chains (end hooks from label
touching are suspected — check end_dir quality there), or a dash_trains
variant seeded from the frontage's 95 px dash components (they pass the CC
filter, so they are skeleton chains, not dash_train candidates — the two
mechanisms currently have a coverage seam at [line_px, min_len]).

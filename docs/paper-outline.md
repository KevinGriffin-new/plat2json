# Paper outline — oracle-validated extraction of cadastral subdivision plans

Working draft outline for a methods paper on the plat2json pipeline.
Primary venue: *Transactions in GIS* or *IJGIS* (GIScience framing:
cadastral data capture, parcel-fabric integration, validation methodology).
Alternative venue: *ICDAR* (document-analysis framing — see §V at the end
for the restructuring notes).

Source material: `STATUS.md` (iterations 1–22), `docs/PIPELINE.md`,
`eval/results/RESULTS.md`, goldens under `eval/goldens/`.

---

## Candidate titles

1. *Closing the Plat: Multi-Oracle Validation for Automated Extraction of
   Subdivision Plans*
2. *From Scan to Fabric: Oracle-Guided Capture and Capture-Blind Validation
   of Cadastral Plats*
3. *Every Lot Accounted For: Validated Reconstruction of Subdivision Plats
   with Parcel-Fabric Oracles*

(1) is the current favorite — "closing" reads both as traverse/face closure
and as closing the loop against external oracles.

## Abstract (sketch)

Subdivision plats are the authoritative record of parcel geometry, yet they
survive mostly as scans and stroked-vector PDFs with no machine-readable
structure. We present a pipeline that reconstructs closed, area-validated
lot polygons from plat images and — where a jurisdiction publishes a parcel
fabric — resolves each face's legal identity by position. The core
methodological claim is not the capture front-end but the **validation
architecture**: a ranked ensemble of independent oracles (ring closure >
printed lot areas > fabric areas, with the fabric authoritative only on
position/identity), an **oracle-guided capture loop whose validating gate
stays blind to the guiding oracle**, and a built-in physical sanity check
(the recovered map rotation must equal grid convergence at the site).
We further show that the multiset of printed lot areas is a practical
**fingerprint for plan retrieval**, identifying an anonymized plat's true
plan number from 233 candidates with a 36/36 area match. Two case studies
(a Wyoming vector-derived plat: 18/18 lots closed, fabric fit RMS 0.30 m;
a British Columbia raster scan: 31/36 lots on the first pass, retrieval
36/36) and a 100-sheet reader evaluation (median per-sheet bearing recall
0.95) support the claims. Per-mechanism ablations show that capture
heuristics are plat-class-specific — the corner-erasure rescue that gained
+2 lots in Wyoming cost −3 in BC — arguing for gated, oracle-checked
mechanisms over universal defaults.

---

## 1. Introduction

- **Problem.** Plats/plans are the legal source of parcel geometry;
  downstream GIS fabrics are derived, generalized, and (for areas)
  often just transcriptions of the plat's printed numbers. Automating
  plat→structured-geometry is an old goal; the hard part is not tracing
  ink but *knowing when the reconstruction is right*.
- **Gap.** Map/plan vectorization literature reports pixel- or
  segment-level metrics (coverage, IoU, chamfer). None of these certify
  the object that matters legally: a **closed lot ring with the correct
  area and the correct identity**. Coverage is gameable (iteration 15:
  vtracer hit 93.7% coverage while producing unusable double-contour
  outlines).
- **Thesis.** Treat extraction as a *measurement* problem: every capture
  claim must be validated by an oracle that did not feed the capture.
  The pipeline is organized around that discipline.
- **Contributions** (each maps to a section):
  1. **Multi-oracle validation with per-axis oracle ranking** (§4):
     closure (mm-grade, internal) > printed areas (surveyor-published) >
     fabric areas (0.1–1% tax-grade); the fabric is *authoritative only on
     position and identity* — the one axis internal oracles cannot see.
     The recovered similarity-fit rotation ≡ grid convergence serves as a
     free physical sanity check on the whole chain.
  2. **Oracle-guided capture with capture-blind validation** (§5): fabric
     corridors guide crop extension / chain rescue / corner erasure, while
     the printed-area gate — never seen by capture — does the validating.
  3. **Area-multiset fingerprint retrieval** (§6): the printed lot areas
     form a high-entropy fingerprint that retrieves a plan's identity from
     a fabric sweep without any position information.
  4. **Per-mechanism ablations across plat classes** (§8): corner-monument
     erasure +2 lots (WY) vs −3 lots (BC); global knobs (min-length) fail
     where gated local mechanisms succeed.
- Scope statement up front: raster-first capture with VLM label reading;
  dimension-first COGO reconstruction is future work (§10).

## 2. Background and related work

- **Cadastral / plat digitization**: manual COGO entry workflows;
  parcel-fabric build programs (e.g., ParcelMap BC); prior automated
  plat-vectorization attempts.
- **Map and engineering-drawing vectorization**: raster→vector
  (skeleton/trace methods, learned vectorization), polygonization of
  scanned maps; why their metrics stop at linework fidelity.
- **Text reading on drawings**: classical OCR on rotated dimension labels.
  We can cite our own negative-results record here — iterations 1–14
  (STATUS.md) document six classical approaches (Tesseract, stroke
  clustering + PCA deskew, cv2 morphology, EasyOCR/CRAFT, linework-removal
  + OCR, geometry-guided deskew) that all failed on rotated DMS bearings;
  the unsolved core was label→segment association, not rotation math.
  VLM readers dissolve the reading half of that problem (§7).
- **Vision-language models for document extraction**: recent VLM document
  AI; open local models (Qwen2.5-VL class) making blind, reproducible,
  license-clean reading feasible on consumer hardware.
- **Validation/QA traditions in surveying**: traverse closure, misclosure
  standards — the paper imports this culture into document extraction.

## 3. Problem setting and materials

- Input classes, in increasing difficulty: (a) vector PDFs with a real
  text layer (free exact goldens); (b) vector PDFs with stroked glyphs
  (EPP12345: 25,199 segments, 0 curves, 94 chars of true text);
  (c) raster scans (482.pdf, Example-plan, BLM/GLO historical scans).
- Case-study plats:
  - **area482** — "Area Thirty 3 Estates", Sweetwater Co., Wyoming.
    18 parcels + road ROW; scan of vector-plotted sheet; imperial units;
    equal-area lot classes (eight 65,340 sqft lots) that make identity
    genuinely hard.
  - **Example-plan / EPP46435** — Fort St. John, BC. 36 metric lots,
    raster scan, *anonymized* plan number (EPP99999 placeholder) — which
    is what makes it the natural retrieval test.
- Oracles available per jurisdiction (`fabric_providers.py`): Wyoming
  statewide ArcGIS fabric (lot labels, sqft), ParcelMap BC WFS (PIDs, m²,
  no lot numbers). Provider-agnostic snapshot schema; adding a
  jurisdiction = one function.
- Goldens: printed-areas keys banked *before* capture
  (`area482.printed_areas.json`, `example_plan.printed_areas.json`),
  fabric snapshots with provenance, NCDOT vector-text keys.

## 4. The validation architecture (Contribution 1)

The paper's center of gravity. Present as a framework, not an
implementation detail.

- **4.1 Oracle inventory and per-axis ranking.**
  - Ring closure: internal, mm-grade, catches topology errors.
  - Printed lot areas: the surveyor's published numbers; independent of
    capture; validates scale + shape jointly (single RANSAC-fitted
    sqft-per-unit² scale: fitted 247.63 vs 248.0 theoretical on 482).
  - Fabric areas: coarse (assessor geometry re-shoelaces to 0.1–1% of
    printed) and sometimes *not independent at all* — Wyoming's
    `landgrosss` equals printed areas 18/18 exactly, i.e., plat-derived;
    treat as transcription check only.
  - Fabric **position**: the one authoritative external axis → identity
    adjudication. Greedy area-matching *within* an equal-area class is
    provably unreliable (iteration 18: several "matched LOT n" labels
    pointed at other same-size lots; iteration 20: position corrected
    7/16 area-class labels — the "LOT 1" face was actually LOT 15).
  - Key principle to state formally: *rank oracles by grade per axis, and
    never let an oracle validate the mechanism it guided.*
- **4.2 The rotation ≡ grid-convergence sanity check.** The
  RANSAC-consensus similarity fit recovers a rotation with no geodetic
  input; if the chain is right, that rotation must equal the grid
  convergence at the site. Observed: +1.24° == UTM 12N convergence
  (Wyoming); −0.05° == the plan's own grid bearings (BC, ISA 50 / UTM 10).
  Two independent jurisdictions, both on the nose — a built-in physical
  end-to-end check that costs nothing.
- **4.3 Robust fitting details.** Naive anchoring on unique-printed-area
  matches fails (RMS 76 m — near-equal-value mislabels poison it);
  triple-sampled RANSAC over all area-compatible pairs lands RMS 0.25 m
  over 16 lots (482). Label-first anchor pairing with unique-value
  fallback (needed in BC where the fabric has no lot numbers; value-only
  anchoring cannot separate ~1%-grade area bands).
- **4.4 Negative-space evidence.** The open set (fabric lots with no
  reconstructed face) as an *independent confirmation channel*:
  iteration 20's fabric said exactly {LOT 1, LOT 3} were missing —
  agreeing with the internal joinery diagnosis from a completely
  different evidence source.

## 5. Oracle-guided capture, capture-blind validation (Contribution 2)

- The corridor loop (PIPELINE.md step 6): inverse-transform the fabric's
  open-lot rings into plan pixel space as search corridors; corridor-gated
  mechanisms (all inert without the flag):
  1. **Corridor crop extension** — closed LOT 1 (the true blocker was the
     largest-connected-component crop clipping the east frontage, not the
     min-length cut; worth telling as a diagnosis story).
  2. **Corridor-gated sub-min-length chain rescue** (with the cautionary
     result: rescue re-admitted a monument-symbol outline that glued
     LOT 3 into an unusable blob — "at symbol corners, removal beats
     addition").
  3. **Corner-monument disc erasure** — closed LOT 3; now opt-in after
     the BC regression (§8).
- **The hygiene rule as a falsifiable protocol**: the corridor may guide
  *capture*; the printed-area gate never sees the corridor, so a closed
  lot at +0.00% area delta (LOT 3) is independent evidence, not
  circularity. Spell out the information-flow diagram (figure).
- Result: 16/18 → 18/18 on 482 with validation intact.

## 6. Area-multiset fingerprint retrieval (Contribution 3)

- Setting: the BC Example-plan is anonymized (EPP99999); the fabric query
  correctly returns 0 hits (report as a finding — absence from fabric is
  informative, not a failure mode).
- Method: bank the 36 printed areas; sweep a bbox +
  `PARCEL_CLASS='Subdivision'` fabric query (233 parcels); score each
  candidate plan by multiset area match within tolerance.
- Result: **36/36 within 1.5% for EPP46435 only; next best 20/36** — the
  areas alone identify the plan and simultaneously deliver a per-lot
  oracle for the rest of the pipeline.
- Discussion: entropy of the area multiset as the retrieval key (ties to
  the equal-area-class discussion — areas that defeat *within-plat*
  identity still work *across* plats); relation to the text-key ladder
  (plan number > parent plan > name fragment > fingerprint); privacy/
  licensing note — retrieval used only open fabric attributes.

## 7. The reading envelope (supporting result, not a headline claim)

Compress `eval/results/RESULTS.md` into one section; the reader is a
commodity the pipeline consumes, and the interesting material is the
*measurement methodology*.

- Instrument: local Qwen2.5-VL (7B on 8 GB RTX 4060; 32B on M1 Max),
  blind reads (never shown a key), tiled at validated settings.
- **NCDOT corpus (100 real road-plat sheets)**: per-sheet **median
  bearing recall 0.95, mean 0.87** (headline, macro); pooled micro 0.810
  quadrant-tolerant (0.763 strict) reported only as volume statistic.
  Distances macro median 0.79. Four genuine failures (dense sheets,
  recall < 0.2) scoped as future reader work.
  - Methodological point worth a subsection: **the sheet, not the item,
    is the unit of replication** — one systematic cause (a dropped
    quadrant letter) replicated across 22 items on one sheet; item
    pooling treats ~3,900 correlated bearings as independent trials.
  - Twice, apparent reader failures were scorer/golden bugs
    (quadrant-tolerance fix +4.7 pts, 45 sheets recovered, zero
    regressions; Wolf Creek golden fragmenting comma coordinates).
    State the lesson: **audit the instrument before blaming the reader.**
- **Resolution/quality envelope**: no cliff from resolution alone
  (graceful slope, bearings ~90% down to 7 px glyphs); the cliff is
  image *quality* (blur/noise/JPEG), and that corner also weakens the
  fail-safe (plausible-wrong misreads appear: N89°30'W → N69°30'W).
  Crop-to-content is a deployment lever comparable to DPI. 32B is
  resolution-robust to 512 px; neither model gains above ~896 px →
  efficient operating point 768–896 px.
- **License-free correctness proof** (blm_ms52a, 1892 mineral survey):
  recovered side/end bearings 56°09' + 33°51' = 90°00' exactly — a
  geometric invariant confirms the read with no answer key. Nice
  self-contained example of oracle thinking applied to reading.

## 8. Ablations and per-mechanism accounting (Contribution 4)

- **Corner-monument erasure across plat classes**: +2 lots on 482
  (triangle+dot monuments that eat the corner vertex) vs **−3 lots on
  the BC plan (31→28; faces 60→52)** — small iron-post circles shared
  with closed neighbours merge faces when erased. One plat-class's cure
  is another's poison → mechanism is opt-in, triggered by autopsy
  evidence (symbol-welded corners), not default.
- **Global knob vs gated mechanism**: lowering min-length globally closes
  LOT 3 but re-admits debris that splits the road face and degrades
  closed lots (LOT 11 → 3.0%) — the measured argument for corridor-gated
  local rescue over global thresholds.
- **Stitch-tier ladder** (iterations 16–19): merge-only 1 face → +repair
  4–6 → +stitch tiers 13 → +corner joins 16 → +corridor loop 18. Each
  tier's gates (collinearity, ink-free corridor, crossing-edge test,
  sign-agnostic alignment) exist because an ungated version measurably
  broke something (e.g., an ungated long join fused two lines straddling
  the road and cut the closed road face in half). Present as a table:
  mechanism / gate / failure it prevents / lots gained.
- **Dash-train reconstruction + easement shadow filter**: 48 easement
  trains dropped, no false keeps (visual verification).
- Optionally: reader ablations fold in here (tile size ~doubled dense-
  sheet bearing recall 40%→75%; model size; resolution ladder).

## 9. Case-study results (the two end-to-end runs)

Could be merged into §5/§6; kept separate so each case study reads as a
complete narrative.

- **area482 (WY)**: 18/18 parcels closed and printed-area matched
  (17 lots ≤ 0.9%, worst 1.24%); fabric fit RMS 0.30 m over 18 consensus
  lots; rotation +1.24° ≡ grid convergence; identity fully
  position-adjudicated; open set → empty via corridor loop.
  Figure: `docs/area482_overlay.png` (fabric red vs reconstructed faces
  cyan on satellite imagery).
- **EPP46435 (BC)**: first-day run on a raster scan in a second
  jurisdiction with a different provider, units, and identity scheme
  (PIDs, no lot numbers): 31/36 printed areas matched first pass
  (0.2–6% deltas), fabric RMS 0.68 m over 27 consensus lots, rotation
  −0.05° ≡ grid bearings; retrieval 36/36 (§6); open set of 5 PIDs
  scoped with the per-ring autopsy recipe.
- Frame explicitly: one plat *developed on*, one plat *transferred to*.
  The BC run is the generalization evidence — same chain, one new
  provider function — but say plainly it is still N=2 (§10).

## 10. Limitations (honest, specific)

- **N = 2 end-to-end case studies.** The capture front-end and corridor
  loop are validated on two plats (one per jurisdiction). The 100-sheet
  NCDOT corpus validates only the *reading* axis, not face closure or
  identity. The corner-erasure reversal between the two plats is itself
  evidence that per-plat-class behavior varies — more classes (metes-and-
  bounds, condo strata, GLO townships) are needed before claiming
  generality of the capture heuristics. What *is* corpus-scale: the
  reader envelope and the validation framework's design.
- **Corpus economics: LTSA plan-image licensing.** ParcelMap BC's fabric
  is open (Open Government Licence BC), but the *plan images* needed for
  a BC-wide capture corpus are licensed per-document from LTSA at
  non-trivial cost. A province-scale evaluation therefore needs either a
  research agreement or a jurisdiction with both open fabric *and* open
  plan images; candidates and the resulting corpus design belong in
  future work.
- **Dimension-first COGO reconstruction is not yet the primary path.**
  The pipeline currently traces ink and validates against printed
  numbers; the surveyor's ideal — reconstruct geometry *from* the read
  bearings/distances (exact COGO), with traverse closure as the internal
  gate, and use the raster only for association — is designed
  (label→segment association is the open problem, per STATUS.md) but not
  the operating mode. Consequence: reconstruction precision is bounded
  by raster fidelity (scale-fit residuals, ~0.3–0.7 m fabric RMS) rather
  than by the published dimensions themselves.
- Secondary caveats: four dense NCDOT sheets defeat the 7B reader;
  fabric availability/quality varies by jurisdiction (the framework
  degrades to closure + printed areas where no fabric exists);
  recall ±~5 pt granularity on small per-figure label counts; synthetic
  degradations are hand-tuned, not calibrated scanner MTF; equal-area
  identity requires either fabric position or the (planned) lot-number
  reader.

## 11. Conclusion and future work

- Restate: validation architecture > any single capture trick; the
  discipline (ranked oracles, guide/validate separation, physical sanity
  checks, audit-the-instrument) transfers to other document-extraction
  domains.
- Future work, in priority order: dimension-first COGO with closure as
  the primary gate (needs label→segment association); the four dense-
  sheet reader failures; corpus expansion (licensing-permitting BC, or
  an open-plan-image jurisdiction); label-reader-based identity for
  fabric-less plats (new/BLM/GLO); geolocate-then-zoning validators.

---

## Figures and tables (working list)

| # | Item | Source |
|---|------|--------|
| F1 | Pipeline diagram: capture → gates → fabric → corridor → overlay, with the guide-vs-validate information-flow boundary drawn explicitly | PIPELINE.md |
| F2 | area482 overlay: fabric vs reconstructed faces on imagery | docs/area482_overlay.png |
| F3 | Stitch-tier ladder: faces closed per mechanism tier (0→4→6→8→13→16→18) | STATUS iters 16–21 |
| F4 | Fingerprint retrieval: match count per candidate plan (36/36 vs next-best 20/36 among 233) | STATUS iter 22 |
| F5 | Reading envelope: recall vs glyph px, resolution-only vs +quality stack | RESULTS R-CLIFF |
| T1 | Oracle inventory: axis / grade / independence / role | PIPELINE.md §oracle hygiene |
| T2 | Case-study summary: 482 vs EPP46435 (lots, closure, area deltas, RMS, rotation-vs-convergence) | STATUS iters 20–22 |
| T3 | NCDOT reader results: macro + pooled, with the four failures | RESULTS R-NCDOT |
| T4 | Ablation table: mechanism / gate / failure prevented / Δlots per plat class | STATUS iters 16–22 |
| T5 | 7B vs 32B × resolution ladder | RESULTS R-RES |

## V. Venue notes

- **Transactions in GIS / IJGIS (primary).** Lead with contributions 1–3;
  the fabric-integration and identity-adjudication story is the GIScience
  hook; reader evaluation compressed to §7. Emphasize reproducibility:
  committed goldens, URL-only source manifests, provider-agnostic schema.
  TGIS is friendlier to systems/methods papers; IJGIS wants the framework
  generalized more formally (consider formalizing the oracle ranking as a
  small decision calculus if targeting IJGIS).
- **ICDAR (alternative).** Restructure: the *document analysis* story
  leads — stroked-glyph plats as an adversarial OCR domain (iterations
  1–14 as motivation), VLM blind reading with the unit-of-replication and
  audit-the-scorer methodology, geometric invariants as answer-free
  correctness proofs (blm_ms52a), and the fabric only as an external
  validation dataset. Contribution 4 (ablations) and the reading envelope
  (§7) get promoted; §4–5 compress to "downstream validation". Shorter
  page budget — cut §6 to a subsection.
- Either way, the negative-results record (classical OCR iterations) and
  the two scorer-bug postmortems are unusual, credibility-building
  material — keep them.

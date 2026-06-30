# Results — how reliably are survey plans read from real scans?

The committed **reads** + **goldens** are the auditable record — every number
below can be checked by reading those fixtures directly. To re-run the `score/`
scripts, regenerate the working dir (`_sources/<slug>/`) from the manifest URL
via `harness/acquire.py`; to regenerate a read from scratch, re-issue
`read_prompt.txt` to any capable VLM (blind — never show it a key).

The unit of the reading-robustness axis is **glyph height in pixels** (the
median height of a dimension-label digit in the image the reader actually sees),
not DPI — a plat with larger text survives lower DPI.

---

## Synthetic baseline — degrade a clean vector plat (`county_test`)
The Boise vector PDF's text layer is the surveyor's exact published values = a
free, high-entropy golden (`goldens/county_test.key_p0.json`). Rasterizing it and
sweeping a synthetic degradation stack (downsample + Gaussian blur + sensor
noise + JPEG + skew + uneven lighting), blind-reading vs that golden:

| level | ~glyph px | bearing recall | distance recall | precision |
|-------|-----------|----------------|-----------------|-----------|
| clean | ~18 | 100% | 99% | 98–99% |
| light | ~12 | 96% | 99% | 94–100% |
| moderate | ~8 | 83% | 89% | 89–97% |
| knee | ~7 | 21% | 14% | 71–77% |
| severe | ~6 | 0% | 0% | — |

Read literally this says "a cliff at ~7–8 px." The real-scan tests below show
that reading is **misattributed** — the cliff is driven by the bundled
*quality* degradations, not the pixel count.

---

## R1 — a genuine BLM scan placed on the curve (`glo_t28nr71w`)
A real 1997 BLM cadastral scan (native ~404 DPI, read at 300 DPI), median
dimension-label glyph **21 px**. Scored vs an OCR-of-the-field-notes key
(`goldens/glo_t28nr71w.fieldnote_key.json`):

- **bearing recall 20/21 = 95%**, distance recall 7/9 = 78%
- self-check: **23/25 read bearings match a recovered segment azimuth (≤3′)** —
  the reader is accurate, not hallucinating.

At ~21 px (above the synthetic clean point) the real scan reads ~95% — on the
curve's easy end. The lower distance figure is **golden-limited** (sparse
field-note key), not perception-limited. ⇒ genuine scanner artifacts do not
degrade reading above the legibility floor.

---

## R-CLIFF — does the cliff hold on real artifacts? (`glo_t28nr71w`)
Took the genuine scan, cropped the densest figure (a rotated mineral-survey lot
group with non-cardinal bearings), and swept the glyph-px ladder **two ways**:
(A) resolution only — downsample the real scan, real artifacts carried down;
(B) the same levels **+ a realistic scan stack** (blur + noise + JPEG). Blind
read each, scored vs the clean (24 px) read.

**A) resolution only**

| glyph | bearing recall | distance recall | non-ref errors |
|-------|----------------|-----------------|----------------|
| 24 px | 100% | 100% | 0 |
| 14 px | 100% | 90% | 1 |
| 10 px | 100% | 85% | 1 |
| 8 px | 100% | 71% | 2 |
| 7 px | 90% | 57% | 2 |

**B) + realistic scan degradation**

| glyph | bearing recall | distance recall | non-ref errors |
|-------|----------------|-----------------|----------------|
| 10 px | 100% | 80% | 1 |
| 9 px | 90% | 66% | 4 |
| 8 px | 80% | 52% | 8 |

Findings:
1. **No cliff from resolution alone.** Reading degrades as a graceful slope;
   high-entropy bearings hold ~90% to 7 px. The synthetic 8→7 px collapse
   (83→21%) does not reproduce.
2. **The cliff is image QUALITY (blur/noise/SNR), not pixel count.** Adding the
   realistic stack steepens the decline.
3. **Fail-safe is conditional.** Pure-resolution loss keeps errors ≤3 (omission).
   Small glyphs **+ noise** breaks it: errors jump 2→8 at 8 px, including a
   plausible-wrong misread (`N.89°30'W`→`N.69°30'W`).
4. **Tight-crop presentation lowers the floor.** The synthetic test tiled the
   full sheet (glyph = tiny fraction of a big tile → under-sampled); cropping to
   content keeps glyphs densely sampled. Crop-to-content / zoom is a real
   deployment lever, not just DPI.

Fixtures: `reads/glo_t28nr71w_cliff/reads.json` (9 blind reads), scored by
`score/score_level.py`.

---

## R-MS — high-entropy non-cardinal real scan (`blm_ms52a`)
A genuinely-scanned 1892 US Mineral Survey (Garfield lode), native 8478×6038 px.
Two blind subagents (left/right tiles, no key). Correctness checked by
**license-free geometry**, not an external key (`goldens/blm_ms52a.invariants.json`).

- **Entropy:** reference bearings span **9 distinct 10° buckets, 56°–326°**, both
  N/S and E/W — genuinely high-entropy (no cardinal degeneracy).
- **Blind-read recall: 9/10 = 90%** of the high-entropy reference bearings.
- **License-free correctness PROOF:** the recovered side bearing **56°09'** and
  end bearing **33°51'** sum to **exactly 90°00'** (sides ⊥ ends, as a valid lode
  claim requires), and opposite sides read **1500 = 1500 ft**. Two non-cardinal
  bearings satisfying an exact perpendicularity constraint cannot be
  coincidentally correct — the geometry confirms the read with no answer key.
- **Fail-safe held:** the one edge-cut bearing was returned `N.56°8'?E.` with a
  '?', not fabricated.

⇒ on a 138-year-old scan the reader recovers non-cardinal high-entropy courses
at 90% with geometrically-provable accuracy. This corroborates R1 while removing
the low-entropy confound that caps regular-grid (PLSS) numbers.

Fixture: `reads/blm_ms52a.reads.json`.

---

## Vector-golden recall curve — 4 county plats, local 7B VLM (R-VEC)
`vector_golden.py` harvests an exact golden straight from a vector plat's text
layer (no OCR, no scope guessing); the reader stays blind on the rendered raster.
All four sheets read at the validated quality setting (`--tile 1100` full-res
tiles, `--max-side 1536`, 7B Qwen2.5-VL on an 8 GB RTX 4060), scored vs the
published-values golden:

| sheet | density | bearing recall | distance recall |
|-------|---------|----------------|-----------------|
| county_test (Boise) | dense (48 brg) | 36/48 (75%) | 51/71 (72%) |
| adams_prc24_12 | dense (27 brg) | 23/27 (85%) | 61/89 (69%) |
| adams_prc2025 | small (15 brg) | 15/15 (100%) | 12/23 (52%) |
| adams_wolfcreek | tiny (4 brg) | 4/4 (100%) | 9/9 (100%) |

Two findings: (1) **bearings read robustly** on a clean vector render — 75–85% on
dense sheets, 100% on simple ones; the `--tile 1100` lever holds across all four
(it ~doubled county_test bearings 40%→75% vs the 2200px→1536 baseline). (2)
**distances also read well (52–100%), and the reader was being under-credited by a
golden bug.** Wolf Creek's text layer is a structured `Segment#/Course/Length/
North/East` listing, not inline labels; `vector_golden.py` was (a) *rejecting* the
real `Length:` values as label-prefixed and (b) *fragmenting* comma coordinates
(`North: 702,613.8799`) into fake small distances — so the golden held coordinate
debris instead of legs. Fixed (comma-aware decimals that fail the range gate;
coordinate-label exclusion that still keeps `Length:`): Wolf Creek distance recall
jumped 6/18 → **9/9**, with no change to the inline-format sheets. Lesson: when a
recall number looks bad, audit the golden before blaming the reader. The remaining
genuine gap is `adams_prc2025` (52%) — small sheet, a few tiny values the 7B misses.

Goldens: `goldens/{county_test.key_p0,adams_prc24_12.key_p42,adams_prc2025.key_p1,adams_wolfcreek.key_p19}.json`.
Source images URL-only (Adams County = public record, not public domain) —
re-fetch + render locally via the manifest URLs; only the numeric keys are committed.

---

## Local model size — 7B vs 32B (R-MODEL)
Same blind instrument, same tiles (`--tile 1100`, `--max-side 1536`), two local
models: **Qwen2.5-VL-7B Q4_K_M on an 8 GB RTX 4060** vs **Qwen2.5-VL-32B Q4_K_M on
a 32 GB M1 Max** (llama.cpp Metal). The 32B does not fit the 4060's 8 GB at all —
the Mac's unified memory is what makes the comparison possible.

| sheet | metric | 7B (4060) | 32B (M1 Max) |
|-------|--------|-----------|--------------|
| county_test | bearings | 36/48 (75%) | 42/48 (88%) |
| county_test | distances | 51/71 (72%) | 61/71 (86%) |
| adams_prc24_12 | bearings | 23/27 (85%) | 24/27 (89%) |
| adams_prc24_12 | distances | 61/89 (69%) | 73/89 (82%) |

The 32B lifts recall on both sheets, **most on distances (+10, +12 pts)** — the axis
the 7B was weakest on — and on bearings where the 7B had headroom (county +13 pts;
adams was already at 85%). Cost: **~27 s/tile vs ~2–4 s** (~7× slower; ~20 min vs
~3 min per sheet). Operative takeaway: 7B for fast corpus sweeps, 32B for a
high-accuracy pass on a sheet that matters. (Reads are the blind instrument's, not
committed per-sheet here; regenerate via the manifest + the local server of choice.)

---

## Resolution sweep — 7B vs 32B (R-RES)
Overnight matrix: both local models read the SAME tiles of all 4 vector-golden
sheets at a max-side ladder (1100→512 px), scored vs the published goldens. 7B
Q4_K_M on the 8 GB RTX 4060; 32B Q4_K_M on the 32 GB M1 Max (llama.cpp Metal).
Recall below is POOLED across the 4 sheets (94 bearings, 192 distances total).

| max-side | 7B bearings | 32B bearings | 7B distances | 32B distances |
|----------|-------------|--------------|--------------|---------------|
| 1100 | 78/94 (83%) | 85/94 (90%) | 133/192 (69%) | 160/192 (83%) |
| 1024 | 79/94 (84%) | 84/94 (89%) | 133/192 (69%) | 156/192 (81%) |
|  896 | 78/94 (83%) | 88/94 (94%) | 144/192 (75%) | 157/192 (82%) |
|  768 | 77/94 (82%) | 87/94 (93%) | 134/192 (70%) | 160/192 (83%) |
|  640 | 77/94 (82%) | 84/94 (89%) | 133/192 (69%) | 160/192 (83%) |
|  512 | 70/94 (74%) | 84/94 (89%) | 119/192 (62%) | 157/192 (82%) |

Findings:
1. **The 32B leads at every resolution, on both axes** — biggest on distances
   (~+13–20 pts), the 7B's weak axis. Its full-res edge (R-MODEL) is not a
   resolution artifact; it holds all the way down the ladder.
2. **The 32B is resolution-robust to 512 px** — pooled recall barely moves from
   1100→512 (bearings 90%→89%, distances 83%→82%). The 7B has a cliff at 512
   (bearings 83%→74%, distances 69%→62%): same "small-glyph cliff" the synthetic
   sweep showed, but the bigger model rides through it.
3. **Neither model gains from >~896 px.** Both are flat from 768–1100 (the 7B's
   distance recall actually peaks at 896). 1100 px buys nothing over ~768–896 and
   costs the most time → ~768–896 px is the efficient operating point.
4. **Cost** (whole 24-read matrix): 7B **37 min** total; 32B **302 min** (~8×).
   The 32B scales with resolution (1100: 4244 s/row → 512: 1831 s/row), so
   dropping 1100→768 trims ~40% off the 32B with no recall loss.

Operative guidance: 32B at max-side ~768 for the best accuracy/throughput; 7B at
≥640 for fast corpus sweeps (avoid 512 on the 7B). Reads archived under
`~/overnight-{vlm,7b}/results/reads/` (regenerable; not committed).

---

## Net
Three independent real-scan points revise the synthetic "cliff at 7–8 px" to:
**reading degrades gracefully with resolution; the cliff appears only under
combined image-quality loss, and that corner is also where the fail-safe
weakens.** The operative deployment variables are glyph SNR/sharpness and
crop-to-content presentation, with a confidence gate for the low-DPI∩low-SNR
corner. What plat2json's *geometry* recovers is scored separately (`score.py`);
this file is the *reading* (label) reliability envelope.

### Honest caveats
- Small label counts per figure → recall %s are coarse (±~5 pt).
- Synthetic/added degradation is hand-tuned (INTER_AREA + blur/noise/JPEG), not a
  calibrated scanner MTF or a physical flatbed/phone capture.
- Some goldens are self-referential (clean read / geometry); the field-note and
  vector-text keys are the independent ones.

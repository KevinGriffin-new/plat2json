# plat2json / eval — reading-reliability harness

`plat2json` turns a survey-plat PDF/image into plan-JSON geometry. **This
sub-utility answers the next question:** on real and degraded plans, how
reliably is the survey data actually recovered — and *where does it break?* It
scores both the geometry plat2json extracts and the (separate) blind label-read
layer, against **license-free goldens**, so no CAD license is ever needed to
know the answer.

The committed `reads/` + `goldens/` are the **auditable record** — every number
in `results/RESULTS.md` can be checked by reading them directly. (Wiring the
scorers to consume those fixtures in-place, so they re-run with no working dir,
is a small natural next step toward a turnkey regression harness.)

## What it measures (the axes)
- **Geometry recall** — do plat2json's recovered legs match the plan's shape?
  (`score/score.py`: closure, length-recall with ratio-vote scale, shape profile.)
- **Label-read reliability** — can a blind reader transcribe the printed
  bearings/distances, and how does that degrade with scan quality? (`score/score_run.py`,
  `score_level.py`; see `results/RESULTS.md`.)
- **Failure mode** — does it fail SAFE (omit / mark `?`) or hallucinate? The
  strict no-guess `read_prompt.txt` engineers the safe mode; the results show
  exactly where it weakens.

## Headline finding (`results/RESULTS.md`)
A synthetic degradation sweep suggested "reading cliffs at ~7–8 px glyph
height." Three genuine-scan tests (1892–2025, public domain) **revise that**:
reading degrades *gracefully* with resolution alone — high-entropy bearings hold
~90% down to 7 px — and the cliff appears only under combined image-quality loss
(blur/noise/compression), which is also where the fail-safe weakens. The
operative variables are glyph **SNR/sharpness** and **crop-to-content**
presentation, not DPI. A high-entropy 1892 mineral-survey read is validated for
free by the claim's own geometry (`56°09' + 33°51' = 90°00'`, no answer key).

## Layout
```
eval/
  manifest.json        # corpus: each plan's public URL, license, redistribution flag, golden type
  read_prompt.txt      # the fixed blind-read instrument (strict no-guess)
  harness/
    acquire.py         # fetch + prep a source from its public URL
    prep_plan.py       # render -> ink-crop -> tile -> run plat2json
    locate_fieldnotes.py  ocr_fieldnotes.py   # build a field-note course key
    merge_lines.py     # Hough-fragment consolidation (geometry side)
  score/
    score.py           # geometry: closure / length-recall / shape profile
    score_run.py       # labels: self-check + recall vs a --gt key
    score_level.py     # degradation-ladder scorer
    score_county.py    # scan-robustness scorer
  goldens/             # SMALL license-free keys (facts only, no images)
  reads/               # committed blind-read fixtures (results reproduce offline)
  results/
    RESULTS.md         # the findings (R1, R-CLIFF, R-MS, the curves)
    corpus_sources.md  # where to find scanned plans + a golden
  _sources/            # gitignored working dir (downloads, renders, tiles)
```

## Reproduce
The `reads/` + `goldens/` fixtures already document every result — read them to
audit the numbers. To re-run the scorers, regenerate the per-plan working dir
(`_sources/<slug>/`) from the public source, since the scorers operate on that
layout:
```bash
pip install -r requirements.txt          # SYSTEM python (fitz/cv2/numpy/PIL/skimage)
python harness/acquire.py albany/t28nr71w        # fetch + prep from the public URL
#   then issue read_prompt.txt to any capable VLM, BLIND, one tile at a time, and
#   save the union of JSON arrays to _sources/<slug>/_vlm_reads.json
#   (the committed reads/ copies are the reference), then:
python score/score_run.py glo_t28nr71w
```
The blind read is done by an **external VLM** (any capable model) fed
`read_prompt.txt` — no model SDK ships here. Blindness (the reader never sees a
key) is what makes the recall numbers trustworthy.

## How goldens are established (and why they're trustworthy)
Source from an authoritative origin, re-derive each value by a second
independent method, and verify **blind**. Menu of license-free goldens:
a vector text layer, OCR of bundled field notes, a self-printed curve/line
table, or — strongest for high-entropy values — the survey's own **geometric
invariants** (a lode claim's sides are perpendicular to its ends; a traverse must
close). A value forced to satisfy an exact structural constraint verifies itself.

## Redistribution rule (what's committed vs URL-only)
This repo is MIT/public, so committing = republishing. Copyright protects
**expression, not facts** (Feist; for boundary surveys, merger doctrine too).
- **Committed (facts, any source):** numeric goldens, blind-read fixtures,
  plat2json geometry traces.
- **Gated (expression):** the source raster/image — committed only for
  public-domain (US-federal) sources; otherwise URL-only, re-fetched locally.
- **Never:** a pictorial redraw of a copyrighted sheet.
Per-plan flags live in `manifest.json`. *Not legal advice; map/survey copyright
is murky — the conservative default is "don't republish the source image."*

## Status
Experimental, like plat2json itself. The corpus is small and the degradation is
hand-tuned, not a calibrated scanner. It is enough to show the reading-reliability
*shape* and to serve as a regression net — not a survey-grade benchmark yet.
Contributions of flat-URL high-entropy scanned plats (with a license-free golden)
are especially welcome.

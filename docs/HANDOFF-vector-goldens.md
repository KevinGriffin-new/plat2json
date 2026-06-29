# Handoff — vector-PDF goldens (next build)

Status as of 2026-06-28. Picking this up after a machine restart on workstation-lewis.

## TL;DR

Field-note recall doesn't scale (1 clean golden out of 7 random townships). The
next build is **vector-PDF goldens**: modern county-recorder final plats are
vector PDFs whose **text layer is the surveyor's exact published bearings and
distances** — a perfect golden with no OCR and no scope guessing. Build a small
pipeline to harvest those, score the reader against them, and grow a clean recall
corpus.

## Why we're pivoting (the field-note finding)

We extended to 6 more WY BLM county plats and tried to auto-build field-note
goldens (`curate_goldens.py`). Result: **only `t28nr71w` yielded a clean,
read-agreeing golden** (14/26 bearings, 7/12 distances vs an independent tesseract
OCR). The other six failed for three reasons, none fixable by better selection:

1. **Handwritten** field notes (pre-~1970) — tesseract can't read cursive.
2. **OCR variance** even on modern scans — same tool gave 2 bearings on one file,
   133 on another.
3. **Scope mismatch** — the plat *sheet* shows a few courses; the field-note
   *file* often records the whole township. They can't agree.

See `eval/harness/_sources/_corpus_curation.json` for the per-township verdicts.
Conclusion: keep field notes only for rare aligned townships like `t28nr71w`.

## The vector-golden pipeline to build

The reader must stay **blind** — it reads the *rendered raster*, never the text
layer. The text layer is used only to build the golden, separately.

1. **`vector_golden.py`** (new): given a vector plat PDF —
   - find the page where `page.get_text()` returns substantial text (BLM scans
     return 0 chars on plat pages; vector plats return the real course text);
   - parse that text for DMS quadrant bearings and decimal distances (reuse the
     `dms()` / `num()` parsers in `eval/score/score_run.py`) → write
     `{ "bearings_az": [...], "distances_m": [...] }` as the golden;
   - render the same page to a raster and tile it (reuse `prep_plan.py`), staging
     a `_sources/<slug>/` like `acquire.py` does — but **do not** pass the text
     layer onward.
2. **Read**: `python eval/harness/vlm_read.py <slug> --workers 1 --max-side 1536 --prompt-file eval/read_prompt_local.txt`
3. **Score**: `python eval/score/score_run.py <slug> --gt _sources/<slug>/vector_key.json`
4. Collect a recall curve across several sheets.

### Where to find vector plats
- County recorder portals that publish final plats as vector PDFs. One is already
  in `eval/manifest.json` as `county_test` (Boise County, ID):
  `https://www.boisecounty.us/wp-content/uploads/2025/09/Exh-13-Final-Plat.pdf`.
- Look for more county recorders with "final plat" PDF downloads (modern, vector).
- **Redistribution:** public-record ≠ public-domain. Commit only the numeric
  golden (facts), never the source image, unless it's US-federal PD. See the rule
  in `eval/manifest.json`.

## After-restart checklist (workstation-lewis)

The box has no systemd service for the model — it must be relaunched.

```bash
cd ~/plat2json
git fetch origin && git checkout main && git pull        # local clone was on the deleted feature branch
source .venv/bin/activate

# relaunch the VL server (model cached at ~/models/qwen2.5-vl-7b/, loads offline)
pkill -f llama-server; sleep 2
nohup bash serve_vl7.sh >/dev/null 2>&1 </dev/null &
sleep 25 && curl -s localhost:8080/v1/models | grep -o '"id":"[^"]*"'   # expect the VL model

# recreate the score<->harness sources bridge if missing
[ -e eval/score/_sources ] || ln -s ../harness/_sources eval/score/_sources
```

Tesseract 5.5.0 + pytesseract are installed (only needed for the field-note path,
not the vector path). Stable read config on the 8 GB 4060: 7B Q4_K_M,
`serve_vl7.sh` at `-c 4096`, client `--workers 1 --max-side 1536` — image-decode
activations are the binding VRAM constraint, so don't raise context.

## Current state
- Reader merged to `main`; demo live at
  https://kevingriffin-new.github.io/plat2json/plat-reader-demo.html
- 7 BLM sheets staged under `eval/harness/_sources/glo_*`; 1 clean golden
  (`t28nr71w`), 6 excluded (curation JSON above).
- Cleanup TODO: remove `cand_key.json` and `fieldnotes.pdf` from the excluded
  `_sources/glo_*` dirs (curator temp files).

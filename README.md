# plat2json

Extract drawable geometry from a **vector survey-plat PDF** into a `plan-JSON`
that Open CAD Studio's LandSurvey plugin (`LS_IMPORTPLAN`) — or any CAD — can
draw. One extraction, many sinks (OpenCAD / MicroSurvey / Civil 3D / FreeCAD).

**▶ [Live demo](https://kevingriffin-new.github.io/plat2json/plat-reader-demo.html)** — watch a local vision model trace a plat, read its rotated bearings and distances, and close the traverse.

> **Status: experimental / work-in-progress.** It produces a rough geometry
> *skeleton*, not survey-grade output. Read [STATUS.md](STATUS.md) before relying
> on it. Honest summary:
> - **Geometry** comes out as many short Hough segments (fragment-soup — ~100+
>   segments for ~10 real edges); needs **skeleton path-tracing** to clean up.
> - **Arcs** aren't fitted by the main pass. `fit_arcs.py` fits them from the
>   geometry; `arc_refine.py` snaps them to a human-read curve table.
> - **Labels** (bearings, distances) are now read by a **local vision model**
>   (Qwen2.5-VL via llama.cpp) — 13/21 bearings and 7/9 distances on the first
>   real sheet, cross-checked against the field notes. See
>   [LOCAL_VLM_READER.md](LOCAL_VLM_READER.md). Classical OCR (six approaches in
>   `experiments/`) is what *failed* here; STATUS.md keeps that history. Curve-table
>   values (`r=`/`a=`) aren't read yet — next.

## Why this exists

Survey plats are vector PDFs with linework flattened to line segments, text as
stroked glyphs (no text layer), and no arc primitives. The authoritative geometry
lives in the published bearings / distances / curve table — so the hard part is
*reading stroked labels*, not tracing lines. This repo is the staging ground for
that work, kept separate from any one CAD plugin on purpose.

## Install

    pip install -r requirements.txt

Core pipeline needs PyMuPDF, OpenCV, NumPy, scikit-image. The `experiments/`
OCR scripts also need `pytesseract` (+ a system Tesseract install) and/or
`easyocr` (pulls PyTorch — large).

## Usage

    python plat2json.py INPUT.pdf OUTPUT.json [--dpi 300] [--plot-scale 250]
    python fit_arcs.py        # geometry plan-JSON -> arcs   (edit IN/OUT at top)
    python arc_refine.py      # snap arcs to a published curve table

## plan-JSON schema (the interchange contract)

    {
      "lines":   [[x1, y1, x2, y2, "LAYER"], ...],
      "arcs":    [[cx, cy, radius, start_deg, end_deg, "LAYER"], ...],
      "circles": [[cx, cy, radius, "LAYER"], ...],
      "texts":   [[x, y, "string", "STYLE"], ...]
    }

Coordinates are world metres, north-up — matching `LS_IMPORTPLAN`.

## Layout

- `plat2json.py` — main CLI: PDF -> geometry plan-JSON
- `fit_arcs.py`, `arc_refine.py` — arc fitting / curve-table refinement on a plan-JSON
- `experiments/` — WIP label-OCR research stages (Tesseract baseline, cv2
  morphology, EasyOCR/CRAFT, vector + geometry-guided deskew). See STATUS.md.
- `eval/harness/vlm_read.py` + `serve_vl7.sh` + `LOCAL_VLM_READER.md` — the
  **local-VLM label reader** (the working approach) and its 8 GB tuning journey
- `docs/plat-reader-demo.html` — the interactive teaching demo (live link above)
- `STATUS.md` — honest pipeline status, findings per iteration, and next steps

## Next step

Highest-leverage fix: replace the Hough vectorization with **skeleton
path-tracing** (ordered per-curve polylines) so geometry is clean and arc-fitting
becomes reliable — then tackle label OCR. Details in STATUS.md.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with any survey authority or CAD
vendor; for use with plats you have the right to process.

# plat2json

Extract drawable geometry from a **vector survey-plat PDF** into a `plan-JSON`
that Open CAD Studio's LandSurvey plugin (`LS_IMPORTPLAN`) — or any CAD — can
draw. One extraction, many sinks (OpenCAD / MicroSurvey / Civil 3D / FreeCAD).

**▶ [Live demo](https://kevingriffin-new.github.io/plat2json/plat-reader-demo.html)** — watch a local vision model trace a plat, read its rotated bearings and distances, and close the traverse.

**▶ [Try it in your browser](https://kevingriffin-new.github.io/plat2json/try.html)** — drop one vector plat PDF, get plan-JSON and a DXF back. Runs in the tab (Pyodide); nothing is uploaded.

**▶ [Plat parsing theatre](https://kevingriffin-new.github.io/plat2json/plat-parsing-animation.html)** — interactive Three.js animation of plan ingestion, association, COGO closure, and JSON export. It can record its own 20-second WebM; see [`docs/RECORDING.md`](docs/RECORDING.md).

**▶ [Subdivision parsing theatre](https://kevingriffin-new.github.io/plat2json/subdivision-parsing-animation.html)** — slower, overhead Three.js walkthrough of a dense subdivision sheet. It pauses at seven search and categorization checks before reporting closure evidence.

**▶ [Area Thirty3 parsing study](https://kevingriffin-new.github.io/plat2json/area-thirty3-parsing-animation.html)** — source-backed walkthrough based on the Area Thirty3 Estates final plat: roadway detection, curve-table association, curved frontage, and the closure/area gate.

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
    python cogo_assemble.py --key sheet_assoc_key.json --reads sheet_assoc_reads.json \
        --links controls.json --out sheet_plan.json
    python plan2dxf.py        IN.json OUT.dxf

## plan-JSON schema (the interchange contract)

    {
      "lines":     [[x1, y1, x2, y2, "LAYER"], ...],
      "arcs":      [[cx, cy, radius, start_deg, end_deg, "LAYER"], ...],
      "circles":   [[cx, cy, radius, "LAYER"], ...],
      "texts":     [[x, y, "string", "STYLE"], ...],
      "polylines": [[[x, y], [x, y, bulge], ..., "LAYER"], ...]
    }

Coordinates are world metres, north-up — matching `LS_IMPORTPLAN`.

`polylines` (optional) are ordered vertex chains; a polyline-aware importer
turns each into one polyline entity. A vertex may carry a third element, the
**bulge** of the segment *starting at that vertex*: `bulge = tan(sweep/4)`,
CCW positive (the AutoCAD LWPolyline convention). Two-element vertices are
straight segments.

**Duplicate contract:** for sinks without polyline support, chains are also
restated flattened — straight edges into `lines` and bulged segments into
`arcs` — using the *same rounded values*, so a polyline-aware importer can
recognize and skip those duplicates. Importers without polyline support just
ignore `polylines` and draw `lines`/`arcs` as before.


## Export to DXF

    python plan2dxf.py INPUT.json OUTPUT.dxf

Writes an AutoCAD R2013 DXF of the plan-JSON geometry. `$INSUNITS` is metres
by default (the v1 contract) and follows the plan's `unit` when that is `ft`
(or `mm` / `in`). Layers are the JSON layer names, plus two v2 extras:

| Layer | Contents |
|---|---|
| (JSON names, e.g. `PROPERTY_LINE`) | linework, arcs, circles, bulge `LWPOLYLINE`s |
| `0` | v1 `texts` (the contract gives them a STYLE, not a layer) |
| `ANNOTATION` | v2 course labels (bearing + distance), aligned to the segment |
| `FLAGS` | flagged courses (segment overlay + midpoint mark); rings that fail the same misclosure test as `cogo_assemble.py` when `report.needs_human` |

Default ACI colors live in `DEFAULT_LAYER_TABLE` in `plan2dxf.py` and are easy
to change; there is no client layer-standard remapping yet.

When `polylines` are present they are preferred, and matching restated `lines`
/ `arcs` are skipped (the v1 duplicate contract). Pass `--no-prefer-polylines`
to emit every list.

**This is a drawing of the recovered skeleton, not survey-grade output.**

## Vector-only extraction (and the browser sample)

    python vector_plan.py PLAT.pdf OUT.json [--page 1] [--plot-scale 250] [--dxf OUT.dxf]

For vector PDFs, `vector_plan.py` skips rendering entirely: it reads the page's
own drawing paths and text layer (PyMuPDF), turns circular Bezier curves into
bulge arcs, closed arc paths into monument circles, drops the sheet frame and
glyph-sized paths, and harvests bearings / distances from the text layer with
the `vector_golden.py` rules into a `labels` block. No OpenCV, no VLM. Scanned
or image-only pages are detected (`looks_scanned`) instead of returning an empty
drawing; they need the vision reader.

[`docs/try.html`](docs/try.html) runs the same `vector_plan.py` + `plan2dxf.py`
in the browser under Pyodide 0.29.5 (the newest release that ships PyMuPDF),
inside a Web Worker. One page, 20 MB cap, 60 s timeout; the PDF never leaves
the tab. A GitHub Pages workflow (`.github/workflows/pages.yml`) assembles
`docs/` plus `vector_plan.py`, `plan2dxf.py`, and `eval/harness/vector_golden.py`
so the page loads them same-origin. If that 404s (for example while Pages is
still pointed at the branch source), it falls back to jsDelivr pinned to a
commit SHA, not `@main`. After merging, set Settings → Pages → Source to
"GitHub Actions" once. From a checkout, `python -m http.server` at the repo
root (open `/docs/try.html`) still works: the worker looks one directory up.
`docs/samples/sample-vector-plat.pdf` is a synthetic sample made by
`docs/samples/make_sample.py`.

Limits: it keeps all sheet linework (title block, vicinity map, leaders), not
just the parcel; labels are listed, not bound to edges; stroked-glyph labels
(no text layer) are not read. A rough skeleton, not survey-grade.

## Layout

- `plat2json.py` — main CLI: PDF -> geometry plan-JSON
- `plan2dxf.py` — plan-JSON -> DXF (v1 geometry and v2 course labels)
- `vector_plan.py` — vector PDF -> plan-JSON from its own paths + text layer (no raster, no VLM)
- `docs/try.html` + `docs/try-worker.js` — the in-browser sample (Pyodide)
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

### Optional link-traverse verification

`cogo_assemble.py --links controls.json` forward-computes specified course
paths between independently known control coordinates. It reports endpoint
residuals, linear misclosure, and relative precision in the output's `links`
block. It is diagnostic only: it never Bowditch-adjusts detected courses or
clears their flags.

```json
{
 "links": [{
  "id": "west-boundary",
  "start_EN": [500000.0, 5450000.0],
  "end_EN": [500120.0, 5450060.0],
  "segs": [{"edge": 12, "forward": true}, {"edge": 13, "forward": false}]
 }]
}
```

Every named edge must have exactly one complete straight bearing/distance
course. Coordinates must be independent control in the same bearing basis and
units as the courses; geometry-derived endpoints do not make a valid link
check. Curve-only legs are intentionally reported as `not_evaluable`.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with any survey authority or CAD
vendor; for use with plats you have the right to process.

#!/usr/bin/env python3
"""prep_plan.py - stage one plan for a blind plan-reading run (R1 + R2).

Stages one plan for a blind plan-reading run in one command:
  render/load -> ink-crop -> overlapping tiles -> plat2json geometry ->
  overview + a _readplan.json the next session iterates with blind subagents.

Run with the SYSTEM python (has fitz, cv2, numpy, PIL, skimage):
    python prep_plan.py <input.pdf|.png|.jpg> <slug> [--scale 250] [--dpi 300]
                        [--tile 2200] [--overlap 350] [--page 0]

Outputs under _sources/<slug>/ (gitignored):
    tiles/tile_rNcM.png        legible tiles (overlap reduces seam-split labels)
    _overview.png              downscaled, for human tile-targeting
    _plan_plat2json.json       geometry segment bag (shape side, R1)
    _readplan.json             {tiles, plat2json, scale, read_prompt} for R2

Then (next session): spawn one BLIND general-purpose subagent per tile with
read_prompt (none must see any answer key), collect each JSON array, save the
union to _sources/<slug>/_vlm_reads.json, and run score_run.py.
"""
import argparse, json, math, os, subprocess, sys

PLAT2JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plat2json.py")
READ_PROMPT = (
    "Read the image at {tile}\n\nIt is a tile cropped from a survey plan. "
    "Transcribe EVERY dimension annotation visible: (1) bearings/azimuths like "
    "`156° 48' 05\"` (DMS; sometimes only degrees or split fragments), and "
    "(2) distances as plain decimals like `23.76` (metres). Some labels are "
    "ROTATED along survey lines - read them at any orientation.\n\nRules: "
    "transcribe EXACTLY what is printed; do NOT compute/infer/normalize/guess; "
    "illegible glyph -> '?'. IGNORE title block, plan numbers, hectare areas, "
    "road/river names, legend/notes.\n\nReturn ONLY a JSON array; each item "
    '{{"raw":"<exact text>","kind":"bearing"|"distance","rotated":true|false}}. '
    "No prose, no markdown fence."
)


def load_gray(path, dpi, page):
    import numpy as np
    if path.lower().endswith(".pdf"):
        import fitz
        pix = fitz.open(path)[page].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    from PIL import Image
    return np.asarray(Image.open(path).convert("L"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input"); ap.add_argument("slug")
    ap.add_argument("--scale", type=float, default=250.0)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--tile", type=int, default=2200)
    ap.add_argument("--overlap", type=int, default=350)
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--tiles-only", action="store_true",
                    help="skip plat2json (run it separately on a downsampled copy)")
    a = ap.parse_args()
    import numpy as np
    from PIL import Image

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sources", a.slug)
    tdir = os.path.join(out, "tiles"); os.makedirs(tdir, exist_ok=True)

    g = load_gray(a.input, a.dpi, a.page)
    ink = g < 200
    ys, xs = np.where(ink)
    if len(xs) == 0:
        sys.exit("blank page")
    m = 40
    x0, y0 = max(0, xs.min() - m), max(0, ys.min() - m)
    x1, y1 = min(g.shape[1], xs.max() + m), min(g.shape[0], ys.max() + m)
    img = Image.fromarray(g[y0:y1, x0:x1]); W, H = img.size
    print(f"ink-cropped to {W}x{H}")

    ov = img.copy(); ov.thumbnail((1000, 1600)); ov.save(os.path.join(out, "_overview.png"))

    step = a.tile - a.overlap
    cols = max(1, math.ceil((W - a.overlap) / step))
    rows = max(1, math.ceil((H - a.overlap) / step))
    tiles = []
    for r in range(rows):
        for c in range(cols):
            cx0, cy0 = c * step, r * step
            cx1, cy1 = min(W, cx0 + a.tile), min(H, cy0 + a.tile)
            if (cx1 - cx0) < 400 or (cy1 - cy0) < 400:
                continue  # sliver
            crop = img.crop((cx0, cy0, cx1, cy1))
            if float((np.asarray(crop) < 200).mean()) < 0.002:
                continue  # near-blank
            fn = os.path.join(tdir, f"tile_r{r}c{c}.png"); crop.save(fn)
            tiles.append(os.path.abspath(fn))
    print(f"grid {rows}x{cols}, kept {len(tiles)} tiles (overlap {a.overlap}px)")

    plat = None if a.tiles_only else os.path.join(out, "_plan_plat2json.json")
    if not a.tiles_only:
        try:
            r = subprocess.run([sys.executable, PLAT2JSON, a.input, plat,
                                "--plot-scale", str(a.scale)],
                               capture_output=True, text=True, timeout=600)
            print("plat2json:", (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "ok")
        except Exception as e:  # noqa: BLE001
            print("plat2json FAILED:", e); plat = None

    readplan = {"slug": a.slug, "input": os.path.abspath(a.input),
                "scale": a.scale, "tiles": tiles, "plat2json": plat,
                "read_prompt_template": READ_PROMPT,
                "next": "spawn 1 BLIND general-purpose subagent per tile with "
                        "read_prompt_template.format(tile=path); save union to "
                        "_vlm_reads.json; then python score_run.py " + a.slug}
    with open(os.path.join(out, "_readplan.json"), "w", encoding="utf-8") as fh:
        json.dump(readplan, fh, indent=1)
    print(f"-> {os.path.join(out, '_readplan.json')}")


if __name__ == "__main__":
    main()

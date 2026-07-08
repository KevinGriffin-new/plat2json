#!/usr/bin/env python3
"""assoc_study.py - label->segment ASSOCIATION study (geometry-guided crops).

The reading problem is solved (see eval/results/RESULTS.md); the open problem is
joining read values to the segment each one describes. This study measures that
join directly, on vector NCDOT sheets where the truth is free:

  golden side  : the PDF's own vector content. Text spans give every label WITH
                 position + rotation; drawings give exact segments. Binding each
                 label to its nearest parallel overlapping segment = an
                 association answer key (no OCR, no human).
  reader side  : render the page to pixels (reader stays BLIND to the vectors),
                 cut ONE oriented crop per segment (band along the segment,
                 rotated so the segment is horizontal, drafting convention keeps
                 the text upright), ask the VLM for the labels that belong to
                 the central line, and score the per-segment reads against the
                 golden bindings.

Also emits _vlm_reads.json (the union of all per-segment reads) plus a vector
_plan_plat2json.json so the standard score_run.py pooled recall is comparable
with the tile baselines (ncdot_summary.tsv / dense_summary.tsv).

Pilot honesty caveats (logged, not hidden):
  * crops are cut for golden-LABELED segments plus a sampled unlabeled control
    set (--unlabeled), so "which segments carry labels" leaks into crop
    selection. Binding recall is unaffected; precision is estimated from the
    unlabeled control crops' spurious-emission rate.
  * line/curve-table rows (L12 / C3 tags) are excluded from binding - table
    association is tag-based and trivially solved downstream, not spatial.

usage (venv python; fitz + cv2 + Pillow):
  assoc_study.py <pdf> <page0idx> <slug> [--phase all|stage|read|score]
      [--dpi 400] [--band 5.0] [--min-len 40] [--max-segs 0] [--unlabeled 1.0]
      [--url http://127.0.0.1:8080] [--workers 1] [--max-side 1152]
      [--gt <committed key.json>]   (only echoed into the score json for audit)

writes under eval/harness/_sources/<slug>/:
  plat.png  _assoc_key.json  crops/seg_NNNN_wN.png  _assoc_reads.json
  _vlm_reads.json  _plan_plat2json.json  _assoc_score.json
"""
import argparse
import json
import math
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "score")))

import vector_golden as vg          # BEARING_RE + distance-context filters
import vlm_read                     # read_tile / extract_array (the fixed instrument)
import score_run                    # dms / dms_cands / num (matching rules)

TABLE_TAG_RE = re.compile(r"^\s*[LC]\s?\d+\b")   # line/curve table row tag
STATION_PREFIX_RE = re.compile(r"\+\s?$")        # '10+08.58' / '+43.94' station
OFFSET_SUFFIX_RE = re.compile(r"^\s*['′]?\s*[LR]T\b", re.IGNORECASE)  # 50.00' LT
ANGLE_TOL_DEG = 20.0     # label dir vs segment dir (mod 180)
PROJ_LO, PROJ_HI = -0.15, 1.15   # label center projection along the segment


# ---------------------------------------------------------------- golden side

def _page_labels(page):
    """Bearing/distance labels from the text layer, with center/angle/size.
    One fitz 'line' (a text run) may hold both the bearing and the distance."""
    labels = []
    d = page.get_text("dict")
    for blk in d.get("blocks", []):
        for ln in blk.get("lines", []):
            text = "".join(s["text"] for s in ln.get("spans", []))
            if not text.strip():
                continue
            x0, y0, x1, y1 = ln["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            dx, dy = ln.get("dir", (1, 0))
            ang = math.degrees(math.atan2(dy, dx))
            size = max((s.get("size", 0) for s in ln.get("spans", [])), default=0)
            table = bool(TABLE_TAG_RE.match(text))
            for m in vg.BEARING_RE.finditer(text):
                labels.append({"raw": vg.norm_bearing(m), "kind": "bearing",
                               "cx": cx, "cy": cy, "angle": ang, "size": size,
                               "table": table})
            clean = vg.BEARING_RE.sub(" ", text)
            for m in vg.DECIMAL_RE.finditer(clean):
                pre = clean[max(0, m.start() - 8):m.start()]
                post = clean[m.end():m.end() + 8]
                if (vg.EQUALS_PREFIX_RE.search(pre) or vg.COORD_PREFIX_RE.search(pre)
                        or vg.CURVE_WORD_RE.search(pre)
                        or STATION_PREFIX_RE.search(pre)):
                    continue
                if vg.AREA_SUFFIX_RE.match(post) or vg.INCH_SUFFIX_RE.match(post) \
                        or OFFSET_SUFFIX_RE.match(post):
                    continue
                v = float(m.group().replace(",", ""))
                if vg.DIST_MIN <= v <= vg.DIST_MAX:
                    labels.append({"raw": m.group(), "kind": "distance",
                                   "cx": cx, "cy": cy, "angle": ang, "size": size,
                                   "table": table})
    return labels


ANG_BIN, RHO_BIN = 0.75, 1.0     # collinearity hash bins (deg, pt)


def _page_segments(page, min_len, gap=8.0):
    """Line segments from the vector drawings, CHAINED. CAD plan sheets emit
    dashed/patterned linework as tens of thousands of micro-segments (randolph:
    335k 'l' items, median 0.3 pt) - the property/ROW line a bearing labels does
    not exist as one vector. Group micro-segments by their infinite line
    (quantized angle + signed perpendicular offset, union-find over neighbor
    bins), then merge each group's collinear runs across gaps <= `gap` pt
    (dash gaps). Emits chains >= min_len. Page points, y-down."""
    raw = []
    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] == "l":
                p0, p1 = item[1], item[2]
                raw.append((p0.x, p0.y, p1.x, p1.y))
            elif item[0] == "re":
                r = item[1]
                raw += [(r.x0, r.y0, r.x1, r.y0), (r.x1, r.y0, r.x1, r.y1),
                        (r.x1, r.y1, r.x0, r.y1), (r.x0, r.y1, r.x0, r.y0)]
    raw = [s for s in raw if math.hypot(s[2] - s[0], s[3] - s[1]) >= 1.0]

    # hash each segment's infinite line: direction normalized to dx>0 half-plane
    keys, parent = [], list(range(len(raw)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    binmap = {}
    for i, (x0, y0, x1, y1) in enumerate(raw):
        dx, dy = x1 - x0, y1 - y0
        if dx < 0 or (dx == 0 and dy < 0):
            dx, dy = -dx, -dy
        th = math.degrees(math.atan2(dy, dx))          # (-90, 90]
        L = math.hypot(dx, dy)
        nx, ny = -dy / L, dx / L                       # unit normal
        rho = x0 * nx + y0 * ny
        ka, kr = int(round(th / ANG_BIN)), int(round(rho / RHO_BIN))
        keys.append((ka, kr))
        for da in (-1, 0, 1):
            for dr in (-1, 0, 1):
                k = (ka + da, kr + dr)
                if k in binmap:
                    union(i, binmap[k])
        binmap[keys[-1]] = i
    groups = {}
    for i in range(len(raw)):
        groups.setdefault(find(i), []).append(i)

    segs = []
    for members in groups.values():
        # direction from the group's longest member; project all endpoints
        longest = max(members, key=lambda i: math.hypot(
            raw[i][2] - raw[i][0], raw[i][3] - raw[i][1]))
        x0, y0, x1, y1 = raw[longest]
        dx, dy = x1 - x0, y1 - y0
        if dx < 0 or (dx == 0 and dy < 0):
            dx, dy, x0, y0 = -dx, -dy, x1, y1
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        ivals = []
        for i in members:
            a = (raw[i][0] - x0) * ux + (raw[i][1] - y0) * uy
            b = (raw[i][2] - x0) * ux + (raw[i][3] - y0) * uy
            ivals.append((min(a, b), max(a, b)))
        ivals.sort()
        lo, hi = ivals[0]
        runs = []
        for a, b in ivals[1:]:
            if a <= hi + gap:
                hi = max(hi, b)
            else:
                runs.append((lo, hi))
                lo, hi = a, b
        runs.append((lo, hi))
        for a, b in runs:
            if b - a >= min_len:
                segs.append((x0 + a * ux, y0 + a * uy,
                             x0 + b * ux, y0 + b * uy))
    return segs


def _bind(labels, segs):
    """Bind each non-table label to the nearest roughly-parallel segment whose
    span it overlaps. Returns (bindings, unbound); bindings = label + seg id."""
    bound, unbound = [], []
    per_seg = {}
    for lab in labels:
        if lab["table"]:
            unbound.append({**lab, "why": "table_row"})
            continue
        cap = max(12.0, 4.0 * lab["size"])
        best, best_d = None, cap
        for i, (x0, y0, x1, y1) in enumerate(segs):
            sdx, sdy = x1 - x0, y1 - y0
            slen = math.hypot(sdx, sdy)
            sang = math.degrees(math.atan2(sdy, sdx))
            dd = abs(lab["angle"] - sang) % 180
            if min(dd, 180 - dd) > ANGLE_TOL_DEG:
                continue
            t = ((lab["cx"] - x0) * sdx + (lab["cy"] - y0) * sdy) / (slen * slen)
            if not (PROJ_LO <= t <= PROJ_HI):
                continue
            px, py = x0 + t * sdx, y0 + t * sdy
            d = math.hypot(lab["cx"] - px, lab["cy"] - py)
            if d < best_d:
                best_d, best = d, i
        if best is None:
            unbound.append({**lab, "why": "no_segment_in_range"})
        else:
            per_seg.setdefault(best, []).append((best_d, lab))
    for seg_i, cands in per_seg.items():
        x0, y0, x1, y1 = segs[seg_i]
        span = math.hypot(x1 - x0, y1 - y0)
        by_kind = {}
        for d, lab in sorted(cands, key=lambda c: c[0]):
            by_kind.setdefault(lab["kind"], []).append((d, lab))
        for kind, ks in by_kind.items():
            # a short segment attracting a same-kind stack = a table rule or a
            # notes block, not a course line - drop the lot as suspect
            if span < 250 and len(ks) >= 3:
                for d, lab in ks:
                    unbound.append({**lab, "why": f"suspect_table_{seg_i}"})
                continue
            # a long CHAIN legitimately spans several courses -> scale the cap
            cap = max(2, int(span // 150))
            for d, lab in ks[:cap]:
                bound.append({**lab, "seg": seg_i, "perp_pt": round(d, 2)})
            for d, lab in ks[cap:]:
                unbound.append({**lab, "why": f"overfull_segment_{seg_i}"})
    return bound, unbound


# ---------------------------------------------------------------- reader side

def _crop_windows(img, seg_px, band_px, max_w, overlap):
    """Oriented band crops along one segment (page raster, y-down px).
    Rotation normalized to (-90, 90] so drafting-convention text is upright.
    Long bands are windowed at max_w with overlap. Returns list of arrays."""
    import cv2
    import numpy as np
    x0, y0, x1, y1 = seg_px
    ang = math.degrees(math.atan2(y1 - y0, x1 - x0))
    if ang > 90:
        ang -= 180
    elif ang <= -90:
        ang += 180
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    slen = math.hypot(x1 - x0, y1 - y0)
    w = int(slen + 2 * band_px)
    h = int(2 * band_px)
    # rotate a padded ROI (not the whole sheet) around the segment center
    r = int(math.hypot(w, h) / 2) + 4
    rx0, ry0 = max(0, int(cx - r)), max(0, int(cy - r))
    roi = img[ry0:min(img.shape[0], int(cy + r)), rx0:min(img.shape[1], int(cx + r))]
    if roi.size == 0:
        return []
    M = cv2.getRotationMatrix2D((cx - rx0, cy - ry0), ang, 1.0)
    rot = cv2.warpAffine(roi, M, (roi.shape[1], roi.shape[0]),
                         flags=cv2.INTER_CUBIC, borderValue=255)
    bx0 = int(cx - rx0 - w / 2)
    by0 = int(cy - ry0 - h / 2)
    band = rot[max(0, by0):by0 + h, max(0, bx0):bx0 + w]
    if band.size == 0:
        return []
    wins = []
    step = max(1, max_w - overlap)
    for wx in range(0, max(1, band.shape[1] - overlap), step):
        wins.append(band[:, wx:wx + max_w])
        if wx + max_w >= band.shape[1]:
            break
    return wins


def stage(a, base):
    import fitz
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    doc = fitz.open(a.pdf)
    page = doc[a.page]
    labels = _page_labels(page)
    segs = _page_segments(page, a.min_len, a.gap)
    bound, unbound = _bind(labels, segs)
    labeled_ids = sorted({b["seg"] for b in bound})
    unlabeled_ids = [i for i in range(len(segs)) if i not in set(labeled_ids)]
    random.Random(0).shuffle(unlabeled_ids)
    control_ids = sorted(unlabeled_ids[:round(a.unlabeled * len(labeled_ids))])
    if a.max_segs:
        labeled_ids = labeled_ids[:a.max_segs]
        control_ids = control_ids[:a.max_segs]
        keep = set(labeled_ids)
        bound = [b for b in bound if b["seg"] in keep]

    os.makedirs(os.path.join(base, "crops"), exist_ok=True)
    pix = page.get_pixmap(dpi=a.dpi, colorspace=fitz.csGRAY)
    img_pil = Image.frombytes("L", [pix.width, pix.height], pix.samples)
    img_pil.save(os.path.join(base, "plat.png"))
    import numpy as np
    img = np.asarray(img_pil)
    s = a.dpi / 72.0
    glyph_px = max(6.0, (sorted(l["size"] for l in bound)[len(bound) // 2]
                         if bound else 6.0)) * s
    band_px = min(max(a.band * glyph_px, 80), 280)

    n_crops = 0
    for i in labeled_ids + control_ids:
        seg_px = tuple(v * s for v in segs[i])
        for wi, win in enumerate(_crop_windows(img, seg_px, band_px,
                                               a.crop_w, a.crop_overlap)):
            Image.fromarray(win).save(
                os.path.join(base, "crops", f"seg_{i:04d}_w{wi}.png"))
            n_crops += 1

    key = {"pdf": os.path.basename(a.pdf), "page": a.page, "dpi": a.dpi,
           "band_px": round(band_px, 1), "glyph_px": round(glyph_px, 1),
           "segments": [[round(v, 2) for v in sg] for sg in segs],
           "labeled_ids": labeled_ids, "control_ids": control_ids,
           "bindings": bound, "unbound": unbound}
    json.dump(key, open(os.path.join(base, "_assoc_key.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    # vector lines -> the standard plan json, so score_run's self-check works
    json.dump({"lines": [list(sg) for sg in segs], "arcs": [], "circles": [],
               "texts": []},
              open(os.path.join(base, "_plan_plat2json.json"), "w",
                   encoding="utf-8"))
    print(f"[{a.slug}] stage: {len(segs)} segs, {len(labels)} labels -> "
          f"{len(bound)} bound on {len(labeled_ids)} segs "
          f"(+{len(control_ids)} unlabeled controls), {len(unbound)} unbound, "
          f"{n_crops} crops (band {band_px:.0f}px @ {a.dpi}dpi)")


def read(a, base):
    key = json.load(open(os.path.join(base, "_assoc_key.json"), encoding="utf-8"))
    reads_path = os.path.join(base, "_assoc_reads.json")
    reads = json.load(open(reads_path, encoding="utf-8")) \
        if os.path.exists(reads_path) else {}
    prompt = open(a.prompt_file, encoding="utf-8").read()
    crops = sorted(f for f in os.listdir(os.path.join(base, "crops"))
                   if f.endswith(".png"))
    todo = [f for f in crops if f[:-4] not in reads]
    print(f"[{a.slug}] read: {len(todo)}/{len(crops)} crops to go -> {a.url}")
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(vlm_read.read_tile, os.path.join(base, "crops", f),
                          prompt, a.url, "qwen2.5-vl", a.max_side, 0.0, 180): f
                for f in todo}
        done = 0
        for fut in cf.as_completed(futs):
            f = futs[fut]
            try:
                _, items = fut.result()
                reads[f[:-4]] = items
            except Exception as e:  # noqa: BLE001
                print(f"  FAIL {f}: {type(e).__name__}: {e}")
            done += 1
            if done % 25 == 0 or done == len(todo):
                json.dump(reads, open(reads_path, "w", encoding="utf-8"),
                          ensure_ascii=False)
                print(f"  {done}/{len(todo)}")
    json.dump(reads, open(reads_path, "w", encoding="utf-8"), ensure_ascii=False)
    # union -> the standard reads file (per-seg crops replace tiles as the
    # sampling scheme; score_run then gives the pooled-recall comparable row)
    union = [it for items in reads.values() for it in items]
    json.dump(union, open(os.path.join(base, "_vlm_reads.json"), "w",
                          encoding="utf-8"), ensure_ascii=False)
    print(f"[{a.slug}] read: union {len(union)} raw emissions")


def _match(lab, items):
    """Does any read item in this segment's crops match the golden label?"""
    if lab["kind"] == "bearing":
        want = score_run.dms(lab["raw"])
        for it in items:
            for cand in score_run.dms_cands(it["raw"]):
                d = abs(cand - want)
                if min(d % 180, 180 - d % 180) <= 0.05:
                    return True
    else:
        want = float(lab["raw"].replace(",", ""))
        for it in items:
            if it.get("kind") == "bearing":   # num('N 84°04\'46" W') -> 46.0
                continue
            v = score_run.num(it["raw"])
            if v is not None and abs(v - want) <= 0.2:
                return True
    return False


def score(a, base):
    key = json.load(open(os.path.join(base, "_assoc_key.json"), encoding="utf-8"))
    reads = json.load(open(os.path.join(base, "_assoc_reads.json"), encoding="utf-8"))
    by_seg = {}
    for name, items in reads.items():
        seg = int(name.split("_")[1])
        by_seg.setdefault(seg, []).extend(items)
    all_items = [it for items in by_seg.values() for it in items]

    hit = {"bearing": [0, 0], "distance": [0, 0]}
    anywhere = {"bearing": 0, "distance": 0}
    for lab in key["bindings"]:
        k = lab["kind"]
        hit[k][1] += 1
        if _match(lab, by_seg.get(lab["seg"], [])):
            hit[k][0] += 1
        if _match(lab, all_items):
            anywhere[k] += 1
    ctrl = key["control_ids"]
    spurious = sum(1 for s in ctrl
                   if any(score_run.dms(it["raw"]) is not None
                          or score_run.num(it["raw"]) is not None
                          for it in by_seg.get(s, [])))
    nb, nd = hit["bearing"], hit["distance"]
    out = {"binding_bearing": f"{nb[0]}/{nb[1]}",
           "binding_distance": f"{nd[0]}/{nd[1]}",
           "anywhere_bearing": f"{anywhere['bearing']}/{nb[1]}",
           "anywhere_distance": f"{anywhere['distance']}/{nd[1]}",
           "spurious_controls": f"{spurious}/{len(ctrl)}",
           "unbound_labels": len(key["unbound"]),
           "gt": a.gt or ""}
    json.dump(out, open(os.path.join(base, "_assoc_score.json"), "w",
                        encoding="utf-8"), indent=1)
    print(f"[{a.slug}] binding recall: bearings {out['binding_bearing']}, "
          f"distances {out['binding_distance']}")
    print(f"  value-anywhere recall: bearings {out['anywhere_bearing']}, "
          f"distances {out['anywhere_distance']}")
    print(f"  spurious emissions on unlabeled controls: {out['spurious_controls']}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf"); ap.add_argument("page", type=int); ap.add_argument("slug")
    ap.add_argument("--phase", choices=["all", "stage", "read", "score"], default="all")
    ap.add_argument("--dpi", type=int, default=400)
    ap.add_argument("--band", type=float, default=5.0,
                    help="crop half-height in glyph heights (default 5)")
    ap.add_argument("--min-len", type=float, default=40.0,
                    help="ignore chained segments shorter than this (pt)")
    ap.add_argument("--gap", type=float, default=8.0,
                    help="max collinear gap to chain across (pt, dash gaps)")
    ap.add_argument("--max-segs", type=int, default=0,
                    help="cap labeled segments (pilot smoke runs)")
    ap.add_argument("--unlabeled", type=float, default=1.0,
                    help="unlabeled control crops as a ratio of labeled segs")
    ap.add_argument("--crop-w", type=int, default=1100)
    ap.add_argument("--crop-overlap", type=int, default=200)
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--max-side", type=int, default=1152)
    ap.add_argument("--prompt-file",
                    default=os.path.join(HERE, "..", "read_prompt_assoc.txt"))
    ap.add_argument("--gt", default=None,
                    help="committed golden path (echoed into the score json)")
    a = ap.parse_args()
    base = os.path.join(HERE, "_sources", a.slug)
    os.makedirs(base, exist_ok=True)
    if a.phase in ("all", "stage"):
        if os.path.exists(os.path.join(base, "_assoc_key.json")) \
                and os.listdir(os.path.join(base, "crops")):
            print(f"[{a.slug}] already staged")
        else:
            stage(a, base)
    if a.phase in ("all", "read"):
        read(a, base)
    if a.phase in ("all", "score"):
        score(a, base)


if __name__ == "__main__":
    main()

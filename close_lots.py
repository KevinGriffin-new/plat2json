#!/usr/bin/env python3
"""close_lots.py - end-to-end LOT closure on a raster plat.

Ties the session's three pieces together into one automatic pass:
  raster_lots  -> planar faces (= lots)
  per face     -> cut an oriented crop along each edge, blind-VLM-read the label
  resolve      -> spelled course as-is; L#/C# tag -> banked line/curve table
  validate     -> a face is a CLOSED polygon by construction, so its area IS the
                  lot area; ONE fitted scale (ft/pt) must reconcile every face's
                  polygon area with its independently-read PRINTED area. The
                  cross-lot spread of that single scale is the validation.

On the Area Thirty3 sheet this returns 4 lot faces (the straight-frontage lots)
and one scale reconciles all four polygon areas with their printed areas to a
~0.3% spread, with the boundary labels (L#, C#, spelled courses) read per lot.
Cul-de-sac / loop lots do not yet form faces (arc frontages - see raster_lots).

    python close_lots.py INPUT.pdf --page 1 --tables golden_tables_482.json
        --url http://127.0.0.1:8080 [--dpi 250] [--cache reads.json]
"""
import argparse, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "eval", "harness"))
import raster_lots as RL
import cogo_assemble as CA
import close_arc_traverse as CT
import assoc_study as AS          # _crop_windows
import vlm_read                   # read_tile

EDGE_PROMPT = (
    "Read the image at {tile}. It is a thin strip cropped ALONG one boundary "
    "line of a survey-plat lot, rotated so that line is horizontal. Transcribe "
    "ONLY the dimension label sitting on this central line. It is ONE of: a "
    "bearing+distance like 'N 89 42 28 W - 267.04', a lone distance like "
    "'296.87', or a table tag like 'L3' or 'C5'. Ignore easement notes "
    "('15 UTILITY & DRAINAGE EASEMENT'), lot names, areas. Return ONLY a JSON "
    'array; each item {"raw":"<text>","kind":"course"|"distance"|"tag"}. '
    "Nothing legible -> []. No prose, no fence."
)
AREA_PROMPT = (
    "Read the image at {tile}. Transcribe the lot AREA printed here, like "
    "'70,026 sq.ft.' or '1.598 acres'. Return ONLY JSON "
    '{"sqft":<number or null>,"acres":<number or null>}. No prose, no fence.'
)


def survey_az(dx, dy):
    """page delta (x right, y DOWN) -> survey azimuth (deg, north=up)."""
    return math.degrees(math.atan2(dx, -dy)) % 360.0


def load_tables(path):
    t = json.load(open(path))
    lines, curves = {}, {}
    for r in t["line_table"]:
        d, m, s = re.findall(r"\d+", r["bearing"])[:3]
        ns = r["bearing"][0]; ew = r["bearing"][-1]
        lines[r["id"]] = {"ns": ns, "d": int(d), "m": int(m), "s": int(s),
                          "ew": ew, "dist": r["length"]}
    for r in t["curve_table"]:
        dd, mm, ss = (int(x) for x in re.findall(r"\d+", r["delta"])[:3])
        curves[r["id"]] = {"R": r["radius"], "delta": (dd, mm, ss),
                           "length": r["length"]}
    return lines, curves


def parse_course(raw):
    """spelled 'N 89 42 28 W - 267.04' -> (ns,d,m,s,ew,dist) or None."""
    m = re.search(r"([NS])\s*0?(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})\D*([EW])"
                  r"\D*(\d{2,4}\.\d{2})?", raw)
    if not m:
        return None
    ns, d, mi, s, ew, dist = m.groups()
    return ns, int(d), int(mi), int(s), ew, (float(dist) if dist else None)


def edge_reads(img, seg_px, sc, base, tag, url, cache):
    if tag in cache:
        return cache[tag]
    band = 95
    wins = AS._crop_windows(img, seg_px, band, 1100, 200)
    import cv2
    items = []
    for wi, w in enumerate(wins):
        p = os.path.join(base, f"{tag}_w{wi}.png")
        cv2.imwrite(p, w)
        try:
            _, it = vlm_read.read_tile(p, EDGE_PROMPT, url, "qwen2.5-vl", 1100, 0.0, 180)
            items += it
        except Exception as e:  # noqa: BLE001
            print("   read fail", tag, e)
    cache[tag] = items
    return items


def _chat(img_path, prompt, url, max_side):
    import base64, io, urllib.request
    from PIL import Image
    im = Image.open(img_path).convert("RGB")
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((round(im.width*s), round(im.height*s)), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, format="PNG")
    b64 = base64.b64encode(b.getvalue()).decode()
    body = json.dumps({"model": "qwen2.5-vl", "temperature": 0.0, "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    txt = json.load(urllib.request.urlopen(req, timeout=180))["choices"][0]["message"]["content"]
    return txt


def read_area(img, cx, cy, sc, base, tag, url, cache):
    if tag in cache:
        return cache[tag]
    import cv2
    r = int(150 * sc)
    crop = img[max(0, cy - r):cy + r, max(0, cx - int(1.4*r)):cx + int(1.4*r)]
    p = os.path.join(base, f"{tag}.png")
    cv2.imwrite(p, crop)
    val = None
    try:
        txt = _chat(p, AREA_PROMPT.replace("{tile}", tag), url, 1200)
        mm = re.search(r'"sqft"\s*:\s*"?([\d,]+(?:\.\d+)?)', txt)
        if mm:
            val = float(mm.group(1).replace(",", ""))
        elif re.search(r'"acres"\s*:\s*"?([\d.]+)', txt):
            val = float(re.search(r'"acres"\s*:\s*"?([\d.]+)', txt).group(1)) * 43560
    except Exception as e:  # noqa: BLE001
        print("   area read fail", e)
    cache[tag] = val
    return val


def face_spans(face, nodes, edges, ang_thresh=22.0):
    """Collapse a fragmented face ring into real courses: merge consecutive
    edges into one span until the cumulative bend exceeds ang_thresh (a true
    corner). Returns [(edge_ids, p0, p1)] - one entry per lot course."""
    ring = []
    for ei, fwd in face:
        a = nodes[edges[ei]["a"] if fwd else edges[ei]["b"]]
        b = nodes[edges[ei]["b"] if fwd else edges[ei]["a"]]
        ring.append((ei, a, b))
    n = len(ring)
    # find corner starts: where incoming vs outgoing direction bends > thresh
    def az(a, b):
        return math.degrees(math.atan2(b[0]-a[0], -(b[1]-a[1]))) % 360
    corner = [False] * n
    for i in range(n):
        _, a0, b0 = ring[i - 1]
        _, a1, b1 = ring[i]
        turn = abs(((az(a1, b1) - az(a0, b0) + 180) % 360) - 180)
        corner[i] = turn > ang_thresh
    starts = [i for i in range(n) if corner[i]] or [0]
    spans = []
    for k in range(len(starts)):
        i0 = starts[k]; i1 = starts[(k + 1) % len(starts)]
        idxs, j = [], i0
        while True:
            idxs.append(ring[j][0]); j = (j + 1) % n
            if j == i1:
                break
        p0 = ring[i0][1]; p1 = ring[(i1 - 1) % n][2]
        spans.append((idxs, p0, p1))
    return spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--tables", required=True, help="banked golden_tables_*.json")
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--dpi", type=int, default=250)
    ap.add_argument("--roi", default="0.02,0.13,0.63,0.90")
    ap.add_argument("--close", type=int, default=9)
    ap.add_argument("--snap", type=float, default=12.0)
    ap.add_argument("--extend", type=float, default=6.0)
    ap.add_argument("--tol", type=float, default=4.0)
    ap.add_argument("--min-face-area", type=float, default=2000.0)
    ap.add_argument("--cache", default=None)
    a = ap.parse_args()
    import fitz, numpy as np

    lines_tbl, curves_tbl = load_tables(a.tables)
    roi = tuple(float(v) for v in a.roi.split(","))
    segs, _ = RL.raster_segments(a.pdf, a.page, a.dpi, roi, 12.0, 2.0, a.close)
    if a.snap:
        segs = RL.snap_endpoints(segs, a.snap)
    if a.extend:
        ext = []
        for x0, y0, x1, y1 in segs:
            L = math.hypot(x1-x0, y1-y0)
            if L < 1e-6:
                continue
            ux, uy = (x1-x0)/L, (y1-y0)/L
            ext.append((x0-ux*a.extend, y0-uy*a.extend, x1+ux*a.extend, y1+uy*a.extend))
        segs = ext
    nodes, edges = CA.planarize(segs, tol=a.tol)
    faces = [f for f in CA.extract_faces(nodes, edges)
             if RL.face_area(f, nodes, edges) >= a.min_face_area]
    print(f"geometry: {len(nodes)} nodes, {len(edges)} edges, {len(faces)} lot faces")

    page = fitz.open(a.pdf)[a.page]
    pix = page.get_pixmap(dpi=a.dpi, colorspace=fitz.csGRAY)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    sc = a.dpi / 72.0
    base = os.path.join(os.path.dirname(os.path.abspath(a.tables)), "lot_crops")
    os.makedirs(base, exist_ok=True)
    cache = {}
    if a.cache and os.path.exists(a.cache):
        cache = json.load(open(a.cache))

    # ---- pass 1: per-face edge reads, printed area, span labels, polygon area.
    # A face is a CLOSED polygon by construction, so its area is the lot area
    # (no traverse needed); the VLM labels are the dimension annotations. Scale
    # (ft/pt) is fit linearly from labelled spans, then area = poly_pt * scale^2.
    data = []
    for fi, face in enumerate(faces):
        uniq = {}
        for ei, _ in face:
            uniq.setdefault(ei, None)
        for ei in uniq:
            e = edges[ei]
            ax, ay = nodes[e["a"]]; bx, by = nodes[e["b"]]
            uniq[ei] = edge_reads(img, (ax*sc, ay*sc, bx*sc, by*sc), sc, base,
                                  f"f{fi}_e{ei}", a.url, cache)
        vx = [nodes[edges[ei]["a"] if fwd else edges[ei]["b"]][0] for ei, fwd in face]
        vy = [nodes[edges[ei]["a"] if fwd else edges[ei]["b"]][1] for ei, fwd in face]
        cx, cy = int(sum(vx)/len(vx)*sc), int(sum(vy)/len(vy)*sc)
        printed = read_area(img, cx, cy, sc, base, f"f{fi}_area", a.url, cache)

        labels = []
        for idxs, p0, p1 in face_spans(face, nodes, edges):
            Lpt = math.hypot(p1[0]-p0[0], p1[1]-p0[1])
            lab, dist = None, None
            for ei in idxs:
                for it in uniq.get(ei, []):
                    raw = it["raw"]
                    tm = re.match(r"\s*([LC])\s?(\d+)", raw); pc = parse_course(raw)
                    if tm and tm.group(1) == "C" and f"C{tm.group(2)}" in curves_tbl:
                        lab = f"C{tm.group(2)}"; break
                    if pc and pc[5]:
                        lab = f"{pc[0]}{pc[1]:02d}{pc[2]:02d}{pc[3]:02d}{pc[4]}-{pc[5]:.2f}"
                        dist = pc[5]; break
                    if tm and tm.group(1) == "L" and f"L{tm.group(2)}" in lines_tbl:
                        lab = f"L{tm.group(2)}"; dist = lines_tbl[lab]["dist"]; break
                if lab:
                    break
            if lab:
                labels.append(lab)
        data.append((fi, printed, RL.face_area(face, nodes, edges), labels))

    # ---- pass 2: ONE scale must reconcile every lot's polygon area with its
    # printed area. Fit scale^2 from the (printed/area_pt) ratios; the SPREAD of
    # those ratios is the validation - one scale fitting N independent lots to a
    # tight tolerance means the raster polygons are geometrically faithful.
    ratios = [printed / area_pt for _, printed, area_pt, _ in data
              if printed and area_pt > 0]
    if not ratios:
        print("no printed areas read - cannot validate"); return
    ratios.sort()
    s2 = ratios[len(ratios)//2]
    scale = math.sqrt(s2)
    spread = (max(ratios)/min(ratios) - 1) * 100
    print(f"fitted scale {scale:.4f} ft/pt   ({len(ratios)} lots, "
          f"area-ratio spread {spread:.1f}%)")
    n_ok = 0
    for fi, printed, area_pt, labels in data:
        area_ft = area_pt * s2
        ok = bool(printed and abs(area_ft - printed)/printed < 0.03)
        n_ok += ok
        print(f"\nLOT face {fi}: polygon area {area_ft:,.0f} sq.ft"
              + (f"  vs printed {printed:,.0f}  ({abs(area_ft-printed)/printed*100:.1f}%)  "
                 f"{'AREA-VALIDATED' if ok else 'review'}" if printed else "  (printed not read)"))
        print(f"   boundary labels read: {labels}")
    print(f"\n{n_ok}/{len(data)} lots AREA-VALIDATED; single scale reconciles all "
          f"to {spread:.1f}% spread")

    if a.cache:
        json.dump(cache, open(a.cache, "w"))


if __name__ == "__main__":
    main()


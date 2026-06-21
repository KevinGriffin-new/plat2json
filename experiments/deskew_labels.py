"""v2.1: rotated-label reader.

The whole drawing is flattened to short segments, so stroke length can't
separate text from linework. Instead: cluster all short strokes by proximity,
then keep only clusters whose *oriented* extent (via PCA) looks like a single
line of text — a few pt tall, modest length. That rejects the long thin
line/arc chains and the big boundary loops, leaving text labels. Deskew each by
its PCA angle and OCR.
"""
import math
from collections import defaultdict

import fitz
import numpy as np
import pytesseract
from PIL import Image

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
PDF = r"sample_plat.pdf"

GLYPH_MAX = 6.0      # pt: ignore strokes longer than this
EPS = 3.0            # pt: cluster strokes whose midpoints are within this
MIN_STROKES = 6
CROSS_MIN, CROSS_MAX = 1.5, 12.0   # text cap-height band (pt), orientation-independent
ALONG_MIN, ALONG_MAX = 3.0, 130.0  # text run length (pt)
DPI = 600

doc = fitz.open(PDF)
page = doc[0]

segs = []
for d in page.get_drawings():
    for it in d["items"]:
        if it[0] == "l":
            segs.append((it[1].x, it[1].y, it[2].x, it[2].y))
segs = np.array(segs)
length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
glyph = segs[length <= GLYPH_MAX]
mid = np.column_stack(((glyph[:, 0] + glyph[:, 2]) / 2, (glyph[:, 1] + glyph[:, 3]) / 2))
print(f"segments: {len(segs)}  glyph-scale: {len(glyph)}")

parent = list(range(len(mid)))
def find(a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a
def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb

grid = defaultdict(list)
for i, (x, y) in enumerate(mid):
    grid[(int(x // EPS), int(y // EPS))].append(i)
for (cx, cy), idxs in grid.items():
    neigh = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            neigh += grid.get((cx + dx, cy + dy), [])
    for i in idxs:
        for j in neigh:
            if i < j and (mid[i, 0]-mid[j, 0])**2 + (mid[i, 1]-mid[j, 1])**2 <= EPS*EPS:
                union(i, j)
clusters = defaultdict(list)
for i in range(len(mid)):
    clusters[find(i)].append(i)

cands = []
for members in clusters.values():
    if len(members) < MIN_STROKES:
        continue
    m = glyph[members]
    pts = np.column_stack([
        np.concatenate([m[:, 0], m[:, 2]]),
        np.concatenate([m[:, 1], m[:, 3]]),
    ]).astype(float)
    c = pts.mean(0)
    pc = pts - c
    _, _, vt = np.linalg.svd(pc, full_matrices=False)
    proj = pc @ vt.T
    along = float(proj[:, 0].max() - proj[:, 0].min())
    cross = float(proj[:, 1].max() - proj[:, 1].min())
    if not (CROSS_MIN <= cross <= CROSS_MAX and ALONG_MIN <= along <= ALONG_MAX):
        continue
    xs, ys = pts[:, 0], pts[:, 1]
    bb = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
    ang = math.degrees(math.atan2(vt[0, 1], vt[0, 0]))
    cands.append((bb, ang, len(members), round(along, 1), round(cross, 1)))
print(f"clusters: {len(clusters)}  text-shaped candidates: {len(cands)}")

def ocr(im):
    d = pytesseract.image_to_data(im, config="--psm 7", output_type=pytesseract.Output.DICT)
    toks = [(t.strip(), int(float(c))) for t, c in zip(d["text"], d["conf"]) if t.strip()]
    if not toks:
        return "", -1
    return " ".join(t for t, _ in toks), max(c for _, c in toks)

results = []
for bb, ang, n, along, cross in cands:
    rect = fitz.Rect(bb[0] - 2, bb[1] - 2, bb[2] + 2, bb[3] + 2)
    if rect.width < 1 or rect.height < 1:
        continue
    pix = page.get_pixmap(clip=rect, dpi=DPI)
    if pix.width < 5 or pix.height < 5:
        continue
    im = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    best = ("", -1)
    for rot in (ang, ang + 180):
        r = im.rotate(rot, expand=True, fillcolor=(255, 255, 255))
        if r.width < 5 or r.height < 5:
            continue
        txt, cf = ocr(r)
        if txt and cf > best[1]:
            best = (txt, cf)
    if best[0]:
        results.append((best[0], best[1], round(ang, 1), n, (round(bb[0]), round(bb[1]))))

print(f"\nrecognized {len(results)} labels (conf-sorted):")
for t, c, a, n, pos in sorted(results, key=lambda r: -r[1]):
    print(f"  conf{c:3d}  ang{a:6.1f}  n{n:3d}  @{pos}  {t!r}")

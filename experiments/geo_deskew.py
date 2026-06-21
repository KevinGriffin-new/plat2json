"""v6.1: geometry-guided per-segment deskew, driven by VECTOR segments.

The boundary is one connected pixel blob, so raster components can't give
individual edges. Instead take the long vector line segments (the real boundary/
road edges; arcs + text are the short flattened ones), convert to raster scale,
and for each: extract a band along it from the text-only raster and rotate to
true horizontal so its bearing/distance label reads.
"""
import math

import cv2
import fitz
import numpy as np
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
PDF = r"sample_plat.pdf"
DPI = 300
SC = DPI / 72.0
SEG_MIN_PT = 25.0   # vector segments longer than this = real boundary/road edges
LINE_PX = 70        # raster CC length above which we treat ink as linework
BAND = 60           # px each side of the line to capture parallel labels

page = fitz.open(PDF)[0]

# --- text-only raster (drop long + tiny raster CCs) for clean OCR ---
pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
_, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
text = np.zeros_like(bw)
for i in range(1, n):
    x, y, w, h, area = stats[i]
    if max(w, h) <= LINE_PX and area >= 6:
        text[lab == i] = 255
textimg = 255 - text

# --- long vector segments = boundary/road edges (exact angles) ---
segs = []
for d in page.get_drawings():
    for it in d["items"]:
        if it[0] == "l":
            segs.append((it[1].x, it[1].y, it[2].x, it[2].y))
segs = np.array(segs)
Lpt = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
edges = segs[Lpt > SEG_MIN_PT] * SC  # to raster px

def deskew_strip(center, length, angle):
    size = (int(length + 50), BAND * 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rot = cv2.warpAffine(textimg, M, (textimg.shape[1], textimg.shape[0]),
                         flags=cv2.INTER_CUBIC, borderValue=255)
    return cv2.getRectSubPix(rot, size, center)

def ocr(im):
    d = pytesseract.image_to_data(im, config="--psm 6", output_type=pytesseract.Output.DICT)
    toks = [(t.strip(), int(float(c))) for t, c in zip(d["text"], d["conf"]) if t.strip()]
    if not toks:
        return "", -1
    return " ".join(t for t, _ in toks), int(np.mean([c for _, c in toks]))

strips, results = [], []
for (x0, y0, x1, y1) in edges:
    length = math.hypot(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ang = math.degrees(math.atan2(y1 - y0, x1 - x0))
    best = ("", -1, None)
    for a in (ang, ang + 180):
        strip = deskew_strip((cx, cy), length, a)
        txt, cf = ocr(strip)
        score = cf + (50 if any(ch.isdigit() for ch in txt) else 0)
        if txt and score > best[1]:
            best, beststrip = (txt, score, a), strip
    if best[0] and any(ch.isdigit() for ch in best[0]):
        results.append((best[0], int(best[1]), round(ang, 1), int(length), (int(cx), int(cy))))
        if len(strips) < 16:
            strips.append(beststrip)

if strips:
    wmax = max(s.shape[1] for s in strips)
    rows = [cv2.copyMakeBorder(s, 3, 3, 0, wmax - s.shape[1], cv2.BORDER_CONSTANT, value=255)
            for s in strips]
    cv2.imwrite(r"_plat_strips.png", np.vstack(rows))

print(f"long vector edges: {len(edges)}   labeled reads: {len(results)}\n")
seen = set()
for txt, sc, ang, length, pos in sorted(results, key=lambda r: -r[1]):
    key = txt
    if key in seen:
        continue
    seen.add(key)
    print(f"  score{sc:3d}  ang{ang:6.1f}  len{length:4d}  @{pos}  {txt!r}")

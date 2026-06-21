"""v3: raster-morphology label detector (the proper CV approach).

1. render page to grayscale, binarize (ink=white)
2. remove linework: drop connected components whose longest side exceeds a
   character-scale threshold (lines/arcs/boundary are long; glyphs are small)
3. dilate the surviving glyph strokes so a label's characters merge into one
   blob, regardless of the label's rotation
4. per blob: minAreaRect -> angle, deskew a line-free crop, OCR
Also writes an overlay PNG so we can see detection quality.
"""
import cv2
import fitz
import numpy as np
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
PDF = r"sample_plat.pdf"
DPI = 400
LINE_PX = 120          # CCs longer than this (px) are linework -> removed
MERGE_PX = 11          # dilation kernel to merge chars within a label
MIN_LONG, MAX_LONG = 26, 700   # label blob long side (px)
MIN_SHORT, MAX_SHORT = 12, 110 # label blob short side (px)

doc = fitz.open(PDF)
page = doc[0]
pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
H, W = g.shape
_, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)  # ink=255

# remove linework (long components)
n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
clean = np.zeros_like(bw)
for i in range(1, n):
    x, y, w, h, area = stats[i]
    if max(w, h) > LINE_PX or area < 8:
        continue
    clean[lab == i] = 255

# merge characters into label blobs
k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MERGE_PX, MERGE_PX))
dil = cv2.dilate(clean, k, iterations=1)
n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(dil, 8)
print(f"page {W}x{H}  raw CCs {n-1}  after line-removal blobs {n2-1}")

overlay = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
results = []
for i in range(1, n2):
    x, y, w, h, area = stats2[i]
    lo, hi = min(w, h), max(w, h)
    if not (MIN_LONG <= hi <= MAX_LONG and MIN_SHORT <= lo <= MAX_SHORT):
        continue
    ys, xs = np.where(lab2 == i)
    rect = cv2.minAreaRect(np.column_stack([xs, ys]).astype(np.float32))
    (cx, cy), (rw, rh), ang = rect
    if rw < rh:
        ang += 90
    pad = 8
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    sub = 255 - clean[y0:y1, x0:x1]  # black ink on white, linework already gone
    best = ("", -1)
    for da in (0, 180):
        M = cv2.getRotationMatrix2D(((x1 - x0) / 2, (y1 - y0) / 2), ang + da, 1.0)
        rot = cv2.warpAffine(sub, M, (x1 - x0, y1 - y0),
                             flags=cv2.INTER_CUBIC, borderValue=255)
        d = pytesseract.image_to_data(rot, config="--psm 7",
                                      output_type=pytesseract.Output.DICT)
        toks = [(t.strip(), int(float(c))) for t, c in zip(d["text"], d["conf"]) if t.strip()]
        if toks:
            txt = " ".join(t for t, _ in toks)
            cf = max(c for _, c in toks)
            if cf > best[1]:
                best = (txt, cf)
    box = cv2.boxPoints(rect).astype(int)
    ok = bool(best[0]) and best[1] >= 40
    cv2.drawContours(overlay, [box], 0, (0, 160, 0) if ok else (0, 0, 220), 2)
    if best[0]:
        results.append((best[0], best[1], round(ang, 1), (x, y)))

out = r"_plat_detect.png"
cv2.imwrite(out, overlay)
results.sort(key=lambda r: -r[1])
print(f"recognized {len(results)} labels; overlay -> {out}\n")
for t, c, a, pos in results:
    print(f"  conf{c:3d}  ang{a:6.1f}  @{pos}  {t!r}")

"""Read the r=/a= curve labels, guided by the fitted arc geometry.

For each fitted arc we know center/radius/sweep, so the label sits near the arc
midpoint, oriented tangent. Crop there, rotate by the tangent angle, OCR with an
r=/a= whitelist. Use the read radius to snap/validate the arc.
"""
import json, math, re
import cv2
import fitz
import numpy as np
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
PDF = r"sample_plat.pdf"
DPI = 300; SC = DPI / 72.0; PT2M = 25.4 / 72 / 1000 * 250

page = fitz.open(PDF)[0]; Hpt = page.rect.height
pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
_, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
LINE_PX = int(90 * DPI / 400)
text = np.zeros_like(bw)
for i in range(1, n):
    x, y, w, h, area = stats[i]
    if max(w, h) <= LINE_PX and area >= 6:
        text[lab == i] = 255
textimg = 255 - text

arcs = json.load(open(r"epp12345_plan_arcs.json"))["arcs"]

def m2px(Xm, Ym):
    return Xm / PT2M * SC, (Hpt - Ym / PT2M) * SC

def deskew(center, size, angle):
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rot = cv2.warpAffine(textimg, M, (textimg.shape[1], textimg.shape[0]),
                         flags=cv2.INTER_CUBIC, borderValue=255)
    return cv2.getRectSubPix(rot, size, center)

def ocr(im):
    return pytesseract.image_to_string(
        im, config="--psm 6 -c tessedit_char_whitelist=ra=0123456789.").strip()

# Targeting from arc geometry gets us to the label; sweep the deskew angle to
# find the rotation that makes r=/a= read (fragmented arcs give an off tangent).
strips, reads = [], []
for idx, (cx, cy, r, s, e, _) in enumerate(arcs):
    mid = math.radians((s + e) / 2)
    tang = math.degrees(mid) + 90
    best = (-1, "", None, None, None)
    for off in (0, 3.0, -3.0):
        Xo, Yo = cx + (r + off) * math.cos(mid), cy + (r + off) * math.sin(mid)
        ox, oy = m2px(Xo, Yo)
        for da in (0, 15, -15, 30, -30, 45, -45):
            for flip in (0, 180):
                strip = deskew((ox, oy), (320, 150), tang + da + flip)
                txt = ocr(strip)
                rm = re.search(r"r=?([\d]+\.[\d]+)", txt)
                am = re.search(r"a=?([\d]+\.[\d]+)", txt)
                score = (3 if rm else 0) + (2 if am else 0)
                if (rm or am) and score > best[0]:
                    best = (score, txt.replace("\n", " "),
                            float(rm.group(1)) if rm else None,
                            float(am.group(1)) if am else None, strip)
    if best[2] or best[3]:
        reads.append((round(r, 2), best[2], best[3], best[1]))
        if len(strips) < 12:
            strips.append(best[4])

if strips:
    w = max(s.shape[1] for s in strips)
    rows = [cv2.copyMakeBorder(s, 3, 3, 0, w - s.shape[1], cv2.BORDER_CONSTANT, value=255) for s in strips]
    cv2.imwrite(r"_curve_strips.png", np.vstack(rows))

print(f"arcs: {len(arcs)}   curve labels read: {len(reads)}\n")
print(f"{'fit_r':>7} {'ocr_r':>8} {'ocr_a':>8}   raw")
for fr, orr, oa, raw in sorted(reads, key=lambda r: -(r[1] or 0)):
    print(f"{fr:7.2f} {str(orr):>8} {str(oa):>8}   {raw!r}")

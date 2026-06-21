"""v4: learned multi-oriented detector (EasyOCR / CRAFT detection).

Render the plat and let EasyOCR's CRAFT detector find text regions at arbitrary
orientation, then recognize them. Dumps detections (text, conf, box center) and
writes an overlay so we can judge whether the rotated bearings/curve labels come
through as whole strings — the thing the morphology pass fragmented.
"""
import sys
import cv2
import fitz
import numpy as np
import easyocr

PDF = r"sample_plat.pdf"
DPI = int(sys.argv[1]) if len(sys.argv) > 1 else 200
CANVAS = int(sys.argv[2]) if len(sys.argv) > 2 else 4000

doc = fitz.open(PDF)
page = doc[0]
pix = page.get_pixmap(dpi=DPI)
img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
if pix.n == 4:
    img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
print(f"render {pix.width}x{pix.height} @ {DPI}dpi  canvas={CANVAS}")

reader = easyocr.Reader(["en"], gpu=False, verbose=False)
res = reader.readtext(
    img,
    canvas_size=CANVAS,
    mag_ratio=1.0,
    text_threshold=0.5,
    low_text=0.3,
    link_threshold=0.4,
    rotation_info=[90, 180, 270],
    detail=1,
    paragraph=False,
)
print(f"detections: {len(res)}\n")

overlay = img.copy()
rows = []
for box, text, conf in res:
    pts = np.array(box).astype(int)
    cx, cy = int(np.mean(pts[:, 0])), int(np.mean(pts[:, 1]))
    cv2.polylines(overlay, [pts], True, (0, 160, 0) if conf > 0.5 else (0, 0, 220), 2)
    rows.append((conf, text, cx, cy))

out = r"_plat_easyocr.png"
cv2.imwrite(out, cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
print(f"overlay -> {out}\n")
for conf, text, cx, cy in sorted(rows, key=lambda r: -r[0]):
    print(f"  {conf:.2f}  @({cx:5d},{cy:5d})  {text!r}")

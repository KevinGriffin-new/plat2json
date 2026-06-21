"""v5: EasyOCR on the linework-free raster (vector-assisted).

Uses our unique asset — the exact vector linework — to erase the boundary/road/
arc lines first (done in the previous step -> _plat_linefree.png), then runs the
learned detector on the clean text-only image. Tests whether removing the
line-overlap confound lets the rotated bearing/curve labels read as whole strings.
"""
import cv2
import numpy as np
import easyocr

SRC = r"_plat_linefree.png"
img = cv2.imread(SRC)
rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
print(f"image {img.shape[1]}x{img.shape[0]}")

reader = easyocr.Reader(["en"], gpu=False, verbose=False)
res = reader.readtext(
    rgb,
    canvas_size=4500,
    mag_ratio=1.0,
    text_threshold=0.4,
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
cv2.imwrite(r"_plat_linefree_ocr.png", overlay)

for conf, text, cx, cy in sorted(rows, key=lambda r: -r[0]):
    print(f"  {conf:.2f}  @({cx:5d},{cy:5d})  {text!r}")

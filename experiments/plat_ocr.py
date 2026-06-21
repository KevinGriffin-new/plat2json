"""Plan-label OCR harness for LTSA vector plats (EPP12345 sample).

v1: full-page sparse-text OCR baseline — shows what Tesseract catches with the
labels left horizontal, so we can see which labels are upright vs. which are
rotated and need the deskew pass (iteration 2).
"""
import sys
import fitz
import pytesseract
from PIL import Image

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

PDF = r"sample_plat.pdf"
DPI = 400
CONF = 40

doc = fitz.open(PDF)
page = doc[0]
pix = page.get_pixmap(dpi=DPI)
img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

# PSM 11 = sparse text: find as much text as possible, no layout assumptions.
data = pytesseract.image_to_data(
    img, config="--psm 11", output_type=pytesseract.Output.DICT
)
words = []
for i, t in enumerate(data["text"]):
    t = t.strip()
    try:
        conf = int(float(data["conf"][i]))
    except ValueError:
        conf = -1
    if t and conf >= CONF:
        words.append((t, conf, data["left"][i], data["top"][i]))

print(f"DPI={DPI}  page={pix.width}x{pix.height}  words(conf>={CONF}): {len(words)}")
for t, c, x, y in sorted(words, key=lambda w: (w[3], w[2])):
    print(f"  conf{c:3d}  ({x:5d},{y:5d})  {t!r}")

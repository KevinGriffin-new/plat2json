#!/usr/bin/env python3
"""locate_fieldnotes.py - find the pages of a BLM field-note PDF that describe a
target township's boundary courses, so OCR-keying runs on the right pages.

Locate the field-note pages for a township (boundary segments scatter across volumes):
the plan assumed a township is a CONTIGUOUS same-(T,R) block (true for a
self-bundled SUBDIVISION volume, e.g. glo_t28nr71w -> 95%). It is NOT true for
an EXTERIOR-resurvey volume (e.g. fieldnotes_ext3028.pdf = "Dependent resurvey
of the exteriors of Tps. 25-28 N., Group 158"): a township's four boundaries
are scattered under boundary-SEGMENT headers, and a SHARED boundary line is
filed once under one of the two townships it divides. So locating township
(T,R) means gathering every page whose header bounds (T,R) under the
shared-boundary adjacency rule -- not just pages naming (T,R).

Adjacency (this region: Range numbers increase WESTWARD, Township NORTHWARD):
  our EAST  bdy = line R|R-1 = "E. bdy of (T,R)"   or "W. bdy of (T,R-1)"
  our WEST  bdy = line R|R+1 = "W. bdy of (T,R)"   or "E. bdy of (T,R+1)"
  our NORTH bdy = line T|T+1 = "N. bdy of (T,R)"   or "S. bdy of (T+1,R)"  or a std parallel
  our SOUTH bdy = line T|T-1 = "S. bdy of (T,R)"   or "N. bdy of (T-1,R)"  or a std parallel

Header OCR is cached to _sources/<slug>/_header_ocr.json (re-OCR with --refresh).
Run with SYSTEM python (fitz + pytesseract).

    python locate_fieldnotes.py <slug> --twp 28N80W [--strip 0.25] [--dpi 250] [--refresh]
"""
import argparse, json, os, re
import fitz, pytesseract
from PIL import Image, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))

DIRW = {"north": "N", "n": "N", "south": "S", "s": "S",
        "east": "E", "e": "E", "west": "W", "w": "W"}

# faint 1938 typewriter scans OCR digits as look-alike letters in the T/R slots.
# Within the constrained "R. xx W" / "T. xx N" slot, translate look-alikes -> digits.
_DIGIT = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0",
                        "l": "1", "I": "1", "i": "1", "|": "1", "!": "1",
                        "Z": "2", "z": "2", "B": "8", "S": "8", "G": "6",
                        "g": "9", "T": "7", "A": "4"})
# digit OR look-alike letter, 1-2 chars
DR = r"([0-9OoQDlIi|!ZzBSGgTA]{1,2})"


def _dz(s):
    """letter-tolerant 2-char number -> int, or None if it can't be made numeric."""
    v = s.translate(_DIGIT)
    return int(v) if v.isdigit() else None


# township token: "T(ownship) NN N(.,) R(ange) NN W" -- tolerant of punctuation
# noise around N and before W ("R. 80, W", "T. 28.N.,").
TR = re.compile(r"(?:Township\s+|T\.?\s*)" + DR + r"\s*N[.,]{0,3}\s*,?\s*"
                r"(?:Range\s+|R\.?\s*)" + DR + r"\s*[.,]{0,3}\s*W", re.I)
# direction: the word "boundary" reliably OCRs garbled (bousdary/peundary/Bay),
# so don't anchor on it. Two robust cues instead:
#   abbr:  "E. Bdy. of" / "E. Bay. of"  (letter + B-word + of)
#   word:  east/south/east/west, gated by a fuzzy boundary token in the title
DIR_ABBR = re.compile(r"\b([NSEW])\.\s*B[a-z]{1,4}\.?\s+of\b")
DIR_WORD = re.compile(r"\b(north|south|east|west)\b", re.I)
BOUND = re.compile(r"b\w{0,2}[su]dary|bound|\bbdy\b|\bbay\b", re.I)


def parse_header(t):
    """-> list of (dir in NSEW, township_T, range_R) for this title strip."""
    toks = [(_dz(a), _dz(b)) for a, b in TR.findall(t)]
    toks = [(a, b) for a, b in toks if a and b and 1 <= a <= 45 and 1 <= b <= 120]
    if not toks:
        return []
    th, rh = toks[0]
    dirw = None
    m = DIR_ABBR.search(t)
    if m:
        dirw = DIRW[m[1].lower()]
    else:
        m = DIR_WORD.search(t)
        if m and BOUND.search(t):
            dirw = DIRW[m[1].lower()]
    return [(dirw, th, rh)] if dirw else []
# standard parallel running E-W "through Range NN W" (forms a T|T+1 line)
STDPAR = re.compile(r"standard parallel.*?through\s+(?:Range\s+|R\.?\s*)" + DR + r"\s*W", re.I)
TYPE = re.compile(r"(dependent resurvey|reestablishment|subdivision|exterior|"
                  r"retracement|standard parallel|guide meridian)", re.I)


def header_ocr(slug, dpi, strip, refresh):
    base = os.path.join(HERE, "_sources", slug)
    cache = os.path.join(base, "_header_ocr.json")
    if os.path.exists(cache) and not refresh:
        c = json.load(open(cache, encoding="utf-8"))
        if c.get("dpi") == dpi and abs(c.get("strip", 0) - strip) < 1e-6:
            return c["pages"], _find_pdf(base)
    pdf = _find_pdf(base)
    d = fitz.open(pdf)
    pages = []
    for i in range(d.page_count):
        pix = d[i].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        im = Image.frombytes("L", [pix.width, pix.height], pix.samples)
        im = im.crop((0, 0, im.width, int(im.height * strip)))
        # binarize + upscale: recovers the 8<->3, 80<->B0 digit confusion on
        # faint typewriter scans (psm 6 = treat as a uniform text block)
        im = ImageOps.autocontrast(im, cutoff=2).point(lambda p: 0 if p < 140 else 255)
        im = im.resize((im.width * 2, im.height * 2))
        pages.append(" ".join(
            pytesseract.image_to_string(im, config="--psm 6").split()))
    json.dump({"dpi": dpi, "strip": strip, "pdf": os.path.basename(pdf),
               "pages": pages}, open(cache, "w"), indent=1)
    return pages, pdf


def _find_pdf(base):
    cands = [f for f in os.listdir(base) if f.lower().endswith(".pdf")
             and ("fieldnote" in f.lower() or "ext" in f.lower())]
    if not cands:
        cands = [f for f in os.listdir(base) if f.lower().endswith(".pdf")]
    return os.path.join(base, sorted(cands, key=len, reverse=True)[0])


def bounds_target(dirw, th, rh, T, R):
    """Does boundary-segment (dirw, Township th, Range rh) bound township (T,R)?
    Returns which side of (T,R) it is, or None."""
    if dirw == "E" and (th, rh) == (T, R):      return "E"   # our east, named directly
    if dirw == "W" and (th, rh) == (T, R):      return "W"
    if dirw == "N" and (th, rh) == (T, R):      return "N"
    if dirw == "S" and (th, rh) == (T, R):      return "S"
    if dirw == "E" and (th, rh) == (T, R + 1):  return "W"   # E.bdy of western neighbor
    if dirw == "W" and (th, rh) == (T, R - 1):  return "E"   # W.bdy of eastern neighbor
    if dirw == "N" and (th, rh) == (T - 1, R):  return "S"   # N.bdy of southern neighbor
    if dirw == "S" and (th, rh) == (T + 1, R):  return "N"   # S.bdy of northern neighbor
    return None


def runs(pages):
    """contiguous int runs -> 'a-b' strings"""
    out, s = [], None
    for i in range(len(pages) + 1):
        on = i < len(pages) and pages[i]
        if on and s is None:
            s = i
        elif not on and s is not None:
            out.append((s, i - 1)); s = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--twp", required=True, help="e.g. 28N80W")
    ap.add_argument("--strip", type=float, default=0.16)
    ap.add_argument("--dpi", type=int, default=250)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    m = re.match(r"(\d{1,2})N(\d{1,2})W", a.twp, re.I)
    if not m:
        ap.error("--twp must look like 28N80W")
    T, R = int(m[1]), int(m[2])

    pages, pdf = header_ocr(a.slug, a.dpi, a.strip, a.refresh)
    print(f"[{a.slug}] {os.path.basename(pdf)}  {len(pages)} pages; "
          f"target T{T}N R{R}W")

    hit = {}   # page -> list of (side, why)
    for i, t in enumerate(pages):
        sides = {}
        for dirw, th, rh in parse_header(t):
            side = bounds_target(dirw, th, rh, T, R)
            if side:
                sides.setdefault(side, f"{dirw}.bdy of T{th}N R{rh}W")
        # standard parallel through our range, near our township -> N or S, low conf
        for mm in STDPAR.finditer(t):
            if _dz(mm[1]) == R:
                sides.setdefault("par", f"std parallel through R{R}W (N/S, low-conf)")
        if sides:
            hit[i] = sides

    # report
    by_side = {}
    for p, sides in hit.items():
        for s in sides:
            by_side.setdefault(s, []).append(p)
    sidename = {"N": "north", "S": "south", "E": "east", "W": "west",
                "par": "std-parallel (N/S?)"}
    print("\n  boundary segments found for this township:")
    for s in ["N", "S", "E", "W", "par"]:
        if s in by_side:
            for p in sorted(by_side[s]):
                print(f"    {sidename[s]:20} p{p:3}  <- {hit[p][s]}")
    missing = [sidename[s] for s in ["N", "S", "E", "W"] if s not in by_side]
    matched = sorted(hit)
    print(f"\n  matched pages: {matched}")
    print(f"  page runs: {[f'{a}-{b}' if a!=b else str(a) for a,b in runs([i in hit for i in range(len(pages))])]}")
    if missing:
        print(f"  WARNING: no header matched the {', '.join(missing)} boundary "
              f"(may be a std parallel, OCR-missed, or filed under a neighbor)")
    # also list any DIRECT (T,R) mentions for transparency
    direct = [i for i, t in enumerate(pages) if any(
        (_dz(x), _dz(y)) == (T, R) for x, y in TR.findall(t))]
    print(f"  pages directly naming T{T}N R{R}W: {direct}")
    # emit a --pages-friendly union (expand each run by neighbors handled by caller)
    out = os.path.join(HERE, "_sources", a.slug, "_locate.json")
    json.dump({"twp": a.twp, "matched_pages": matched,
               "by_side": {k: sorted(v) for k, v in by_side.items()},
               "missing_sides": missing, "direct": direct}, open(out, "w"), indent=1)
    print(f"  -> {out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Harvest the curve geometry from a vector plat's text layer.

Two sources, both exact (no OCR):
  1. the structured CURVE [DATA] TABLE - column-oriented:
       CURVE | C1 C2 .. Cn | RADIUS | r1..rn | DELTA | d1..dn | LENGTH | l1..ln
       [| CHORD DIRECTION | b1..bn | CHORD LENGTH | c1..cn]
  2. inline course labels:  R=63.00' L=98.96' [Δ=90°00'00"] [Chd=S44°59'39"E 53.03']

Emits a curve golden: [{id, radius, length, delta, chord_brg, chord_len, src}].
Radii/lengths are kept in the printed unit (feet here); facts only.
"""
import re

# section header line -> record key
SECTIONS = {
    "CURVE": "id", "COURSE": "id", "CURVE #": "id", "CURVE NO": "id", "NO.": "id",
    "RADIUS": "radius", "DELTA": "delta", "LENGTH": "length", "ARC": "length",
    "ARC LENGTH": "length", "CHORD DIRECTION": "chord_brg",
    "CHORD BEARING": "chord_brg", "CHORD BRG": "chord_brg",
    "CHORD LENGTH": "chord_len", "CHORD": "chord_len", "TANGENT": "tangent",
}
ID_RE = re.compile(r"^C\d{1,3}$", re.I)
NUM_RE = re.compile(r"^\d+(?:\.\d+)?'?$")
DMS_RE = re.compile(r"^\d+°\d+'\d+(?:\.\d+)?\"?$")
BRG_RE = re.compile(r"^[NS]\d+°\d+'\d+(?:\.\d+)?\"?[EW]$", re.I)


def _val(key, ln):
    if key == "id":
        return ln.upper() if ID_RE.match(ln) else None
    if key in ("radius", "length", "chord_len", "tangent"):
        return float(ln.rstrip("'")) if NUM_RE.match(ln) else None
    if key == "delta":
        return ln if DMS_RE.match(ln) else None
    if key == "chord_brg":
        return ln if BRG_RE.match(ln) else None
    return None


def parse_table(text):
    U = text.upper()
    m = re.search(r"CURVE\s+DATA\s+TABLE|CURVE\s+TABLE|CURVE\s+DATA", U)
    if not m:
        return []
    lines = [l.strip() for l in text[m.start():].splitlines() if l.strip()]
    cols, cur = {}, None
    for ln in lines:
        up = ln.upper()
        if up in SECTIONS:
            cur = SECTIONS[up]
            cols.setdefault(cur, [])
            continue
        if cur is None:
            continue
        v = _val(cur, ln)
        if v is not None:
            cols[cur].append(v)
        else:
            cur = None  # left the column block (e.g. LEGEND) -> wait for next header
    ids = cols.get("id", [])
    recs = []
    for i, cid in enumerate(ids):
        rec = {"id": cid, "src": "table"}
        for k in ("radius", "delta", "length", "chord_brg", "chord_len", "tangent"):
            col = cols.get(k, [])
            rec[k] = col[i] if i < len(col) else None
        recs.append(rec)
    return recs


def parse_inline(text):
    """Inline course curves: R=.. L=.. [Δ=..] [Chd=.. <chord>']."""
    recs = []
    for m in re.finditer(r"R\s*=\s*(\d+(?:\.\d+)?)'?\s*L\s*=\s*(\d+(?:\.\d+)?)'?", text):
        tail = text[m.end():m.end() + 60]
        delta = re.search(r"[Δ∆]\s*=\s*(\d+°\d+'\d+(?:\.\d+)?\"?)", tail)
        chd = re.search(r"CHD?\s*=\s*([NS]\d+°\d+'\d+\"?[EW])\s*\|?\s*(\d+(?:\.\d+)?)'?",
                        tail, re.I)
        recs.append({"id": None, "src": "inline",
                     "radius": float(m.group(1)), "length": float(m.group(2)),
                     "delta": delta.group(1) if delta else None,
                     "chord_brg": chd.group(1) if chd else None,
                     "chord_len": float(chd.group(2)) if chd else None,
                     "tangent": None})
    return recs


def harvest_curves(text):
    table = parse_table(text)
    inline = parse_inline(text)
    # de-dupe inline against table by (radius, length) within 0.05'
    def key(r):
        return (round(r["radius"], 1), round(r["length"] or 0, 1))
    seen = {key(r) for r in table}
    inline = [r for r in inline if key(r) not in seen]
    return table + inline


if __name__ == "__main__":
    import sys, json, fitz
    doc = fitz.open(sys.argv[1])
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    recs = harvest_curves(doc[page].get_text())
    print(f"{len(recs)} curves harvested from page {page}:")
    for r in recs:
        print(f"  {r['src']:6s} {str(r['id']):4s} R={r['radius']:>8} L={r['length']:>8} "
              f"Δ={str(r['delta']):>11} chord={str(r['chord_brg'])} {r['chord_len']}")
    print(json.dumps(recs, ensure_ascii=False))

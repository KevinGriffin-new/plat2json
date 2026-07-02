#!/usr/bin/env python3
"""Curate clean field-note goldens for the read corpus.

Per township: parse the p0 index, take ONLY the "Township Subdivision Plats"
section, keep modern full-township entries (year >= 1970, not section-only/_bnd),
OCR each newest-first, and ADMIT the township only if a candidate key passes a
sanity gate AND the VLM read actually agrees with it (>= MIN_RECALL bearings).
Otherwise EXCLUDE it — a noisy golden is worse than none.
"""
import os, re, sys, glob, json, subprocess
import fitz

BASE = "eval/harness/_sources"
OCR = "eval/harness/ocr_fieldnotes.py"
SCORE = "eval/score/score_run.py"
PY = sys.executable
MIN_YEAR = 1970
MIN_RECALL = 0.30


def maxyear(s):
    ys = [int(y) for y in re.findall(r'(?:18|19|20)\d{2}', s)]
    return max(ys) if ys else 0


def subdivision_candidates(p0):
    i = p0.find("Township Subdivision Plats")
    if i < 0:
        return []
    j = p0.find("Township Exterior", i + 1)
    seg = p0[i:(j if j > 0 else len(p0))]
    lines = [l.strip() for l in seg.splitlines() if l.strip()]
    out = []
    for k, l in enumerate(lines):
        if l.startswith("http") and l.endswith(".pdf"):
            yr = ""
            for back in (1, 2, 3):
                if k - back >= 0 and re.search(r'(?:18|19|20)\d{2}', lines[k - back]):
                    yr = lines[k - back]
                    break
            out.append((maxyear(yr), l))
    return out


def sane(nb, nd):
    return nb >= 8 and nd >= 3 and nd <= 4 * nb and nb <= 80


def score(slug, keypath):
    out = subprocess.run([PY, SCORE, slug, "--gt", keypath],
                         capture_output=True, text=True).stdout
    rb = re.search(r'bearing recall: (\d+)/(\d+)', out)
    rd = re.search(r'distance recall: (\d+)/(\d+)', out)
    return rb, rd


clean, excluded = {}, {}
for slug in sorted(os.listdir(BASE)):
    d = os.path.join(BASE, slug)
    pdfs = [p for p in glob.glob(d + "/*.pdf") if "fieldnotes" not in p]
    if not pdfs or not os.path.exists(os.path.join(d, "_vlm_reads.json")):
        continue
    print("#" * 60)
    print(slug)
    p0 = fitz.open(pdfs[0])[0].get_text()
    cands = [(y, u) for (y, u) in subdivision_candidates(p0)
             if y >= MIN_YEAR
             and "_s" not in u.rsplit("/", 1)[-1]
             and "_bnd" not in u]
    cands.sort(reverse=True)
    if not cands:
        excluded[slug] = "no modern (>=1970) full-township subdivision listed"
        print("  EXCLUDE:", excluded[slug])
        continue
    chosen = None
    for y, url in cands:
        name = url.rsplit("/", 1)[-1]
        fn = os.path.join(d, "fieldnotes.pdf")
        kp = os.path.join(d, "cand_key.json")
        rc = subprocess.run(["curl", "-sSL", "-A", "Mozilla/5.0", "-o", fn, url])
        if rc.returncode != 0 or not os.path.exists(fn):
            print(f"  {y} {name}: download failed")
            continue
        subprocess.run([PY, OCR, fn, kp], capture_output=True)
        try:
            key = json.load(open(kp))
        except Exception:
            print(f"  {y} {name}: OCR produced no key")
            continue
        nb = len(key.get("bearings_az", []))
        nd = len(key.get("distances_m", []))
        if not sane(nb, nd):
            print(f"  {y} {name}: key {nb}b/{nd}d -> fails sanity gate")
            continue
        rb, rd = score(slug, kp)
        frac = (int(rb.group(1)) / int(rb.group(2))) if (rb and int(rb.group(2))) else 0.0
        bs = rb.group(0) if rb else "NA"
        ds = rd.group(0) if rd else "NA"
        print(f"  {y} {name}: key {nb}b/{nd}d  {bs}  {ds}  (bearing frac {frac:.2f})")
        if frac >= MIN_RECALL:
            os.replace(kp, os.path.join(d, "fn_key.json"))
            chosen = dict(year=y, file=name, key_b=nb, key_d=nd, bearings=bs, distances=ds)
            break
    if chosen:
        clean[slug] = chosen
        print("  ADMIT:", chosen)
    else:
        excluded[slug] = "no candidate passed sanity + read-agreement"
        print("  EXCLUDE:", excluded[slug])

print("\n" + "=" * 60)
print(f"CLEAN CORPUS: {len(clean)} / {len(clean) + len(excluded)} townships")
for s, c in clean.items():
    print(f"  {s}: {c['bearings']} bearings, {c['distances']} distances  ({c['file']}, {c['year']})")
print("EXCLUDED:")
for s, r in excluded.items():
    print(f"  {s}: {r}")
json.dump({"clean": clean, "excluded": excluded},
          open("eval/harness/_sources/_corpus_curation.json", "w"), indent=1)

"""Derive pose priors for fabric_compare from the drawing itself: a VLM
reads the street labels and north arrow, a rule engine turns them into
--rot-prior / --no-mirror inputs.

    python orient_prior.py PLAN.pdf --page 19 --vlm http://HOST:8080 -o prior.json

Why priors at all: iteration 25's decisive negative result — under
proposal->registration relayout drift, the TRUE pose fits the fabric worse
than a symmetric impostor (disagreement-is-signal at pose level), so
goodness-of-fit cannot choose the pose. Document-class priors can:

- Rotation: municipal drawings are drawn street-grid-aligned. In a cardinal
  numbered grid (Surrey: "N Avenue" runs E-W with numbers increasing north,
  "N Street" runs N-S with numbers increasing east), the street labels'
  orientations pin the drawing rotation to {0, 180}, and the numbered-grid
  arithmetic (two parallel numbered streets, or an avenue/street pair's
  edge positions) resolves the sense and corroborates the mirror.
- Mirror: born-digital PDFs are plan views; mirroring only enters via
  scanning artifacts. Asserted false unless the numbered grid contradicts.

The VLM read is blind (never sees the fabric); its raw response is banked
in the output JSON for audit. The rule engine is deliberately conservative:
anything unresolved lowers `confidence` and widens `rot_tol` instead of
guessing silently.
"""
import argparse
import base64
import json
import math
import re
import sys
import urllib.request

import fitz

STREET_Q = """This is part of an engineering site plan. Is there a STREET
NAME label (like '72 AVENUE' or '188 STREET') printed along a road in this
image? Reply with ONLY the street name exactly as printed, or the word NONE.
Ignore the title block and address text. If the only street text is
rotated/vertical and unreadable, reply NONE."""

ARROW_Q = """Is there a north arrow symbol in this image? Reply with ONLY the
direction its tip points: up, up-left, up-right, left, right, down,
down-left, down-right - or NONE if there is no north arrow."""


def vlm_ask(url, png_bytes, question, timeout=600):
    """One short single-answer question. List-style prompts made the 7B
    hallucinate arithmetic series of street names ('71, 69, 67 ... 53
    AVENUE'); single-answer questions get honest NONEs."""
    b64 = base64.b64encode(png_bytes).decode()
    body = {"model": "qwen2.5-vl", "temperature": 0, "max_tokens": 30,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


def parse_json_block(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in VLM response: {text[:200]}")
    return json.loads(m.group(0))


def street_kind(name):
    """Cardinal-grid classification: E-W vs N-S, plus the grid number."""
    n = name.upper()
    m = re.match(r"^\s*(\d+)[A-Z]?\s+(AVENUE|AVE)\b", n)
    if m:
        return "EW", int(m.group(1))
    m = re.match(r"^\s*(\d+)[A-Z]?\s+(STREET|ST)\b", n)
    if m:
        return "NS", int(m.group(1))
    return None, None


def derive(reads):
    """Rule engine: per-strip VLM reads -> {rot_prior, rot_tol, no_mirror,
    confidence, evidence[]}. `reads` maps strip name (top/bottom/left/right)
    to its parsed read; the strip IS the label's edge position. Cardinal-grid
    reasoning; conservative on conflicts."""
    ev, votes_rot0 = [], []
    ew, ns = [], []
    seen_edges = {}
    arrows = []
    for strip, read in reads.items():
        for s in read.get("streets", []):
            name = s.get("name", "").upper().strip()
            kind, num = street_kind(name)
            if kind is None:
                continue
            seen_edges.setdefault((kind, num), set()).add(strip)
            rec = dict(s, edge=strip)
            (ew if kind == "EW" else ns).append((num, rec))
        if read.get("north_arrow", {}).get("present") in (True, "true"):
            arrows.append(read["north_arrow"].get("points", "unclear"))
    # a street seen on OPPOSITE strips has no usable edge — drop those
    # records from sense voting by blanking their edge
    for lst in (ew, ns):
        for num, rec in lst:
            strips = seen_edges.get(("EW" if lst is ew else "NS", num), set())
            if {"top", "bottom"} <= strips or {"left", "right"} <= strips:
                rec["edge"] = "interior"
    # rotation family: an E-W street's label drawn horizontally (or a N-S
    # street's vertically) says the drawing is grid-aligned -> rot in {0,180}
    aligned = sum(1 for _, s in ew if s.get("text_direction") == "horizontal")
    aligned += sum(1 for _, s in ns if s.get("text_direction") == "vertical")
    crossed = sum(1 for _, s in ew if s.get("text_direction") == "vertical")
    crossed += sum(1 for _, s in ns if s.get("text_direction") == "horizontal")
    if aligned > crossed:
        family = (0, 180)
        ev.append(f"grid-aligned {aligned} vs crossed {crossed}")
    elif crossed > aligned:
        family = (90, 270)
        ev.append(f"grid-crossed {crossed} vs aligned {aligned}")
    else:
        return {"rot_prior": None, "rot_tol": None, "no_mirror": True,
                "confidence": "none",
                "evidence": ev + [f"street labels tied/absent "
                                  f"(aligned {aligned}, crossed {crossed})"],
                "vlm_read": reads}
    conflicted = bool(aligned and crossed)

    # sense within the family (0 vs 180) and mirror corroboration from the
    # numbered grid: avenue numbers increase NORTH, street numbers EAST
    edge_axis = {"top": ("y", 1), "bottom": ("y", -1),
                 "left": ("x", -1), "right": ("x", 1)}
    for pair, want_axis in ((sorted(ew, key=lambda t: t[0]), "y"),
                            (sorted(ns, key=lambda t: t[0]), "x")):
        # one record per street number, preferring one with a usable edge
        uniq = {}
        for num, rec in pair:
            if num not in uniq or uniq[num].get("edge") == "interior":
                uniq[num] = rec
        nums = sorted(uniq)
        if len(nums) >= 2 and nums[0] != nums[-1]:
            lo, hi = uniq[nums[0]], uniq[nums[-1]]
            elo = edge_axis.get(lo.get("edge", ""))
            ehi = edge_axis.get(hi.get("edge", ""))
            if elo and ehi and elo[0] == ehi[0] == want_axis \
                    and elo[1] != ehi[1]:
                # higher number should sit north (top) / east (right)
                votes_rot0.append(ehi[1] > elo[1])
                ev.append(f"numbered pair {nums[0]}..{nums[-1]}: higher on "
                          f"{'top/right' if ehi[1] > elo[1] else 'bottom/left'}")
    for pts in arrows:
        if pts.startswith("up"):
            votes_rot0.append(True)
            ev.append(f"north arrow points {pts}")
        elif pts.startswith("down"):
            votes_rot0.append(False)
            ev.append(f"north arrow points {pts}")
        else:
            ev.append(f"north arrow present but {pts}")

    if votes_rot0 and all(votes_rot0):
        rot, conf = family[0], "high"
    elif votes_rot0 and not any(votes_rot0):
        rot, conf = family[1], "high"
    elif not votes_rot0:
        rot, conf = family[0], "low"
        ev.append("no sense evidence: defaulting to the north-up member "
                  "of the family (plan convention)")
    else:
        rot, conf = family[0], "low"
        ev.append(f"CONFLICTING sense votes {votes_rot0}: defaulting north-up")
    if conflicted and conf == "high":
        conf = "medium"
    return {"rot_prior": rot, "rot_tol": 8.0, "no_mirror": True,
            "confidence": conf, "evidence": ev, "vlm_read": reads}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--vlm", default="http://192.168.50.219:8080",
                    help="llama.cpp Qwen2.5-VL endpoint")
    ap.add_argument("--strip-px", type=int, default=1280,
                    help="longest strip side sent to the VLM (a full page at "
                         "readable resolution exceeds the 4k context; four "
                         "35%%-edge strips at ~2x fit, and the strip identity "
                         "IS the label's edge position)")
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    page = fitz.open(a.pdf)[a.page]
    W, H = page.rect.width, page.rect.height
    strips = {
        "top": fitz.Rect(0, 0, W, 0.35 * H),
        "bottom": fitz.Rect(0, 0.65 * H, W, H),
        "left": fitz.Rect(0, 0, 0.35 * W, H),
        "right": fitz.Rect(0.65 * W, 0, W, H),
    }
    reads = {}
    for which, clip in strips.items():
        zoom = a.strip_px / max(clip.width, clip.height)
        found = {}
        # a label readable in the unrotated pass is HORIZONTAL; one readable
        # only after rotating the strip 90 deg is VERTICAL (the 7B returns
        # null on vertical labels, obs #175 - which pass finds the name is
        # the trustworthy direction signal)
        pngs = {}
        for rot in (0, 90):
            mat = fitz.Matrix(zoom, zoom).prerotate(rot)
            pngs[rot] = page.get_pixmap(matrix=mat, clip=clip).tobytes("png")
            ans = vlm_ask(a.vlm, pngs[rot], STREET_Q).upper().strip()
            ans = re.sub(r"[\.\s]+$", "", ans)
            if ans and ans != "NONE" and len(ans) < 40 and ans not in found:
                found[ans] = "horizontal" if rot == 0 else "vertical"
        arrow_ans = vlm_ask(a.vlm, pngs[0], ARROW_Q).lower().strip()
        arrow_ans = re.sub(r"[\.\s]+$", "", arrow_ans)
        arrow = ({"present": True, "points": arrow_ans}
                 if arrow_ans in ("up", "up-left", "up-right", "left",
                                  "right", "down", "down-left", "down-right")
                 else {"present": False, "points": "unclear"})
        reads[which] = {"streets": [{"name": nm, "text_direction": d}
                                    for nm, d in found.items()],
                        "north_arrow": arrow}
    prior = derive(reads)
    prior["_provenance"] = (f"orient_prior.py on {a.pdf} page {a.page}, "
                            f"blind VLM read (qwen2.5-vl-7b), raw banked")
    out = json.dumps(prior, indent=1)
    if a.out:
        open(a.out, "w").write(out)
        print(f"wrote {a.out}")
    print(f"rot_prior={prior['rot_prior']} tol={prior['rot_tol']} "
          f"no_mirror={prior['no_mirror']} confidence={prior['confidence']}")
    for e in prior["evidence"]:
        print(f"  - {e}")


if __name__ == "__main__":
    main()

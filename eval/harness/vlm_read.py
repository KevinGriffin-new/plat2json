#!/usr/bin/env python3
"""vlm_read.py - run the blind tile read on a LOCAL VLM (llama.cpp server).

Fills the slot the eval harness left for an "external VLM": instead of fanning
read_prompt.txt out to cloud subagents one tile at a time, this points the SAME
fixed instrument at a local llama.cpp `llama-server` running a vision model
(e.g. Qwen2.5-VL), so the blind read is free, offline, and batchable.

It reads the tiles prep_plan.py already staged, sends each (with read_prompt) to
the OpenAI-compatible /v1/chat/completions endpoint, and writes the UNION of the
per-tile JSON arrays to _sources/<slug>/_vlm_reads.json - the exact file
score_run.py consumes. Nothing else in the repo changes.

    # 1. stage tiles (existing step)
    python harness/prep_plan.py <plan.pdf|.png> <slug>
    # 2. blind read on the local VLM (this script)
    python harness/vlm_read.py <slug> --url http://127.0.0.1:8080
    # 3. score (existing step)
    python score/score_run.py <slug>

Stdlib + Pillow only (Pillow already rides with the pipeline). The reader stays
BLIND - it is never given any answer key - so the recall numbers stay honest.
"""
import argparse
import base64
import concurrent.futures as cf
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def load_readplan(slug):
    base = os.path.join(HERE, "_sources", slug)
    rp = os.path.join(base, "_readplan.json")
    if not os.path.exists(rp):
        sys.exit(f"no _readplan.json under {base} - run prep_plan.py {slug} first")
    return base, json.load(open(rp, encoding="utf-8"))


def encode_tile(path, max_side):
    """PNG -> base64, optionally downscaling the long side (VRAM/quality knob)."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if max_side and max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def extract_array(text):
    """Pull the first top-level JSON array from the reply (tolerant of a stray
    fence or prose, though read_prompt.txt forbids both)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        v = json.loads(text)
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        pass
    i, j = text.find("["), text.rfind("]")
    if i != -1 and j > i:
        try:
            v = json.loads(text[i:j + 1])
            return v if isinstance(v, list) else []
        except json.JSONDecodeError:
            return []
    return []


def read_tile(path, prompt_tmpl, url, model, max_side, temp, timeout):
    prompt = prompt_tmpl.replace("{tile}", os.path.basename(path))
    b64 = encode_tile(path, max_side)
    body = json.dumps({
        "model": model,
        "temperature": temp,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," + b64}},
        ]}],
    }).encode()
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        # surface the server's actual reason (e.g. "model has no vision encoder",
        # CUDA OOM) instead of a bare "500 Internal Server Error"
        detail = e.read().decode("utf-8", "replace")[:300].replace("\n", " ")
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    txt = out["choices"][0]["message"]["content"]
    clean = []
    for it in extract_array(txt):
        if isinstance(it, dict) and "raw" in it:
            clean.append({"raw": str(it["raw"]),
                          "kind": it.get("kind", ""),
                          "rotated": bool(it.get("rotated", False))})
    return path, clean


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--url", default="http://127.0.0.1:8080",
                    help="llama-server base url (default %(default)s)")
    ap.add_argument("--model", default="qwen2.5-vl",
                    help="model name (arbitrary for llama.cpp)")
    ap.add_argument("--max-side", type=int, default=1280,
                    help="downscale each tile's long side to N px (VRAM/quality knob)")
    ap.add_argument("--workers", type=int, default=4,
                    help="concurrent tiles (match server n_parallel slots)")
    ap.add_argument("--temp", type=float, default=0.0)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N tiles (smoke test)")
    ap.add_argument("--prompt-file", default=None,
                    help="override the read prompt (e.g. eval/read_prompt_local.txt); "
                         "default: _readplan.json template, else read_prompt.txt")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    base, plan = load_readplan(a.slug)
    tiles = plan.get("tiles", [])
    if a.limit:
        tiles = tiles[:a.limit]
    if not tiles:
        sys.exit("no tiles in _readplan.json")
    if a.prompt_file:
        prompt_tmpl = open(a.prompt_file, encoding="utf-8").read()
    else:
        prompt_tmpl = plan.get("read_prompt_template") or \
            open(os.path.join(HERE, "..", "read_prompt.txt"), encoding="utf-8").read()
    out_path = a.out or os.path.join(base, "_vlm_reads.json")

    print(f"[{a.slug}] {len(tiles)} tiles -> {a.url}  "
          f"(model={a.model}, max_side={a.max_side}, workers={a.workers})")
    union, done, failed = [], 0, 0
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(read_tile, t, prompt_tmpl, a.url, a.model,
                          a.max_side, a.temp, a.timeout): t for t in tiles}
        for fut in cf.as_completed(futs):
            t = futs[fut]
            try:
                _, items = fut.result()
                union.extend(items)
                done += 1
                print(f"  ok   {os.path.basename(t):22s} +{len(items):3d}  "
                      f"({done}/{len(tiles)})")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  FAIL {os.path.basename(t):22s} {type(e).__name__}: {e}")

    json.dump(union, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    nb = sum(1 for x in union if x["kind"] == "bearing")
    nd = sum(1 for x in union if x["kind"] == "distance")
    print(f"\n-> {out_path}: {len(union)} labels "
          f"({nb} bearings, {nd} distances) from {done} tiles, {failed} failed")
    print(f"   next: python {os.path.join('score', 'score_run.py')} {a.slug}")


if __name__ == "__main__":
    main()

# Local VLM blind reader (llama.cpp + Qwen2.5-VL)

The `eval/` harness was built to score a **blind label read** done by "any capable
external VLM" — historically a cloud model, fanned out one tile at a time. This
wires that slot to a **local** `llama.cpp` server instead, so the read is free,
offline, batchable, and auto-scored against the committed goldens.

```
prep_plan.py → tiles/*.png → vlm_read.py (local Qwen2.5-VL) → _vlm_reads.json → score_run.py
```

## Result (the reason this exists)

On `glo_albany_t28nr71w` (BLM cadastral plat, raster), a fully local **Qwen2.5-VL-7B
(Q4_K_M)** on a single **RTX 4060 (8 GB)** scored, against the surveyor's field-note key:

| reader | bearings | distances |
|--------|----------|-----------|
| Qwen2.5-VL-**3B** | 0 / 21 | 0 / 9 |
| Qwen2.5-VL-**7B** | **13 / 21** | **7 / 9** |

(Recall is *understated* — many correct reads are interior subdivision courses that
aren't in the exterior field-note key.) The 3B can read large text but not the small
rotated DMS courses; the 7B can. **The 7B is the working baseline.**

## One-time setup

```bash
# 1. CUDA toolkit (you need nvcc to BUILD; the driver alone is not enough)
sudo apt-get install -y nvidia-cuda-toolkit build-essential cmake git libcurl4-openssl-dev

# 2. build llama.cpp with CUDA (4060 = Ada = arch 89)
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build --config Release -j"$(nproc)"

# 3. python deps for the harness (use a venv; Debian/Ubuntu block system pip)
cd ~/plat2json && python3 -m venv .venv && source .venv/bin/activate
pip install -r eval/requirements.txt
```

## Run it

```bash
# serve the vision model in the background (logs to a file; NOT tmux — see Tuning #4)
pkill -f llama-server; sleep 2
nohup bash serve_vl7.sh >/dev/null 2>&1 &     # first run downloads ~6 GB to ~/models/
tail -f ~/llama_vl7.log                         # Ctrl-C when you see "server is listening"
curl -s localhost:8080/v1/models | grep -o '"id":"[^"]*"'   # confirm it's up

# stage a plan, read it, score it
python eval/harness/acquire.py albany/t28nr71w                 # -> slug glo_albany_t28nr71w
ln -s ../harness/_sources eval/score/_sources                  # ONE-TIME path bridge (see note)
python eval/harness/vlm_read.py glo_albany_t28nr71w \
    --workers 1 --max-side 1536 --prompt-file eval/read_prompt_local.txt
python eval/score/score_run.py glo_albany_t28nr71w \
    --gt eval/goldens/glo_t28nr71w.fieldnote_key.json
```

> **Path bridge:** `prep_plan.py`/`acquire.py` write under `eval/harness/_sources/`,
> but the scorers read `eval/score/_sources/`. The `ln -s` above makes them agree.

Two server scripts are provided:
- **`serve_vl7.sh`** — Qwen2.5-VL-**7B** Q4_K_M + F16 projector, `-c 4096`. The reader. ~6.7 GB.
- **`serve_vl.sh`** — Qwen2.5-VL-**3B**, `-c 8192`. Faster/lighter; fine for big text, weak on courses.

## Knobs (`vlm_read.py`)

| flag | what | notes |
|------|------|-------|
| `--workers N` | concurrent tiles | **keep at 1 on 8 GB** (see Tuning #3) |
| `--max-side N` | downscale tile long side | the resolution/VRAM dial; **1536** is the 8 GB sweet spot |
| `--prompt-file P` | override the read prompt | use `eval/read_prompt_local.txt` (see Tuning #5) |
| `--limit N` | only first N tiles | smoke test |

## How it was tuned for 8 GB (the journey, so you don't repeat it)

Every one of these was a real failure with a real fix:

| # | symptom | cause | fix |
|---|---------|-------|-----|
| 1 | every image → `HTTP 500` | a **text-only** model was on `:8080` (no vision encoder) | serve a VL model **with its `--mmproj`** |
| 2 | model swap "didn't take" (`/v1/models` still text) | old server still **held the port**; new one couldn't bind | `pkill -9 -f llama-server` **and** `systemctl --user stop/disable llama-server`; verify `port 8080 free` |
| 3 | `failed to find a memory slot for batch of size 2048` → **segfault** | **4 concurrent image decodes** (default `--parallel 4` × client `workers 4`) contend for KV slots | server `--parallel 1` **and** client `--workers 1` |
| 4 | flaky SSH/tmux session kept **dropping** | server's verbose log **firehose** drowned a mobile link | serve scripts redirect to `~/llama_vl*.log`; run with `nohup` (no tmux) |
| 5 | every "bearing" identical: `156° 48' 05"` | the 3B **parroted read_prompt.txt's literal example** when it couldn't read | `eval/read_prompt_local.txt` has **no copyable example**; pass via `--prompt-file`. Failures become honest (omit, don't fake) |
| 6 | honest read = **0 labels** | 3B genuinely can't resolve small rotated DMS | step up to the **7B** (`serve_vl7.sh`) |
| 7 | 7B: `request (4385 tokens) exceeds context (4096)` | 7B vision encoder emits **more tokens/image**; a 2048px tile = ~4385 | don't raise context — **lower `--max-side` to 1536** (~2500 tokens) |
| 8 | 7B `-c 8192` → **OOM `Aborted (core dumped)`** on first decode | bigger context left only 900 MiB; the **image-decode activations** (not the KV cache) are the binding VRAM constraint | keep `-c 4096` + `--max-side 1536` → ~1.1 GB headroom for activations |

**The two load-bearing lessons:** on a small GPU, (a) serialize everything
(`--parallel 1`, `--workers 1`) — concurrent image decodes, not total VRAM, are what
segfault you; and (b) the binding VRAM constraint is the **per-image activation
spike**, so trade *image size* (`--max-side`) before *context* (`-c`).

## Next levers (to chase the remaining misses)

The stragglers are the faintest/smallest glyphs. In impact order:
1. **Smaller tiles at full resolution** — `prep_plan.py --tile 1100`: each glyph stays
   sharp without a huge image blowing VRAM (decouples glyph sharpness from image size).
2. **Tighten the ignore-list** — `M.S.`/lode-name/`A.P.` labels still slip in tagged as
   bearings; harmless to recall, but post-filter or harden the prompt.

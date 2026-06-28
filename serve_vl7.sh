#!/usr/bin/env bash
# Launch Qwen2.5-VL-7B (Q4_K_M) for the plat2json blind read on an 8 GB GPU.
# A stronger reader than the 3B for the small rotated DMS courses, at the cost of
# nearly the whole card. STOP the 3B first (frees ~8 GB) and keep --parallel 1.
#
# Usage (after `pkill -f llama-server`):
#     nohup bash serve_vl7.sh >/dev/null 2>&1 &
#     tail -f ~/llama_vl7.log        # watch download + load; Ctrl-C when "listening"
#
# First run downloads ~6 GB (model + F16 vision projector) to ~/models/ (resumable).
# Confirm up:  curl -s localhost:8080/v1/models | grep -o '"id":"[^"]*"'
set -euo pipefail

BIN="$HOME/llama.cpp/build/bin/llama-server"
DIR="$HOME/models/qwen2.5-vl-7b"
REPO="https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main"
MODEL="$DIR/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf"
MMPROJ="$DIR/mmproj-F16.gguf"
LOG="$HOME/llama_vl7.log"

mkdir -p "$DIR"
[ -f "$MODEL" ]  || { echo "downloading 7B Q4_K_M (~4.7 GB)…"; curl -fL -C - -o "$MODEL"  "$REPO/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf"; }
[ -f "$MMPROJ" ] || { echo "downloading vision projector F16 (~1.3 GB)…"; curl -fL -C - -o "$MMPROJ" "$REPO/mmproj-F16.gguf"; }

echo "starting Qwen2.5-VL-7B on :8080  ->  logging to $LOG"
exec "$BIN" \
  -m "$MODEL" --mmproj "$MMPROJ" \
  --host 0.0.0.0 --port 8080 \
  -ngl 99 -fa on \
  --parallel 1 --cache-ram 0 \
  -c 8192 \
  -lv 1 \
  >"$LOG" 2>&1

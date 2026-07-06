#!/usr/bin/env bash
# Serve Qwen3-VL-8B-Instruct Q4_K_M on :8080 for the A/B vs Qwen2.5-VL-7B.
# Same 8 GB discipline as serve_vl7.sh (--parallel 1, -c 4096) plus the newer
# mtmd knobs that cap the image-encode activation spike (the binding constraint).
set -euo pipefail
BIN="$HOME/llama.cpp/build/bin/llama-server"
DIR="$HOME/models/qwen3-vl-8b"
REPO="https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF/resolve/main"
MODEL="$DIR/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
MMPROJ="$DIR/mmproj-F16.gguf"
LOG="$HOME/llama_vl8.log"

mkdir -p "$DIR"
[ -f "$MODEL" ]  || curl -fL -C - -o "$MODEL"  "$REPO/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
[ -f "$MMPROJ" ] || curl -fL -C - -o "$MMPROJ" "$REPO/mmproj-F16.gguf"

exec "$BIN" \
  -m "$MODEL" --mmproj "$MMPROJ" \
  --host 0.0.0.0 --port 8080 \
  -ngl 99 -fa on \
  --parallel 1 --cache-ram 0 \
  -c 4096 \
  --mtmd-batch-max-tokens 512 --image-max-tokens 1024 \
  >"$LOG" 2>&1

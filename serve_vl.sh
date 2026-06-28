#!/usr/bin/env bash
# Launch the local Qwen2.5-VL server for the plat2json blind read.
# Tuned for an 8 GB GPU (RTX 4060): single slot, prompt cache off, roomy context
# so a tile's ~2k image tokens can seat without the "failed to find a memory
# slot" segfault.
#
# Usage (inside tmux so an SSH drop can't kill it):
#     tmux new -s vlm        # or: tmux attach -t vlm
#     bash serve_vl.sh
#
# All server output goes to ~/llama_vl.log, NOT the terminal, so a flaky mobile
# link doesn't drown in the server's verbose logging (that flood is what was
# dropping the SSH/tmux session). Then detach with: Ctrl-b  d
#
# Watch startup:   tail -f ~/llama_vl.log
# Confirm it's up: curl -s localhost:8080/v1/models | grep -o '"id":"[^"]*"'
set -euo pipefail

BIN="$HOME/llama.cpp/build/bin/llama-server"
LOG="$HOME/llama_vl.log"

echo "starting Qwen2.5-VL-3B on :8080  ->  logging to $LOG"
echo "(terminal stays quiet on purpose; detach with Ctrl-b d, check with curl)"
exec "$BIN" \
  -hf ggml-org/Qwen2.5-VL-3B-Instruct-GGUF \
  --host 0.0.0.0 --port 8080 \
  -ngl 99 -fa on \
  --parallel 1 --cache-ram 0 \
  -c 8192 \
  -lv 1 \
  >"$LOG" 2>&1

#!/usr/bin/env bash
# A/B: Qwen3-VL-8B vs the Qwen2.5-VL-7B baseline on the 4 vector-golden sheets.
# Same tiles, same prompt, same scorer; only the served model changes.
# Baseline for comparison = the s0 (temp 0) rows in consensus_summary.tsv.
# Ends by killing the server so lib.sh's ensure_server restores the 7B after.
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/ab_qwen3vl8.tsv"
printf "sheet\tmodel\tbearings\tdistances\tseconds\n" > "$SUMMARY"
declare -A GOLD=(
  [county_test_t1100]=county_test.key_p0.json
  [adams_prc24_12]=adams_prc24_12.key_p42.json
  [adams_prc2025]=adams_prc2025.key_p1.json
  [adams_wolfcreek]=adams_wolfcreek.key_p19.json
)

qlog "switching server to Qwen3-VL-8B (first run downloads ~6.2 GB)"
pkill -f "[l]lama-server"; sleep 4
nohup bash "$QDIR/serve_vl8.sh" >> "$QDIR/logs/server_vl8.log" 2>&1 </dev/null &
up=0
for i in $(seq 1 360); do   # generous: covers the model download
  sleep 10
  if curl -s --max-time 3 "$URL/v1/models" 2>/dev/null | grep -q "Qwen3-VL-8B"; then up=1; break; fi
done
[ "$up" = "1" ] || { qlog "Qwen3-VL-8B server never came up"; pkill -f "[l]lama-server"; exit 1; }
qlog "Qwen3-VL-8B serving"

rc=0
for sheet in county_test_t1100 adams_prc24_12 adams_prc2025 adams_wolfcreek; do
  [ -d "$EVAL/harness/_sources/$sheet/tiles" ] || continue
  qlog "read $sheet (qwen3-vl-8b)"
  t0=$(date +%s)
  out="$RESULTS/reads/${sheet}_q3vl8.json"
  if ! "$PY" "$EVAL/harness/vlm_read.py" "$sheet" --workers 1 --prompt-file "$PROMPT" --out "$out"; then
    qlog "read FAILED $sheet"; rc=1; continue
  fi
  secs=$(( $(date +%s) - t0 ))
  cp "$out" "$EVAL/harness/_sources/$sheet/_vlm_reads.json"
  res=$("$PY" "$EVAL/score/score_run.py" "$sheet" --gt "$EVAL/goldens/${GOLD[$sheet]}" 2>&1)
  echo "$res"
  b=$(echo "$res" | grep -o 'bearing recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
  d=$(echo "$res" | grep -o 'distance recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
  printf "%s\tqwen3-vl-8b\t%s\t%s\t%s\n" "$sheet" "${b:-NA}" "${d:-NA}" "$secs" >> "$SUMMARY"
done

qlog "A/B done; releasing GPU back to the 7B"
pkill -f "[l]lama-server"; sleep 3
exit $rc

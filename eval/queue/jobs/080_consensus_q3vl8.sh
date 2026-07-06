#!/usr/bin/env bash
# Wave 4: consensus test for Qwen3-VL-8B (the fair fight vs the 7B union,
# which beat the 32B single-pass in wave 1). 5 reads/sheet, union + maj2.
# Manages its own server (lib's ensure_server would relaunch the 7B).
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/consensus_q3vl8.tsv"
printf "sheet\tvariant\tbearings\tdistances\n" > "$SUMMARY"
declare -A GOLD=(
  [county_test_t1100]=county_test.key_p0.json
  [adams_prc24_12]=adams_prc24_12.key_p42.json
  [adams_prc2025]=adams_prc2025.key_p1.json
  [adams_wolfcreek]=adams_wolfcreek.key_p19.json
)
ensure_vl8() {
  curl -s --max-time 4 "$URL/v1/models" 2>/dev/null | grep -q "Qwen3-VL-8B" && return 0
  qlog "launching Qwen3-VL-8B"
  pkill -f "[l]lama-server"; sleep 4
  nohup bash "$QDIR/serve_vl8.sh" >> "$QDIR/logs/server_vl8.log" 2>&1 </dev/null &
  for i in $(seq 1 60); do
    sleep 5
    curl -s --max-time 3 "$URL/v1/models" 2>/dev/null | grep -q "Qwen3-VL-8B" && return 0
  done
  qlog "Qwen3-VL-8B FAILED to come up"; return 1
}
for sheet in county_test_t1100 adams_prc24_12 adams_prc2025 adams_wolfcreek; do
  [ -d "$EVAL/harness/_sources/$sheet/tiles" ] || continue
  for k in 0 1 2 3 4; do
    out="$RESULTS/reads/${sheet}_q3s${k}.json"
    [ -e "$out" ] && continue
    ensure_vl8 || { pkill -f "[l]lama-server"; exit 1; }
    t="0.7"; [ "$k" -eq 0 ] && t="0.0"
    qlog "read $sheet q3 sample$k temp=$t"
    "$PY" "$EVAL/harness/vlm_read.py" "$sheet" --workers 1 --temp "$t" \
        --prompt-file "$PROMPT" --out "$out" || qlog "read FAILED $sheet q3s$k"
  done
  for variant in union maj2; do
    tgt="$EVAL/harness/_sources/$sheet/_vlm_reads.json"
    "$PY" "$QDIR/union_reads.py" "$variant" "$RESULTS/reads/${sheet}"_q3s*.json > "$tgt" || continue
    res=$("$PY" "$EVAL/score/score_run.py" "$sheet" --gt "$EVAL/goldens/${GOLD[$sheet]}" 2>&1)
    b=$(echo "$res" | grep -o 'bearing recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
    d=$(echo "$res" | grep -o 'distance recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
    printf "%s\t%s\t%s\t%s\n" "$sheet" "$variant" "${b:-NA}" "${d:-NA}" >> "$SUMMARY"
  done
done
qlog "q3vl8 consensus done; releasing GPU back to the 7B"
pkill -f "[l]lama-server"; sleep 3
exit 0

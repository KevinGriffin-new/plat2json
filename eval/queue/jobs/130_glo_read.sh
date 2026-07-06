#!/usr/bin/env bash
# Wave 5: 7B read of the GLO pilot sheets staged by 120 (GPU, small batch).
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/glo_summary.tsv"
[ -s "$SUMMARY" ] || printf "slug\tbearings\tdistances\tfrag\tseconds\n" > "$SUMMARY"
for d in "$EVAL/harness/_sources"/glo_dm*/; do
  [ -d "$d/tiles" ] || continue
  slug=$(basename "$d")
  [ -e "$RESULTS/reads/${slug}.json" ] && continue
  ensure_server || { qlog "server unrecoverable"; exit 1; }
  qlog "read $slug"
  t0=$(date +%s)
  "$PY" "$EVAL/harness/vlm_read.py" "$slug" --workers 1 --prompt-file "$PROMPT" || { qlog "read FAILED $slug"; continue; }
  secs=$(( $(date +%s) - t0 ))
  res=$("$PY" "$EVAL/score/score_run.py" "$slug" 2>&1); echo "$res"
  nb=$(echo "$res" | grep -o '[0-9]* complete bearings' | grep -o '^[0-9]*')
  nd=$(echo "$res" | grep -o '[0-9]* distances,' | grep -o '^[0-9]*')
  nf=$(echo "$res" | grep -o '[0-9]* fragments' | grep -o '^[0-9]*')
  printf "%s\t%s\t%s\t%s\t%s\n" "$slug" "${nb:-NA}" "${nd:-NA}" "${nf:-NA}" "$secs" >> "$SUMMARY"
  cp "$d/_vlm_reads.json" "$RESULTS/reads/${slug}.json"
done
qlog "glo pilot reads done"

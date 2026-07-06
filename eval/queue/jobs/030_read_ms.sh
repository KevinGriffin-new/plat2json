#!/usr/bin/env bash
# 7B blind read + self-check score of every staged blm_ms* sheet.
# Resumable: skips any slug whose read is already in results/reads/.
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/ms_summary.tsv"
[ -s "$SUMMARY" ] || printf "slug\tbearings\tdistances\tfrag\tself_b\tself_d\tseconds\n" > "$SUMMARY"
for d in "$EVAL/harness/_sources"/blm_ms*/; do
  [ -d "$d/tiles" ] || continue
  slug=$(basename "$d")
  [ -e "$RESULTS/reads/${slug}.json" ] && { qlog "skip $slug (done)"; continue; }
  ensure_server || { qlog "server unrecoverable -- abort job"; exit 1; }
  qlog "read $slug"
  t0=$(date +%s)
  if ! "$PY" "$EVAL/harness/vlm_read.py" "$slug" --workers 1 --prompt-file "$PROMPT"; then
    qlog "read FAILED $slug"; continue
  fi
  secs=$(( $(date +%s) - t0 ))
  res=$("$PY" "$EVAL/score/score_run.py" "$slug" 2>&1); echo "$res"
  nb=$(echo "$res" | grep -o '[0-9]* complete bearings' | grep -o '^[0-9]*')
  nd=$(echo "$res" | grep -o '[0-9]* distances' | head -1 | grep -o '^[0-9]*')
  nf=$(echo "$res" | grep -o '[0-9]* fragments' | grep -o '^[0-9]*')
  sb=$(echo "$res" | grep -o 'bearings [0-9]*/[0-9]* match' | grep -o '[0-9]*/[0-9]*')
  sd=$(echo "$res" | grep -o 'distances [0-9]*/[0-9]* match' | grep -o '[0-9]*/[0-9]*')
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$slug" "${nb:-NA}" "${nd:-NA}" "${nf:-NA}" "${sb:-NA}" "${sd:-NA}" "$secs" >> "$SUMMARY"
  cp "$d/_vlm_reads.json" "$RESULTS/reads/${slug}.json"
done
qlog "ms read sweep done"

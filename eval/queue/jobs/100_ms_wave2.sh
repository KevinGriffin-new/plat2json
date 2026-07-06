#!/usr/bin/env bash
# Wave 4: the remaining mineral surveys (items 81..end of ms_available.txt).
# Acquire and read INTERLEAVED so the GPU never sits idle behind downloads.
# Fully resumable via the results/reads/ check. This is the multi-day tail.
source "$HOME/plat-queue/lib.sh"
LIST="$RESULTS/ms_available.txt"
SUMMARY="$RESULTS/ms_summary.tsv"
[ -s "$LIST" ] || { qlog "no ms_available.txt"; exit 1; }
n=0
while read -r item; do
  n=$((n+1)); [ "$n" -le 80 ] && continue     # wave 1 covered 1..80
  ms=${item#*/}; slug="blm_$ms"
  [ -e "$RESULTS/reads/${slug}.json" ] && continue
  if [ ! -d "$EVAL/harness/_sources/$slug/tiles" ]; then
    qlog "acquire $item"
    "$PY" "$QDIR/acquire_ms.py" "$item" || { qlog "FAILED acquire $item"; continue; }
    sleep 3
  fi
  ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
  qlog "read $slug ($n/$(wc -l < "$LIST"))"
  t0=$(date +%s)
  "$PY" "$EVAL/harness/vlm_read.py" "$slug" --workers 1 --prompt-file "$PROMPT" || { qlog "read FAILED $slug"; continue; }
  secs=$(( $(date +%s) - t0 ))
  res=$("$PY" "$EVAL/score/score_run.py" "$slug" 2>&1); echo "$res"
  nb=$(echo "$res" | grep -o '[0-9]* complete bearings' | grep -o '^[0-9]*')
  nd=$(echo "$res" | grep -o '[0-9]* distances' | head -1 | grep -o '^[0-9]*')
  nf=$(echo "$res" | grep -o '[0-9]* fragments' | grep -o '^[0-9]*')
  sb=$(echo "$res" | grep -o 'bearings [0-9]*/[0-9]* match' | grep -o '[0-9]*/[0-9]*')
  sd=$(echo "$res" | grep -o 'distances [0-9]*/[0-9]* match' | grep -o '[0-9]*/[0-9]*')
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$slug" "${nb:-NA}" "${nd:-NA}" "${nf:-NA}" "${sb:-NA}" "${sd:-NA}" "$secs" >> "$SUMMARY"
  cp "$EVAL/harness/_sources/$slug/_vlm_reads.json" "$RESULTS/reads/${slug}.json"
done < "$LIST"
qlog "ms wave2 done"

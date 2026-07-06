#!/usr/bin/env bash
# Wave 6: stage + read ALL mineral surveys in results/glo_index.tsv (the 244
# Montana cluster from the 120 scout), using the fixed native-resolution
# stager (glo_stage.py). Interleaved and resumable, like the ms jobs.
# Public domain source -- images are committable if any become goldens.
source "$HOME/plat-queue/lib.sh"
IDX="$RESULTS/glo_index.tsv"
[ -s "$IDX" ] || { qlog "no glo_index.tsv"; exit 1; }
SUMMARY="$RESULTS/glo_summary.tsv"
[ -s "$SUMMARY" ] || printf "slug\tbearings\tdistances\tfrag\tseconds\n" > "$SUMMARY"
total=$(awk -F'\t' 'NR>1 && $2==1' "$IDX" | wc -l); n=0
awk -F'\t' 'NR>1 && $2==1 {print $1}' "$IDX" | while read -r dm; do
  n=$((n+1)); slug="glo_dm$dm"
  [ -e "$RESULTS/reads/${slug}.json" ] && continue
  if ! "$PY" "$QDIR/glo_stage.py" "$dm"; then qlog "stage FAILED $slug"; sleep 3; continue; fi
  sleep 3
  ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
  qlog "read $slug ($n/$total)"
  t0=$(date +%s)
  "$PY" "$EVAL/harness/vlm_read.py" "$slug" --workers 1 --prompt-file "$PROMPT" || { qlog "read FAILED $slug"; continue; }
  secs=$(( $(date +%s) - t0 ))
  res=$("$PY" "$EVAL/score/score_run.py" "$slug" 2>&1); echo "$res"
  nb=$(echo "$res" | grep -o '[0-9]* complete bearings' | grep -o '^[0-9]*')
  nd=$(echo "$res" | grep -o '[0-9]* distances,' | grep -o '^[0-9]*')
  nf=$(echo "$res" | grep -o '[0-9]* fragments' | grep -o '^[0-9]*')
  printf "%s\t%s\t%s\t%s\t%s\n" "$slug" "${nb:-NA}" "${nd:-NA}" "${nf:-NA}" "$secs" >> "$SUMMARY"
  cp "$EVAL/harness/_sources/$slug/_vlm_reads.json" "$RESULTS/reads/${slug}.json"
done
qlog "glo MT harvest done"

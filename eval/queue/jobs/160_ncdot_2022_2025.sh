#!/usr/bin/env bash
# Wave 5: NCDOT years 2025 + 2022 (harvester skips already-harvested slugs),
# then read+score any unread ncdot_* sheet.
source "$HOME/plat-queue/lib.sh"
"$PY" "$QDIR/ncdot_harvest.py" 40 \
  "/dsplan/2025%20highway%20letting/" "/dsplan/2022%20highway%20letting/" \
  || qlog "harvest exited nonzero (continuing to reads)"
SUMMARY="$RESULTS/ncdot_summary.tsv"
for d in "$EVAL/harness/_sources"/ncdot_*/; do
  [ -d "$d/tiles" ] || continue
  slug=$(basename "$d")
  gt=$(ls "$EVAL/goldens/${slug}".key_p*.json 2>/dev/null | head -1)
  [ -n "$gt" ] || continue
  [ -e "$RESULTS/reads/${slug}.json" ] && continue
  ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
  qlog "read $slug"
  t0=$(date +%s)
  "$PY" "$EVAL/harness/vlm_read.py" "$slug" --workers 1 --prompt-file "$PROMPT" || { qlog "read FAILED $slug"; continue; }
  secs=$(( $(date +%s) - t0 ))
  res=$("$PY" "$EVAL/score/score_run.py" "$slug" --gt "$gt" 2>&1); echo "$res"
  b=$(echo "$res" | grep -o 'bearing recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
  d2=$(echo "$res" | grep -o 'distance recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
  printf "%s\t%s\t%s\t%s\n" "$slug" "${b:-NA}" "${d2:-NA}" "$secs" >> "$SUMMARY"
  cp "$d/_vlm_reads.json" "$RESULTS/reads/${slug}.json"
done
qlog "ncdot 2022/2025 done"

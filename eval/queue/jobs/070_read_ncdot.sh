#!/usr/bin/env bash
# Wave 3: 7B blind read of each harvested NCDOT sheet, scored vs its
# vector-text golden. Resumable (skips slugs already in results/reads/).
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/ncdot_summary.tsv"
[ -s "$SUMMARY" ] || printf "slug\tbearings\tdistances\tseconds\n" > "$SUMMARY"
for d in "$EVAL/harness/_sources"/ncdot_*/; do
  [ -d "$d/tiles" ] || continue
  slug=$(basename "$d")
  gt=$(ls "$EVAL/goldens/${slug}".key_p*.json 2>/dev/null | head -1)
  [ -n "$gt" ] || { qlog "no golden for $slug, skip"; continue; }
  [ -e "$RESULTS/reads/${slug}.json" ] && { qlog "skip $slug (done)"; continue; }
  ensure_server || { qlog "server unrecoverable -- abort job"; exit 1; }
  qlog "read $slug (gt=$(basename "$gt"))"
  t0=$(date +%s)
  if ! "$PY" "$EVAL/harness/vlm_read.py" "$slug" --workers 1 --prompt-file "$PROMPT"; then
    qlog "read FAILED $slug"; continue
  fi
  secs=$(( $(date +%s) - t0 ))
  res=$("$PY" "$EVAL/score/score_run.py" "$slug" --gt "$gt" 2>&1); echo "$res"
  b=$(echo "$res" | grep -o 'bearing recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
  d2=$(echo "$res" | grep -o 'distance recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
  printf "%s\t%s\t%s\t%s\n" "$slug" "${b:-NA}" "${d2:-NA}" "$secs" >> "$SUMMARY"
  cp "$d/_vlm_reads.json" "$RESULTS/reads/${slug}.json"
done
qlog "ncdot read sweep done"

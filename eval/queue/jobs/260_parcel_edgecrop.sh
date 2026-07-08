#!/usr/bin/env bash
# Wave 9: EDGE-CROP parcel run — the production association design. Job 250's
# per-CHAIN strips were unusable on uniform lot fabric: one window shows a
# whole neighbourhood of look-alike labels (50.00' x N) and no aligner can
# un-mix them. Here the stage planarizes FIRST and crops one strip per atomic
# edge: crop index = edge id, association true by construction. Assembler
# consumes the staged planar graph verbatim (key.planar).
# NOTE: never name a variable URL/PJ/EVAL/PY/QDIR/RESULTS/PROMPT (lib.sh).
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/closure_summary.tsv"
[ -s "$SUMMARY" ] || printf "sheet\tcourses\tclean\trings\tworst_precision\tneeds_human\tseconds\n" > "$SUMMARY"
PP="$RESULTS/parcel_pdfs"

for slug in county_test adams_prc24_12 adams_prc2025; do
  gt=$(ls "$EVAL"/goldens/"${slug}".key_p*.json 2>/dev/null | head -1)
  [ -n "$gt" ] || { qlog "no golden for $slug -- skip"; continue; }
  [ -f "$PP/$slug.pdf" ] || { qlog "no pdf for $slug -- skip"; continue; }
  page=$(basename "$gt" | sed 's/.*key_p\([0-9]*\)\.json/\1/')
  vslug="${slug}__edge"
  [ -e "$RESULTS/reads/${vslug}.plan_assoc.json" ] && { qlog "skip $vslug (done)"; continue; }
  ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
  qlog "edgecrop $vslug (page $page)"
  t0=$(date +%s)
  "$PY" "$EVAL/harness/assoc_study.py" "$PP/$slug.pdf" "$page" "$vslug" \
        --gt "$gt" --crop-edges --workers 1 \
    || { qlog "assoc FAILED $vslug"; continue; }
  out=$("$PY" "$PJ/cogo_assemble.py" \
        --key "$EVAL/harness/_sources/$vslug/_assoc_key.json" \
        --reads "$EVAL/harness/_sources/$vslug/_assoc_reads.json" \
        --out "$EVAL/harness/_sources/$vslug/_plan_assoc.json" 2>&1) \
    || { qlog "assemble FAILED $vslug"; echo "$out" | tail -5; continue; }
  echo "$out"
  secs=$(( $(date +%s) - t0 ))
  co=$(echo "$out" | grep -o 'courses: [0-9]*' | grep -o '[0-9]*' | head -1)
  cl=$(echo "$out" | grep -o '([0-9]* clean' | grep -o '[0-9]*' | head -1)
  ri=$(echo "$out" | grep -o 'rings closed: [0-9]*' | grep -o '[0-9]*' | head -1)
  wp=$(echo "$out" | grep -o '1:[0-9]*' | sort -t: -k2 -n | head -1)
  nh=$(echo "$out" | grep -o 'needs_human: \w*' | awk '{print $2}')
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "${slug}__edge" "${co:-NA}" "${cl:-NA}" \
    "${ri:-0}" "${wp:-NA}" "${nh:-NA}" "$secs" >> "$SUMMARY"
  cp "$EVAL/harness/_sources/$vslug/_plan_assoc.json"  "$RESULTS/reads/${vslug}.plan_assoc.json"
  cp "$EVAL/harness/_sources/$vslug/_assoc_reads.json" "$RESULTS/reads/${vslug}.assoc.json" 2>/dev/null
  cp "$EVAL/harness/_sources/$vslug/_assoc_key.json"   "$RESULTS/reads/${vslug}.assoc_key.json" 2>/dev/null
done
qlog "parcel edgecrop done"

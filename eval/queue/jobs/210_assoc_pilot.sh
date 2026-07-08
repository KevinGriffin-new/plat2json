#!/usr/bin/env bash
# Wave 8: label->segment ASSOCIATION pilot (geometry-guided per-segment crops).
# Smoke-tests assoc_study.py end-to-end on 2 sheets before the full study (220):
#   - randolph (dense-sheet failure; wave-7 d400t900 rescued it 12/75 -> 67/75,
#     so per-segment crops should read it too IF the crop/prompt scheme works)
#   - the first control sheet (alphabetical, high-recall population)
# Capped at 60 labeled segments per sheet to keep the pilot under ~1 h.
# Golden = the sheet's own vector text layer WITH positions (built by the stage
# phase); committed key is passed for the pooled score_run comparison row.
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/assoc_summary.tsv"
[ -s "$SUMMARY" ] || printf "sheet\tvariant\tbind_brg\tbind_dist\tanyw_brg\tanyw_dist\tspurious\tpooled_brg\tpooled_dist\tseconds\n" > "$SUMMARY"
P="$RESULTS/ncdot_pdfs"

DENSE_EXCLUDE="yadkin_u_5809|forsyth_u_5536|cleveland|randolph_u_5813"
CONTROL1=$(ls "$EVAL"/goldens/ncdot_*.key_p*.json | sed 's/.*\///;s/\.key_p[0-9]*\.json//' \
  | grep -Ev "$DENSE_EXCLUDE" | sort | head -1)

for slug in ncdot_randolph_u_5813_c204843 "$CONTROL1"; do
  gt=$(ls "$EVAL"/goldens/"${slug}".key_p*.json 2>/dev/null | head -1)
  [ -n "$gt" ] || { qlog "no golden for $slug -- skip"; continue; }
  [ -f "$P/$slug.pdf" ] || { qlog "no pdf for $slug -- skip"; continue; }
  page=$(basename "$gt" | sed 's/.*key_p\([0-9]*\)\.json/\1/')
  vslug="${slug}__assocpilot"
  [ -e "$RESULTS/reads/${vslug}.assoc.json" ] && { qlog "skip $vslug (done)"; continue; }
  ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
  qlog "assoc pilot $vslug (page $page)"
  t0=$(date +%s)
  out=$("$PY" "$EVAL/harness/assoc_study.py" "$P/$slug.pdf" "$page" "$vslug" \
        --gt "$gt" --max-segs 60 --unlabeled 0.5 --workers 1 2>&1) \
    || { qlog "assoc FAILED $vslug"; echo "$out" | tail -5; continue; }
  echo "$out"
  secs=$(( $(date +%s) - t0 ))
  res=$("$PY" "$EVAL/score/score_run.py" "$vslug" --gt "$gt" 2>&1); echo "$res"
  bb=$(echo "$out" | grep -o 'binding recall: bearings [0-9]*/[0-9]*'  | grep -o '[0-9]*/[0-9]*' | head -1)
  bd=$(echo "$out" | grep -o 'distances [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*' | head -1)
  ab=$(echo "$out" | grep -o 'anywhere recall: bearings [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*' | head -1)
  ad=$(echo "$out" | grep 'anywhere recall' | grep -o 'distances [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*' | head -1)
  sp=$(echo "$out" | grep -o 'controls: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*' | head -1)
  pb=$(echo "$res" | grep -o 'bearing recall: [0-9]*/[0-9]*'  | grep -o '[0-9]*/[0-9]*' | head -1)
  pd=$(echo "$res" | grep -o 'distance recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*' | head -1)
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$slug" "assocpilot" \
    "${bb:-NA}" "${bd:-NA}" "${ab:-NA}" "${ad:-NA}" "${sp:-NA}" "${pb:-NA}" "${pd:-NA}" "$secs" >> "$SUMMARY"
  cp "$EVAL/harness/_sources/$vslug/_assoc_reads.json" "$RESULTS/reads/${vslug}.assoc.json"
  cp "$EVAL/harness/_sources/$vslug/_assoc_key.json"   "$RESULTS/reads/${vslug}.assoc_key.json"
done
qlog "assoc pilot done"

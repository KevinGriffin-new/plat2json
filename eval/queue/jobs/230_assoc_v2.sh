#!/usr/bin/env bash
# Wave 8 v2: rebuild the association keys with the station/offset distance
# filter ('+43.94' stations, "50.00' LT" offsets polluted the v1 distance
# denominators) and re-score. Reads are keyed per segment and the chaining is
# deterministic, so existing reads are REUSED; only crops for newly-labeled
# segments (and the reshuffled control sample) hit the GPU. Appends 'assocv2'
# rows to assoc_summary.tsv - compare against the same sheet's 'assoc' row.
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/assoc_summary.tsv"
P="$RESULTS/ncdot_pdfs"

DENSE="ncdot_yadkin_u_5809_c204935 ncdot_forsyth_u_5536_c204980 ncdot_cleveland_34497_3_12_r_2707d_r_2707e_c20 ncdot_randolph_u_5813_c204843"
DENSE_EXCLUDE="yadkin_u_5809|forsyth_u_5536|cleveland|randolph_u_5813"
CONTROLS=$(ls "$EVAL"/goldens/ncdot_*.key_p*.json | sed 's/.*\///;s/\.key_p[0-9]*\.json//' \
  | grep -Ev "$DENSE_EXCLUDE" | sort | head -6)

for slug in $DENSE $CONTROLS; do
  gt=$(ls "$EVAL"/goldens/"${slug}".key_p*.json 2>/dev/null | head -1)
  [ -n "$gt" ] || { qlog "no golden for $slug -- skip"; continue; }
  [ -f "$P/$slug.pdf" ] || { qlog "no pdf for $slug -- skip"; continue; }
  page=$(basename "$gt" | sed 's/.*key_p\([0-9]*\)\.json/\1/')
  vslug="${slug}__assoc"
  [ -e "$RESULTS/reads/${vslug}.assocv2.json" ] && { qlog "skip $vslug (v2 done)"; continue; }
  rm -f "$EVAL/harness/_sources/$vslug/_assoc_key.json"
  ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
  qlog "assoc v2 $vslug (page $page)"
  t0=$(date +%s)
  out=$("$PY" "$EVAL/harness/assoc_study.py" "$P/$slug.pdf" "$page" "$vslug" \
        --gt "$gt" --workers 1 2>&1) \
    || { qlog "assoc v2 FAILED $vslug"; echo "$out" | tail -5; continue; }
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
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$slug" "assocv2" \
    "${bb:-NA}" "${bd:-NA}" "${ab:-NA}" "${ad:-NA}" "${sp:-NA}" "${pb:-NA}" "${pd:-NA}" "$secs" >> "$SUMMARY"
  cp "$EVAL/harness/_sources/$vslug/_assoc_reads.json" "$RESULTS/reads/${vslug}.assocv2.json"
  cp "$EVAL/harness/_sources/$vslug/_assoc_key.json"   "$RESULTS/reads/${vslug}.assocv2_key.json"
done
qlog "assoc v2 done"

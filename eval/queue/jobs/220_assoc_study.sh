#!/usr/bin/env bash
# Wave 8: label->segment ASSOCIATION study, full run (after the 210 pilot).
# 4 dense-failure sheets + 6 controls, uncapped, unlabeled controls 1:1.
# Measures per sheet:
#   binding recall  = golden (label,segment) pairs read correctly ON that segment
#   anywhere recall = same labels read in ANY crop (isolates reading vs binding)
#   spurious rate   = parseable emissions on unlabeled control crops
#   pooled recall   = score_run.py vs the committed golden (comparable with the
#                     tile baselines in ncdot_summary.tsv / dense_summary.tsv)
# NOTE (honesty): crops target golden-labeled segments + sampled unlabeled
# controls, so crop SELECTION leaks label presence; binding recall is unaffected,
# precision comes from the control crops. See assoc_study.py header.
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/assoc_summary.tsv"
[ -s "$SUMMARY" ] || printf "sheet\tvariant\tbind_brg\tbind_dist\tanyw_brg\tanyw_dist\tspurious\tpooled_brg\tpooled_dist\tseconds\n" > "$SUMMARY"
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
  [ -e "$RESULTS/reads/${vslug}.assoc.json" ] && { qlog "skip $vslug (done)"; continue; }
  ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
  qlog "assoc $vslug (page $page)"
  t0=$(date +%s)
  out=$("$PY" "$EVAL/harness/assoc_study.py" "$P/$slug.pdf" "$page" "$vslug" \
        --gt "$gt" --workers 1 2>&1) \
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
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$slug" "assoc" \
    "${bb:-NA}" "${bd:-NA}" "${ab:-NA}" "${ad:-NA}" "${sp:-NA}" "${pb:-NA}" "${pd:-NA}" "$secs" >> "$SUMMARY"
  cp "$EVAL/harness/_sources/$vslug/_assoc_reads.json" "$RESULTS/reads/${vslug}.assoc.json"
  cp "$EVAL/harness/_sources/$vslug/_assoc_key.json"   "$RESULTS/reads/${vslug}.assoc_key.json"
done
qlog "assoc study done"

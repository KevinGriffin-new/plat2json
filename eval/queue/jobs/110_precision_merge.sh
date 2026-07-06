#!/usr/bin/env bash
# Wave 5: map the consensus recall/precision frontier (CPU only, minutes).
# For each vector-golden sheet, score union-of-k (k=2,3,5) and maj2/maj3 of the
# five 7B samples; record EMITTED label counts alongside recall — emitted vs
# matched is the precision proxy the wave-1 TSV missed.
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/precision_summary.tsv"
printf "sheet\tvariant\temit_b\temit_d\trecall_b\trecall_d\n" > "$SUMMARY"
declare -A GOLD=(
  [county_test_t1100]=county_test.key_p0.json
  [adams_prc24_12]=adams_prc24_12.key_p42.json
  [adams_prc2025]=adams_prc2025.key_p1.json
  [adams_wolfcreek]=adams_wolfcreek.key_p19.json
)
for sheet in county_test_t1100 adams_prc24_12 adams_prc2025 adams_wolfcreek; do
  files=( "$RESULTS/reads/${sheet}"_s0.json "$RESULTS/reads/${sheet}"_s1.json \
          "$RESULTS/reads/${sheet}"_s2.json "$RESULTS/reads/${sheet}"_s3.json \
          "$RESULTS/reads/${sheet}"_s4.json )
  [ -e "${files[0]}" ] || { qlog "no samples for $sheet"; continue; }
  tgt="$EVAL/harness/_sources/$sheet/_vlm_reads.json"
  run_variant() {  # $1 label, rest = merge args
    local label="$1"; shift
    "$PY" "$QDIR/union_reads.py" "$@" > "$tgt" || return
    local res b d eb ed
    res=$("$PY" "$EVAL/score/score_run.py" "$sheet" --gt "$EVAL/goldens/${GOLD[$sheet]}" 2>&1)
    eb=$(echo "$res" | grep -o '[0-9]* complete bearings' | grep -o '^[0-9]*')
    ed=$(echo "$res" | grep -o '[0-9]* distances,' | grep -o '^[0-9]*')
    b=$(echo "$res" | grep -o 'bearing recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
    d=$(echo "$res" | grep -o 'distance recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
    printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$sheet" "$label" "${eb:-NA}" "${ed:-NA}" "${b:-NA}" "${d:-NA}" >> "$SUMMARY"
  }
  run_variant s0        union "${files[0]}"
  run_variant union_k2  union "${files[@]:0:2}"
  run_variant union_k3  union "${files[@]:0:3}"
  run_variant union_k5  union "${files[@]}"
  run_variant maj2_k5   maj2  "${files[@]}"
  run_variant maj3_k5   maj3  "${files[@]}"
done
qlog "precision frontier done -> $SUMMARY"

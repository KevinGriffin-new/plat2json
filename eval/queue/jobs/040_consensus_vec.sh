#!/usr/bin/env bash
# Consensus experiment: can k-sample voting close the 7B->32B gap?
# Per sheet: 5 reads (greedy + 4 @ temp 0.7), then score each sample plus the
# union and the >=2-vote majority against the vector golden.
# Motivation: R-RES puts the whole 7B matrix at 37 min vs 302 min for the 32B;
# a 5x 7B ensemble is still ~4x cheaper than one 32B pass.
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/consensus_summary.tsv"
printf "sheet\tvariant\tbearings\tdistances\tseconds\n" > "$SUMMARY"
declare -A GOLD=(
  [county_test_t1100]=county_test.key_p0.json
  [adams_prc24_12]=adams_prc24_12.key_p42.json
  [adams_prc2025]=adams_prc2025.key_p1.json
  [adams_wolfcreek]=adams_wolfcreek.key_p19.json
)
for sheet in county_test_t1100 adams_prc24_12 adams_prc2025 adams_wolfcreek; do
  [ -d "$EVAL/harness/_sources/$sheet/tiles" ] || { qlog "skip $sheet (not staged)"; continue; }
  for k in 0 1 2 3 4; do
    out="$RESULTS/reads/${sheet}_s${k}.json"
    [ -e "$out" ] && continue
    ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
    t="0.7"; [ "$k" -eq 0 ] && t="0.0"
    qlog "read $sheet sample$k temp=$t"
    t0=$(date +%s)
    "$PY" "$EVAL/harness/vlm_read.py" "$sheet" --workers 1 --temp "$t" \
        --prompt-file "$PROMPT" --out "$out" || { qlog "read FAILED $sheet s$k"; continue; }
    echo "$sheet s$k $(( $(date +%s) - t0 ))s" >> "$RESULTS/consensus_times.log"
  done
  for variant in s0 s1 s2 s3 s4 union maj2; do
    tgt="$EVAL/harness/_sources/$sheet/_vlm_reads.json"
    case "$variant" in
      union|maj2)
        "$PY" "$QDIR/union_reads.py" "$variant" "$RESULTS/reads/${sheet}"_s*.json > "$tgt" || continue ;;
      *)
        [ -e "$RESULTS/reads/${sheet}_${variant}.json" ] || continue
        cp "$RESULTS/reads/${sheet}_${variant}.json" "$tgt" ;;
    esac
    res=$("$PY" "$EVAL/score/score_run.py" "$sheet" --gt "$EVAL/goldens/${GOLD[$sheet]}" 2>&1)
    b=$(echo "$res" | grep -o 'bearing recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
    d=$(echo "$res" | grep -o 'distance recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*')
    printf "%s\t%s\t%s\t%s\t-\n" "$sheet" "$variant" "${b:-NA}" "${d:-NA}" >> "$SUMMARY"
  done
done
qlog "consensus experiment done"

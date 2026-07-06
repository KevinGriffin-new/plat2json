#!/usr/bin/env bash
# Wave 7: union-of-3 consensus on the 4 dense failures at the ORIGINAL staging
# (tile 1100) — does the wave-5 efficiency point rescue density-driven misses?
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/dense_summary.tsv"
[ -s "$SUMMARY" ] || printf "sheet\tvariant\tbearings\tdistances\tseconds\n" > "$SUMMARY"
SHEETS="ncdot_yadkin_u_5809_c204935 ncdot_forsyth_u_5536_c204980 ncdot_cleveland_34497_3_12_r_2707d_r_2707e_c20 ncdot_randolph_u_5813_c204843"
for slug in $SHEETS; do
  gt=$(ls "$EVAL/goldens/${slug}".key_p*.json | head -1)
  [ -d "$EVAL/harness/_sources/$slug/tiles" ] || { qlog "no staging for $slug"; continue; }
  for k in 0 1 2; do
    out="$RESULTS/reads/${slug}_dn${k}.json"
    [ -e "$out" ] && continue
    ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
    qlog "read $slug dense-sample$k"
    t0=$(date +%s)
    "$PY" "$EVAL/harness/vlm_read.py" "$slug" --workers 1 --temp 0.7 \
        --prompt-file "$PROMPT" --out "$out" || { qlog "read FAILED $slug dn$k"; continue; }
    echo "$slug dn$k $(( $(date +%s) - t0 ))s" >> "$RESULTS/dense_times.log"
  done
  for variant in union3 maj2of3; do
    mode=union; [ "$variant" = maj2of3 ] && mode=maj2
    tgt="$EVAL/harness/_sources/$slug/_vlm_reads.json"
    "$PY" "$QDIR/union_reads.py" "$mode" "$RESULTS/reads/${slug}"_dn*.json > "$tgt" || continue
    res=$("$PY" "$EVAL/score/score_run.py" "$slug" --gt "$gt" 2>&1)
    b=$(echo "$res" | grep -o 'bearing recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*' | head -1)
    d=$(echo "$res" | grep -o 'distance recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*' | head -1)
    printf "%s\t%s\t%s\t%s\t-\n" "$slug" "$variant" "${b:-NA}" "${d:-NA}" >> "$SUMMARY"
  done
done
qlog "dense union3 done"

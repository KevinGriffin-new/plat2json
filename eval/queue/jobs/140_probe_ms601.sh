#!/usr/bin/env bash
# Wave 5: extend the WY mineral-survey enumeration ms601..ms1200 (HEAD probe,
# 2 s spacing). Appends to results/ms_available.txt.
source "$HOME/plat-queue/lib.sh"
OUT="$RESULTS/ms_available.txt"
BASE="https://www.wy.blm.gov/cadastral/mineralsurvey"
UA="Mozilla/5.0 (plat2json eval harness)"
probe() { curl -s -o /dev/null -w "%{http_code}" -I -A "$UA" --max-time 15 "$1"; }
before=$(wc -l < "$OUT")
for n in $(seq 601 1200); do
  b=$(( (n/50)*50 )); range=$(printf "%03d-%03d" "$b" "$((b+49))")
  hit_a=0
  for suf in "" a; do
    code=$(probe "$BASE/$range/ms${n}${suf}.pdf")
    if [ "$code" = "200" ]; then
      echo "$range/ms${n}${suf}" >> "$OUT"; qlog "HIT ms${n}${suf}"
      [ "$suf" = "a" ] && hit_a=1
    fi
    sleep 2
  done
  if [ "$hit_a" = "1" ]; then
    for suf in b c d; do
      code=$(probe "$BASE/$range/ms${n}${suf}.pdf")
      [ "$code" = "200" ] || break
      echo "$range/ms${n}${suf}" >> "$OUT"; qlog "HIT ms${n}${suf}"
      sleep 2
    done
  fi
done
qlog "probe 601-1200 done: $(( $(wc -l < "$OUT") - before )) new (total $(wc -l < "$OUT"))"

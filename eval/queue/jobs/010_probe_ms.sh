#!/usr/bin/env bash
# Enumerate BLM WY mineral-survey plats ms1..ms600 by polite HEAD probe.
# ~2 requests/number, 2 s spacing (be a good citizen; no index page exists).
# Output: results/ms_available.txt  (one "<range>/<msN[sfx]>" per line)
source "$HOME/plat-queue/lib.sh"
OUT="$RESULTS/ms_available.txt"
BASE="https://www.wy.blm.gov/cadastral/mineralsurvey"
UA="Mozilla/5.0 (plat2json eval harness)"
: > "$OUT"
probe() { curl -s -o /dev/null -w "%{http_code}" -I -A "$UA" --max-time 15 "$1"; }
for n in $(seq 1 600); do
  b=$(( (n/50)*50 )); lo=$b; hi=$((b+49)); [ "$lo" -eq 0 ] && lo=1
  range=$(printf "%03d-%03d" "$lo" "$hi")
  hit_a=0
  for suf in "" a; do
    code=$(probe "$BASE/$range/ms${n}${suf}.pdf")
    if [ "$code" = "200" ]; then
      echo "$range/ms${n}${suf}" >> "$OUT"; qlog "HIT ms${n}${suf}"
      [ "$suf" = "a" ] && hit_a=1
    fi
    sleep 2
  done
  if [ "$hit_a" = "1" ]; then   # lettered series: keep going b, c, ...
    for suf in b c d; do
      code=$(probe "$BASE/$range/ms${n}${suf}.pdf")
      [ "$code" = "200" ] || break
      echo "$range/ms${n}${suf}" >> "$OUT"; qlog "HIT ms${n}${suf}"
      sleep 2
    done
  fi
done
qlog "probe done: $(wc -l < "$OUT") plats found"
[ -s "$OUT" ]

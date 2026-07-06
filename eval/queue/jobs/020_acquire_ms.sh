#!/usr/bin/env bash
# Download + stage the first 80 mineral-survey plats found by 010 (3 s spacing).
source "$HOME/plat-queue/lib.sh"
LIST="$RESULTS/ms_available.txt"
[ -s "$LIST" ] || { qlog "no ms_available.txt -- run 010 first"; exit 1; }
n=0
while read -r item; do
  n=$((n+1)); [ "$n" -gt 80 ] && break
  ms=${item#*/}
  if [ -d "$EVAL/harness/_sources/blm_$ms/tiles" ]; then
    qlog "skip $ms (already staged)"; continue
  fi
  qlog "acquire $item"
  "$PY" "$QDIR/acquire_ms.py" "$item" || qlog "FAILED acquire $item"
  sleep 3
done < "$LIST"
qlog "acquire done"

#!/usr/bin/env bash
# Wave 5: scout BLM GLO Records (national, public domain). Network/CPU only.
# Scans the dm_id window around the known mineral-survey cluster, builds
# results/glo_index.tsv, stages up to 5 pilot mineral-survey plats, and logs
# 2 fieldnote-conversion attempts (the open question from research).
source "$HOME/plat-queue/lib.sh"
"$PY" "$QDIR/glo_scout.py" 147000 147600 5

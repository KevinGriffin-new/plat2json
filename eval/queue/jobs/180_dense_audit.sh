#!/usr/bin/env bash
# Wave 7: page-selection audit for the 4 dense-sheet reader failures (CPU).
source "$HOME/plat-queue/lib.sh"
P="$RESULTS/ncdot_pdfs"
"$PY" "$QDIR/page_audit.py" \
  "$P/ncdot_yadkin_u_5809_c204935.pdf:1" \
  "$P/ncdot_forsyth_u_5536_c204980.pdf:10" \
  "$P/ncdot_cleveland_34497_3_12_r_2707d_r_2707e_c20.pdf:12" \
  "$P/ncdot_randolph_u_5813_c204843.pdf:25" \
  | tee "$RESULTS/dense_audit.tsv"

#!/usr/bin/env bash
# Wave 7: dense-sheet rescue — re-tile the 4 failures tighter/sharper and re-read.
#   t800     = dpi 300, tile 800, overlap 150  (fewer glyphs/tile, no downscale @896)
#   d400t900 = dpi 400, tile 900, overlap 180  (more px per glyph, still native in tile)
# Scored vs the ORIGINAL committed goldens. Baseline = ncdot_summary.tsv rows.
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/dense_summary.tsv"
[ -s "$SUMMARY" ] || printf "sheet\tvariant\tbearings\tdistances\tseconds\n" > "$SUMMARY"
P="$RESULTS/ncdot_pdfs"
declare -A PAGE=(
  [ncdot_yadkin_u_5809_c204935]=1
  [ncdot_forsyth_u_5536_c204980]=10
  [ncdot_cleveland_34497_3_12_r_2707d_r_2707e_c20]=12
  [ncdot_randolph_u_5813_c204843]=25
)
for slug in "${!PAGE[@]}"; do
  gt=$(ls "$EVAL/goldens/${slug}".key_p*.json | head -1)
  for cfg in "t800:300:800:150" "d400t900:400:900:180"; do
    IFS=: read -r name dpi tile ov <<< "$cfg"
    vslug="${slug}__${name}"
    [ -e "$RESULTS/reads/${vslug}.json" ] && { qlog "skip $vslug (done)"; continue; }
    "$PY" "$QDIR/dense_stage.py" "$P/$slug.pdf" "${PAGE[$slug]}" "$vslug" "$dpi" "$tile" "$ov" \
      || { qlog "stage FAILED $vslug"; continue; }
    ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
    qlog "read $vslug"
    t0=$(date +%s)
    "$PY" "$EVAL/harness/vlm_read.py" "$vslug" --workers 1 --prompt-file "$PROMPT" \
      || { qlog "read FAILED $vslug"; continue; }
    secs=$(( $(date +%s) - t0 ))
    res=$("$PY" "$EVAL/score/score_run.py" "$vslug" --gt "$gt" 2>&1); echo "$res"
    b=$(echo "$res" | grep -o 'bearing recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*' | head -1)
    d=$(echo "$res" | grep -o 'distance recall: [0-9]*/[0-9]*' | grep -o '[0-9]*/[0-9]*' | head -1)
    printf "%s\t%s\t%s\t%s\t%s\n" "$slug" "$name" "${b:-NA}" "${d:-NA}" "$secs" >> "$SUMMARY"
    cp "$EVAL/harness/_sources/$vslug/_vlm_reads.json" "$RESULTS/reads/${vslug}.json"
  done
done
qlog "dense retile done"

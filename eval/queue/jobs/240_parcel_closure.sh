#!/usr/bin/env bash
# Wave 8/M3: closure end-to-end on PARCEL sheets. NCDOT road strips don't
# close; subdivision plats do. Acquire the 4 vector-golden subdivision sheets,
# run the association study (assoc_study.py), then assemble courses and run the
# traverse-closure self-check (cogo_assemble.py). The deliverable metric is
# rings closed + precision ratio - the "provably right or explicitly flagged"
# gate that import_plan_json consumers will trust.
source "$HOME/plat-queue/lib.sh"
SUMMARY="$RESULTS/closure_summary.tsv"
[ -s "$SUMMARY" ] || printf "sheet\tcourses\tclean\trings\tworst_precision\tneeds_human\tseconds\n" > "$SUMMARY"
PP="$RESULTS/parcel_pdfs"; mkdir -p "$PP"

declare -A URL=(
  [county_test]="https://www.boisecounty.us/wp-content/uploads/2025/09/Exh-13-Final-Plat.pdf"
  [adams_prc24_12]="https://adamscountyco.gov/wp-content/uploads/2025/08/PRC2024-00012-submittal3.pdf"
  [adams_prc2025]="https://adamscountyco.gov/wp-content/uploads/2026/03/PRC2025-00014-submittal3.pdf"
  [adams_wolfcreek]="https://adamscountyco.gov/wp-content/uploads/2025/08/PLT2024-00007-submittal1.pdf"
)
# already-staged copies from earlier waves (county sites are Cloudflare-gated;
# curl gets a challenge page, so prefer what is on disk)
declare -A LOCAL=(
  [county_test]="$EVAL/harness/_sources/county_test/Exh-13-Final-Plat.pdf"
  [adams_prc24_12]="$EVAL/harness/_sources/corpus_fetch/adams_prc24_12.pdf"
  [adams_prc2025]="$EVAL/harness/_sources/corpus_fetch/adams_prc2025.pdf"
  [adams_wolfcreek]="$EVAL/harness/_sources/corpus_fetch/adams_wolfcreek.pdf"
)

for slug in county_test adams_prc24_12 adams_prc2025 adams_wolfcreek; do
  gt=$(ls "$EVAL"/goldens/"${slug}".key_p*.json 2>/dev/null | head -1)
  [ -n "$gt" ] || { qlog "no golden for $slug -- skip"; continue; }
  page=$(basename "$gt" | sed 's/.*key_p\([0-9]*\)\.json/\1/')
  vslug="${slug}__assoc"
  [ -e "$RESULTS/reads/${vslug}.plan_assoc.json" ] && { qlog "skip $vslug (done)"; continue; }
  # a Cloudflare challenge page is not a plat
  [ -s "$PP/$slug.pdf" ] && ! head -c 5 "$PP/$slug.pdf" | grep -q "%PDF" \
    && { qlog "stale non-PDF for $slug -- refetch"; rm -f "$PP/$slug.pdf"; }
  if [ ! -s "$PP/$slug.pdf" ]; then
    if [ -s "${LOCAL[$slug]}" ]; then
      qlog "local copy $slug"
      cp "${LOCAL[$slug]}" "$PP/$slug.pdf"
    else
      qlog "fetch $slug"
      curl -sL --retry 3 --max-time 300 -o "$PP/$slug.pdf" "${URL[$slug]}" \
        || { qlog "fetch FAILED $slug"; continue; }
      sleep 2
      head -c 5 "$PP/$slug.pdf" | grep -q "%PDF" \
        || { qlog "fetch got non-PDF for $slug -- skip"; rm -f "$PP/$slug.pdf"; continue; }
    fi
  fi
  ensure_server || { qlog "server unrecoverable -- abort"; exit 1; }
  qlog "assoc+closure $vslug (page $page)"
  t0=$(date +%s)
  "$PY" "$EVAL/harness/assoc_study.py" "$PP/$slug.pdf" "$page" "$vslug" \
        --gt "$gt" --workers 1 \
    || { qlog "assoc FAILED $vslug"; continue; }
  out=$("$PY" "$PJ/cogo_assemble.py" \
        --key "$EVAL/harness/_sources/$vslug/_assoc_key.json" \
        --reads "$EVAL/harness/_sources/$vslug/_assoc_reads.json" \
        --out "$EVAL/harness/_sources/$vslug/_plan_assoc.json" 2>&1) \
    || { qlog "assemble FAILED $vslug"; echo "$out" | tail -5; continue; }
  echo "$out"
  secs=$(( $(date +%s) - t0 ))
  co=$(echo "$out" | grep -o 'courses: [0-9]*' | grep -o '[0-9]*' | head -1)
  cl=$(echo "$out" | grep -o '([0-9]* clean' | grep -o '[0-9]*' | head -1)
  ri=$(echo "$out" | grep -o 'rings closed: [0-9]*' | grep -o '[0-9]*' | head -1)
  wp=$(echo "$out" | grep -o '1:[0-9]*' | sort -t: -k2 -n | head -1)
  nh=$(echo "$out" | grep -o 'needs_human: \w*' | awk '{print $2}')
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$slug" "${co:-NA}" "${cl:-NA}" \
    "${ri:-0}" "${wp:-NA}" "${nh:-NA}" "$secs" >> "$SUMMARY"
  cp "$EVAL/harness/_sources/$vslug/_plan_assoc.json"  "$RESULTS/reads/${vslug}.plan_assoc.json"
  cp "$EVAL/harness/_sources/$vslug/_assoc_reads.json" "$RESULTS/reads/${vslug}.assoc.json" 2>/dev/null
  cp "$EVAL/harness/_sources/$vslug/_assoc_key.json"   "$RESULTS/reads/${vslug}.assoc_key.json" 2>/dev/null
done
qlog "parcel closure done"

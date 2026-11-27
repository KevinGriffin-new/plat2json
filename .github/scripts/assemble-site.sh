#!/bin/sh
# Assemble the GitHub Pages site: docs/ plus the Python files try.html loads,
# and (optionally) stamp the worker's fallback SHA. The sample PDF is already
# in docs/samples/. Used by .github/workflows/pages.yml and by local checks.
set -eu
if [ "$#" -lt 1 ]; then
  echo "usage: assemble-site.sh DEST [SHA]" >&2
  exit 2
fi
dest=$1
sha=${2:-}
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

rm -rf "$dest"
mkdir -p "$dest"
cp -a "$root/docs/." "$dest/"
cp "$root/vector_plan.py" "$root/plan2dxf.py" "$dest/"
mkdir -p "$dest/eval/harness"
cp "$root/eval/harness/vector_golden.py" "$dest/eval/harness/"
find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} +

if [ -n "$sha" ]; then
  sed -i "s/const PINNED_SHA = \"[^\"]*\"/const PINNED_SHA = \"$sha\"/" "$dest/try-worker.js"
  grep -q "const PINNED_SHA = \"$sha\"" "$dest/try-worker.js" \
    || { echo "assemble-site.sh: could not stamp PINNED_SHA" >&2; exit 1; }
fi

echo "assembled $dest"

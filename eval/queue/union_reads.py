#!/usr/bin/env python3
"""Union / majority merge of repeated blind reads.

usage: union_reads.py union|maj2|maj3 read1.json read2.json ...
Prints the merged JSON array to stdout. A label counts once per sample file;
'union' keeps everything seen, 'majN' keeps labels seen in >=N samples.
"""
import collections
import json
import sys

mode, files = sys.argv[1], sys.argv[2:]
need = {"union": 1, "maj2": 2, "maj3": 3}[mode]
cnt = collections.Counter()
keep = {}
for f in files:
    seen = set()
    for x in json.load(open(f, encoding="utf-8")):
        k = (x.get("raw", "").strip(), x.get("kind", ""))
        if k in seen:
            continue
        seen.add(k)
        cnt[k] += 1
        keep.setdefault(k, x)
print(json.dumps([keep[k] for k, c in cnt.items() if c >= need]))

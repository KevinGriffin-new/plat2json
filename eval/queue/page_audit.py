#!/usr/bin/env python3
"""Audit which PDF page holds the course text vs which page was keyed.

usage: page_audit.py <pdf>:<keyed_page> [...]
Per PDF: count DMS-bearing regex hits in each page's text layer; report the
keyed page's count, the best page, and OK/MISMATCH. Venv python.
"""
import re
import sys

import fitz

BRG = re.compile(r"[NS]\s*\.?\s*\d{1,3}\s*[°o]\s*\d{1,2}\s*['’]", re.I)
print("pdf\tpages\tkeyed\tbest\tverdict")
for arg in sys.argv[1:]:
    path, kp = arg.rsplit(":", 1)
    kp = int(kp)
    doc = fitz.open(path)
    counts = [len(BRG.findall(p.get_text())) for p in doc]
    bi = max(range(len(counts)), key=lambda i: counts[i])
    verdict = "OK" if (bi == kp or counts[kp] >= 0.8 * counts[bi]) else "MISMATCH"
    print(f"{path.rsplit('/',1)[-1]}\t{len(doc)}\tp{kp}({counts[kp]} brg)"
          f"\tp{bi}({counts[bi]} brg)\t{verdict}")

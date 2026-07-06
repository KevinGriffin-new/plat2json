#!/usr/bin/env bash
# Wave 3: harvest NCDOT Right-of-Way vector sheets (verified self-golden source:
# text layer carries N 55°06'45" W style bearings). Network/CPU only, no GPU.
source "$HOME/plat-queue/lib.sh"
"$PY" "$QDIR/ncdot_harvest.py" 20

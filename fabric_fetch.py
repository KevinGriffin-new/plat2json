"""Geolocate a plat from a text key and snapshot its parcel fabric.

    python fabric_fetch.py --provider parcelmap_bc --key EPP12340 -o snap.json
    python fabric_fetch.py --provider wyoming_statewide --key "AREA THIRTY 3 EST" -o snap.json

Writes the normalized snapshot format consumed by fabric_compare.py (see
fabric_providers.py docstring). Snapshot in hand, the full per-plat chain is:
capture (plat2json) -> gates (face_check --printed-sqft) -> fit/identity
(fabric_compare) -> corridor rerun if lots stay open (fabric_compare
--corridor-out, plat2json --rescue-corridor) -> QGIS overlay.
"""
import argparse
import json
import sys

from fabric_providers import PROVIDERS


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    ap.add_argument("--key", required=True,
                    help="text key: BC plan number / legal-description fragment")
    ap.add_argument("--epsg", type=int, default=None,
                    help="projected output CRS (default: provider's choice)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    fn = PROVIDERS[a.provider]
    snap = fn(a.key, a.epsg) if a.epsg else fn(a.key)
    n, ni = len(snap["parcels"]), len(snap.get("interests", []))
    if not n:
        sys.exit(f"no parcels for key {a.key!r} — check the key "
                 f"(and whether the plan is registered in this fabric yet)")
    json.dump(snap, open(a.out, "w"), indent=1)
    areas = sorted((p["area"] or 0) for p in snap["parcels"])
    print(f"{a.provider}: {n} parcels ({ni} interest/easement) "
          f"epsg={snap['epsg']} units={snap['units']} -> {a.out}")
    print(f"  areas [{snap['units']}]: min {areas[0]:.0f}  max {areas[-1]:.0f}  "
          f"labels e.g. {[p['label'] for p in snap['parcels'][:4]]}")


if __name__ == "__main__":
    main()

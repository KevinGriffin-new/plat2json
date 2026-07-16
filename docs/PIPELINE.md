# Any-plat pipeline: capture → gates → fabric oracle → corridor → overlay

Validated end-to-end on `482.pdf` (Area Thirty 3 Estates, Sweetwater Co WY:
18/18 parcels closed, printed areas ≤1.3%, fabric fit RMS 0.30 m — see
STATUS.md iterations 20–21 and `docs/area482_overlay.png`). The same chain is
scriptable for any plat whose jurisdiction has a queryable parcel fabric;
British Columbia is wired in via ParcelMap BC.

## The chain

```
0. TEXT KEYS      read the plan number / subdivision name off the sheet
                  (BC: plan number EPP/EPS/VIP/KAP/BCP… printed in the title
                  block; the local VLM reader or the PDF text layer gets it)

1. FABRIC SNAPSHOT
   python fabric_fetch.py --provider parcelmap_bc --key EPP12340 -o snap.json
   python fabric_fetch.py --provider wyoming_statewide --key "AREA THIRTY 3 EST" -o snap.json

2. PRINTED-AREAS GOLDEN   bank every printed lot area off the sheet FIRST
   (survey-plan-pdf-extraction rule: cheap answer keys before slow work).
   Format: {"parcels":[{"id":"LOT 1","sqft":65523}, …]} — use "area" with
   metric values for BC plans; units must match the snapshot's units.

3. CAPTURE        python plat2json.py PLAT.pdf plan.json --page N
                  [--plot-scale 250]      # BC sheets are metric-scaled

4. GATES          python face_check.py plan.json --printed-sqft printed.json
                  closure + printed-area validation; identity labels within an
                  equal-area class are NOT identity (see step 5)

5. FIT + IDENTITY python fabric_compare.py plan.json snap.json printed.json
                       [--out faces.geojson] [--dump-transform T.json]
                  RANSAC-consensus similarity fit onto the fabric. Sanity:
                  recovered rotation ≈ grid convergence at the site. Output:
                  per-lot identity (position-adjudicated), centroid residuals,
                  area deltas, and the open set (fabric lots with no face).

6. CORRIDOR LOOP  if lots stay open:
                  python fabric_compare.py … --corridor-out cor.json \
                        --corridor-lots "<open lots>" --corridor-halfwidth-m 3
                  python plat2json.py PLAT.pdf plan.json --page N \
                        --rescue-corridor cor.json [--rescue-floor 0.35]
                  Corridor-gated mechanisms (all inert without the flag):
                  crop extension, sub-min_len chain rescue, and — opt-in via
                  --rescue-erase-corners — corner-monument disc erasure.
                  Erasure closed 482's triangle+dot corners (+2 lots) but
                  REGRESSED the BC Example-plan (31→28: small iron-post
                  circles shared with closed neighbours merge faces) — try it
                  only when the autopsy shows symbol-welded corners.
                  Re-run steps 4–5. The corridor GUIDES capture;
                  the printed-area gate (never seen by capture) validates.

7. OVERLAY        load faces.geojson + snap.json + XYZ imagery in QGIS
                  (qgis MCP, or the plugin's TCP bridge directly). Embed a
                  "crs" member in projected GeoJSON — OGR assumes WGS84
                  without it. QGIS 4: render offscreen via QgsMapSettings.
```

## Providers (`fabric_providers.py`)

| provider | key | endpoint | units | labels | grade |
|---|---|---|---|---|---|
| `wyoming_statewide` | legal-description fragment | state ArcGIS FeatureServer `/query` | sqft | lot numbers from `legal` | tax fabric ~0.1–1% |
| `parcelmap_bc` | plan number | BC openmaps WFS `PMBC_PARCEL_FABRIC_POLY_SVW` (Open Gov Licence BC) | m² | **PIDs** (fabric carries no lot numbers) | LTSA-maintained; `FEATURE_AREA_SQM` is fabric-computed, not the plan's printed area |

BC notes:
- Query is exact `PLAN_NUMBER='EPP…'` — the strongest text key in the ladder
  (one query, no LIKE ambiguity). A plan absent from the fabric usually means
  it is too new or unregistered — that is a finding, not a failure.
- `PARCEL_CLASS='Interest'` rows (easements/SRWs filed under the same plan)
  are excluded from lots but kept under `"interests"` in the snapshot —
  useful as additional corridors/QA layers.
- Identity adjudication maps face→PID. PID→lot-number is a title-search
  question (LTSA), out of fabric scope.
- Pick `--epsg` by longitude: UTM 8–11N (26908–26911) or BC Albers 3005.

Adding a jurisdiction = one function in `fabric_providers.py` returning the
normalized snapshot: `{provider, units, epsg, _provenance, parcels:[{label,
area, ring, attrs}]}` with rings in projected metres. Everything downstream
(fit, identity, corridor, overlay) is provider-agnostic.

## Oracle hygiene (non-negotiable)

Rank oracles: plan closure (mm) > printed areas > fabric areas (~0.1–1%).
The fabric's unique power is POSITION → identity, which internal oracles
cannot see. Attribute areas that are plat-derived (Wyoming `landgrosss`) are
transcription checks on your golden, not independent measurements. When the
corridor guides capture, the printed-area gate must stay blind to it.

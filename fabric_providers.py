"""Parcel-fabric providers: geolocate a plat from its text keys and fetch a
normalized fabric snapshot any jurisdiction's oracle can feed to
fabric_compare.

Normalized snapshot format (written by fabric_fetch.py):
    {"_provenance": "...", "provider": "...", "units": "sqft"|"m2",
     "epsg": 26912,
     "parcels": [{"label": "LOT 1", "area": 65523.0,
                  "ring": [[x, y], ...], "attrs": {...}}, ...]}
- `units` is the unit of `area` AND of the printed-areas golden it will be
  compared against; ring coordinates are always metres (projected CRS).
- `label` is the best per-parcel identity the provider has: a lot number
  where the fabric carries one (Wyoming legal descriptions), else a PID
  (ParcelMap BC's fabric has no lot-number attribute — face->PID is still
  full identity adjudication; PID->lot is a title-search question).

Providers:
- wyoming_statewide: ArcGIS FeatureServer /query; key = a fragment of the
  subdivision's legal description (e.g. "AREA THIRTY 3 EST").
- parcelmap_bc: LTSA-fed ParcelMap BC fabric via the BC openmaps WFS
  (GeoServer, Open Government Licence BC); key = the plan number printed on
  every BC plan (EPP/EPS/VIP/VAP/KAP/BCP...). PARCEL_CLASS='Interest' rows
  (easements / SRWs filed under the same plan) are excluded from lots but
  kept under "interests" for corridor/QA use.
"""
import datetime
import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "plat2json-fabric/1.0"}


def _get_json(url, params, timeout=60):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}", headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _iso_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------


def fetch_wyoming_statewide(key, epsg=26912):
    """key: legal-description fragment, e.g. 'AREA THIRTY 3 EST'."""
    url = ("https://services3.arcgis.com/r0iJ85SKZ4zAzz3P/arcgis/rest/services/"
           "Wyoming_Parcels_for_2026/FeatureServer/0/query")
    safe = key.upper().replace("'", "''")
    d = _get_json(url, {
        "where": f"UPPER(legal) LIKE '%{safe}%'",
        "outFields": "parcelnb,legal,landgrosss,jurisdicti",
        "returnGeometry": "true", "outSR": epsg, "f": "json"})
    parcels = []
    for f in d.get("features", []):
        a = f["attributes"]
        legal = a.get("legal") or ""
        label = ("TRACT " + legal.rsplit("TR", 1)[-1].strip() if " TR " in f" {legal} "
                 or legal.endswith("TR A") else
                 "LOT " + legal.rsplit("LOT", 1)[-1].strip() if "LOT" in legal
                 else a.get("parcelnb", "?"))
        parcels.append({"label": label, "area": a.get("landgrosss"),
                        "ring": f["geometry"]["rings"][0], "attrs": a})
    return {"provider": "wyoming_statewide", "units": "sqft", "epsg": epsg,
            "_provenance": f"Fetched {_iso_now()} from {url} "
                           f"where UPPER(legal) LIKE '%{safe}%' outSR={epsg}. "
                           "Assessor tax fabric: area attr landgrosss is "
                           "plat-derived (transcription check, not an "
                           "independent measurement); geometry ~0.1-1% grade.",
            "parcels": parcels}


def fetch_parcelmap_bc(key, epsg=26910):
    """key: BC plan number, e.g. 'EPP12340'. epsg: UTM zone 8-11 (3154-3161,
    26908-26911) or BC Albers 3005 depending on where the plan is."""
    url = "https://openmaps.gov.bc.ca/geo/pub/ows"
    safe = key.upper().replace("'", "''")
    d = _get_json(url, {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "pub:WHSE_CADASTRE.PMBC_PARCEL_FABRIC_POLY_SVW",
        "outputFormat": "application/json", "srsName": f"EPSG:{epsg}",
        "CQL_FILTER": f"PLAN_NUMBER='{safe}'"}, timeout=120)
    parcels, interests = [], []
    for f in d.get("features", []):
        p = f["properties"]
        g = f["geometry"]
        ring = (g["coordinates"][0] if g["type"] == "Polygon"
                else g["coordinates"][0][0])  # largest assumption: first poly
        rec = {"label": p.get("PID_FORMATTED") or p.get("PARCEL_NAME")
               or f'PFP{p.get("PARCEL_FABRIC_POLY_ID")}',
               "area": p.get("FEATURE_AREA_SQM"),
               "ring": ring, "attrs": p}
        (interests if p.get("PARCEL_CLASS") == "Interest" else parcels).append(rec)
    return {"provider": "parcelmap_bc", "units": "m2", "epsg": epsg,
            "_provenance": f"Fetched {_iso_now()} from BC openmaps WFS "
                           f"PMBC_PARCEL_FABRIC_POLY_SVW PLAN_NUMBER='{safe}' "
                           f"srsName=EPSG:{epsg} (Open Government Licence BC). "
                           "LTSA-maintained fabric; FEATURE_AREA_SQM is "
                           "computed from fabric geometry, NOT the plan's "
                           "printed area. No lot-number attribute: labels are "
                           "PIDs; Interest-class parcels (easements/SRWs) "
                           "listed separately.",
            "parcels": parcels, "interests": interests}


PROVIDERS = {
    "wyoming_statewide": fetch_wyoming_statewide,
    "parcelmap_bc": fetch_parcelmap_bc,
}

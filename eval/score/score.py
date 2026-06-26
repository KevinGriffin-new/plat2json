"""R1 plan-reading benchmark scorer.

Scores a *recovered extraction* against license-free ground truth, across the
corpus defined in manifest.json. Reuses plan_validator.py (closure/area/inverse)
for the deterministic geometry math and adds the three metrics the research
agenda calls for:

  - closure residual      : forward-compute recovered legs; misclosure (m)
  - label-match rate       : recovered printed labels vs the plan's published
                             values (bearings/distances/areas), within tolerance
  - geometry<->label delta : per-leg |measured azimuth - label azimuth| and
                             |measured length - label distance| (the consistency
                             checker; on a real plat these deltas ARE the errors)

This is CAD-free and license-free: it never sets a coordinate, it only measures.
Run: python score.py            (scores every plan with an available extraction)
     python score.py <plan_id>  (scores one)

Recovered-extraction input shapes accepted (auto-detected):
  legs   : {"start_EN":[E,N], "legs":[{"az":deg,"dist":m,[arc keys]}], ...}
  rings  : {"<lotid>": {"ring":[[E,N]...], "printed_area_m2":..}, ...}
           or {"rings":[[[E,N]...], ...]}
  labels : {"labels":[{"kind","value",..}]} or a bare [ {label}, .. ] list
"""
import json
import math
import os
import sys
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # parent plan2cad/ for plan_validator
import plan_validator as pv  # noqa: E402


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def _resolve(path):
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(HERE, path))


def load_json(path):
    with open(_resolve(path), encoding="utf-8") as fh:
        return json.load(fh)


def legs_from_extraction(obj):
    """Return (start_EN, legs) from whatever recovered shape we were given.

    Falls back to deriving legs from the first available ring/polygon.
    """
    if isinstance(obj, dict) and obj.get("legs"):
        start = obj.get("start_EN") or obj.get("start") or [0.0, 0.0]
        return list(start), obj["legs"]
    rings = rings_from_extraction(obj)
    if rings:
        return list(rings[0][0]), pv.legs_from_corners(rings[0])
    return None, None


def rings_from_extraction(obj):
    """Return a list of rings ([[E,N],...]) from a recovered shape, or []."""
    if isinstance(obj, dict):
        if obj.get("rings"):
            return [r for r in obj["rings"] if len(r) >= 3]
        out = []
        for v in obj.values():
            if isinstance(v, dict) and isinstance(v.get("ring"), list) and len(v["ring"]) >= 3:
                out.append(v["ring"])
        return out
    return []


def labels_from_extraction(obj):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get("labels"), list):
        return obj["labels"]
    return []


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def score_closure(start, legs, tol_m):
    _, mvec, mdist = pv.run_traverse(start, legs)
    perim = sum(l["dist"] for l in legs)
    return {
        "n_legs": len(legs),
        "misclose_m": round(mdist, 4),
        "perimeter_m": round(perim, 3),
        "precision_1_in": (round(perim / mdist) if mdist > 1e-12 else None),
        "pass": mdist <= tol_m,
    }


def score_label_match(labels, published, tol):
    """Match recovered labels against published values by kind, within tolerance.

    published: {"distances":[..], "bearings_az":[deg..], "areas_m2":[..]}.
    Greedy nearest match; reports recall per kind.
    """
    rec = {"dist": [], "bearing": [], "area": []}
    for lb in labels:
        k, v = lb.get("kind"), lb.get("value")
        if v is None:
            continue
        if k in ("dist", "distance"):
            rec["dist"].append(float(v))
        elif k in ("bearing", "azimuth"):
            rec["bearing"].append(float(v))
        elif k == "area":
            rec["area"].append(float(v))

    def recall(got, want, tol_val, circular=False):
        if not want:
            return None
        pool = list(got)
        hit = 0
        for w in want:
            best_i, best_d = None, tol_val
            for i, g in enumerate(pool):
                d = abs(g - w)
                if circular:
                    d = min(d % 180.0, 180.0 - (d % 180.0))
                if d <= best_d:
                    best_d, best_i = d, i
            if best_i is not None:
                hit += 1
                pool.pop(best_i)
        return {"matched": hit, "of": len(want), "rate": round(hit / len(want), 3)}

    return {
        "distances": recall(rec["dist"], published.get("distances", []), tol["dist_m"]),
        "bearings": recall(rec["bearing"], published.get("bearings_az", []),
                           tol["bearing_arcmin"] / 60.0, circular=True),
        "areas": recall(rec["area"], published.get("areas_m2", []), tol["area_m2"]),
        "n_recovered": {k: len(v) for k, v in rec.items()},
    }


def score_geom_vs_label(start, legs, tol):
    """Per-leg consistency: measured azimuth/length vs the leg's labelled az/dist.

    Expects legs to carry both the measured geometry and the read label. We
    accept legs of the form {"az","dist","label_az","label_dist"}; if a leg has
    no label fields it is skipped. Flags any leg beyond tolerance.
    """
    flags, n = [], 0
    for i, lg in enumerate(legs):
        if "label_az" not in lg and "label_dist" not in lg:
            continue
        n += 1
        d_az = d_d = None
        if "label_az" in lg:
            raw = abs((lg["az"] - lg["label_az"] + 180) % 360 - 180)
            d_az = round(raw * 60, 2)  # arcmin
        if "label_dist" in lg:
            d_d = round(abs(lg["dist"] - lg["label_dist"]), 4)
        over = (d_az is not None and d_az > tol["bearing_arcmin"]) or \
               (d_d is not None and d_d > tol["dist_m"])
        if over:
            flags.append({"leg": i, "d_az_arcmin": d_az, "d_dist_m": d_d})
    if n == 0:
        return None
    return {"checked": n, "flagged": len(flags), "details": flags[:10]}


def lines_from_extraction(obj):
    """plat2json shape: {"lines":[[x0,y0,x1,y1,layer], ...]}."""
    if isinstance(obj, dict) and isinstance(obj.get("lines"), list) and obj["lines"]:
        if isinstance(obj["lines"][0], (list, tuple)) and len(obj["lines"][0]) >= 4:
            return obj["lines"]
    return []


def score_geometry_profile(lines, published_distances, tol):
    """For a geometry-only segment bag (Hough fragment-soup): quantify the
    fragmentation and, if published leg distances are known, segment-length recall.

    A geometry-only output HAS lengths but NOT topology, so closure is not
    computable — length recall is the honest metric (Hough
    gives lengths, not connectivity).
    """
    lens = [math.hypot(L[2] - L[0], L[3] - L[1]) for L in lines]
    xs = [c for L in lines for c in (L[0], L[2])]
    ys = [c for L in lines for c in (L[1], L[3])]
    prof = {
        "n_segments": len(lines),
        "total_length_m": round(sum(lens), 2),
        "extent_m": [round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)] if xs else None,
        "len_min_max_m": [round(min(lens), 3), round(max(lens), 3)] if lens else None,
        "n_segments_over_1m": sum(1 for v in lens if v > 1.0),
    }
    if published_distances:
        pool = sorted(lens, reverse=True)
        hit, matched = 0, []
        for w in published_distances:
            best_i, best_d = None, tol["dist_m"]
            for i, g in enumerate(pool):
                if abs(g - w) <= best_d:
                    best_d, best_i = abs(g - w), i
            if best_i is not None:
                hit += 1
                matched.append({"dist": round(w, 3), "seg": round(pool.pop(best_i), 3)})
        prof["length_recall"] = {"matched": hit, "of": len(published_distances),
                                 "rate": round(hit / len(published_distances), 3)}
    else:
        prof["length_recall"] = "PENDING (no published distances banked for this plan)"
    return prof


def score_shape_recall(lines, gt_legs, tol):
    """General-shape recall (ParcelMap-level, not survey-grade): of the known
    boundary leg lengths, how many appear among the extracted segment lengths.

    Scale is unknown per plan, so ratio-vote it (ratio-vote detected
    lengths vs known distances) from the top-K longest of each list, then do a
    1:1 greedy match (pop on hit) at a loose tol. n_segments is reported so the
    reader can judge distractor-pool inflation.
    """
    plen = sorted((math.hypot(L[2] - L[0], L[3] - L[1]) for L in lines), reverse=True)
    gt = sorted((float(x) for x in gt_legs), reverse=True)
    if not plen or not gt:
        return {"recall": None, "note": "empty lines or gt"}
    K = min(8, len(gt), len(plen))
    scale = statistics.median(gt[k] / plen[k] for k in range(K))  # robust scale
    pool = sorted((p * scale for p in plen), reverse=True)
    hit = 0
    for w in gt:
        t = max(tol.get("shape_m", 1.0), tol.get("shape_frac", 0.02) * w)
        bi, bd = None, t
        for k, g in enumerate(pool):
            if abs(g - w) <= bd:
                bd, bi = abs(g - w), k
        if bi is not None:
            hit += 1
            pool.pop(bi)
    return {"shape_recall": {"matched": hit, "of": len(gt), "rate": round(hit / len(gt), 3)},
            "ratio_vote_scale": round(scale, 4), "n_extracted_segments": len(plen),
            "tol": "max(shape_m, shape_frac*len)"}


def score_area(rings, published_areas, tol_m2):
    if not rings or not published_areas:
        return None
    got = sorted(pv.shoelace(r) for r in rings)
    want = sorted(published_areas)
    rows, pool = [], list(got)
    for w in want:
        best_i, best_d = None, tol_m2
        for i, g in enumerate(pool):
            if abs(g - w) <= best_d:
                best_d, best_i = abs(g - w), i
        if best_i is not None:
            rows.append({"published": round(w, 3), "computed": round(pool[best_i], 3),
                         "diff": round(best_d, 4), "pass": True})
            pool.pop(best_i)
        else:
            rows.append({"published": round(w, 3), "computed": None, "pass": False})
    return {"matched": sum(r["pass"] for r in rows), "of": len(want), "rows": rows}


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def score_plan(plan, tol):
    ex = plan.get("extraction", {})
    gt = plan.get("ground_truth", {})
    res = {"id": plan["id"], "axes": plan.get("axes", {}),
           "gt_type": gt.get("type"), "status": ex.get("status")}
    if ex.get("status") != "available" or not ex.get("output"):
        res["result"] = "NO-EXTRACTION (pending)"
        return res
    try:
        obj = load_json(ex["output"])
    except Exception as e:  # noqa: BLE001
        res["result"] = f"LOAD-ERROR: {e}"
        return res

    lines = lines_from_extraction(obj)
    # A plat2json segment bag has "lines" but no real legs/rings/labels — handle
    # it as a geometry-only profile and skip the leg/ring derivation (which would
    # fabricate a meaningless ring from fragment-soup).
    if lines and not (isinstance(obj, dict) and (obj.get("legs") or obj.get("rings"))):
        gt_legs = None
        legs_file = gt.get("gt_legs_file")
        if legs_file and os.path.exists(_resolve(legs_file)):
            gt_legs = load_json(legs_file).get("gt_legs_m")
        if gt_legs:
            res["metrics"] = {"shape_recall": score_shape_recall(lines, gt_legs, tol)}
        else:
            res["metrics"] = {"geometry_profile":
                              score_geometry_profile(lines, gt.get("published_distances_m"), tol)}
        res["result"] = "SCORED"
        return res

    start, legs = legs_from_extraction(obj)
    rings = rings_from_extraction(obj)
    labels = labels_from_extraction(obj)
    metrics = {}

    if legs:
        metrics["closure"] = score_closure(start, legs, tol["closure_m"])
        gvl = score_geom_vs_label(start, legs, tol)
        if gvl:
            metrics["geom_vs_label"] = gvl
    if labels:
        pub = {}
        if "published_areas_m2" in gt:
            pub["areas_m2"] = gt["published_areas_m2"]
        if "published" in gt:
            pub.update(gt["published"])
        if pub:
            metrics["label_match"] = score_label_match(labels, pub, tol)
        else:
            metrics["labels_recovered"] = len(labels)
    if rings:
        pub_areas = gt.get("published_areas_m2")
        if not pub_areas and gt.get("rings_file") is None:
            pub_areas = [v.get("printed_area_m2") for v in obj.values()
                         if isinstance(v, dict) and v.get("printed_area_m2")] or None
        if pub_areas:
            am = score_area(rings, pub_areas, tol["area_m2"])
            if am:
                metrics["area_match"] = am

    res["metrics"] = metrics
    res["result"] = "SCORED" if metrics else "NO-METRICS (shape unrecognized)"
    return res


def main():
    manifest = load_json("manifest.json")
    tol = manifest["tolerances"]
    only = sys.argv[1] if len(sys.argv) > 1 else None
    plans = [p for p in manifest["plans"] if (only is None or p["id"] == only)]

    print(f"R1 benchmark - {len(plans)} plan(s), tolerances {tol}\n" + "=" * 64)
    avail = pending = 0
    for p in plans:
        r = score_plan(p, tol)
        ax = r["axes"]
        tag = f"{ax.get('mode','?')}/{ax.get('era','?')}/{ax.get('input_form','?')}"
        print(f"\n[{r['id']}]  ({tag})  gt={r['gt_type']}")
        if r["result"].startswith(("NO-EXTRACTION", "LOAD-ERROR")):
            pending += 1
            print(f"    {r['result']}")
            continue
        avail += 1
        for name, m in r.get("metrics", {}).items():
            print(f"    {name}: {json.dumps(m, ensure_ascii=False)}")
        if not r.get("metrics"):
            print(f"    {r['result']}")
    print("\n" + "=" * 64)
    print(f"coverage: {avail} scored, {pending} pending extraction, "
          f"{len(manifest['plans'])} total in corpus")
    print("Failure profile = the rows above; pending rows are the acquisition backlog.")


if __name__ == "__main__":
    main()

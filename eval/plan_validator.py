"""Deterministic survey-computation math for the eval harness.

The closure / inverse / area primitives that score.py reuses, plus lot-level
validation. The point of the lot layer: a subdivision plat prints per-lot
areas that are mathematically redundant with its bearings/distances (and its
curve r=/a= dimensions, through the circular-segment term). So a blind read
can be validated with NO external answer key by checking, per lot:

  closure   — the read courses must return to start   (catches bearing misreads)
  area      — shoelace(+arc segments) vs printed area (catches distance misreads)
  block sum — lot areas + dedicated road = parent     (catches lot-level misses)

These are the same open formulas municipal subdivision review is built on
(area by coordinates, bearing-bearing intersection, triangle-area cutoffs).

Conventions: points are [E, N] metres; azimuths are decimal degrees clockwise
from north. Bearings parse from full-circle DMS ("92°31'19\"") or quadrant
form ("N56°09'E").

Self-test: python plan_validator.py --selftest
The test vectors are the instructor-computed worked examples from a public
survey-computations lecture (GEOM 2120 L8A, "Subdivision of Land Parcels"):
numeric facts only. The module's math must reproduce a second authority's
published answers before it is trusted to judge any read.
"""
import math
import re
import sys


# --------------------------------------------------------------------------- #
# bearings
# --------------------------------------------------------------------------- #
_DMS_RE = re.compile(
    r"""^\s*(?P<pre>[NSns])?\s*
        (?P<deg>\d+(?:\.\d+)?)\s*[°º]?\s*
        (?:(?P<min>\d+(?:\.\d+)?)\s*['’′]\s*)?
        (?:(?P<sec>\d+(?:\.\d+)?)\s*["”″]\s*)?
        (?P<post>[EWew])?\s*$""",
    re.VERBOSE,
)


def dms_to_deg(value):
    """Parse a bearing/angle to decimal degrees.

    Accepts a number (passed through), full-circle DMS ("152°20'38\"",
    "92°31'"), or a quadrant bearing ("N56°09'E", "S33°51'W") which is
    converted to a whole-circle azimuth.
    """
    if isinstance(value, (int, float)):
        return float(value)
    m = _DMS_RE.match(str(value))
    if not m:
        raise ValueError(f"unparseable bearing: {value!r}")
    deg = float(m.group("deg")) + float(m.group("min") or 0) / 60.0 \
        + float(m.group("sec") or 0) / 3600.0
    pre = (m.group("pre") or "").upper()
    post = (m.group("post") or "").upper()
    if not pre and not post:
        return deg
    quad = pre + post
    if quad == "NE":
        az = deg
    elif quad == "SE":
        az = 180.0 - deg
    elif quad == "SW":
        az = 180.0 + deg
    elif quad == "NW":
        az = 360.0 - deg
    elif quad == "N":
        az = 0.0 if deg == 0 else deg
    elif quad == "S":
        az = 180.0
    else:  # bare E / W
        az = 90.0 if quad == "E" else 270.0
    return az % 360.0


def deg_to_dms(deg):
    """Format decimal degrees as D°MM'SS\" (rounded to the second)."""
    deg = deg % 360.0
    total = round(deg * 3600)
    d, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{d}°{m:02d}'{s:02d}\""


def ang_diff_deg(a, b):
    """Smallest absolute difference between two azimuths, degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


# --------------------------------------------------------------------------- #
# coordinate primitives  (points are [E, N])
# --------------------------------------------------------------------------- #
def forward(pt, az_deg, dist):
    """Point + bearing/distance -> new point."""
    az = math.radians(az_deg)
    return [pt[0] + dist * math.sin(az), pt[1] + dist * math.cos(az)]


def inverse(p1, p2):
    """Two points -> (azimuth_deg, distance)."""
    de, dn = p2[0] - p1[0], p2[1] - p1[1]
    return math.degrees(math.atan2(de, dn)) % 360.0, math.hypot(de, dn)


def brg_brg_intersect(p1, az1_deg, p2, az2_deg):
    """Bearing-bearing intersection of two rays; returns [E, N].

    Raises ValueError on (near-)parallel bearings.
    """
    a1, a2 = math.radians(az1_deg), math.radians(az2_deg)
    d1 = (math.sin(a1), math.cos(a1))
    d2 = (math.sin(a2), math.cos(a2))
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denom) < 1e-12:
        raise ValueError("bearings are parallel; no intersection")
    de, dn = p2[0] - p1[0], p2[1] - p1[1]
    t = (de * d2[1] - dn * d2[0]) / denom
    return [p1[0] + t * d1[0], p1[1] + t * d1[1]]


def shoelace_signed(ring):
    """Signed area of a polygon (positive = counter-clockwise in E/N)."""
    if len(ring) >= 2 and ring[0] == ring[-1]:
        ring = ring[:-1]
    s = 0.0
    for i in range(len(ring)):
        e1, n1 = ring[i]
        e2, n2 = ring[(i + 1) % len(ring)]
        s += e1 * n2 - e2 * n1
    return s / 2.0


def shoelace(ring):
    """Absolute polygon area (area by coordinates)."""
    return abs(shoelace_signed(ring))


def triangle_cutoff_dist(area, b, gamma_deg):
    """The subdivision cutoff formula c = 2*A / (b * sin(gamma))."""
    return 2.0 * area / (b * math.sin(math.radians(gamma_deg)))


def segment_area(r, delta_rad):
    """Circular segment area between an arc and its chord."""
    return r * r * (delta_rad - math.sin(delta_rad)) / 2.0


# --------------------------------------------------------------------------- #
# legs / traverse  (a leg is {"az": deg, "dist": m} + optional arc keys)
# --------------------------------------------------------------------------- #
def legs_from_corners(ring):
    """Ring of corners -> legs around the full closed boundary."""
    if len(ring) >= 2 and ring[0] == ring[-1]:
        ring = ring[:-1]
    legs = []
    for i in range(len(ring)):
        az, dist = inverse(ring[i], ring[(i + 1) % len(ring)])
        legs.append({"az": az, "dist": dist})
    return legs


def run_traverse(start, legs):
    """Forward-compute legs from start; return (points, misclose_vec, misclose_m).

    A leg travels by its chord: {"az", "dist"} directly, or an arc leg
    {"r", "arc", "side": "L"|"R"} whose chord is derived from the incoming
    tangent (az of the previous leg) — the tangent-curve case on a plat.
    An arc leg may instead carry an explicit chord {"az", "dist"} alongside
    its arc keys (the non-tangent case).
    """
    pts = [list(start)]
    tangent = None
    for lg in legs:
        if "az" in lg and "dist" in lg:
            az, dist = float(lg["az"]), float(lg["dist"])
            if "r" in lg and ("arc" in lg or "delta" in lg):
                delta = _arc_delta(lg)
                tangent = az + (delta / 2.0 if lg.get("side", "R") == "R" else -delta / 2.0)
            else:
                tangent = az
        elif "r" in lg and ("arc" in lg or "delta" in lg):
            if tangent is None:
                raise ValueError("arc leg with no chord needs a previous leg for its tangent")
            delta = _arc_delta(lg)
            half = delta / 2.0 if lg.get("side", "R") == "R" else -delta / 2.0
            az = tangent + half
            dist = 2.0 * float(lg["r"]) * math.sin(abs(_arc_delta_rad(lg)) / 2.0)
            tangent = tangent + 2.0 * half
        else:
            raise ValueError(f"leg needs az/dist or r/arc: {lg}")
        pts.append(forward(pts[-1], az, dist))
    mvec = (pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
    return pts, mvec, math.hypot(*mvec)


def _arc_delta_rad(lg):
    if "delta" in lg:
        return math.radians(dms_to_deg(lg["delta"]))
    return float(lg["arc"]) / float(lg["r"])


def _arc_delta(lg):
    return math.degrees(_arc_delta_rad(lg))


def area_from_legs(start, legs):
    """Area enclosed by the legs: shoelace over the chord polygon, then each
    arc leg's circular segment added if it bulges away from the interior,
    subtracted if it bulges into it.
    """
    pts, _, _ = run_traverse(start, legs)
    ring = pts[:-1] if math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) < 1e-9 else pts
    signed = shoelace_signed(ring)
    area = abs(signed)
    ccw = signed > 0
    for lg in legs:
        if "r" in lg and ("arc" in lg or "delta" in lg):
            seg = segment_area(float(lg["r"]), abs(_arc_delta_rad(lg)))
            # CCW travel keeps the interior on the left, so an arc bowing
            # right (side R) bows outward and adds area; CW is the mirror.
            outward = (lg.get("side", "R") == "R") == ccw
            area += seg if outward else -seg
    return area


# --------------------------------------------------------------------------- #
# simple circular curves (the road-plan invariants)
#
# A simple curve is fully determined by ANY TWO of {R, delta, arc L, chord C,
# tangent T, mid-ordinate M, external E} — and road plans label several
# (r=, a=, delta, curve tables with T and C). That redundancy makes curve
# labels self-validating: solve from each labeled pair, vote, and the label
# no solution explains is the misread.
# --------------------------------------------------------------------------- #
CURVE_KEYS = ("r", "delta", "arc", "chord", "tangent", "mid_ordinate", "external")


def _bisect(f, lo, hi, iters=200):
    flo = f(lo)
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        fm = f(mid)
        if fm == 0.0:
            return mid
        if (flo < 0) != (fm < 0):
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2.0


def solve_curve(**known):
    """Fill in every simple-curve element from any sufficient pair of them.

    Accepts a subset of r, delta (deg or DMS string), arc, chord, tangent,
    mid_ordinate, external. Returns {r, delta_deg, arc, chord, tangent,
    mid_ordinate, external, degree_arc_100} (degree of curve, arc definition,
    per 100 units — how historical cadastral plans dimension curves).
    """
    k = {key: v for key, v in known.items() if v is not None}
    if "delta" in k:
        k["delta"] = dms_to_deg(k["delta"])
    r, delta = k.get("r"), k.get("delta")
    # reduce every supported pair to (r, delta)
    if r is None or delta is None:
        if r is not None:
            if "arc" in k:
                delta = math.degrees(k["arc"] / r)
            elif "chord" in k:
                delta = math.degrees(2 * math.asin(k["chord"] / (2 * r)))
            elif "tangent" in k:
                delta = math.degrees(2 * math.atan(k["tangent"] / r))
            elif "mid_ordinate" in k:
                delta = math.degrees(2 * math.acos(1 - k["mid_ordinate"] / r))
            elif "external" in k:
                delta = math.degrees(2 * math.acos(r / (r + k["external"])))
        elif delta is not None:
            half = math.radians(delta) / 2.0
            if "arc" in k:
                r = k["arc"] / math.radians(delta)
            elif "chord" in k:
                r = k["chord"] / (2 * math.sin(half))
            elif "tangent" in k:
                r = k["tangent"] / math.tan(half)
            elif "mid_ordinate" in k:
                r = k["mid_ordinate"] / (1 - math.cos(half))
            elif "external" in k:
                r = k["external"] / (1 / math.cos(half) - 1)
        elif "arc" in k and "chord" in k:
            # C/L = sin(d/2)/(d/2), monotone decreasing in d — bisect on d/2
            ratio = k["chord"] / k["arc"]
            half = _bisect(lambda x: math.sin(x) / x - ratio, 1e-9, math.pi - 1e-9)
            delta = math.degrees(2 * half)
            r = k["arc"] / math.radians(delta)
        elif "arc" in k and "tangent" in k:
            # T/L = tan(d/2)/d, monotone increasing in d — bisect on d/2
            ratio = k["tangent"] / k["arc"]
            half = _bisect(lambda x: math.tan(x) / (2 * x) - ratio, 1e-9, math.pi / 2 - 1e-9)
            delta = math.degrees(2 * half)
            r = k["arc"] / math.radians(delta)
        if r is None or delta is None:
            raise ValueError(f"insufficient curve elements to solve: {sorted(k)}")
    half = math.radians(delta) / 2.0
    return {
        "r": r,
        "delta_deg": delta,
        "arc": r * math.radians(delta),
        "chord": 2 * r * math.sin(half),
        "tangent": r * math.tan(half),
        "mid_ordinate": r * (1 - math.cos(half)),
        "external": r * (1 / math.cos(half) - 1),
        "degree_arc_100": math.degrees(100.0 / r),
    }


def check_curve(labels, tol=None):
    """Cross-validate a set of read curve labels by pair-voting.

    labels: dict with >=2 of CURVE_KEYS (delta may be a DMS string). Solves
    the curve from every sufficient pair, keeps the solution that explains
    the most labels, and reports the labels it can NOT explain — with 3+
    labels a single misread is both detected and IDENTIFIED.
    Tolerances: dist_m (default 0.05) for length-like elements, delta_arcsec
    (default 30) for the delta angle.
    """
    tol = tol or {}
    dist_tol = tol.get("dist_m", 0.05)
    delta_tol = tol.get("delta_arcsec", 30.0) / 3600.0
    vals = {key: (dms_to_deg(v) if key == "delta" else float(v))
            for key, v in labels.items() if key in CURVE_KEYS and v is not None}
    keys = sorted(vals)
    if len(keys) < 2:
        raise ValueError("need at least two curve labels to cross-check")
    best = None
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            try:
                sol = solve_curve(**{keys[i]: vals[keys[i]], keys[j]: vals[keys[j]]})
            except (ValueError, ZeroDivisionError):
                continue
            explained, suspects = [], []
            for key in keys:
                got = sol["delta_deg"] if key == "delta" else sol[key]
                ok = abs(got - vals[key]) <= (delta_tol if key == "delta" else dist_tol)
                (explained if ok else suspects).append(key)
            cand = {"defining_pair": (keys[i], keys[j]), "explained": explained,
                    "suspects": suspects, "solution": sol}
            if best is None or len(explained) > len(best["explained"]):
                best = cand
    best["pass"] = not best["suspects"]
    return best


def deflection_deg(arc, r):
    """Deflection angle (at the BC, from the tangent) subtending an arc: 90*L/(pi*R)."""
    return 90.0 * arc / (math.pi * r)


def parse_station(s):
    """Chainage like '2+327.076' -> 2327.076 (the + is notation, not addition)."""
    return float(str(s).replace("+", ""))


def curve_stations(bc, tangent_az, r, arcs, side="R"):
    """Coordinates of stakeout points along a curve, by deflection + chord
    from the BC. bc is [E, N]; tangent_az is the back-tangent bearing at the
    BC; arcs are arc lengths from the BC. Returns [{arc, deflection_deg,
    chord, az, pt}] — deflections are + for a right-hand curve, - for left.
    """
    sign = 1.0 if side.upper().startswith("R") else -1.0
    out = []
    for a in arcs:
        d = deflection_deg(a, r)
        az = (tangent_az + sign * d) % 360.0
        ch = 2.0 * r * math.sin(math.radians(d))
        out.append({"arc": a, "deflection_deg": sign * d, "chord": ch,
                    "az": az, "pt": forward(bc, az, ch)})
    return out


# --------------------------------------------------------------------------- #
# lot-level validation (the subdivision-plat invariants)
# --------------------------------------------------------------------------- #
def courses_to_legs(courses):
    """Read courses -> traverse legs. A course is
    {"bearing": <dms|deg>, "dist": m}  or an arc
    {"r": m, "arc": m [, "side": "L"|"R", "chord_bearing": .., "chord": ..]}.
    """
    legs = []
    for c in courses:
        lg = {}
        if "bearing" in c:
            lg["az"] = dms_to_deg(c["bearing"])
        if "chord_bearing" in c:
            lg["az"] = dms_to_deg(c["chord_bearing"])
        if "dist" in c:
            lg["dist"] = float(c["dist"])
        if "chord" in c:
            lg["dist"] = float(c["chord"])
        for k in ("r", "arc", "delta", "side"):
            if k in c:
                lg[k] = c[k]
        legs.append(lg)
    return legs


def validate_lot(courses, printed_area=None, tol=None):
    """Check one lot's read courses against its own geometry + printed area.

    Returns {closure_m, precision_1_in, area_m2, printed_area_m2, area_diff_m2,
    closure_pass, area_pass, pass}. Tolerances: closure_m (default 0.05) and
    area_m2 (default max(0.5, 0.2% of printed) — printed areas are rounded and
    come from the surveyor's adjusted coordinates, so exact match is not fair).
    """
    tol = tol or {}
    legs = courses_to_legs(courses)
    _, _, misclose = run_traverse([0.0, 0.0], legs)
    perim = sum(lg["dist"] for lg in legs if "dist" in lg) or None
    closure_tol = tol.get("closure_m", 0.05)
    out = {
        "n_courses": len(legs),
        "closure_m": round(misclose, 4),
        "precision_1_in": (round(perim / misclose) if perim and misclose > 1e-12 else None),
        "closure_pass": misclose <= closure_tol,
    }
    if printed_area is not None:
        area = area_from_legs([0.0, 0.0], legs)
        area_tol = tol.get("area_m2", max(0.5, 0.002 * printed_area))
        out.update({
            "area_m2": round(area, 2),
            "printed_area_m2": printed_area,
            "area_diff_m2": round(area - printed_area, 2),
            "area_pass": abs(area - printed_area) <= area_tol,
        })
    out["pass"] = out["closure_pass"] and out.get("area_pass", True)
    return out


def validate_lots(lots, parent_area=None, road_area=None, tol=None):
    """Validate a set of lots and, when the parent parcel (and any dedicated
    road) area is known, the block-tiling invariant: sum(lots) + road = parent.
    lots: [{"id", "courses", "printed_area_m2"}].
    """
    tol = tol or {}
    per_lot = {lot.get("id", str(i)): validate_lot(lot["courses"], lot.get("printed_area_m2"), tol)
               for i, lot in enumerate(lots)}
    out = {"lots": per_lot,
           "n_pass": sum(1 for r in per_lot.values() if r["pass"]),
           "n_lots": len(per_lot)}
    if parent_area is not None:
        total = sum(r.get("area_m2") or 0.0 for r in per_lot.values()) + (road_area or 0.0)
        sum_tol = tol.get("block_sum_m2", max(1.0, 0.002 * parent_area))
        out["block_sum"] = {"lots_plus_road_m2": round(total, 2),
                            "parent_m2": parent_area,
                            "diff_m2": round(total - parent_area, 2),
                            "pass": abs(total - parent_area) <= sum_tol}
    return out


# --------------------------------------------------------------------------- #
# self-test — instructor-computed worked examples (GEOM 2120 L8A)
# --------------------------------------------------------------------------- #
def _selftest():
    fails = []

    def chk(name, got, want, tol):
        ok = abs(got - want) <= tol
        if not ok:
            fails.append(name)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got:.4f}, published {want}")

    print("[1] Equal-frontage example (lecture pp.7-8)")
    p = {1: [200.000, 100.000], 2: [203.181, 136.361],
         3: [245.241, 138.197], 4: [243.347, 102.145]}  # [E, N]
    chk("area of Lot 1 by coordinates", shoelace([p[1], p[2], p[3], p[4]]), 1541.1, 0.05)
    p5 = [(p[1][0] + p[4][0]) / 2, (p[1][1] + p[4][1]) / 2]
    chk("midpoint 5 N", p5[1], 101.073, 0.001)
    chk("midpoint 5 E", p5[0], 221.673, 0.001)
    az25, d25 = inverse(p[2], p5)
    chk("inverse 2-5 bearing (deg)", az25, dms_to_deg("152°20'38\""), 1.5 / 3600)
    chk("inverse 2-5 distance", d25, 39.840, 0.001)
    chk("area of triangle 1-2-5", shoelace([p[1], p[2], p5]), 392.33, 0.05)
    az23, _ = inverse(p[2], p[3])
    # mm-rounded published coordinates move a derived bearing a couple of
    # seconds; 3" is the honest tolerance for inverses of rounded coords.
    chk("bearing 2-3 (deg)", az23, dms_to_deg("87°30'00\""), 3.0 / 3600)
    gamma = az25 - az23
    chk("interior angle at 2 (deg)", gamma, dms_to_deg("64°50'38\""), 4.0 / 3600)
    c = triangle_cutoff_dist(1541.1 / 2 - 392.33, d25, gamma)
    chk("cutoff distance 2-6", c, 20.977, 0.002)
    p6 = forward(p[2], az23, c)
    chk("point 6 N", p6[1], 137.276, 0.002)
    chk("point 6 E", p6[0], 224.138, 0.002)

    print("[2] Fixed-bearing example (lecture pp.18-20)")
    q = {11: [500.0, 200.0], 12: [635.0, 155.0], 13: [740.0, 175.0],
         14: [715.0, 335.0], 15: [620.0, 310.0]}
    chk("area of Lot 2 by coordinates", shoelace([q[11], q[12], q[13], q[14], q[15]]),
        26325.0, 0.5)
    az1314, _ = inverse(q[13], q[14])
    chk("bearing 13-14 mod 180 (deg)", az1314 % 180.0, dms_to_deg("171°07'10\""), 1.5 / 3600)
    p18 = brg_brg_intersect(q[11], 90.0, q[13], az1314)
    chk("trial point 18 E", p18[0], 736.093, 0.002)
    chk("trial point 18 N", p18[1], 200.000, 0.002)
    _, b = inverse(q[11], p18)
    chk("trial line distance 11-18", b, 236.093, 0.002)
    chk("area 11-12-13-18", shoelace([q[11], q[12], q[13], p18]), 6663.67, 0.15)
    atr = 26325.0 / 2 - 6663.67
    chk("additional area ATR", atr, 6498.83, 0.15)
    az1115, _ = inverse(q[11], q[15])
    chk("bearing 11-15 (deg)", az1115, dms_to_deg("47°29'22\""), 1.5 / 3600)
    th1, th2 = 90.0 - az1115, az1314 % 180.0 - 90.0
    x = math.sqrt(b * b - 2 * atr * (1 / math.tan(math.radians(th1))
                                     + 1 / math.tan(math.radians(th2))))
    chk("new boundary length x (16-17)", x, 198.821, 0.01)
    h = 2 * atr / (x + b)
    chk("offset h", h, 29.886, 0.002)
    d1116 = h / math.sin(math.radians(th1))
    d1817 = h / math.sin(math.radians(th2))
    chk("distance 11-16", d1116, 44.228, 0.002)
    chk("distance 18-17", d1817, 30.249, 0.002)
    p16 = forward(q[11], az1115, d1116)
    p17 = forward(p18, az1314, d1817)
    chk("point 16 N", p16[1], 229.886, 0.002)
    chk("point 16 E", p16[0], 532.603, 0.002)
    chk("point 17 N", p17[1], 229.886, 0.002)
    chk("point 17 E", p17[0], 731.424, 0.002)

    print("[3] Arc-lot invariants (synthetic, exact by construction)")
    # Subdivision-scale toy lot: 20 m square, east side replaced by a
    # semicircular arc bulging outward (a cul-de-sac-ish curve): area =
    # 400 + pi*r^2/2 with r = 10. Traverse CCW from (0,0).
    arc_lot = [
        {"bearing": 90.0, "dist": 20.0},                     # south side, eastward
        {"r": 10.0, "arc": math.pi * 10.0, "side": "R",
         "chord_bearing": 0.0, "chord": 20.0},               # east side as arc: travel N, bulge E = right-hand
        {"bearing": 270.0, "dist": 20.0},                    # north side, westward
        {"bearing": 180.0, "dist": 20.0},                    # west side, southward
    ]
    want = 400.0 + math.pi * 100.0 / 2.0
    r = validate_lot(arc_lot, printed_area=round(want, 1), tol={"closure_m": 1e-6, "area_m2": 0.1})
    chk("arc lot closure", r["closure_m"], 0.0, 1e-9)
    chk("arc lot area (chord polygon + segment)", r["area_m2"], want, 0.1)
    if not r["pass"]:
        fails.append("arc lot validate_lot pass")
    # A 1 m distance misread must be flagged. Closure catches it; note the
    # area over the implicitly-closed broken traverse may coincidentally
    # match (a sheared polygon keeps its area), which is why validate_lot
    # ANDs the checks instead of trusting area alone.
    bad = [dict(arc_lot[0], dist=21.0)] + arc_lot[1:]
    rb = validate_lot(bad, printed_area=round(want, 1), tol={"closure_m": 0.05})
    ok = (not rb["closure_pass"]) and (not rb["pass"])
    print(f"  {'PASS' if ok else 'FAIL'}  distance-misread detection: "
          f"closure_pass={rb['closure_pass']} pass={rb['pass']}")
    if not ok:
        fails.append("distance-misread detection")
    # A flipped curve direction (arc read bulging INTO the lot instead of out)
    # keeps the same chord — closure is perfectly blind to it — but the area
    # moves by two circular segments. Only the printed-area check catches it:
    flipped = [arc_lot[0], dict(arc_lot[1], side="L")] + arc_lot[2:]
    rf = validate_lot(flipped, printed_area=round(want, 1), tol={"closure_m": 0.05})
    ok = rf["closure_pass"] and (not rf["area_pass"])
    print(f"  {'PASS' if ok else 'FAIL'}  curve-direction-misread detection: "
          f"closure_pass={rf['closure_pass']} area_pass={rf['area_pass']} "
          f"(closure blind, area catches: diff {rf['area_diff_m2']} m2)")
    if not ok:
        fails.append("curve-direction-misread detection")

    print("[4] Circular curve example (L7 lecture pp.16-20)")
    delta1 = dms_to_deg("101°42'12\"") - 30.0
    chk("delta from tangent bearings (deg)", delta1, dms_to_deg("71°42'12\""), 0.5 / 3600)
    cv = solve_curve(r=300.0, delta=delta1)
    chk("tangent distance T", cv["tangent"], 216.778, 0.002)
    chk("curve length L", cv["arc"], 375.438, 0.002)
    bc1 = 360.277 - cv["tangent"]
    chk("chainage of BC", bc1, 143.499, 0.002)
    chk("chainage of EC", bc1 + cv["arc"], 518.937, 0.003)
    stations1 = [  # (arc from BC, published deflection, published chord)
        (56.501, "5°23'44\"", 56.418),
        (156.501, "14°56'41\"", 154.732),
        (256.501, "24°29'39\"", 248.759),
        (356.501, "34°02'36\"", 335.892),
        (375.438, "35°51'06\"", 351.413),
    ]
    for arc, dpub, cpub in stations1:
        d = deflection_deg(arc, 300.0)
        chk(f"deflection @ arc {arc}", d, dms_to_deg(dpub), 1.0 / 3600)
        chk(f"chord      @ arc {arc}", 2 * 300.0 * math.sin(math.radians(d)), cpub, 0.005)
    chk("final deflection == delta/2", deflection_deg(cv["arc"], 300.0), delta1 / 2, 1e-9)

    print("[5] Road centreline curve example (L7 lecture pp.24-27)")
    delta2 = dms_to_deg("162°18'22\"") - dms_to_deg("80°30'30\"")
    chk("delta angle (deg)", delta2, dms_to_deg("81°47'52\""), 0.5 / 3600)
    cv2 = solve_curve(r=150.0, delta=delta2)
    chk("tangent length T", cv2["tangent"], 129.929, 0.002)
    chk("curve length L", cv2["arc"], 214.146, 0.002)
    d1 = 238.479 * math.sin(math.radians(dms_to_deg("44°46'19\""))) \
        / math.sin(math.radians(dms_to_deg("98°12'08\"")))
    chk("sine-law distance D1 (11-PI)", d1, 169.693, 0.005)
    chk("distance D2 (11-BC)", d1 - cv2["tangent"], 39.764, 0.005)
    bc_stn = parse_station("2+327.076")
    chk("chainage of EC", bc_stn + cv2["arc"], parse_station("2+541.222"), 0.005)
    bc2 = [1495.779, 4795.685]  # published BC coordinates [E, N]
    az_bt = dms_to_deg("80°30'30\"")
    rows = [  # (station, published bearing BC->stn, chord, E, N)
        ("2+350", "84°53'11\"", 22.901, 1518.589, 4797.726),
        ("2+400", "94°26'09\"", 72.208, 1567.771, 4790.100),
        ("2+450", "103°59'06\"", 119.513, 1611.750, 4766.803),
        ("2+500", "113°32'04\"", 163.506, 1645.685, 4730.397),
        ("2+541.222", "121°24'26\"", 196.418, 1663.419, 4693.328),
    ]
    arcs = [parse_station(s) - bc_stn for s, *_ in rows]
    pts = curve_stations(bc2, az_bt, 150.0, arcs, side="R")
    for (stn, bpub, cpub, epub, npub), got in zip(rows, pts):
        chk(f"stn {stn} bearing (deg)", got["az"], dms_to_deg(bpub), 2.0 / 3600)
        chk(f"stn {stn} chord", got["chord"], cpub, 0.005)
        chk(f"stn {stn} E", got["pt"][0], epub, 0.005)
        chk(f"stn {stn} N", got["pt"][1], npub, 0.005)

    print("[6] Curve-label pair-voting (synthetic misread injection)")
    good = {"r": 300.0, "delta": "71°42'12\"", "arc": 375.438,
            "tangent": 216.778, "chord": 351.413}
    rg = check_curve(good)
    ok = rg["pass"] and len(rg["explained"]) == 5
    print(f"  {'PASS' if ok else 'FAIL'}  consistent labels all explained: "
          f"explained={rg['explained']}")
    if not ok:
        fails.append("curve voting consistent")
    bad = dict(good, tangent=261.778)  # digit-swap misread of T
    rb2 = check_curve(bad)
    ok = (not rb2["pass"]) and rb2["suspects"] == ["tangent"]
    print(f"  {'PASS' if ok else 'FAIL'}  misread label identified: "
          f"suspects={rb2['suspects']} via pair {rb2['defining_pair']}")
    if not ok:
        fails.append("curve voting misread id")

    print(f"\nRESULT: {'OK' if not fails else 'FAILED ' + str(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)

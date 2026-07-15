#!/usr/bin/env python3
"""close_arc_traverse.py - traverse closure + area for a lot boundary that
mixes straight courses with CURVE-TABLE arcs.

The exterior-boundary closer sums straight lat/dep vectors; subdivision LOTS
front on curved roads, so a lot ring is line courses + one-or-more arc courses
(each an r=/delta= row of the curve table). This closes such a ring:

  * each LINE advances by (dist*sin az, dist*cos az); heading := az.
  * each CURVE is tangent to the running heading (road frontages are tangent
    curves): chord = 2R sin(delta/2) at chord_az = heading + turn*delta/2, then
    heading := heading + turn*delta  (turn = +1 right / -1 left).
  * area = shoelace(vertices) + sum over arcs of turn * circular-segment area
    (1/2 R^2 (delta_rad - sin delta_rad)) - the bulge each arc adds to / removes
    from the straight-chord polygon.

Closure precision (misclosure/perimeter) and area-vs-printed are the two
oracles - the same ones that vindicated the exterior boundary and the curve
table (L = R*delta).
"""
import math


def _az(ns, d, m, s, ew):
    a = d + m / 60 + s / 3600
    if ns == "N" and ew == "E": return a
    if ns == "S" and ew == "E": return 180 - a
    if ns == "S" and ew == "W": return 180 + a
    return 360 - a                      # N..W


def close_traverse(courses):
    """courses: ordered list of
        {"line": (NS,d,m,s,EW, dist)}                     straight leg
        {"curve": (R, (dd,mm,ss) delta, turn)}            tangent arc, turn +1/-1
    Returns dict with vertices, misclosure, perimeter, precision, area_sqft."""
    x = y = 0.0
    heading = None
    verts = [(0.0, 0.0)]
    arcs = []            # (apex_x, apex_y, magnitude) for signed area via bulge test
    per = 0.0
    for c in courses:
        if "line" in c or "line_az" in c:
            if "line_az" in c:
                az, dist = c["line_az"]           # azimuth already resolved
            else:
                ns, d, m, s, ew, dist = c["line"]
                az = _az(ns, d, m, s, ew)
            heading = az
            x += dist * math.sin(math.radians(az))
            y += dist * math.cos(math.radians(az))
            per += dist
        else:
            cv = c["curve"]
            R, (dd, mm, ss), turn = cv[0], cv[1], cv[2]
            entry = cv[3] if len(cv) > 3 else 0.0   # tangent-in offset from heading:
            delta = dd + mm / 60 + ss / 3600         # 0 = tangent curve, +-90 = radial
            if heading is None:
                raise ValueError("curve cannot be the first course (needs a tangent-in)")
            tin = heading + entry
            dr = math.radians(delta)
            chord = 2 * R * math.sin(dr / 2)
            chord_az = tin + turn * delta / 2
            x0p, y0p = x, y
            x += chord * math.sin(math.radians(chord_az))
            y += chord * math.cos(math.radians(chord_az))
            heading = tin + turn * delta
            # sagitta apex of the arc, perpendicular to the chord on the bulge side
            sag = R - R * math.cos(dr / 2)
            mx, my = (x0p + x) / 2, (y0p + y) / 2
            perp = math.radians(chord_az) - turn * math.pi / 2   # bulge = away from center
            arcs.append((mx + sag * math.sin(perp), my + sag * math.cos(perp),
                         0.5 * R * R * (dr - math.sin(dr))))
            per += R * dr                      # arc length, not chord, for perimeter
        verts.append((x, y))
    mis = math.hypot(x, y)                      # back to (0,0)
    poly = 0.0
    for i in range(len(verts) - 1):
        x0, y0 = verts[i]; x1, y1 = verts[i + 1]
        poly += x0 * y1 - x1 * y0
    chord_area = poly / 2.0

    def inside(px, py):                         # ray-cast point-in-polygon
        c = False; n = len(verts)
        for i in range(n - 1):
            x0, y0 = verts[i]; x1, y1 = verts[i + 1]
            if (y0 > py) != (y1 > py) and \
               px < (x1 - x0) * (py - y0) / (y1 - y0 + 1e-30) + x0:
                c = not c
        return c
    # each arc adds its segment if it bulges OUT of the chord polygon, else subtracts
    area = abs(chord_area) + sum(
        (mag if not inside(ax, ay) else -mag) for ax, ay, mag in arcs)
    return {"misclosure": mis, "perimeter": per,
            "precision": f"1:{int(per/mis)}" if mis > 1e-9 else "exact",
            "area_sqft": area, "area_acres": area / 43560.0, "verts": verts}


def close_best(courses):
    """Resolve each curve's unknown turn (+1/-1) by choosing the sign
    combination that minimises misclosure. Curves may be given turn=0 (unknown);
    known turns are respected. Returns (result, turns)."""
    import itertools
    idx = [i for i, c in enumerate(courses) if "curve" in c and c["curve"][2] == 0]
    if not idx:
        return close_traverse(courses), [c["curve"][2] for c in courses if "curve" in c]
    best, best_turns = None, None
    for combo in itertools.product((+1, -1), repeat=len(idx)):
        trial = [dict(c) for c in courses]
        for k, i in enumerate(idx):
            R, delta, _ = trial[i]["curve"]
            trial[i]["curve"] = (R, delta, combo[k])
        r = close_traverse(trial)
        if best is None or r["misclosure"] < best["misclosure"]:
            best, best_turns = r, combo
    return best, best_turns


if __name__ == "__main__":
    # LOT 5, Area Thirty3 Estates - read off sheet 2, values from the banked
    # line/curve tables. Clockwise from the SW corner (bottom of the west side):
    #   W side up | top | E side down | L3 frontage | C5 frontage arc -> back
    lot5 = [
        {"line": ("N", 0, 17, 32, "E", 296.87)},   # west side  (up)
        {"line": ("S", 89, 41, 51, "E", 234.30)},  # north/top  (east)
        {"line": ("S", 0, 17, 32, "W", 298.97)},   # east side  (down)
        {"line": ("N", 89, 42, 28, "W", 194.54)},  # L3 frontage (west)
        {"curve": (370.00, (6, 10, 4), +1)},       # C5 arc, tangent to L3
    ]
    lot5[-1] = {"curve": (370.00, (6, 10, 4), 0)}   # turn unknown -> auto-resolve
    r, _ = close_best(lot5)
    print(f"LOT 5  (tangent arc C5)   misclosure {r['misclosure']:.3f} ft ({r['precision']})"
          f"  area {r['area_sqft']:,.0f} vs printed 70,026 sq.ft "
          f"(diff {abs(r['area_sqft']-70026):.0f})")

    # LOT 11 - a CUL-DE-SAC pie lot: radial sides meet the frontage arc C12 at
    # 90deg (entry=+-90, not tangent). N edge | L5 jog | C12 | S diagonal | W edge.
    def lot11(ls, turn, entry):
        az = _az("N", 0, 17, 33, "E")
        if ls < 0:
            az = (az + 180) % 360
        return [{"line": ("S", 89, 42, 28, "E", 229.56)}, {"line_az": (az, 43.81)},
                {"curve": (280.00, (33, 36, 16), turn, entry)},
                {"line": ("S", 56, 41, 17, "W", 328.56)}, {"line": ("N", 0, 6, 51, "W", 380.63)}]
    best = None
    for ls in (+1, -1):
        for turn in (+1, -1):
            for entry in (0, 90, -90, 180):
                rr = close_traverse(lot11(ls, turn, entry))
                if best is None or rr["misclosure"] < best["misclosure"]:
                    best = rr
    print(f"LOT 11 (radial arc C12)   misclosure {best['misclosure']:.3f} ft ({best['precision']})"
          f"  area {best['area_sqft']:,.0f} vs printed 72,820 sq.ft "
          f"(diff {abs(best['area_sqft']-72820):.0f})")

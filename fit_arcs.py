"""Fit true arcs from the polyline plan.

Chain the line segments into connected polylines (through degree-2 nodes), then
per chain: algebraic circle-fit. Low residual + sane radius + real angular sweep
=> emit an Arc (center, radius, start/end deg) the way LS_IMPORTPLAN expects;
otherwise keep the chain as line segments.
"""
import json, math
import numpy as np

IN = r"epp12345_plan.json"
OUT = r"epp12345_plan_arcs.json"
TOL = 0.5          # m, merge endpoints into shared nodes
RESID_MAX = 0.35   # m, circle-fit residual below this = arc
R_MIN, R_MAX = 2.0, 400.0
SWEEP_MIN = 12.0   # deg

plan = json.load(open(IN))
segs = [(L[0], L[1], L[2], L[3]) for L in plan["lines"]]

nodes = []
def node_id(p):
    for i, q in enumerate(nodes):
        if (q[0]-p[0])**2 + (q[1]-p[1])**2 <= TOL*TOL:
            return i
    nodes.append(p)
    return len(nodes) - 1

adj = {}
for si, (x0, y0, x1, y1) in enumerate(segs):
    a, b = node_id((x0, y0)), node_id((x1, y1))
    if a == b:
        continue
    adj.setdefault(a, []).append((b, si))
    adj.setdefault(b, []).append((a, si))
deg = {k: len(v) for k, v in adj.items()}

# trace maximal chains starting at junctions/endpoints (deg != 2)
used = set()
chains = []
for n in [k for k in adj if deg[k] != 2]:
    for nb, si in adj[n]:
        if si in used:
            continue
        used.add(si)
        seq = [nodes[n], nodes[nb]]
        cur, prev = nb, si
        while deg.get(cur, 0) == 2:
            nxt = [(x, s) for x, s in adj[cur] if s != prev and s not in used]
            if not nxt:
                break
            x, s = nxt[0]
            used.add(s)
            seq.append(nodes[x])
            cur, prev = x, s
        chains.append(np.array(seq, float))

def fit_circle(P):
    x, y = P[:, 0], P[:, 1]
    A = np.c_[2*x, 2*y, np.ones(len(x))]
    c, *_ = np.linalg.lstsq(A, x*x + y*y, rcond=None)
    cx, cy = c[0], c[1]
    r = math.sqrt(max(c[2] + cx*cx + cy*cy, 0))
    resid = float(np.hypot(x - cx, y - cy).std())
    return cx, cy, r, resid

lines, arcs = [], []
nfit = 0
for P in chains:
    is_arc = False
    if len(P) >= 4:
        cx, cy, r, resid = fit_circle(P)
        if resid <= RESID_MAX and R_MIN <= r <= R_MAX:
            ang = np.degrees(np.arctan2(P[:, 1]-cy, P[:, 0]-cx))
            unang = np.unwrap(np.radians(ang))
            sweep = math.degrees(abs(unang[-1] - unang[0]))
            if SWEEP_MIN <= sweep <= 350:
                s, e = math.degrees(unang[0]), math.degrees(unang[-1])
                if e < s:  # ensure CCW start->end
                    s, e = e, s
                arcs.append([round(cx, 4), round(cy, 4), round(r, 4),
                             round(s, 3), round(e, 3), "PROPERTY_LINE"])
                is_arc = True
                nfit += 1
    if not is_arc:
        for i in range(len(P) - 1):
            lines.append([round(P[i, 0], 4), round(P[i, 1], 4),
                          round(P[i+1, 0], 4), round(P[i+1, 1], 4), "PROPERTY_LINE"])

json.dump({"lines": lines, "arcs": arcs, "circles": [], "texts": []}, open(OUT, "w"))
print(f"chains: {len(chains)}  arcs fitted: {nfit}  lines: {len(lines)}")
for a in arcs:
    print(f"  arc  center=({a[0]:.1f},{a[1]:.1f})  r={a[2]:.2f}m  {a[3]:.1f}->{a[4]:.1f} deg")

# preview: lines black, arcs red (sampled)
import cv2
allx = [c for L in lines for c in (L[0], L[2])] + [a[0] for a in arcs]
ally = [c for L in lines for c in (L[1], L[3])] + [a[1] for a in arcs]
S = 4.0; minx, miny = min(allx), min(ally)
Wc = int((max(allx)-minx)*S)+40; Hc = int((max(ally)-miny)*S)+40
cv = np.full((Hc, Wc, 3), 255, np.uint8)
def px(x, y): return (int((x-minx)*S)+20, Hc-(int((y-miny)*S)+20))
for X0, Y0, X1, Y1, _ in lines:
    cv2.line(cv, px(X0, Y0), px(X1, Y1), (30, 30, 30), 1, cv2.LINE_AA)
for cx, cy, r, s, e, _ in arcs:
    ts = np.radians(np.linspace(s, e, 40))
    pts = [px(cx + r*math.cos(t), cy + r*math.sin(t)) for t in ts]
    for i in range(len(pts)-1):
        cv2.line(cv, pts[i], pts[i+1], (0, 0, 220), 2, cv2.LINE_AA)
cv2.imwrite(r"_epp12345_arcs.png", cv)
print("preview -> _epp12345_arcs.png")

"""Refine arcs using the published curve table (human-read).

For each curved region of the geometry: circle-fit, snap radius to the nearest
published value, reconstruct the arc, then validate by comparing the computed
arc length to the published a=. Straight chains stay lines.
"""
import json, math
import numpy as np

IN = r"epp12345_plan.json"
OUT = r"epp12345_plan_final.json"
TABLE = [(15.000, 12.778), (15.000, 13.468), (6.000, 7.234),
         (15.500, 20.915), (65.500, 14.400), (65.500, 29.047)]
PUB_R = sorted({r for r, _ in TABLE})
TOL = 0.5

segs = [(L[0], L[1], L[2], L[3]) for L in json.load(open(IN))["lines"]]

# --- chain segments through degree-2 nodes ---
nodes = []
def nid(p):
    for i, q in enumerate(nodes):
        if (q[0]-p[0])**2 + (q[1]-p[1])**2 <= TOL*TOL:
            return i
    nodes.append(p); return len(nodes)-1
adj = {}
for si, (x0, y0, x1, y1) in enumerate(segs):
    a, b = nid((x0, y0)), nid((x1, y1))
    if a != b:
        adj.setdefault(a, []).append((b, si)); adj.setdefault(b, []).append((a, si))
deg = {k: len(v) for k, v in adj.items()}
used = set(); chains = []
for n in [k for k in adj if deg[k] != 2]:
    for nb, si in adj[n]:
        if si in used: continue
        used.add(si); seq = [nodes[n], nodes[nb]]; cur, prev = nb, si
        while deg.get(cur, 0) == 2:
            nx = [(x, s) for x, s in adj[cur] if s != prev and s not in used]
            if not nx: break
            x, s = nx[0]; used.add(s); seq.append(nodes[x]); cur, prev = x, s
        chains.append(np.array(seq, float))

def fit_circle(P):
    x, y = P[:, 0], P[:, 1]
    c, *_ = np.linalg.lstsq(np.c_[2*x, 2*y, np.ones(len(x))], x*x+y*y, rcond=None)
    cx, cy = c[0], c[1]; r = math.sqrt(max(c[2]+cx*cx+cy*cy, 0))
    return cx, cy, r, float(np.hypot(x-cx, y-cy).std())

# --- classify chains: curved (fits a published radius) vs straight ---
curved, lines = [], []
for P in chains:
    if len(P) >= 4:
        cx, cy, r, resid = fit_circle(P)
        near = min(PUB_R, key=lambda pr: abs(pr - r))
        if resid <= 0.6 and abs(r - near) <= 0.25 * near:
            curved.append((P, cx, cy, near)); continue
    for i in range(len(P)-1):
        lines.append([round(P[i,0],4), round(P[i,1],4), round(P[i+1,0],4), round(P[i+1,1],4), "PROPERTY_LINE"])

# --- cluster curved chains by snapped-radius + spatial proximity = one physical arc ---
clusters = []
for item in curved:
    P, cx, cy, pr = item
    cen = P.mean(0)
    for cl in clusters:
        if cl["r"] == pr and math.hypot(cl["cen"][0]-cen[0], cl["cen"][1]-cen[1]) < 12:
            cl["pts"] = np.vstack([cl["pts"], P]); cl["cen"] = cl["pts"].mean(0); break
    else:
        clusters.append({"r": pr, "pts": P.copy(), "cen": cen})

# --- reconstruct + validate each arc against the table ---
arcs = []; report = []; tbl = TABLE[:]
for cl in clusters:
    P = cl["pts"]; r = cl["r"]
    cx, cy, _, _ = fit_circle(P)
    ang = np.degrees(np.arctan2(P[:, 1]-cy, P[:, 0]-cx))
    s, e = float(ang.min()), float(ang.max())
    if e - s > 300:  # wraps the +/-180 seam -> recompute on shifted angles
        ang2 = (ang + 360) % 360; s, e = float(ang2.min()), float(ang2.max())
    length = r * math.radians(e - s)
    cand = [t for t in tbl if t[0] == r]
    match = min(cand, key=lambda t: abs(t[1]-length)) if cand else None
    if match: tbl.remove(match)
    arcs.append([round(cx,4), round(cy,4), round(r,4), round(s,3), round(e,3), "PROPERTY_LINE"])
    report.append((r, round(length,3), match[1] if match else None))

json.dump({"lines": lines, "arcs": arcs, "circles": [], "texts": []}, open(OUT, "w"))
print(f"chains {len(chains)}  -> {len(arcs)} arcs, {len(lines)} lines  -> {OUT}\n")
print(f"{'r':>7} {'computed_a':>11} {'published_a':>12} {'err(m)':>8}")
for r, comp, pub in sorted(report):
    err = f"{abs(comp-pub):.3f}" if pub else "  -"
    print(f"{r:7.3f} {comp:11.3f} {str(pub):>12} {err:>8}")
unused = [t for t in tbl]
if unused: print("\nunmatched table entries (arc not found in geometry):", unused)

# preview
import cv2
allx=[c for L in lines for c in (L[0],L[2])]+[a[0] for a in arcs]
ally=[c for L in lines for c in (L[1],L[3])]+[a[1] for a in arcs]
S=4.0; mnx,mny=min(allx),min(ally); Wc=int((max(allx)-mnx)*S)+40; Hc=int((max(ally)-mny)*S)+40
cv=np.full((Hc,Wc,3),255,np.uint8)
def px(x,y): return (int((x-mnx)*S)+20, Hc-(int((y-mny)*S)+20))
for X0,Y0,X1,Y1,_ in lines: cv2.line(cv,px(X0,Y0),px(X1,Y1),(30,30,30),1,cv2.LINE_AA)
for cx,cy,r,s,e,_ in arcs:
    ts=np.radians(np.linspace(s,e,60)); pts=[px(cx+r*math.cos(t),cy+r*math.sin(t)) for t in ts]
    for i in range(len(pts)-1): cv2.line(cv,pts[i],pts[i+1],(0,0,220),2,cv2.LINE_AA)
cv2.imwrite(r"_epp12345_final.png",cv); print("preview -> _epp12345_final.png")

#!/usr/bin/env python3
"""Optimizes the continuous sliding-window controller's parameters
(window ms, min average speed, angle clearance) over all labeled
trajectories, reporting the error frontier and leave-one-out."""
import csv, math, re, os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FWD = {"R": (1.0, 0.0), "L": (-1.0, 0.0), "D": (0.0, 1.0), "U": (0.0, -1.0)}
FWD_OVERRIDE = {"g040": (0.0, 1.0)}

def ad(a, b):
    return math.degrees(math.atan2(a[0]*b[1]-a[1]*b[0], a[0]*b[0]+a[1]*b[1]))

cases, trajs = {}, {}
with open(os.path.join(root, "docs", "gesture-cases.csv")) as f:
    for r in csv.DictReader(f):
        cases[r["id"]] = r
with open(os.path.join(root, "docs", "gesture-trajectories.txt")) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        gid, rest = line.split(":", 1)
        pts = re.findall(r"\(([-\d.]+),([-\d.]+),([-\d.]+)\)", rest)
        trajs[gid.strip()] = [(float(a), float(b), float(c)) for a, b, c in pts]

class Case:
    def __init__(s, gid):
        row = cases[gid]
        toks = row["debug_line"].split()
        s.gid = gid
        s.intended = row["intended_gesture"].startswith(("yes", "true"))
        pts = trajs[gid]
        t, p = [0.0], [(0.0, 0.0)]
        for dt, dx, dy in pts:
            t.append(t[-1]+dt); p.append((p[-1][0]+dx, p[-1][1]+dy))
        s.t, s.p, s.n = t, p, len(pts)
        if gid in FWD_OVERRIDE or toks[0] == "stroke":
            fwd = FWD_OVERRIDE.get(gid) or FWD[toks[1]]
            s.rel = (lambda v, fwd=fwd:
                     math.degrees(math.atan2(fwd[0]*v[1]-fwd[1]*v[0],
                                             fwd[0]*v[0]+fwd[1]*v[1])))
            return
        ms = int(toks[3][:-2]) if toks[3].endswith("ms") else None
        la = int(toks[1].rstrip("\u00b0"))
        rest = toks[4:] if ms is not None else toks[3:]
        outcome = rest[0] if rest else "-"
        fired = outcome != "-" and not outcome.endswith(".")
        ref = s.n
        if fired and ms is not None:
            ref = 1
            while ref < s.n and t[ref+1] <= ms + 0.01: ref += 1
        anchor = p[ref]
        s.rel = (lambda v, anchor=anchor, la=la:
                 la if abs(anchor[0])+abs(anchor[1]) < 1e-6 else la + ad(anchor, v))

    def triggers(s, W, vmin, c):
        t, p = s.t, s.p
        for j in range(1, s.n + 1):
            i = j
            while i > 0 and t[j]-t[i-1] < W: i -= 1
            if i > 0: i -= 1
            span = t[j]-t[i]
            if span < W: continue
            v = (p[j][0]-p[i][0], p[j][1]-p[i][1])
            if math.hypot(*v)/(span/1000.0) < vmin: continue
            if abs(s.rel(v)) >= 30 + c:
                return True
        return False

CASES = [Case(g) for g in sorted(trajs) if g in cases]
INT = [x.intended for x in CASES]
GRID = [(W, vm, c) for W in (40, 60, 80, 100, 120, 140, 160)
        for vm in range(100, 701, 50) for c in (0, 5, 10, 15, 20, 25)]
M = [[x.triggers(*g) for g in GRID] for x in CASES]

def err(k, ex=None):
    fp = fn = 0
    for i in range(len(CASES)):
        if i == ex: continue
        if M[i][k] and not INT[i]: fp += 1
        if not M[i][k] and INT[i]: fn += 1
    return fp, fn

res = [(sum(err(k)), *err(k), k) for k in range(len(GRID))]
b = min(r[0] for r in res)
sel = [r for r in res if r[0] == b]
print(f"BEST: {b} errors on {len(sel)}/{len(GRID)} configs")
for want in sorted(set((r[1], r[2]) for r in sel)):
    grp = [GRID[r[3]] for r in sel if (r[1], r[2]) == want]
    dims = list(zip(*grp))
    k = GRID.index(grp[len(grp)//2])
    miss = [CASES[i].gid + ("(FP)" if M[i][k] else "(FN)")
            for i in range(len(CASES)) if M[i][k] != INT[i]]
    print(f"  FP={want[0]} FN={want[1]}: {len(grp)} configs, "
          f"W={sorted(set(dims[0]))} vmin={sorted(set(dims[1]))} C={sorted(set(dims[2]))}")
    print(f"    example {grp[len(grp)//2]} misses {miss}")
loo = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
for i in range(len(CASES)):
    sc = [(sum(err(k, ex=i)), k) for k in range(len(GRID))]
    mn = min(x for x, _ in sc)
    cks = [k for x, k in sc if x == mn]
    k = cks[len(cks)//2]
    a = M[i][k]
    loo[("TP" if INT[i] else "FP") if a else ("FN" if INT[i] else "TN")] += 1
print(f"LEAVE-ONE-OUT: TP={loo['TP']} FP={loo['FP']} TN={loo['TN']} FN={loo['FN']}")

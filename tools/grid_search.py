#!/usr/bin/env python3
"""Search the user-specified v4 structure: NO fresh-start gate, lift
discount 0.8, optimize only the speed gate (lo,hi) and the distance
half-point K. Reports frontier, plateau, and leave-one-out."""
import csv, math, re, os
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRE, CANCEL, WIN, EF_LIFT = 0.30, 0.25, 80.0, 0.8
BIRTH = {"g006": "elbow", "g008": "stop", "g009": "stop", "g010": "stop",
         "g011": "stop", "g012": "down", "g013": "down", "g014": "elbow",
         "g015": "stop", "g019": "stop", "g020": "elbow", "g021": "elbow",
         "g022": "stop", "g023": "stop", "g024": "elbow", "g025": "stop",
         "g026": "stop", "g027": "elbow", "g028": "stop", "g029": "stop",
         "g030": "elbow", "g031": "down", "g032": "down", "g033": "elbow",
         "g034": "down", "g035": "down", "g036": "elbow"}
def parse_line(dl):
    toks = dl.split(); ms = None; rest = toks[3:]
    if rest and rest[0].endswith("ms"): ms = int(rest[0][:-2]); rest = rest[1:]
    return toks[0], int(toks[1].rstrip("\u00b0")), ms, (rest[0] if rest else "-")
def aS(a):
    a = abs(a); d = a - 150.0 if a > 150.0 else min(a - 30.0, 150.0 - a)
    return max(0.0, min(1.0, d / 30.0))
def ad(a, b): return math.degrees(math.atan2(a[0]*b[1]-a[1]*b[0], a[0]*b[0]+a[1]*b[1]))
rows_, trajs = {}, {}
with open(os.path.join(root, "docs", "gesture-cases.csv")) as f:
    for r in csv.DictReader(f): rows_[r["id"]] = r
with open(os.path.join(root, "docs", "gesture-trajectories.txt")) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"): continue
        gid, rest = line.split(":", 1)
        pts = re.findall(r"\(([-\d.]+),([-\d.]+),([-\d.]+)\)", rest)
        trajs[gid.strip()] = [(float(a), float(b), float(c)) for a, b, c in pts]
class C:
    def __init__(s, gid):
        r = rows_[gid]; s.gid = gid
        reason, log_angle, ms, outcome = parse_line(r["debug_line"])
        s.intended = r["intended_gesture"].startswith(("yes", "true"))
        t, p = [0.0], [(0.0, 0.0)]
        for dt, dx, dy in trajs[gid]:
            t.append(t[-1]+dt); p.append((p[-1][0]+dx, p[-1][1]+dy))
        n = len(trajs[gid])
        fired = outcome != "-" and not outcome.endswith(".")
        if fired and ms is not None:
            j = 0
            while j < n and t[j+1] <= ms + 0.01: j += 1
            anchor = p[j]
        else: anchor = p[-1]
        rel = lambda v: log_angle if abs(anchor[0])+abs(anchor[1]) < 1e-6 else log_angle + ad(anchor, v)
        s.ev = []; best = 0.0
        for j in range(1, n+1):
            i = j
            while i > 0 and t[j]-t[i-1] < WIN: i -= 1
            span = t[j]-t[i]
            if span > 0:
                best = max(best, math.hypot(p[j][0]-p[i][0], p[j][1]-p[i][1])/(span/1000.0))
            v = p[j]; a = rel(v); ln = math.hypot(*v)
            s.ev.append((0.0 if (abs(a) < 30 or ln < 2) else aS(a), ln, best))
        vf = p[-1]; af = rel(vf); s.lnf = math.hypot(*vf)
        s.aSf = 0.0 if (abs(af) < 30 or s.lnf < 2) else aS(af)
        s.ef = EF_LIFT if reason == "lift" and BIRTH[gid] != "down" else 1.0
        s.peakf = s.ev[-1][2]
    def applied(s, slo, shi, K):
        sf = lambda pk: max(0.0, min(1.0, (pk-slo)/(shi-slo)))
        lS = lambda l: l/(l+K)
        fired = any(a*lS(l)*sf(pk) >= FIRE for a, l, pk in s.ev)
        full = s.aSf*lS(s.lnf)*s.ef*sf(s.peakf)
        return full >= FIRE if not fired else full >= CANCEL
CASES = [C(g) for g in sorted(BIRTH) if g in trajs]
INT = [c.intended for c in CASES]
GRID = [(slo, slo+w, K)
        for slo in range(0, 401, 25)
        for w in range(50, 701, 50)
        for K in (8, 12, 16, 20, 24, 28, 32, 36, 40, 48)]
M = [[c.applied(*g) for g in GRID] for c in CASES]
def err(k, ex=None):
    fp = fn = 0
    for i in range(len(CASES)):
        if i == ex: continue
        if M[i][k] and not INT[i]: fp += 1
        if not M[i][k] and INT[i]: fn += 1
    return fp, fn
res = [(sum(err(k)), *err(k), k) for k in range(len(GRID))]
bt = min(r[0] for r in res)
for maxfp in (3, 2, 1, 0):
    pool = [r for r in res if r[1] <= maxfp]
    if not pool:
        print(f"FP<={maxfp}: unreachable"); continue
    b = min(r[0] for r in pool)
    sel = [r for r in pool if r[0] == b]
    cfgs = [GRID[r[3]] for r in sel]
    dims = list(zip(*cfgs))
    print(f"FP<={maxfp}: errors={b} (FP={sel[0][1]},FN={sel[0][2]}) on {len(sel)}/{len(GRID)} configs")
    for nm, vals in zip(("speedLo", "speedHi", "K"), dims):
        vv = sorted(set(vals))
        print(f"   {nm}: {vv if len(vv) < 12 else [vv[0], '...', vv[-1]]}")
    mid = cfgs[len(cfgs)//2]; k = GRID.index(mid)
    miss = [CASES[i].gid + ("(FP)" if M[i][k] else "(FN)")
            for i in range(len(CASES)) if M[i][k] != INT[i]]
    print(f"   example {mid} misses: {miss}")
loo = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
for i in range(len(CASES)):
    sc = [(sum(err(k, ex=i)), k) for k in range(len(GRID))]
    mn = min(x for x, _ in sc)
    cks = [k for x, k in sc if x == mn]
    k = cks[len(cks)//2]
    a = M[i][k]
    loo[("TP" if INT[i] else "FP") if a else ("FN" if INT[i] else "TN")] += 1
print(f"LEAVE-ONE-OUT: TP={loo['TP']} FP={loo['FP']} TN={loo['TN']} FN={loo['FN']}")
# fixed sanity rows the user asked about
for cfg in [(150, 450, 8), (100, 250, 20), (150, 300, 24)]:
    if cfg in GRID:
        k = GRID.index(cfg)
        fp, fn = err(k)
        miss = [CASES[i].gid for i in range(len(CASES)) if M[i][k] != INT[i]]
        print(f"config {cfg}: FP={fp} FN={fn} misses={miss}")

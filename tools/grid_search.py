#!/usr/bin/env python3
"""Exhaustive parameter search over the v2 gate structure (no new
mechanisms): speed gate (lo,hi), launch gate (lo,hi), opening window.
Reports the error frontier, the robustness (plateau size) of each optimum,
and leave-one-out cross-validation as an honest generalization estimate."""
import csv, math, re, os, itertools

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRE, CANCEL, WIN = 0.30, 0.25, 80.0
BIRTH = {"g006": "elbow", "g008": "stop", "g009": "stop", "g010": "stop",
         "g011": "stop", "g012": "down", "g013": "down", "g014": "elbow",
         "g015": "stop", "g019": "stop", "g020": "elbow", "g021": "elbow",
         "g022": "stop", "g023": "stop", "g024": "elbow", "g025": "stop",
         "g026": "stop", "g027": "elbow", "g028": "stop", "g029": "stop",
         "g030": "elbow", "g031": "down", "g032": "down", "g033": "elbow",
         "g034": "down", "g035": "down", "g036": "elbow"}

def parse_line(dl):
    toks = dl.split(); reason = toks[0]
    angle = int(toks[1].rstrip("\u00b0")); length = int(toks[2].rstrip("dp"))
    ms = None; rest = toks[3:]
    if rest and rest[0].endswith("ms"): ms = int(rest[0][:-2]); rest = rest[1:]
    outcome = rest[0] if rest else "-"
    return reason, angle, length, ms, outcome

def angle_score(a):
    a = abs(a)
    d = a - 150.0 if a > 150.0 else min(a - 30.0, 150.0 - a)
    return max(0.0, min(1.0, d / 30.0))

def length_score(l): return l / (l + 8.0)
def clamp01(x): return max(0.0, min(1.0, x))

def ang_diff(a, b):
    return math.degrees(math.atan2(a[0]*b[1]-a[1]*b[0], a[0]*b[0]+a[1]*b[1]))

cases_rows, trajs = {}, {}
with open(os.path.join(root, "docs", "gesture-cases.csv")) as f:
    for row in csv.DictReader(f): cases_rows[row["id"]] = row
with open(os.path.join(root, "docs", "gesture-trajectories.txt")) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"): continue
        gid, rest = line.split(":", 1)
        pts = re.findall(r"\(([-\d.]+),([-\d.]+),([-\d.]+)\)", rest)
        trajs[gid.strip()] = [(float(a), float(b), float(c)) for a, b, c in pts]

class Case:
    def __init__(self, gid):
        row = cases_rows[gid]
        self.gid = gid
        self.reason, log_angle, _, ms, outcome = parse_line(row["debug_line"])
        self.intended = row["intended_gesture"].startswith(("yes", "true"))
        self.birth = BIRTH[gid]
        t, p = [0.0], [(0.0, 0.0)]
        for dt, dx, dy in trajs[gid]:
            t.append(t[-1] + dt); p.append((p[-1][0]+dx, p[-1][1]+dy))
        self.t, self.p, self.n = t, p, len(trajs[gid])
        fired_logged = outcome != "-" and not outcome.endswith(".")
        if fired_logged and ms is not None:
            j = 0
            while j < self.n and t[j+1] <= ms + 0.01: j += 1
            anchor = p[j]
        else:
            anchor = p[-1]
        def rel(v):
            if abs(anchor[0]) + abs(anchor[1]) < 1e-6: return log_angle
            return log_angle + ang_diff(anchor, v)
        # precompute per-event features (parameter independent)
        self.base = []      # angle*length at each event, 0 if unclassifiable
        self.peak = []      # windowed peak-so-far at each event
        best = 0.0
        for j in range(1, self.n + 1):
            i = j
            while i > 0 and t[j] - t[i-1] < WIN: i -= 1
            span = t[j] - t[i]
            if span > 0:
                d = math.hypot(p[j][0]-p[i][0], p[j][1]-p[i][1])
                best = max(best, d / (span/1000.0))
            self.peak.append(best)
            v = p[j]; a = rel(v); ln = math.hypot(*v)
            self.base.append(0.0 if (abs(a) < 30 or ln < 2)
                             else angle_score(a) * length_score(ln))
        vf = p[-1]; af = rel(vf); lnf = math.hypot(*vf)
        self.final_base = (0.0 if (abs(af) < 30 or lnf < 2)
                           else angle_score(af) * length_score(lnf))
        self.ef = 0.6 if self.reason == "lift" and self.birth != "down" else 1.0
        self.opening = {}
        for oms in (15, 50):
            j = 1
            while j < self.n and t[j] < oms: j += 1
            self.opening[oms] = math.hypot(*p[j]) / (t[j]/1000.0) if t[j] > 0 else 0.0

    def applied(self, slo, shi, llo, lhi, oms):
        def sf(pk): return clamp01((pk - slo) / (shi - slo))
        lf = 1.0
        if self.birth != "down":
            lf = clamp01((lhi - self.opening[oms]) / (lhi - llo))
        fired = False
        for j in range(self.n):
            if self.base[j] * sf(self.peak[j]) * lf >= FIRE:
                fired = True; break
        full = self.final_base * self.ef * sf(self.peak[-1]) * lf
        if not fired: return full >= FIRE
        return full >= CANCEL

CASES = [Case(g) for g in sorted(BIRTH) if g in trajs]
GRID = [(slo, shi, llo, lhi, oms)
        for slo in range(0, 401, 50)
        for shi in range(1400, 99, -100) if shi > slo
        for llo in range(0, 501, 100)
        for lhi in range(1500, 99, -100) if lhi > llo
        for oms in (15, 50)]
# outcome matrix
M = [[c.applied(*g) for g in GRID] for c in CASES]
INT = [c.intended for c in CASES]

def errors(config_idx, exclude=None):
    fp = fn = 0
    for i, c in enumerate(CASES):
        if i == exclude: continue
        a = M[i][config_idx]
        if a and not INT[i]: fp += 1
        if not a and INT[i]: fn += 1
    return fp, fn

# ---- global frontier ----
results = [(sum(errors(k)), *errors(k), k) for k in range(len(GRID))]
best_total = min(r[0] for r in results)
best = [r for r in results if r[0] == best_total]
fp0 = [r for r in results if r[1] == 0]
best_fp0_total = min(r[0] for r in fp0)
best_fp0 = [r for r in fp0 if r[0] == best_fp0_total]

def describe(rs, name):
    ks = [r[3] for r in rs]
    cfgs = [GRID[k] for k in ks]
    dims = list(zip(*cfgs))
    print(f"{name}: errors={rs[0][0]} (FP={rs[0][1]} FN={rs[0][2]}), "
          f"{len(ks)} configs / {len(GRID)} total")
    for nm, vals in zip(("speedLo", "speedHi", "launchLo", "launchHi", "open"), dims):
        print(f"   {nm}: {sorted(set(vals))}")
    mid = cfgs[len(cfgs)//2]
    k = GRID.index(mid)
    misses = [CASES[i].gid for i in range(len(CASES))
              if (M[i][k] and not INT[i]) or (not M[i][k] and INT[i])]
    print(f"   example {mid} misses: {misses}")

describe(best, "GLOBAL BEST (min FP+FN)")
describe(best_fp0, "BEST WITH FP=0")

# ---- v2 params for reference ----
kv2 = GRID.index((150, 450, 100, 400, 50)) if (150,450,100,400,50) in GRID else None
for cand in [(150, 450, 100, 400, 50), (150, 450, 200, 400, 50)]:
    if cand in GRID:
        k = GRID.index(cand)
        fp, fn = errors(k)
        print(f"shipped-v2-nearest {cand}: FP={fp} FN={fn}")

# ---- leave-one-out ----
loo = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
for i, c in enumerate(CASES):
    scored = [(sum(errors(k, exclude=i)), k) for k in range(len(GRID))]
    mn = min(s for s, _ in scored)
    cand_ks = [k for s, k in scored if s == mn]
    k = cand_ks[len(cand_ks)//2]
    a = M[i][k]
    key = ("TP" if INT[i] else "FP") if a else ("FN" if INT[i] else "TN")
    loo[key] += 1
print(f"LEAVE-ONE-OUT (honest generalization): TP={loo['TP']} FP={loo['FP']} "
      f"TN={loo['TN']} FN={loo['FN']}")

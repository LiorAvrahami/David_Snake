#!/usr/bin/env python3
"""Replays every labeled trajectory case through (a) the original pre-gate
scoring and (b) the SHIPPED v4 scoring (speed gate 150-350 dp/s, distance
half-point 24dp, lift-successor discount 0.8, no fresh-start gate, final
verdict on the recomputed angle), and prints confusion tables."""
import csv, math, re, os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRE, CANCEL, WIN = 0.30, 0.25, 80.0
SPEED_LO, SPEED_HI = 110.0, 380.0
K_OLD, K_NEW = 8.0, 16.0
EF_OLD, EF_NEW = 0.6, 0.8

BIRTH = {"g006": "elbow", "g008": "stop", "g009": "stop", "g010": "stop",
         "g011": "stop", "g012": "down", "g013": "down", "g014": "elbow",
         "g015": "stop", "g019": "stop", "g020": "elbow", "g021": "elbow",
         "g022": "stop", "g023": "stop", "g024": "elbow", "g025": "stop",
         "g026": "stop", "g027": "elbow", "g028": "stop", "g029": "stop",
         "g030": "elbow", "g031": "down", "g032": "down", "g033": "elbow",
         "g034": "down", "g035": "down", "g036": "elbow",
         "g037": "stop", "g038": "elbow", "g039": "stop"}

def parse_line(dl):
    toks = dl.split()
    reason = toks[0]
    angle = int(toks[1].rstrip("\u00b0"))
    ms = None
    rest = toks[3:]
    if rest and rest[0].endswith("ms"):
        ms = int(rest[0][:-2]); rest = rest[1:]
    outcome = rest[0] if rest else "-"
    canceled = any("cancel" in t for t in rest[2:])
    return reason, angle, ms, outcome, canceled

def angle_score(a):
    a = abs(a)
    d = a - 150.0 if a > 150.0 else min(a - 30.0, 150.0 - a)
    return max(0.0, min(1.0, d / 30.0))

def ang_diff(a, b):
    return math.degrees(math.atan2(a[0]*b[1]-a[1]*b[0], a[0]*b[0]+a[1]*b[1]))

def load():
    cases, trajs = {}, {}
    with open(os.path.join(root, "docs", "gesture-cases.csv")) as f:
        for row in csv.DictReader(f):
            cases[row["id"]] = row
    with open(os.path.join(root, "docs", "gesture-trajectories.txt")) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            gid, rest = line.split(":", 1)
            pts = re.findall(r"\(([-\d.]+),([-\d.]+),([-\d.]+)\)", rest)
            trajs[gid.strip()] = [(float(a), float(b), float(c)) for a, b, c in pts]
    return cases, trajs

NEW_ANGLE_FROM = "g040"  # logged angles are peak-segment angles from here
STEP_MS = 180.0          # a fired rotation commits once a step lands

class Replay:
    def __init__(self, gid, row, traj):
        self.gid = gid
        self.reason, log_angle, ms, outcome, self.log_cancel = parse_line(row["debug_line"])
        self.intended = row["intended_gesture"].startswith(("yes", "true"))
        self.fired_rec = outcome != "-" and not outcome.endswith(".")
        t, p = [0.0], [(0.0, 0.0)]
        for dt, dx, dy in traj:
            t.append(t[-1] + dt); p.append((p[-1][0]+dx, p[-1][1]+dy))
        self.n = len(traj)
        # running-max window (speed and its vector) at each event
        wins = []
        best, bv = 0.0, (0.0, 0.0)
        for j in range(1, self.n + 1):
            i = j
            while i > 0 and t[j] - t[i-1] < WIN: i -= 1
            span = t[j] - t[i]
            if span > 0:
                v = (p[j][0]-p[i][0], p[j][1]-p[i][1])
                d = math.hypot(*v)
                if d / (span/1000.0) > best:
                    best, bv = d / (span/1000.0), v
            wins.append((best, bv))
        ref_j = self.n
        if self.fired_rec and ms is not None:
            ref_j = 1
            while ref_j < self.n and t[ref_j+1] <= ms + 0.01: ref_j += 1
        anchor = (wins[ref_j-1][1] if gid >= NEW_ANGLE_FROM else p[ref_j])
        def rel(v):
            if abs(anchor[0]) + abs(anchor[1]) < 1e-6:
                return log_angle
            return log_angle + ang_diff(anchor, v)
        self.t = t
        self.ev = []       # candidate (peak-segment) view per event
        self.ev_old = []   # pre-gate (anchor-vector) view per event
        for j in range(1, self.n + 1):
            pk, vpk = wins[j-1]
            ln = math.hypot(*p[j])
            a = rel(vpk)
            self.ev.append((0.0 if (abs(a) < 30 or ln < 2 or pk <= 0)
                            else angle_score(a), ln, pk))
            ao = rel(p[j])
            self.ev_old.append((0.0 if (abs(ao) < 30 or ln < 2)
                                else angle_score(ao), ln, pk))
        self.lnf = math.hypot(*p[-1])
        pkf, vpkf = wins[-1]
        af = rel(vpkf)
        self.aSf = 0.0 if (abs(af) < 30 or self.lnf < 2 or pkf <= 0) else angle_score(af)
        afo = rel(p[-1])
        self.aSf_old = 0.0 if (abs(afo) < 30 or self.lnf < 2) else angle_score(afo)
        self.peakf = pkf
        self.successor = BIRTH[gid] != "down"

    def simulate(self, new):
        K = K_NEW if new else K_OLD
        ef = ((EF_NEW if new else EF_OLD)
              if self.reason == "lift" and self.successor else 1.0)
        def sf(pk):
            if not new: return 1.0
            return max(0.0, min(1.0, (pk - SPEED_LO) / (SPEED_HI - SPEED_LO)))
        lS = lambda l: l / (l + K)
        ev = self.ev if new else self.ev_old
        aSf = self.aSf if new else self.aSf_old
        fire_ms = None
        for j, (a, ln, pk) in enumerate(ev):
            if a * lS(ln) * sf(pk) >= FIRE:
                fire_ms = self.t[j + 1]
                break
        full = aSf * lS(self.lnf) * ef * sf(self.peakf)
        if fire_ms is None:
            return (full >= FIRE, None, full)
        if self.t[-1] - fire_ms >= STEP_MS:
            return (True, fire_ms, full)
        return (full >= CANCEL, fire_ms, full)

cases, trajs = load()
rows = []
print(f"{'id':5} {'intended':8} {'peak':>5} | {'rec.':>5} {'pre-gate':>12} {'shipped v4':>16}")
for gid in sorted(BIRTH):
    if gid not in trajs: continue
    r = Replay(gid, cases[gid], trajs[gid])
    applied_rec = r.fired_rec and not r.log_cancel
    b_app, _, b_s = r.simulate(False)
    a_app, a_ms, a_s = r.simulate(True)
    rows.append((r.intended, applied_rec, b_app, a_app))
    print(f"{gid:5} {str(r.intended):8} {r.peakf:5.0f} | {str(applied_rec):>5} "
          f"{str(b_app):>5} s={b_s:.2f} {str(a_app):>5} @{str(a_ms):>6} s={a_s:.2f}")

def table(idx):
    c = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    for r in rows:
        a, i = r[idx], r[0]
        c[("TP" if i else "FP") if a else ("FN" if i else "TN")] += 1
    return c

print()
for name, idx in [("RECORDED (mixed builds)", 1), ("PRE-GATE (sim)", 2),
                  ("SHIPPED v4 (sim)", 3)]:
    c = table(idx)
    print(f"{name}: TP={c['TP']} FP={c['FP']} TN={c['TN']} FN={c['FN']}")

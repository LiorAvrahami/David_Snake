#!/usr/bin/env python3
"""Offline evaluation of the candidate gesture scoring against all labeled
cases with trajectories. Replays each case through the CURRENT scoring
(validated against recorded outcomes) and the CANDIDATE scoring, and prints
before/after confusion tables.

Candidate = current score x speedScore(windowed peak) x launchScore(opening
speed, elbow-born only), plus the final-verdict angle recomputed from the
full vector instead of the fire-time snapshot."""
import csv, math, re, os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- parameters -----------------------------------------------------------
FIRE, CANCEL = 0.30, 0.25
WIN = 80.0            # ms window for peak speed
SPEED_LO, SPEED_HI = 150.0, 450.0    # dp/s: 0 below LO, 1 above HI
LAUNCH_LO, LAUNCH_HI = 150.0, 400.0  # dp/s opening speed of elbow-born
OPEN_MS = 50.0        # opening-speed measurement span

# birth of each gesture (from the session logs); launch factor is only
# meaningful for elbow births (momentum flows through an elbow vertex)
BIRTH = {"g006": "elbow", "g008": "stop", "g009": "stop", "g010": "stop",
         "g011": "stop", "g012": "down", "g013": "down", "g014": "elbow",
         "g015": "stop", "g019": "stop", "g020": "elbow", "g021": "elbow",
         "g022": "stop", "g023": "stop", "g024": "elbow",
         "g025": "stop", "g026": "stop", "g027": "elbow",
         "g028": "stop", "g029": "stop", "g030": "elbow"}

def parse_line(dl):
    toks = dl.split()
    reason = toks[0]
    angle = int(toks[1].rstrip("\u00b0"))
    length = int(toks[2].rstrip("dp"))
    ms = None
    rest = toks[3:]
    if rest and rest[0].endswith("ms"):
        ms = int(rest[0][:-2]); rest = rest[1:]
    outcome = rest[0] if rest else "-"
    score = float(rest[1]) if len(rest) > 1 else None
    canceled = any("cancel" in t for t in rest[2:])
    return reason, angle, length, ms, outcome, score, canceled

def angle_score(a):
    a = abs(a)
    d = a - 150.0 if a > 150.0 else min(a - 30.0, 150.0 - a)
    return max(0.0, min(1.0, d / 30.0))

def length_score(l): return l / (l + 8.0)

def clamp01(x): return max(0.0, min(1.0, x))

def speed_score(peak): return clamp01((peak - SPEED_LO) / (SPEED_HI - SPEED_LO))

def ang_diff(a, b):
    """signed degrees from vector a to vector b (screen coords, same
    handedness as the app's fx*dy - fy*dx convention)"""
    cross = a[0] * b[1] - a[1] * b[0]
    dot = a[0] * b[0] + a[1] * b[1]
    return math.degrees(math.atan2(cross, dot))

def load():
    cases = {}
    with open(os.path.join(root, "docs", "gesture-cases.csv")) as f:
        for row in csv.DictReader(f):
            cases[row["id"]] = row
    trajs = {}
    with open(os.path.join(root, "docs", "gesture-trajectories.txt")) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            gid, rest = line.split(":", 1)
            pts = re.findall(r"\(([-\d.]+),([-\d.]+),([-\d.]+)\)", rest)
            trajs[gid.strip()] = [(float(a), float(b), float(c)) for a, b, c in pts]
    return cases, trajs

class Replay:
    def __init__(self, gid, row, traj):
        self.gid = gid
        (self.reason, self.log_angle, self.log_len, self.ms, self.outcome,
         self.log_score, self.log_cancel) = parse_line(row["debug_line"])
        self.intended = row["intended_gesture"].startswith("yes")
        self.birth = BIRTH[gid]
        # cumulative time and position after each event
        self.t, self.p = [0.0], [(0.0, 0.0)]
        for dt, dx, dy in traj:
            self.t.append(self.t[-1] + dt)
            self.p.append((self.p[-1][0] + dx, self.p[-1][1] + dy))
        self.n = len(traj)
        # anchor the relative-angle frame: the logged angle belongs to the
        # vector at fire time (fired) or the final vector (unfired)
        fired_logged = self.outcome not in ("-",) and not self.outcome.endswith(".")
        if fired_logged and self.ms is not None:
            self.anchor_vec = self.vec_at_time(self.ms)
        else:
            self.anchor_vec = self.p[-1]

    def vec_at_time(self, ms):
        j = 0
        while j < self.n and self.t[j + 1] <= ms + 0.01: j += 1
        return self.p[j]

    def rel_angle(self, v):
        if abs(self.anchor_vec[0]) + abs(self.anchor_vec[1]) < 1e-6:
            return self.log_angle
        return self.log_angle + ang_diff(self.anchor_vec, v)

    def peak_speed_upto(self, j):
        """max over windows ending at any event <= j of window displacement
        over window span (span grown backwards to at least WIN ms)"""
        best = 0.0
        for e in range(1, j + 1):
            i = e
            while i > 0 and self.t[e] - self.t[i - 1] < WIN: i -= 1
            span = self.t[e] - self.t[i]
            if span <= 0: continue
            d = math.hypot(self.p[e][0] - self.p[i][0], self.p[e][1] - self.p[i][1])
            best = max(best, d / (span / 1000.0))
        return best

    def opening_speed(self):
        j = 1
        while j < self.n and self.t[j] < OPEN_MS: j += 1
        span = self.t[j]
        if span <= 0: return 0.0
        return math.hypot(*self.p[j]) / (span / 1000.0)

    def end_factor(self, candidate):
        return 0.6 if self.reason == "lift" and self.birth != "down" else 1.0

    def launch_factor(self):
        if self.birth == "down": return 1.0
        return clamp01((LAUNCH_HI - self.opening_speed()) / (LAUNCH_HI - LAUNCH_LO))

    def simulate(self, candidate):
        """returns (applied, fire_ms, final_score)"""
        lf = self.launch_factor() if candidate else 1.0
        fire_j = None
        for j in range(1, self.n + 1):
            v = self.p[j]
            a = self.rel_angle(v)
            if abs(a) < 30 or math.hypot(*v) < 2: continue
            s = angle_score(a) * length_score(math.hypot(*v))
            if candidate:
                s *= speed_score(self.peak_speed_upto(j)) * lf
            if s >= FIRE:
                fire_j = j
                fire_a = a
                break
        # ending verdict
        vf = self.p[-1]
        lenf = math.hypot(*vf)
        ef = self.end_factor(candidate)
        peakf = self.peak_speed_upto(self.n) if candidate else None
        if fire_j is None:
            af = self.rel_angle(vf)
            if abs(af) < 30 or lenf < 2: return (False, None, 0.0)
            s = angle_score(af) * length_score(lenf) * ef
            if candidate: s *= speed_score(peakf) * lf
            return (s >= FIRE, self.t[-1], s)
        # fired: final check
        if candidate:
            af = self.rel_angle(vf)          # the bug fix: fresh angle
            s = angle_score(af) * length_score(lenf) * ef * speed_score(peakf) * lf
        else:
            s = angle_score(fire_a) * length_score(lenf) * ef
        return (s >= CANCEL, self.t[fire_j], s)

def label(applied, intended):
    if applied and intended: return "TP"
    if applied and not intended: return "FP"
    if not applied and intended: return "FN"
    return "TN"

cases, trajs = load()
ids = [g for g in cases if g in trajs and g in BIRTH]
print(f"{'id':5} {'intended':8} {'peak':>5} {'open':>5} | "
      f"{'rec.':>4} {'sim-before':>16} {'sim-after':>16}")
rows = []
for gid in sorted(ids):
    r = Replay(gid, cases[gid], trajs[gid])
    fired_rec = r.outcome not in ("-",) and not r.outcome.endswith(".")
    applied_rec = fired_rec and not r.log_cancel
    b_app, b_ms, b_s = r.simulate(False)
    a_app, a_ms, a_s = r.simulate(True)
    rows.append((gid, r.intended, applied_rec, b_app, a_app, a_ms))
    print(f"{gid:5} {str(r.intended):8} {r.peak_speed_upto(r.n):5.0f} "
          f"{r.opening_speed():5.0f} | {str(applied_rec):>4} "
          f"{str(b_app):>5} @{str(b_ms):>5} s={b_s:.2f} "
          f"{str(a_app):>5} @{str(a_ms):>5} s={a_s:.2f}")

def table(which):
    c = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    for gid, intended, rec, b, a, _ in rows:
        c[label({"rec": rec, "b": b, "a": a}[which], intended)] += 1
    return c

print()
for name, which in [("BEFORE (recorded)", "rec"), ("BEFORE (simulated)", "b"),
                    ("AFTER (candidate)", "a")]:
    c = table(which)
    print(f"{name}: TP={c['TP']} FP={c['FP']} TN={c['TN']} FN={c['FN']}")

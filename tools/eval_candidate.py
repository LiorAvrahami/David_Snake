#!/usr/bin/env python3
"""Replays every labeled trajectory through the anchored-displacement
recognizer (shipped until the input A/B test; tools/recognizers.py has the
current ones) (absolute screen directions; a command fires once the finger is
SWIPE_DP from the anchor along an axis that dominates by AXIS_RATIO; the
anchor then jumps to the finger; a stroke never repeats its last command)
and, for comparison, through the previous 120ms sliding-window controller.
Prints per-case triggers, both confusion tables and first-fire latency.

Commands that equal the current heading are no-ops (as in the engine);
reversals count as commands (the engine allows them without a tail).
Legacy (gesture-era) lines carry an angle relative to the heading; the
heading is recovered from it and snapped to the nearest axis. Stroke-era
lines carry the heading letter at stroke start.

JOINED lists strokes that were logged as several legacy gestures but were
one continuous finger movement: replaying them joined exposes follow-through
fires that per-gesture replay hides. They are reported, not scored.

Run from anywhere: python3 tools/eval_candidate.py"""
import csv, math, re, os, statistics

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWIPE_DP, AXIS_RATIO = 14.0, 1.5          # anchored model
WIN, VMIN, TURN = 120.0, 250.0, 30.0      # previous sliding-window model
FWD = {"R": (1, 0), "L": (-1, 0), "D": (0, 1), "U": (0, -1)}
FWD_OVERRIDE = {"g040": (0, 1)}  # heading known from session context
JOINED = {"g019+g020": (["g019", "g020"], "L", ["yes"])}

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

class Stroke:
    def __init__(s, gid, pts, fwd, labels):
        t, p = [0.0], [(0.0, 0.0)]
        for dt, dx, dy in pts:
            t.append(t[-1]+dt); p.append((p[-1][0]+dx, p[-1][1]+dy))
        s.gid, s.t, s.p, s.n = gid, t, p, len(pts)
        s.fwd, s.labels = fwd, labels

def legacy_heading(row, pts):
    """Heading of a gesture-era line: its logged angle is relative to the
    heading and describes the net vector up to the fire (or the end)."""
    toks = row["debug_line"].split()
    ms = int(toks[3][:-2]) if toks[3].endswith("ms") else None
    la = int(toks[1].rstrip("°"))
    rest = toks[4:] if ms is not None else toks[3:]
    outcome = rest[0] if rest else "-"
    t, x, y, n = 0.0, 0.0, 0.0, 0
    for dt, dx, dy in pts:
        if outcome != "-" and ms is not None and n > 0 and t + dt > ms + 0.01:
            break
        t += dt; x += dx; y += dy; n += 1
    k = round((math.degrees(math.atan2(y, x)) - la) / 90) % 4
    return [(1, 0), (0, 1), (-1, 0), (0, -1)][k]

def load():
    out = []
    for gid in sorted(trajs):
        if gid not in cases: continue
        row = cases[gid]
        labels = [x.strip() for x in row["intended_gesture"].split(",")]
        toks = row["debug_line"].split()
        if gid in FWD_OVERRIDE: fwd = FWD_OVERRIDE[gid]
        elif toks[0] == "stroke": fwd = FWD[toks[1]]
        else:
            fwd = legacy_heading(row, trajs[gid])
            labels = labels[:1]
        out.append(Stroke(gid, trajs[gid], fwd, labels))
    return out

def joined():
    return [Stroke(k, sum((trajs[g] for g in gs), []), FWD[h], labs)
            for k, (gs, h, labs) in JOINED.items()]

def anchored(s, swipe=SWIPE_DP, ratio=AXIS_RATIO):
    """Fire times (ms) of commands that change the heading."""
    fwd, anchor, last, out = s.fwd, s.p[0], None, []
    for j in range(1, s.n + 1):
        d = (s.p[j][0]-anchor[0], s.p[j][1]-anchor[1])
        ax, ay = abs(d[0]), abs(d[1])
        if max(ax, ay) < swipe: continue
        if ax >= ratio*ay: dv = (1 if d[0] > 0 else -1, 0)
        elif ay >= ratio*ax: dv = (0, 1 if d[1] > 0 else -1)
        else: continue
        anchor = s.p[j]
        if dv == last: continue
        last = dv
        if dv == fwd: continue
        fwd = dv; out.append(s.t[j])
    return out

def window(s):
    """The previous controller: every move event re-checks the last WIN ms;
    fast enough and >= TURN degrees off the heading is a turn."""
    t, p, fwd, out = s.t, s.p, s.fwd, []
    for j in range(1, s.n + 1):
        i = j
        while i > 0 and t[j]-t[i-1] < WIN: i -= 1
        if i > 0: i -= 1
        span = t[j]-t[i]
        if span < WIN: continue
        v = (p[j][0]-p[i][0], p[j][1]-p[i][1])
        if math.hypot(*v)/(span/1000.0) < VMIN: continue
        a = ad(fwd, v)
        if abs(a) < TURN: continue
        fwd = ((-fwd[0], -fwd[1]) if abs(a) > 150
               else (-fwd[1], fwd[0]) if a > 0 else (fwd[1], -fwd[0]))
        out.append(t[j])
    return out

def confusion(strokes, model):
    m = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    for s in strokes:
        trig = model(s)
        for k, lab in enumerate(s.labels):
            want = lab.startswith(("yes", "true"))
            got = k < len(trig)
            m[("TP" if want else "FP") if got else ("FN" if want else "TN")] += 1
        m["FP"] += max(0, len(trig) - len(s.labels))
    return m

if __name__ == "__main__":
    S = load()
    for s in S:
        print(f"{s.gid} labels={s.labels} anchored={['%.0f' % x for x in anchored(s)]}"
              f" window={['%.0f' % x for x in window(s)]}")
    for name, model in (("ANCHORED (previous)", anchored), ("WINDOW (older)", window)):
        m = confusion(S, model)
        lat = [model(s)[0] for s in S if s.labels[0].startswith("yes") and model(s)]
        print(f"{name}: TP={m['TP']} FP={m['FP']} TN={m['TN']} FN={m['FN']}"
              f"  median first fire {statistics.median(lat):.0f}ms")
    for s in joined():
        print(f"joined {s.gid} labels={s.labels}"
              f" anchored={['%.0f' % x for x in anchored(s)]}"
              f" window={['%.0f' % x for x in window(s)]}")

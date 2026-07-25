#!/usr/bin/env python3
"""Replays every labeled trajectory through the SHIPPED continuous
sliding-window controller (window 120ms, min average speed 250dp/s,
10-degree clearance past the 30-degree forward cone) and prints the
confusion table. A case counts as applied if any window triggers.

Relative angles: legacy cases (gesture-era lines) anchor the logged angle
to the vector it described; stroke-era lines carry the heading letter at
stroke start (turns during a stroke are approximated away)."""
import csv, math, re, os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN, VMIN, TURN = 120.0, 250.0, 30.0
FWD = {"R": (1.0, 0.0), "L": (-1.0, 0.0), "D": (0.0, 1.0), "U": (0.0, -1.0)}
FWD_OVERRIDE = {"g040": (0.0, 1.0)}  # heading known from session context

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

def rot_cw(v):  return (-v[1], v[0])   # screen coords, y down
def rot_ccw(v): return (v[1], -v[0])

class Replay:
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
        s.labels = [x.strip() for x in row["intended_gesture"].split(",")]
        s.stroke = toks[0] == "stroke"
        if gid in FWD_OVERRIDE or s.stroke:
            s.fwd0 = FWD_OVERRIDE.get(gid) or FWD[toks[1]]
            s.rel = None
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

    def triggers(s, turn=None):
        """All triggers, updating the heading after each (approximation:
        queue timing ignored, a trigger rotates the heading at once)."""
        turn = TURN if turn is None else turn
        t, p = s.t, s.p
        fwd = getattr(s, "fwd0", None)
        out = []
        for j in range(1, s.n + 1):
            i = j
            while i > 0 and t[j]-t[i-1] < WIN: i -= 1
            if i > 0: i -= 1
            span = t[j]-t[i]
            if span < WIN: continue
            v = (p[j][0]-p[i][0], p[j][1]-p[i][1])
            if math.hypot(*v)/(span/1000.0) < VMIN: continue
            if fwd is None:
                a = s.rel(v)
                if abs(a) >= turn:
                    out.append(t[j])
                    break        # legacy frame cannot follow the heading
                continue
            a = math.degrees(math.atan2(fwd[0]*v[1]-fwd[1]*v[0],
                                        fwd[0]*v[0]+fwd[1]*v[1]))
            if abs(a) < turn: continue
            fwd = ((-fwd[0], -fwd[1]) if abs(a) > 150
                   else rot_cw(fwd) if a > 0 else rot_ccw(fwd))
            out.append(t[j])
        return out

m = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
for gid in sorted(trajs):
    if gid not in cases: continue
    r = Replay(gid)
    trig = r.triggers()
    if len(r.labels) > 1 or r.stroke:
        # per-command accounting: labels in order against replayed triggers
        for k, lab in enumerate(r.labels):
            want = lab.startswith(("yes", "true"))
            got = k < len(trig)
            m[("TP" if want else "FP") if got else ("FN" if want else "TN")] += 1
        m["FP"] += max(0, len(trig) - len(r.labels))
        print(f"{gid} labels={r.labels} triggers="
              f"{['%.0fms' % x for x in trig]}")
    else:
        want = r.intended
        got = bool(trig)
        m[("TP" if want else "FP") if got else ("FN" if want else "TN")] += 1
        print(f"{gid} intended={want} trigger="
              f"{'-' if not trig else '%.0fms' % trig[0]}")
print(f"CONTINUOUS (shipped): TP={m['TP']} FP={m['FP']} TN={m['TN']} FN={m['FN']}")

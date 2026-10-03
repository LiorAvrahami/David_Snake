#!/usr/bin/env python3
"""Intent labels for recorded strokes, inferred from the game, and an
event-level scorer for recognizers.

An *intent event* says "around time onset the player started moving the
finger to turn David toward dir" (dir = screen direction U/R/D/L):

  turn   a logged turn that sent David toward the harp, or happened with a
         spear flying close by (dodging); its onset is where the finger
         started moving that way
  want   a double-tap answer "I wanted to go dir": the biggest finger move
         toward dir in the 2 s before the flag (or before death)

A *no-turn event* is a double-tap answer "that turn was unwanted".
Logged turns that fit neither the harp nor a spear are *doubtful*: their
windows are left out of training and scoring.

Usage: python3 tools/intent.py FILE... (prints the label counts)"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build, score_at, DIR

V = {"U": (0, -1), "R": (1, 0), "D": (0, 1), "L": (-1, 0)}
TURNED = {"turn", "queued", "step", "flush", "re-aim", "rotate"}
WIN_MS = 350        # how long after onset a turn still counts as on time


class Event:
    def __init__(self, kind, onset, d, src):
        self.kind, self.onset, self.dir, self.src = kind, onset, d, src

    def __repr__(self):
        return f"{self.kind}:{self.dir}@{self.onset}({self.src})"


def onset_of(s, t, d):
    """Walk back from time t while the finger moved toward d."""
    vx, vy = V[d]
    pts = [x for x in s.samples if x[0] <= t]
    on = t
    for k in range(len(pts) - 1, 0, -1):
        dx, dy = pts[k][1] - pts[k - 1][1], pts[k][2] - pts[k - 1][2]
        if dx * vx + dy * vy > 0.2:
            on = pts[k - 1][0]
        else:
            break
    return on


def spear_near(board, head):
    for sp in filter(None, board.get("spears", "").split(";")):
        x, y, d = sp.split(",")
        x, y = int(x), int(y)
        vx, vy = V[d]
        rx, ry = head[0] - x, head[1] - y
        if abs(rx) + abs(ry) <= 6 and rx * vx + ry * vy > 0:
            return True
    return False


def biggest_move(s, lo, hi, d):
    """Start time and length of the longest run of finger motion toward d."""
    vx, vy = V[d]
    pts = [x for x in s.samples if lo <= x[0] <= hi]
    best = (None, 0.0)
    start, run = None, 0.0
    for k in range(1, len(pts)):
        c = (pts[k][1] - pts[k - 1][1]) * vx + (pts[k][2] - pts[k - 1][2]) * vy
        if c > 0.2:
            if start is None:
                start, run = pts[k - 1][0], 0.0
            run += c
            if run > best[1]:
                best = (start, run)
        else:
            start = None
    return best


def label_play(p):
    """Intent events per stroke index: {i: [Event, ...]}."""
    ev = {i: [] for i in range(len(p.strokes))}
    unwanted = [(f["turn_t"], f["turn_dir"]) for f in p.flags
                if f.get("type") in ("fp", "wrong") and "turn_t" in f]
    for i, s in enumerate(p.strokes):
        cs = s.cmds
        for j, c in enumerate(cs):
            if c["res"] not in TURNED or c["dir"] == c["hd"]:
                continue
            if c["kind"] == "uturn" and j + 1 < len(cs) and cs[j + 1]["kind"] == "uturn" \
                    and cs[j + 1]["t"] == c["t"]:
                continue                        # the U-turn counts once, by its back half
            d = c["dir"]
            if any(abs(t - c["t"]) <= 40 and td == d for t, td in unwanted):
                ev[i].append(Event("none", c["t"], d, "flag-fp"))
                continue
            h, head = c["board"]["harp"], c["head"]
            vx, vy = V[d]
            toward = (h[0] - head[0]) * vx + (h[1] - head[1]) * vy > 0
            src = "harp" if toward else ("spear" if spear_near(c["board"], head) else None)
            ev[i].append(Event("turn" if src else "doubt", onset_of(s, c["t"], d), d, src or "?"))
    for f in p.flags:
        if f.get("type") not in ("fn", "wrong") or not f.get("want"):
            continue
        tref = min(f["t"], p.death["t"]) if p.death and f["phase"] == "LOST" else f["t"]
        best = None
        for i, s in enumerate(p.strokes):
            if s.t1 < tref - 2000 or s.t0 > tref:
                continue
            on, ln = biggest_move(s, tref - 2000, tref, f["want"])
            if on is not None and ln >= 10 and (best is None or ln > best[2]):
                best = (i, on, ln)
        if best:
            ev[best[0]].append(Event("turn", best[1], f["want"], "flag-fn"))
    return ev


def dataset(paths):
    """[(file, play, stroke, events), ...] for every recorded stroke."""
    out = []
    for path in paths:
        _, plays = build(load(path))
        for p in plays:
            ev = label_play(p)
            for i, s in enumerate(p.strokes):
                out.append((os.path.basename(path), p, s, ev[i]))
    return out


def score(fires_of, data):
    """Event-level score of a recognizer. fires_of(p, s) -> [(t, dirLetter)].
    A turn event is hit by a fire of its dir in [onset-50, onset+WIN_MS];
    any other fire outside doubtful windows is unwanted."""
    hit = miss = bad = 0
    lat = []
    for _, p, s, evs in data:
        fires = fires_of(p, s)
        used = set()
        for e in evs:
            if e.kind != "turn":
                continue
            m = [k for k, (t, d) in enumerate(fires)
                 if d == e.dir and e.onset - 50 <= t <= e.onset + WIN_MS and k not in used]
            if m:
                hit += 1
                used.add(m[0])
                lat.append(fires[m[0]][0] - e.onset)
            else:
                miss += 1
        for k, (t, d) in enumerate(fires):
            if k in used:
                continue
            if any(e.kind == "doubt" and e.onset - 50 <= t <= e.onset + WIN_MS for e in evs):
                continue
            bad += 1
    lat.sort()
    return dict(hit=hit, miss=miss, unwanted=bad,
                latency=lat[len(lat) // 2] if lat else None)


if __name__ == "__main__":
    from collections import Counter
    data = dataset(sys.argv[1:])
    c = Counter((e.kind, e.src) for _, _, _, evs in data for e in evs)
    print(f"{len(data)} strokes; events: " + ", ".join(f"{k}/{s} {n}" for (k, s), n in sorted(c.items())))


def replay_fires(rec_cls):
    """fires_of for a tools/recognizers.py class: replay a stroke from its
    logged starting heading; each turn the recognizer makes is assumed
    taken (a reversal with a tail is not); a U-turn counts once, by its
    back half."""
    import recognizers as R

    def fires_of(p, s):
        rec = rec_cls()
        tail = score_at(p, s.t0) > 0
        g = R.Game(DIR.get(s.ih[:1] or "U", 0), tail)
        out = []
        for k, (t, x, y) in enumerate(s.samples):
            if k == 0:
                rec.down(t, x, y)
                continue
            last = k == len(s.samples) - 1 and s.end == "up"
            cmds = rec.up(t, x, y, g) if last else rec.move(t, x, y, g)
            for j, (d, kind) in enumerate(cmds):
                if kind == "uturn" and j == 0:
                    continue
                if d == g.heading or (tail and d == (g.heading + 2) % 4 and kind != "uturn"):
                    continue
                g.heading = d
                out.append((t, R.LETTER[d]))
        return out
    return fires_of

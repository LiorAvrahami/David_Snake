#!/usr/bin/env python3
"""Checks games played with a trained model (ML-1, ...) decision by
decision. The model is rebuilt from each game's own model line alone; the
game is replayed exactly (tools/enginesim.py); at every tick the model reads
the recorded finger samples as the game does (LearnedRecognizer), and its
commands are compared with the logged ones. Needs v1.15+ recordings (model
lines, and the game tick of every finger sample).

Usage: python3 tools/mlparity.py FILE..."""
import bisect, os, sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import enginesim as E
import simlearn as L
import windowsim as WS


class Stroke:
    def __init__(self, t, x, y):
        self.ts, self.xs, self.ys = [t], [x], [y]
        self.lt = 0
        self.fresh = True

    def add(self, t, x, y):
        self.ts.append(t)
        self.xs.append(x)
        self.ys.append(y)
        self.fresh = True


def check_play(p):
    """(decisions that fired, identical, [mismatch descriptions])"""
    spec = p.model["spec"]["recognizer"]
    pol = L.Policy.from_spec(spec)
    if any(s.tks is None for s in p.strokes):
        raise ValueError("no sample ticks in this recording (needs v1.15+)")
    samples = WS.samples_of(p)
    slots = WS.slots_of(p, samples)
    cmds = list(zip(p.cmds, E.held_flags(p.cmds)))
    by_tk = {}
    for c, h in cmds:
        by_tk.setdefault(c["tk"], []).append((c, h))
    real = E.start_sim(p)
    last_tk = p.death["tk"] if p.death else max([s["tk"] for s in p.steps] + [0])
    cur, ended = None, deque()
    fired = same = 0
    bad = []
    i = 0
    for k in range(0, last_tk + 1):
        while i < len(samples) and slots[i] <= k:
            t, x, y, first, last, up = samples[i]
            i += 1
            if first:
                if cur and cur.fresh:
                    ended.append(cur)
                cur = Stroke(t, x, y)
            elif cur:
                cur.add(t, x, y)
            if last and up and cur:
                ended.append(cur)
                cur = None
        mine, s, h, n = [], None, real.heading, 0
        s = ended.popleft() if ended else cur
        if s and s.fresh and not real.lost:
            s.fresh = False
            n = len(s.ts) - 1
            a = pol.decide(L.features(s.ts, s.xs, s.ys, n, s.lt, h))
            if a:
                j = bisect.bisect_left(s.ts, s.ts[n] - 160)
                mine = L.commands(a, h, real, (s.xs[n] - s.xs[j], s.ys[n] - s.ys[j]))
        else:
            s = None
        logged = by_tk.get(k, [])
        got = [(E.LET[d], hold) for d, hold in mine]
        want = [(c["dir"], hold) for c, hold in logged]
        if got or want:
            fired += 1
            if got == want:
                same += 1
            elif len(bad) < 10:
                bad.append(f"g{p.n} tick {k}: model {got}, game logged {want}")
        for c, hold in logged:
            real.swipe(E.LET.index(c["dir"]), hold)
        if s is not None and mine and real.heading != h:
            s.lt = n
        if real.lost or k == last_tk:
            break
        real.tick()
        if real.lost:
            break
    return fired, same, bad


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    tot = ok = 0
    for path in sys.argv[1:]:
        _, plays = build(load(path))
        for p in plays:
            if not p.model or "w_side" not in p.model["spec"].get("recognizer", {}):
                continue
            fired, same, bad = check_play(p)
            tot += fired
            ok += same
            for b in bad:
                print("  " + b)
    print(f"trained-model decisions that turned: {ok}/{tot} identical to the game's")
    sys.exit(0 if ok == tot else 1)

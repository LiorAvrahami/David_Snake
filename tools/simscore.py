#!/usr/bin/env python3
"""Scores input recognizers by simulating the game, as the player proposed.

While a finger is moving, every EVERY_MS the scorer takes the heading the
recognizer would have David facing at that moment (its own turns from the
stroke's starting heading) and David's recorded position, then lets the
game run on with no further turns:

  harp   +1 if the harp lies straight ahead within HARP_CELLS with no tail
         in between, scaled down the farther it is (the player lines up
         with the harp from far away)
  spear  -SPEAR if a flying spear hits David within SPEAR_STEPS steps
         (farther than that, the player dodges it himself); exact only for
         recordings that log every spear throw (v1.6+), skipped otherwise
  wall   -WALL if the very next cell ahead is a wall or the tail: no room
         left to react (a wall farther ahead costs nothing; he turns in time)

A U-turn counts once (its back half), and a side step (a turn, then a
turn back to the old direction within SIDESTEP_MS, about one step) is one
move: the moments between its two turns are not scored, since the player
plans the whole move at once.

Usage: python3 tools/simscore.py FILE... """
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
from gamestate import GameState, V, COLS, ROWS
import intent, recognizers as R

EVERY_MS = 90
SIDESTEP_MS = 250
HARP_CELLS = 10
SPEAR_STEPS = 3
SPEAR = 3.0
WALL = 3.0
TICK_MS = 45
LET = "URDL"


def tick_at(g, t):
    """Game tick at time t, from the nearest earlier step."""
    best = None
    for s in g.steps:
        if s["t"] > t:
            break
        best = s
    if best is None or "tk" not in best:
        return None
    return best["tk"] + int((t - best["t"]) // TICK_MS)


def ahead_harp(x, y, d, harp, tail):
    vx, vy = V[d]
    for n in range(1, HARP_CELLS + 1):
        x, y = x + vx, y + vy
        if not (0 <= x < COLS and 0 <= y < ROWS) or (x, y) in tail:
            return 0.0
        if (x, y) == harp:
            return 1.0 - (n - 1) / HARP_CELLS
    return 0.0


def speared(g, x, y, d, tk):
    """Does a spear hit David within SPEAR_STEPS steps going straight?
    None if spears are unknown for this recording."""
    if not g.p.spears or tk is None:
        return None
    vx, vy = V[d]
    last_step = max((s["tk"] for s in g.steps if s.get("tk", 10 ** 9) <= tk), default=tk)
    next_step = last_step + 4 if last_step + 4 > tk else tk + 1
    hx, hy = x, y
    for k in range(tk, next_step + 4 * (SPEAR_STEPS - 1) + 1):
        if k == next_step or (k > next_step and (k - next_step) % 4 == 0):
            hx, hy = hx + vx, hy + vy
            if not (0 <= hx < COLS and 0 <= hy < ROWS):
                return False            # the wall rule handles walls
        for sx, sy, _ in g.spears_at_tick(k):
            if (sx, sy) == (hx, hy):
                return True
    return False


def moment_score(g, t, d):
    x, y, _, i = g.head_at_time(t)
    sc = g.score_at(t)
    tail = set(g.tail_at_step(i, sc)) if i >= 0 else set()
    vx, vy = V[d]
    nx, ny = x + vx, y + vy
    r = {"harp": 0.0, "wall": 0, "spear": 0, "spear_known": 0}
    if not (0 <= nx < COLS and 0 <= ny < ROWS) or (nx, ny) in tail:
        r["wall"] = 1
    harp = g.harp_at(t)
    if harp:
        r["harp"] = ahead_harp(x, y, d, harp, tail)
    hit = speared(g, x, y, d, tick_at(g, t))
    if hit is not None:
        r["spear_known"] = 1
        r["spear"] = int(hit)
    return r


def load_plays(paths):
    out = []
    for path in paths:
        _, plays = build(load(path))
        out += plays
    return out


def evaluate(paths, fires_of):
    """Sum of moment scores over all strokes, for one recognizer."""
    return evaluate_plays(load_plays(paths), fires_of)


def evaluate_plays(plays, fires_of):
    tot = {"harp": 0.0, "wall": 0, "spear": 0, "spear_known": 0, "moments": 0}
    if True:
        for p in plays:
            g = GameState(p)
            end = p.death["t"] if p.death else float("inf")
            for s in p.strokes:
                fires = fires_of(p, s)
                h = s.ih[:1] or "U"
                skip = []                   # (from, to): inside a side step
                prev = h
                for a, b in zip(fires, fires[1:]):
                    if b[1] == prev and b[0] - a[0] <= SIDESTEP_MS:
                        skip.append((a[0], b[0]))
                    prev = a[1]
                t = s.t0 + EVERY_MS
                k = 0
                while t <= s.t1 + EVERY_MS and t < end:
                    while k < len(fires) and fires[k][0] <= t:
                        h = fires[k][1]
                        k += 1
                    if any(lo <= t < hi for lo, hi in skip):
                        t += EVERY_MS
                        continue
                    r = moment_score(g, t, h)
                    for key in r:
                        tot[key] += r[key]
                    tot["moments"] += 1
                    t += EVERY_MS
    tot["score"] = tot["harp"] - WALL * tot["wall"] - SPEAR * tot["spear"]
    tot["per1000"] = 1000.0 * tot["score"] / max(1, tot["moments"])
    return tot


def logged_fires(p, s):
    """What actually happened: the recorded heading changes in the stroke."""
    out = []
    for k in range(1, len(s.ih)):
        if s.ih[k] != s.ih[k - 1]:
            out.append((s.samples[k][0], s.ih[k]))
    return out


if __name__ == "__main__":
    paths = sys.argv[1:]
    rows = [("as played", logged_fires)]
    for n in ("O", "P", "P28", "S2", "S2F"):
        rows.append((n, intent.replay_fires(R.ALL[n])))
    for name, f in rows:
        r = evaluate(paths, f)
        print(f"{name:10s} score {r['score']:8.1f}   harp {r['harp']:7.1f}   wall/tail ahead {r['wall']:4d}"
              f"   speared {r['spear']:3d} (of {r['spear_known']} moments with spears known)"
              f"   moments {r['moments']}")

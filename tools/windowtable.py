#!/usr/bin/env python3
"""Every course of action a turn model can take in a window, scored once.

A window (tools/windowsim.py: a game tick with finger movement) is read for
250 ms, deciding none, right or left at every tick with a fresh sample; after
a turn one more turn may come until David's second step after it. Within
that, a model can do few things: no turn; a first turn at one of the decision
ticks, either way; after it a second turn at one of the remaining decision
ticks, either way. This enumerates them all (a third turn is not enumerated:
after two turns the course ends with none), running each with the same
PolicyInputs the judge uses and scoring it with the Final score (the turns
shifted together -DELTA..DELTA ticks, ALPHA times the best plus 1 - ALPHA
times the plain run), and records the finger features the model sees at every
decision tick along the way. A turn the engine blocks (into the tail) is the
same course as none at that tick. A course without a score (David speared
from the side or after the harp, or in the phantom tail) is stored as NaN; the
trainer leaves it out.

Per window a small tree: nodes = decision ticks with their features; from each
node three edges, none / right / left, leading to another node (the next
decision) or ending the course with its score. Pickled per game:
{key: [(F: nodes x 17 float32, child: nodes x 3 int (-1 = the course ends),
        final: nodes x 3 float (the score of an ending edge), plain: same,
        depth: nodes int (turns made before the node)), ...]}, with each game's
recognizer (arms) for the train/test split.

Usage: python3 tools/windowtable.py FILE... --out TABLE.pkl [--delta 4] [--alpha 0.9]"""
import math, os, pickle, sys, time
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import intentlearn as I
import simlearn as L
import windowsim as WS

DELTA = 4
ALPHA = 0.9


class NonePolicy:
    layout = L.LAYOUT_NOSPEED

    def decide(self, f):
        return 0


NONE = NonePolicy()


def course(g, spec, force, delta, alpha):
    """Run the window with the forced turns and none elsewhere. Returns
    (record [(tick, features, action, heading, t)], ticks whose command turned
    David, Final score, plain score)."""
    rec, turned = [], []

    def watch(ev, sim, k, t, h0):
        if ev == "cmd" and sim.heading != h0:
            turned.append(k)
    inp = L.PolicyInputs(spec.t0, NONE, g.samples, g.slots, spec.i, spec.stroke, force=force, record=rec)
    plain, outs = WS.run_window_tolerant(spec.real, spec.k, spec.t0, inp, tuple(range(-delta, delta + 1)),
                                         None, watch, spec.prev)
    final, _ = WS.tolerant_score(plain, outs, alpha)
    return rec, turned, final, plain.score


def window_tree(g, spec, delta, alpha):
    F, child, final, plain, depth = [], [], [], [], []

    def node(f, d):
        F.append(np.asarray(f, np.float32))
        child.append([-1, -1, -1])
        final.append([math.nan] * 3)
        plain.append([math.nan] * 3)
        depth.append(d)
        return len(F) - 1

    def chain(force, d):
        """Nodes for the decision ticks after the last forced tick (all of them
        for no force); the none edges link them; the last one's none edge ends
        with this course's scores. Returns (nodes, their ticks)."""
        rec, turned, fin, pl = course(g, spec, force, delta, alpha)
        last_forced = max(force) if force else -1
        for k in force:
            if k not in turned:
                return None, None, None            # a forced turn was blocked: not a course of its own
        post = [r for r in rec if r[0] > last_forced]
        nodes = [node(r[1], d) for r in post]
        ticks = [r[0] for r in post]
        for i, n in enumerate(nodes):
            if i + 1 < len(nodes):
                child[n][0] = nodes[i + 1]
            else:
                final[n][0], plain[n][0] = fin, pl
        return nodes, ticks, (fin, pl)

    root, ticks0, scores0 = chain({}, 0)
    if not root:
        return None
    for i, n in enumerate(root):
        for a in (1, 2):
            nodes1, ticks1, s1 = chain({ticks0[i]: a}, 1)
            if nodes1 is None:                     # blocked: the same as none here
                child[n][a], final[n][a], plain[n][a] = child[n][0], final[n][0], plain[n][0]
                continue
            if not nodes1:                         # the reading ended with the turn
                final[n][a], plain[n][a] = s1
                continue
            child[n][a] = nodes1[0]
            for j, m in enumerate(nodes1):
                for b in (1, 2):
                    rec2, turned2, fin2, pl2 = course(g, spec, {ticks0[i]: a, ticks1[j]: b}, delta, alpha)
                    if ticks1[j] not in turned2:   # blocked second turn: the same as none here
                        child[m][b], final[m][b], plain[m][b] = child[m][0], final[m][0], plain[m][0]
                    else:                          # the course ends (no third turn enumerated)
                        final[m][b], plain[m][b] = fin2, pl2
    return (np.stack(F), np.asarray(child, np.int32), np.asarray(final, np.float64),
            np.asarray(plain, np.float64), np.asarray(depth, np.int8))


def _build(args):
    key, delta, alpha = args
    g = I.GAMES[key]
    t0 = time.time()
    trees = [t for t in (window_tree(g, spec, delta, alpha) for spec in g.specs) if t is not None]
    return key, trees, g.arm, time.time() - t0


def main():
    argv = list(sys.argv)
    opt = {"--out": "", "--delta": str(DELTA), "--alpha": str(ALPHA)}
    for name in list(opt):
        if name in argv:
            i = argv.index(name)
            opt[name] = argv[i + 1]
            del argv[i:i + 2]
    files = argv[1:]
    delta, alpha = int(opt["--delta"]), float(opt["--alpha"])
    keys = []
    for path in files:
        _, plays = build(load(path))
        keys += [(os.path.basename(path), p.n) for p in plays if p.spears and not p.aborted]
    out, arms, secs = {}, {}, 0.0
    with Pool(os.cpu_count(), initializer=I._init, initargs=(files, True, False)) as pool:
        for key, trees, arm, dt in pool.imap_unordered(_build, [(k, delta, alpha) for k in keys]):
            out[key], arms[key] = trees, arm
            secs += dt
            n = sum(len(t[0]) for t in trees)
            print(f"{key[0]} game {key[1]}: {len(trees)} windows, {n} decision nodes, {dt:.0f} s", flush=True)
    with open(opt["--out"], "wb") as f:
        pickle.dump({"delta": delta, "alpha": alpha, "files": files, "trees": out, "arms": arms}, f, protocol=4)
    nw = sum(len(t) for t in out.values())
    nn = sum(len(x[0]) for t in out.values() for x in t)
    print(f"wrote {opt['--out']}: {len(out)} games, {nw} windows, {nn} decision nodes, {secs:.0f} CPU seconds")


if __name__ == "__main__":
    main()

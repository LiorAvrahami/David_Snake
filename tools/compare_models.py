#!/usr/bin/env python3
"""Compares S2-FAST, the live game and trained turn models on the training
and test games of a split (tools/simlearn.py split), with the
tools/windowsim.py scoring.

Prints three tables, per 1000 gestures: the training games and the test
games (a row per model; columns: total, harp, and the penalties of hitting
the wall, being speared, running into the tail), and a summary: training
total, test total, and test minus S2-FAST with its 90% range (bootstrap
over test games).

--tolerance N scores each window by the best of the model's turns moved
together so that its first turn lands N ticks before to N ticks after the
live game's first command in the window (windowsim.run_window_tolerant),
the same placements for every model, so timing counts for nothing and the
score shows the accuracy of the turns; with --alpha A (default 1) the
score is A times that plus (1 - A) times the plain score.

Usage: python3 tools/compare_models.py FILE... --models tools/models/ml-1.json,tools/models/ml-2.json
                                       [--holdout FRAC] [--seed S] [--never-test FILE,...]
                                       [--tolerance N] [--alpha A] [--ref MODEL]"""
import bisect, json, os, random, sys
from collections import defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import intentlearn as I
import recognizers as R
import simlearn as L
import windowsim as WS

CAUSES = ("hit the wall", "speared", "ran into the tail")


def _outcomes(args):
    """Per window and model: (harp, death, why) with the plain or the tolerant score
    (harp - death is the score; with tolerance the best shift's outcome, and death
    adjusted so that harp - death is the alpha-weighted score)."""
    key, models, tol, alpha = args
    g = I.GAMES[key]
    shifts = tuple(range(-tol, tol + 1))
    runs = {name: [] for name in ["S2-FAST", "live"] + [m for m, _ in models]}
    for spec in g.specs:
        inputs = {"S2-FAST": WS.RecInputs(spec.t0, R.ALL["S2F"], g.shadow[spec.k], g.samples, g.slots, spec.i),
                  "live": WS.LogInputs(spec.t0, g.cmds, bisect.bisect_left(g.cmd_tk, spec.k))}
        for name, w in models:
            pol = L.Policy(w[:L.NF], w[L.NF:])
            inputs[name] = L.PolicyInputs(spec.t0, pol, g.samples, g.slots, spec.i, spec.stroke)
        for name, inp in inputs.items():
            if tol:
                plain, outs = WS.run_window_tolerant(spec.real, spec.k, spec.t0, inp, shifts, spec.anchor)
                sc, w = WS.tolerant_score(plain, outs, alpha)
                runs.setdefault(name, []).append((w.harp, w.harp - sc, w.why))
            else:
                w = WS.run_window(spec.real, spec.k, inp)
                runs.setdefault(name, []).append((w.harp, w.death, w.why))
    return key, runs


def row(res, keys, m):
    n = sum(len(res[k][m]) for k in keys)
    harp = sum(h for k in keys for h, _, _ in res[k][m])
    pen = defaultdict(float)
    for k in keys:
        for _, d, why in res[k][m]:
            if why in CAUSES:
                pen[why] += d
    total = harp - sum(pen.values())
    return [1000 * total / n, 1000 * harp / n] + [-1000 * pen[c] / n for c in CAUSES]


def boot(res, keys, m, ref="S2-FAST", n=2000, seed=1):
    per = {k: (sum(h - d for h, d, _ in res[k][m]) - sum(h - d for h, d, _ in res[k][ref]), len(res[k][ref]))
           for k in keys}
    rnd = random.Random(seed)
    ds = []
    for _ in range(n):
        pick = [rnd.choice(keys) for _ in keys]
        ds.append(1000 * sum(per[k][0] for k in pick) / max(1, sum(per[k][1] for k in pick)))
    ds.sort()
    return ds[int(0.05 * n)], ds[int(0.95 * n)]


def main():
    argv = list(sys.argv)
    models, tol, alpha, ref = [], 0, 1.0, "S2-FAST"
    if "--models" in argv:
        i = argv.index("--models")
        for path in argv[i + 1].split(","):
            spec = json.load(open(path))
            models.append((spec["name"], L.wvec(L.Policy.from_spec(spec))))
        del argv[i:i + 2]
    for opt in ("--tolerance", "--alpha", "--ref"):
        if opt in argv:
            i = argv.index(opt)
            if opt == "--tolerance":
                tol = int(argv[i + 1])
            elif opt == "--alpha":
                alpha = float(argv[i + 1])
            else:
                ref = argv[i + 1]
            del argv[i:i + 2]
    args, o = L.options(argv)
    keys = []
    for path in args:
        _, plays = build(load(path))
        keys += [(os.path.basename(path), p.n) for p in plays if p.spears and not p.aborted]
    with Pool(os.cpu_count(), initializer=I._init, initargs=(args,)) as pool:
        arms = {k: arm for k, arm, _ in pool.map(I._info, keys)}
        trn, test = L.split(keys, arms, o["holdout"], o["seed"], o["never_test"])
        res = dict(pool.map(_outcomes, [(k, models, tol, alpha) for k in keys]))
    if tol:
        print(f"Timing-tolerant score: each window's turns moved together to land -{tol}..{tol} ticks "
              f"from the live game's first command, the best counts" + (f" (alpha {alpha:g}: that times {alpha:g} plus the plain score times {1 - alpha:g})" if alpha != 1 else ""))
    names = ["S2-FAST", "live"] + [m for m, _ in models]
    nw = lambda ks: sum(len(res[k]["S2-FAST"]) for k in ks)
    for title, ks in (("Training games", trn), ("Test games", test)):
        if not ks:
            continue
        print(f"\n{title}: {len(ks)} games, {nw(ks)} gestures (per 1000 gestures)")
        print(f"  {'':8s} {'Total':>7s} {'Harp':>7s} {'Wall':>7s} {'Spear':>7s} {'Tail':>7s}")
        for m in names:
            print(f"  {m:8s} " + " ".join(f"{v:7.1f}" for v in row(res, ks, m)))
    if not test:
        test, trn = trn, []                # no split: the summary over every game
        print("\n(no held-out games: the summary is over every game)")
    if test:
        print(f"\nSummary (per 1000 gestures)")
        print(f"  {'':8s} {'Train':>7s} {'Test':>7s}   Test minus {ref} [90% range]")
        base = row(res, test, ref)[0]
        for m in names:
            line = f"  {m:8s} {row(res, trn, m)[0] if trn else float('nan'):7.1f} {row(res, test, m)[0]:7.1f}"
            if m != ref:
                lo, hi = boot(res, test, m, ref)
                line += f"   {row(res, test, m)[0] - base:+6.1f} [{lo:+.1f}, {hi:+.1f}]"
            print(line)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compares S2-FAST, the live game and trained turn models on the training
and test games of a split (tools/simlearn.py split), with the
tools/windowsim.py scoring.

Prints three tables, per 1000 windows with a score: the training games and
the test games (a row per model; columns: total, harp, the penalties of
hitting the wall, being speared, running into the tail, and Excl., the
windows left out: those in which the model's David was speared from the side
or after the harp, or entered the phantom tail, which have no score), and a
summary: training total, test total, and test minus the reference over the
windows both have a score for, with its 90% range (bootstrap over test
games).

--tolerance N scores each window by the Final score: the model's turns
moved together so that its first turn lands N ticks before to N ticks
after where it made it (--anchored: after the live game's first command
in the window, the same placements for every model), the best counting
(windowsim.run_window_tolerant), times --alpha A (default 0.9), plus the
plain run times 1 - A: timing counts for little, the score shows the
accuracy of the turns. A turn placed one tick before the window runs from
the real state one tick earlier.

Usage: python3 tools/compare_models.py FILE... --models tools/models/ml-1.json,tools/models/ml-2.json
                                       [--holdout FRAC] [--seed S] [--never-test FILE,...]
                                       [--tolerance N] [--alpha A] [--anchored] [--ref MODEL]"""
import bisect, json, math, os, random, sys
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
    """Per window and model: (harp, death, why, score): the plain run's, or with
    tolerance the best placement's outcome and the Final score."""
    key, models, tol, alpha, anchored = args
    g = I.GAMES[key]
    shifts = tuple(range(-tol, tol + 1))
    runs = {name: [] for name in ["S2-FAST", "live"] + [m for m, _ in models]}
    for spec in g.specs:
        inputs = {"S2-FAST": WS.RecInputs(spec.t0, R.ALL["S2F"], g.shadow[spec.k], g.samples, g.slots, spec.i),
                  "live": WS.LogInputs(spec.t0, g.cmds, bisect.bisect_left(g.cmd_tk, spec.k))}
        for name, d in models:
            inputs[name] = L.PolicyInputs(spec.t0, L.policy_from_json(d), g.samples, g.slots, spec.i, spec.stroke)
        for name, inp in inputs.items():
            if tol:
                plain, outs = WS.run_window_tolerant(spec.real, spec.k, spec.t0, inp, shifts,
                                                     spec.anchor if anchored else None, prev=spec.prev)
                sc, w = WS.tolerant_score(plain, outs, alpha)
                runs.setdefault(name, []).append((w.harp, w.death, w.why, sc))
            else:
                w = WS.run_window(spec.real, spec.k, inp)
                runs.setdefault(name, []).append((w.harp, w.death, w.why, w.score))
    return key, runs


def row(res, keys, m):
    """Per 1000 windows with a score: the score, the harps, the death penalties by
    cause (of the outcomes that count: with tolerance the best placements'), and
    the number of windows left out (no score)."""
    rs = [r for k in keys for r in res[k][m]]
    ok = [r for r in rs if not math.isnan(r[3])]
    n = max(1, len(ok))
    total = sum(sc for *_, sc in ok)
    harp = sum(h for h, *_ in ok)
    pen = defaultdict(float)
    for _, d, why, _ in ok:
        if why in CAUSES:
            pen[why] += d
    return [1000 * total / n, 1000 * harp / n] + [-1000 * pen[c] / n for c in CAUSES] + [len(rs) - len(ok)]


def boot(res, keys, m, ref="S2-FAST", n=2000, seed=1):
    """m minus ref per 1000 windows over the windows both have a score for, and
    its 90% range over games."""
    per = {}
    for k in keys:
        pairs = [(a[3], b[3]) for a, b in zip(res[k][m], res[k][ref]) if not (math.isnan(a[3]) or math.isnan(b[3]))]
        per[k] = (sum(a - b for a, b in pairs), len(pairs))
    rnd = random.Random(seed)
    ds = []
    for _ in range(n):
        pick = [rnd.choice(keys) for _ in keys]
        ds.append(1000 * sum(per[k][0] for k in pick) / max(1, sum(per[k][1] for k in pick)))
    ds.sort()
    tot = 1000 * sum(per[k][0] for k in keys) / max(1, sum(per[k][1] for k in keys))
    return tot, ds[int(0.05 * n)], ds[int(0.95 * n)]


def main():
    argv = list(sys.argv)
    models, tol, alpha, ref, anchored = [], 0, 0.9, "S2-FAST", "--anchored" in argv
    argv = [a for a in argv if a != "--anchored"]
    if "--models" in argv:
        i = argv.index("--models")
        for path in argv[i + 1].split(","):
            spec = json.load(open(path))
            models.append((spec["name"], L.policy_from_spec(spec).to_json()))
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
        res = dict(pool.map(_outcomes, [(k, models, tol, alpha, anchored) for k in keys]))
    if tol:
        where = "from the live game's first command" if anchored else "from where the model made it"
        print(f"Final score: each window's turns moved together to land -{tol}..{tol} ticks {where}, "
              f"{alpha:g} times the best plus {1 - alpha:g} times the plain run")
    names = ["S2-FAST", "live"] + [m for m, _ in models]
    nw = lambda ks: sum(len(res[k]["S2-FAST"]) for k in ks)
    for title, ks in (("Training games", trn), ("Test games", test)):
        if not ks:
            continue
        print(f"\n{title}: {len(ks)} games, {nw(ks)} windows (per 1000 windows with a score; Excl. = windows "
              f"left out: speared from the side or after the harp, or the phantom tail)")
        print(f"  {'':8s} {'Total':>7s} {'Harp':>7s} {'Wall':>7s} {'Spear':>7s} {'Tail':>7s} {'Excl.':>7s}")
        for m in names:
            r = row(res, ks, m)
            print(f"  {m:8s} " + " ".join(f"{v:7.1f}" for v in r[:5]) + f" {r[5]:7d}")
    if not test:
        test, trn = trn, []                # no split: the summary over every game
        print("\n(no held-out games: the summary is over every game)")
    if test:
        print(f"\nSummary (per 1000 windows with a score; the difference over the windows both have a score for)")
        print(f"  {'':8s} {'Train':>7s} {'Test':>7s}   Test minus {ref} [90% range]")
        for m in names:
            line = f"  {m:8s} {row(res, trn, m)[0] if trn else float('nan'):7.1f} {row(res, test, m)[0]:7.1f}"
            if m != ref:
                tot, lo, hi = boot(res, test, m, ref)
                line += f"   {tot:+6.1f} [{lo:+.1f}, {hi:+.1f}]"
            print(line)


if __name__ == "__main__":
    main()

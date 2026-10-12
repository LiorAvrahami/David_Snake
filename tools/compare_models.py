#!/usr/bin/env python3
"""Compares S2-FAST and trained turn models on the training and test games
of a split (tools/simlearn.py split), with the tools/windowsim.py scoring.

Prints three tables, per 1000 gestures: the training games and the test
games (a row per model; columns: total, harp, and the penalties of hitting
the wall, being speared, running into the tail), and a summary: training
total, test total, and test minus S2-FAST with its 90% range (bootstrap
over test games).

Usage: python3 tools/compare_models.py FILE... --models tools/models/ml-1.json,tools/models/ml-2.json
                                       [--holdout FRAC] [--seed S] [--never-test FILE,...]"""
import json, os, random, sys
from collections import defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import simlearn as L

CAUSES = ("hit the wall", "speared", "ran into the tail")


def _outcomes(args):
    key, models = args
    g = L.GAMES[key]
    out = {"S2-FAST": [(w.harp, w.death, w.why) for w in g.base_w]}
    for name, w in models:
        pol = L.Policy(w[:L.NF], w[L.NF:])
        out[name] = [(o.harp, o.death, o.why) for o in (L.run_w(g, spec, pol) for spec in g.specs)]
    return key, out


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
    models = []
    if "--models" in argv:
        i = argv.index("--models")
        for path in argv[i + 1].split(","):
            spec = json.load(open(path))
            models.append((spec["name"], L.wvec(L.Policy.from_spec(spec))))
        del argv[i:i + 2]
    args, o = L.options(argv)
    keys = []
    for path in args:
        _, plays = build(load(path))
        keys += [(os.path.basename(path), p.n) for p in plays if p.spears and not p.aborted]
    with Pool(os.cpu_count(), initializer=L._init, initargs=(args,)) as pool:
        info = {k: (im, b, pl, arm) for k, im, b, pl, arm in pool.map(L._info, keys)}
        trn, test = L.split(keys, {k: info[k][3] for k in keys}, o["holdout"], o["seed"], o["never_test"])
        res = dict(pool.map(_outcomes, [(k, models) for k in keys]))
    names = ["S2-FAST"] + [m for m, _ in models]
    nw = lambda ks: sum(len(res[k]["S2-FAST"]) for k in ks)
    for title, ks in (("Training games", trn), ("Test games", test)):
        if not ks:
            continue
        print(f"\n{title}: {len(ks)} games, {nw(ks)} gestures (per 1000 gestures)")
        print(f"  {'':8s} {'Total':>7s} {'Harp':>7s} {'Wall':>7s} {'Spear':>7s} {'Tail':>7s}")
        for m in names:
            print(f"  {m:8s} " + " ".join(f"{v:7.1f}" for v in row(res, ks, m)))
    if test:
        print(f"\nSummary (per 1000 gestures)")
        print(f"  {'':8s} {'Train':>7s} {'Test':>7s}   Test minus S2-FAST [90% range]")
        base = row(res, test, "S2-FAST")[0]
        for m in names:
            line = f"  {m:8s} {row(res, trn, m)[0]:7.1f} {row(res, test, m)[0]:7.1f}"
            if m != "S2-FAST":
                lo, hi = boot(res, test, m)
                line += f"   {row(res, test, m)[0] - base:+6.1f} [{lo:+.1f}, {hi:+.1f}]"
            print(line)


if __name__ == "__main__":
    main()

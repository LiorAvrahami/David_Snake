#!/usr/bin/env python3
"""Tunes the S2 reader's settings to maximize the game-simulation score
(tools/simscore.py) on recordings with spear data, by random search.
Games are split: two thirds to tune on, every third game held out to test.

Usage: python3 tools/tune.py [--n N] FILE..."""
import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simscore, intent, recognizers as R

SPACE = {                       # name: (low, high, integer?)
    "fastDp": (5.0, 14.0, False),
    "slowDp": (10.0, 26.0, False),
    "fastSpeed": (250.0, 700.0, False),
    "slowSpeed": (40.0, 220.0, False),
    "forwardDeg": (18.0, 45.0, False),
    "backDeg": (140.0, 175.0, False),
    "cooldownMs": (40, 220, True),
    "chainDp": (8.0, 24.0, False),
    "chainSpeed": (80.0, 400.0, False),
    "chainSectorDeg": (40.0, 75.0, False),
    "liftDp": (6.0, 16.0, False),
    "anchorAgeMs": (150, 450, True),
}


def make(params):
    return type("Tuned", (R.Smart2,), dict(params))


def split(plays):
    train = [p for i, p in enumerate(plays) if i % 3 != 2]
    test = [p for i, p in enumerate(plays) if i % 3 == 2]
    return train, test


def score(plays, cls):
    return simscore.evaluate_plays(plays, intent.replay_fires(cls))


if __name__ == "__main__":
    args = sys.argv[1:]
    n = 150
    if "--n" in args:
        n = int(args[args.index("--n") + 1])
        args = [a for a in args if a not in ("--n", str(n))]
    plays = simscore.load_plays(args)
    train, test = split(plays)
    print(f"{len(plays)} games: {len(train)} to tune on, {len(test)} held out")
    base = {k: getattr(R.Smart2Fast, k) for k in SPACE}
    rows = [("S2-FAST (now)", base)]
    rnd = random.Random(7)
    best = (score(train, make(base))["per1000"], base)
    print(f"start  train {best[0]:7.1f}")
    for i in range(n):
        # half the time explore anywhere, half the time perturb the best
        cand = {}
        for k, (lo, hi, integer) in SPACE.items():
            if rnd.random() < 0.5:
                v = rnd.uniform(lo, hi)
            else:
                span = (hi - lo) * 0.15
                v = min(hi, max(lo, best[1][k] + rnd.uniform(-span, span)))
            cand[k] = int(round(v)) if integer else round(v, 2)
        sc = score(train, make(cand))["per1000"]
        if sc > best[0]:
            best = (sc, cand)
            print(f"#{i:3d} train {sc:7.1f}  " + " ".join(f"{k}={v}" for k, v in cand.items()))
    rows.append(("tuned", best[1]))
    print()
    for name, prm in rows:
        tr = score(train, make(prm))
        te = score(test, make(prm))
        print(f"{name:14s} train {tr['per1000']:7.1f}   held-out {te['per1000']:7.1f}"
              f"   (held-out: harp {te['harp']:.0f}, wall {te['wall']}, speared {te['spear']}, moments {te['moments']})")
    print("\ntuned settings:", best[1])

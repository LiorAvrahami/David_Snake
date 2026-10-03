#!/usr/bin/env python3
"""Grid-searches the anchored-displacement recognizer's two parameters
(swipe distance in dp, dominant-axis ratio) over all labeled strokes,
printing the error count of every configuration and leave-one-out."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_candidate import load, anchored, confusion

S = load()
GRID = [(d, r) for d in (8, 10, 12, 14, 16, 18, 20, 24, 28, 32)
        for r in (1.0, 1.2, 1.5, 2.0, 2.5)]

def errs(strokes, g):
    m = confusion(strokes, lambda s: anchored(s, *g))
    return m["FP"] + m["FN"], m

for g in GRID:
    e, m = errs(S, g)
    print(f"swipe={g[0]:>2}dp ratio={g[1]:.1f}: {e} errors {m}")

loo = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
for i in range(len(S)):
    rest = S[:i] + S[i+1:]
    sc = [(errs(rest, g)[0], k) for k, g in enumerate(GRID)]
    mn = min(x for x, _ in sc)
    best = [k for x, k in sc if x == mn]
    m = errs([S[i]], GRID[best[len(best)//2]])[1]
    for k in loo: loo[k] += m[k]
print(f"LEAVE-ONE-OUT: TP={loo['TP']} FP={loo['FP']} TN={loo['TN']} FN={loo['FN']}")

#!/usr/bin/env python3
"""Trains the turn model on the score alone, as the player defined it
(docs/score_training.md). Nothing is taken from what the live game did.

At every decision point (a game tick with finger movement: tools/windowsim.py's
windows) the three actions none, right and left are each tried by simulation
from the real game state: none goes straight, a turn is made once and then
David goes straight, until the harp or a death (the window score). A turn is
tried at every lag -DELTA..DELTA ticks (a lag before the decision tick runs
from the real state that many ticks earlier); its score is ALPHA times the
best lag plus 1 - ALPHA times lag 0. The right action is the one with the
highest score (a tie goes to none); the model (simlearn.MlpPolicy, no back
answer, no speed input) is fitted to pick it, each decision point weighted by
the gap between the best and the second-best score.

Usage: python3 tools/scorelearn.py FILE... [--holdout FRAC --seed S --never-test FILE,...]
                                   [--hidden H] [--iters N] [--delta D] [--alpha A]
                                   [--save EX.pkl | --load EX.pkl] [--export]
Prints the decision points' label counts and the trained weights (json)."""
import json, math, os, pickle, sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import intentlearn as I
import simlearn as L
import windowsim as WS

DELTA = 4
ALPHA = 0.9
HIDDEN = 16
ITERS = 600
LAYOUT = L.LAYOUT_NOSPEED
ACTS = ("none", "right", "left")


def action_scores(spec, delta=DELTA, alpha=ALPHA):
    """(score of none, of a right turn, of a left turn) at the decision point."""
    h0 = spec.real.heading
    out = [WS.run_window(spec.real, spec.k, WS.Schedule(spec.t0, [], 0)).score]
    for d_abs in ((h0 + 1) % 4, (h0 + 3) % 4):
        log = [(spec.k, spec.t0, [(d_abs, False)])]
        by_lag = {}
        for d in range(-delta, delta + 1):
            if d >= 0:
                by_lag[d] = WS.run_window(spec.real, spec.k, WS.Schedule(spec.t0, log, d), ref=spec.k).score
            elif -d <= len(spec.prev):
                j = -d
                state = spec.prev[j - 1][1]
                before = [(slot, spec.t0, cmds) for slot, _, cmds in reversed(spec.prev[:j]) if cmds]
                by_lag[d] = WS.run_window(state, spec.k - j, WS.Schedule(spec.t0, log, d, before), ref=spec.k).score
        valid = [v for v in by_lag.values() if not math.isnan(v)]
        out.append(alpha * max(valid) + (1 - alpha) * by_lag[0] if valid and not math.isnan(by_lag[0]) else float("nan"))
    return out


def decision_features(g, spec):
    """The finger features the model sees at the decision tick (the stroke so
    far plus the tick's samples, as PolicyInputs reads them), or None."""
    if spec.stroke:
        ts, xs, ys, lt = list(spec.stroke[0]), list(spec.stroke[1]), list(spec.stroke[2]), spec.stroke[3]
    else:
        ts = xs = ys = None
        lt = 0
    i = spec.i
    while i < len(g.samples) and g.slots[i] <= spec.k:
        t, x, y, first, _, _ = g.samples[i][:6]
        i += 1
        if first:
            ts, xs, ys, lt = [t], [x], [y], 0
        elif ts is not None:
            ts.append(t)
            xs.append(x)
            ys.append(y)
    if ts is None:
        return None
    return L.features(ts, xs, ys, len(ts) - 1, lt, spec.real.heading, LAYOUT.speed)


def _examples(args):
    key, delta, alpha = args
    g = I.GAMES[key]
    out = []
    for spec in g.specs:
        f = decision_features(g, spec)
        if f is not None:
            out.append((f, action_scores(spec, delta, alpha)))
    return key, out


def labels(S):
    """Per row of scores (none, right, left): the best action, ties to none, and
    the weight: best minus second best."""
    S = np.asarray(S, float)
    bad = np.isnan(S).any(1)                        # an action without a score: the point is left out
    S = np.where(np.isnan(S), -np.inf, S)
    lab = np.zeros(len(S), int)
    lab[S[:, 1] > S[:, 0]] = 1
    lab[S[:, 2] > np.maximum(S[:, 0], S[:, 1])] = 2
    srt = np.sort(S, 1)
    w = srt[:, 2] - srt[:, 1]
    lab[bad], w[bad] = 0, 0.0
    return lab, w


def fit(F, lab, w, hidden=HIDDEN, iters=ITERS, l2=L.L2, lr=0.01, seed=1):
    """Weighted softmax fit of the one-hidden-layer model: none = 0, right =
    v . relu(W1 f + b1), left the same on mirror(f)."""
    keep = w > 0
    F, lab, w = np.asarray(F, float)[keep], lab[keep], w[keep]
    F, Fm, _ = L.mats(F, LAYOUT.side)
    Y = np.eye(3)[lab]
    wt = (w / w.mean())[:, None]
    nf = LAYOUT.nf
    rng = np.random.default_rng(seed)
    W1 = rng.normal(0, 1.0 / np.sqrt(nf), (hidden, nf))
    b1 = np.zeros(hidden)
    v = rng.normal(0, 0.1, hidden)
    params = [W1, b1, v]
    ms = [np.zeros_like(x) for x in params]
    vs = [np.zeros_like(x) for x in params]
    n = len(F)
    for it in range(1, iters + 1):
        Hr = np.maximum(F @ W1.T + b1, 0.0)
        Hl = np.maximum(Fm @ W1.T + b1, 0.0)
        S = np.stack([np.zeros(n), Hr @ v, Hl @ v], 1)
        P = L.softmax(S)
        G = (P - Y) * wt / n
        dr, dl = G[:, 1], G[:, 2]
        gv = Hr.T @ dr + Hl.T @ dl
        dHr = (dr[:, None] * v[None, :]) * (Hr > 0)
        dHl = (dl[:, None] * v[None, :]) * (Hl > 0)
        gW1 = dHr.T @ F + dHl.T @ Fm
        gb1 = dHr.sum(0) + dHl.sum(0)
        grads = [gW1 + l2 * W1, gb1 + l2 * b1, gv + l2 * v]
        for i, (x, g) in enumerate(zip(params, grads)):
            params[i], ms[i], vs[i] = L.adam(x, g, ms[i], vs[i], it, lr=lr)
        W1, b1, v = params
    pol = L.MlpPolicy(W1.tolist(), b1.tolist(), v.tolist(), None, True, LAYOUT.speed)
    pred = np.argmax(np.stack([np.zeros(n), np.maximum(F @ W1.T + b1, 0.0) @ v,
                               np.maximum(Fm @ W1.T + b1, 0.0) @ v], 1), 1)
    acc = float((wt[:, 0] * (pred == lab)).sum() / wt.sum())
    return pol, acc


def main():
    argv = list(sys.argv)
    opt = {"--hidden": str(HIDDEN), "--iters": str(ITERS), "--delta": str(DELTA), "--alpha": str(ALPHA),
           "--save": "", "--load": ""}
    for name in list(opt):
        if name in argv:
            i = argv.index(name)
            opt[name] = argv[i + 1]
            del argv[i:i + 2]
    export = "--export" in argv
    argv = [a for a in argv if a != "--export"]
    hidden, iters, delta, alpha = int(opt["--hidden"]), int(opt["--iters"]), int(opt["--delta"]), float(opt["--alpha"])
    args, o = L.options(argv)
    keys = []
    for path in args:
        _, plays = build(load(path))
        keys += [(os.path.basename(path), p.n) for p in plays if p.spears and not p.aborted]
    if opt["--load"]:
        with open(opt["--load"], "rb") as f:
            ex, arms = pickle.load(f)
    else:
        with Pool(os.cpu_count(), initializer=I._init, initargs=(args, True, LAYOUT.speed)) as pool:
            arms = {k: arm for k, arm, _ in pool.map(I._info, keys)}
            ex = dict(pool.map(_examples, [(k, delta, alpha) for k in keys]))
        if opt["--save"]:
            with open(opt["--save"], "wb") as f:
                pickle.dump((ex, arms), f)
    trn, test = L.split(keys, arms, o["holdout"], o["seed"], o["never_test"])
    F = [f for k in trn for f, _ in ex[k]]
    S = [s for k in trn for _, s in ex[k]]
    lab, w = labels(S)
    pol, acc = fit(F, lab, w, hidden, iters)
    if export:
        print(json.dumps(pol.to_json()))
        return
    n = len(lab)
    excluded = int(np.isnan(np.asarray(S, float)).any(1).sum())
    print(f"training games {len(trn)}, test {len(test)}; decision points {n}, {excluded} left out (an action without a score), "
          f"{int((w > 0).sum())} with a gap between the best and second-best action")
    for a in range(3):
        m = lab == a
        print(f"  best action {ACTS[a]:5s}: {int(m.sum()):6d} ({100 * m.mean():.0f}%), "
              f"of them with a gap {int((m & (w > 0)).sum()):6d}, mean gap {w[m & (w > 0)].mean() if (m & (w > 0)).any() else 0:.3f}")
    print(f"weighted fit accuracy on the training decision points: {100 * acc:.1f}%")
    print(json.dumps(pol.to_json()))


if __name__ == "__main__":
    main()

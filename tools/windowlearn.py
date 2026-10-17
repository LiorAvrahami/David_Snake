#!/usr/bin/env python3
"""Trains the turn model on the window score alone: the weights that maximize
the Final score of the training games' windows (docs/window_training.md).

tools/windowtable.py has every course of action in every window scored. The
model (simlearn.MlpPolicy: 17 finger inputs, one hidden layer, none / right /
left, no back) is read as a softmax policy over its three scores at every
decision node, so a window's expected Final score is a sum over its courses of
(probability of the course) x (its score), computed exactly on the tree, and so
is its gradient. Adam climbs the mean over the training windows. The model put
in the game is the argmax; its deterministic score is reported alongside. No
label of any kind: nothing is taken from what the live game did.

Usage: python3 tools/windowlearn.py TABLE.pkl [--holdout FRAC --seed S --never-test FILE,...]
                                    [--hidden H] [--iters N] [--lr R] [--l2 X] [--init-seed S]
                                    [--report N] [--eval MODEL.json,...] [--export]"""
import json, os, pickle, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simlearn as L

HIDDEN = 16
ITERS = 3000
LR = 0.01
L2 = 1e-4
LAYOUT = L.LAYOUT_NOSPEED
SWEEPS = 24             # longest decision chain in a window, with margin


class Table:
    """The windows' trees of a set of games, flattened: node features, each
    node's three edges (child node or -1), the ending edges' scores, the roots."""

    def __init__(self, trees_by_key, keys):
        F, child, final, plain, roots = [], [], [], [], []
        off = 0
        for key in keys:
            for Fw, cw, fw, pw, _ in trees_by_key[key]:
                F.append(Fw.astype(np.float64))
                c = cw.astype(np.int64)
                c[c >= 0] += off
                child.append(c)
                final.append(fw)
                plain.append(pw)
                roots.append(off)
                off += len(Fw)
        self.F = np.concatenate(F)
        self.Fm = self.F.copy()
        self.Fm[:, LAYOUT.side] *= -1
        self.child = np.concatenate(child)
        self.final = np.concatenate(final)
        self.plain = np.concatenate(plain)
        self.roots = np.asarray(roots)
        self.ends = self.child < 0
        self.cidx = np.where(self.ends, 0, self.child)
        self.n = len(self.F)
        self.nw = len(self.roots)

    def logits(self, W1, b1, v):
        Hr = np.maximum(self.F @ W1.T + b1, 0.0)
        Hl = np.maximum(self.Fm @ W1.T + b1, 0.0)
        return np.stack([np.zeros(self.n), Hr @ v, Hl @ v], 1), Hr, Hl

    def values(self, P, leaf):
        """V per node under the policy P (node x 3), with the ending edges' scores leaf."""
        V = np.zeros(self.n)
        for _ in range(SWEEPS):
            Q = np.where(self.ends, leaf, V[self.cidx])
            V = (P * Q).sum(1)
        return V, Q

    def reach(self, P):
        """Probability of reaching each node from its window's root under P."""
        R = np.zeros(self.n)
        for _ in range(SWEEPS):
            Rn = np.zeros(self.n)
            Rn[self.roots] = 1.0
            flow = (R[:, None] * P)[~self.ends]
            np.add.at(Rn, self.child[~self.ends], flow)
            R = Rn
        return R

    def greedy(self, Z):
        """The argmax model's course per window: (Final score, plain score, turned?)."""
        cur = self.roots.copy()
        active = np.ones(self.nw, bool)
        fin, pl, turned = np.zeros(self.nw), np.zeros(self.nw), np.zeros(self.nw, bool)
        for _ in range(SWEEPS):
            a = np.argmax(Z[cur], 1)
            turned |= active & (a > 0)
            nxt = self.child[cur, a]
            done = active & (nxt < 0)
            fin[done] = self.final[cur[done], a[done]]
            pl[done] = self.plain[cur[done], a[done]]
            active &= ~done
            cur = np.where(active, np.maximum(nxt, 0), cur)
            if not active.any():
                break
        return fin, pl, turned


def report(tag, T, Z, P=None):
    fin, pl, turned = T.greedy(Z)
    line = f"{tag}: argmax Final {1000 * fin.mean():7.1f}, plain {1000 * pl.mean():7.1f}, turns in {100 * turned.mean():4.1f}% of windows"
    if P is not None:
        V, _ = T.values(P, T.final)
        R = T.reach(P)
        H = -(P * np.log(P + 1e-12)).sum(1)
        line += f" | expected Final {1000 * V[T.roots].mean():7.1f}, entropy {(R * H).sum() / R.sum():.3f}"
    return line


def main():
    argv = list(sys.argv)
    opt = {"--hidden": str(HIDDEN), "--iters": str(ITERS), "--lr": str(LR), "--l2": str(L2), "--init-seed": "1",
           "--report": "100", "--eval": ""}
    for name in list(opt):
        if name in argv:
            i = argv.index(name)
            opt[name] = argv[i + 1]
            del argv[i:i + 2]
    export = "--export" in argv
    argv = [a for a in argv if a != "--export"]
    args, o = L.options(argv)
    with open(args[0], "rb") as f:
        tab = pickle.load(f)
    keys = sorted(tab["trees"])
    trn, test = L.split(keys, tab["arms"], o["holdout"], o["seed"], o["never_test"])
    T = Table(tab["trees"], trn)
    Tt = Table(tab["trees"], test) if test else None
    hidden, iters, lr, l2 = int(opt["--hidden"]), int(opt["--iters"]), float(opt["--lr"]), float(opt["--l2"])
    every = int(opt["--report"])
    log = (lambda s: None) if export else (lambda s: print(s, flush=True))
    log(f"training games {len(trn)}: {T.nw} windows, {T.n} decision nodes; test games {len(test)}: "
        f"{Tt.nw if Tt else 0} windows. Scores per 1000 windows (Final: tolerance {tab['delta']}, alpha {tab['alpha']}).")
    for path in [x for x in opt["--eval"].split(",") if x]:
        spec = json.load(open(path))
        pol = L.policy_from_spec(spec)
        assert not pol.layout.speed, "the table holds the 17 inputs without the speed"
        W1, b1, v = np.asarray(pol.w1), np.asarray(pol.b1), np.asarray(pol.v)
        for tag, TT in (("train", T), ("test", Tt)):
            if TT is not None:
                log(report(f"  {spec['name']:6s} {tag}", TT, TT.logits(W1, b1, v)[0]))
    rng = np.random.default_rng(int(opt["--init-seed"]))
    nf = LAYOUT.nf
    W1 = rng.normal(0, 1.0 / np.sqrt(nf), (hidden, nf))
    b1 = np.zeros(hidden)
    v = rng.normal(0, 0.1, hidden)
    params = [W1, b1, v]
    ms = [np.zeros_like(x) for x in params]
    vs = [np.zeros_like(x) for x in params]
    for it in range(1, iters + 1):
        Z, Hr, Hl = T.logits(W1, b1, v)
        P = L.softmax(Z)
        V, Q = T.values(P, T.final)
        R = T.reach(P)
        G = (R[:, None] * P * (Q - V[:, None])) / T.nw       # dJ/dZ, J = mean expected Final per window
        dv = Hr.T @ G[:, 1] + Hl.T @ G[:, 2]
        dHr = (G[:, 1][:, None] * v[None, :]) * (Hr > 0)
        dHl = (G[:, 2][:, None] * v[None, :]) * (Hl > 0)
        dW1 = dHr.T @ T.F + dHl.T @ T.Fm
        db1 = dHr.sum(0) + dHl.sum(0)
        grads = [-dW1 + l2 * W1, -db1 + l2 * b1, -dv + l2 * v]  # descent on -J
        for i, (x, g) in enumerate(zip(params, grads)):
            params[i], ms[i], vs[i] = L.adam(x, g, ms[i], vs[i], it, lr=lr)
        W1, b1, v = params
        if it % every == 0 or it == iters or it == 1:
            log(report(f"iter {it:5d} train", T, Z, P))
            if Tt is not None:
                log(report(f"           test ", Tt, Tt.logits(W1, b1, v)[0]))
    pol = L.MlpPolicy(W1.tolist(), b1.tolist(), v.tolist(), None, True, LAYOUT.speed)
    print(json.dumps(pol.to_json()))


if __name__ == "__main__":
    main()

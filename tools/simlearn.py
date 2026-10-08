#!/usr/bin/env python3
"""Trains a small turn model on the game simulation (tools/windowsim.py):
finger movement in, a turn (or none) out, judged by what then happens in
the game (harp +, death -, as windowsim scores a gesture window).

The model: at every game tick while the finger is moving, four scores,

  none = 0, right = w_side . f, left = w_side . mirror(f), back = w_back . sym(f)

and the highest wins (back is a U-turn when there is a tail). f describes
the finger only, relative to David's heading: forward and sideways travel
over the last 40/80/160/300 ms, speed, and the time and travel since the
last turn (or touch-down). mirror(f) swaps David's left and right, so the
model is symmetric; sym(f) keeps only what is the same on both sides.
Deciding once per tick loses nothing: the engine acts on a turn at the
next tick anyway.

Training (learning to search): in every gesture window of the training
games, the model plays the window; at each tick it decided, each of the
four answers is tried in the simulation, the model deciding the rest of
the window as usual. The window scores of the four answers are that
tick's example: the weights are fitted to minimize the expected loss
against the best answer (softmax policy, L2). Repeat with the new model;
examples accumulate. The first model copies the live game's own turns.

Games are split in FOLDS by game; each fold is tested by a model trained
on the others. Reported per 1000 gestures, against S2-FAST on the same
games.

Usage: python3 tools/simlearn.py FILE... [--w 2,3] [--iters N] [--export W]"""
import bisect, json, math, os, random, sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import enginesim as E
import recognizers as R
import windowsim as WS

WIN_MS = (40, 80, 160, 300)
NF = 3 * len(WIN_MS) + 1 + 4 + 1          # features + bias
SIDE = [1, 4, 7, 10, 15]                  # the signed sideways features
ACTIONS = ("none", "right", "left", "back")
FOLDS = 3
L2 = 1e-3


def features(ts, xs, ys, k, lt, h):
    """Finger features at sample k, heading h, last turn at sample lt."""
    fx, fy = R.VEC[h]
    rx, ry = -fy, fx                      # David's right on screen
    t, x, y = ts[k], xs[k], ys[k]
    f = []
    j40 = k
    for w in WIN_MS:
        j = bisect.bisect_left(ts, t - w, 0, k + 1)
        if w == 40:
            j40 = j
        dx, dy = x - xs[j], y - ys[j]
        s = (dx * rx + dy * ry) / 20.0
        f += [(dx * fx + dy * fy) / 20.0, s, abs(s)]
    dt = t - ts[j40]
    f.append(math.hypot(x - xs[j40], y - ys[j40]) * 2.0 / dt if dt > 0 else 0.0)   # per 500 dp/s
    dx, dy = x - xs[lt], y - ys[lt]
    s = (dx * rx + dy * ry) / 40.0
    f += [min(t - ts[lt], 400) / 400.0, (dx * fx + dy * fy) / 40.0, s, abs(s), 1.0]
    return f


def mirror(f):
    g = list(f)
    for i in SIDE:
        g[i] = -g[i]
    return g


def sym(f):
    g = list(f)
    for i in SIDE:
        g[i] = 0.0
    return g


class Policy:
    def __init__(self, ws=None, wb=None):
        self.ws = list(ws) if ws is not None else [0.0] * NF
        self.wb = list(wb) if wb is not None else [0.0] * NF

    def scores(self, f):
        ws, wb = self.ws, self.wb
        r = sum(a * b for a, b in zip(ws, f))
        m = f[:]
        for i in SIDE:
            m[i] = -m[i]
        l = sum(a * b for a, b in zip(ws, m))
        for i in SIDE:
            m[i] = 0.0
        b = sum(a * c for a, c in zip(wb, m))
        return (0.0, r, l, b)

    def decide(self, f):
        s = self.scores(f)
        return max(range(4), key=lambda a: s[a])

    def to_json(self):
        return {"features": NF, "w_side": self.ws, "w_back": self.wb}


def commands(a, h, sim, vec):
    if a == 1:
        return [((h + 1) % 4, False)]
    if a == 2:
        return [((h + 3) % 4, False)]
    if a == 3:
        if sim.has_tail:
            cs = R.u_turn(h, vec[0], vec[1], sim, 3.0)
            return [(cs[0][0], False), (cs[1][0], True)]
        return [((h + 2) % 4, False)]
    return []


class PolicyInputs(WS.Inputs):
    """The model reading the recorded finger samples in a window, deciding
    once per tick on the latest sample. force = {tick: action} overrides
    its decision there; record collects (tick, features, action)."""

    def __init__(self, k0, W, pol, samples, slots, i0, stroke, force=None, record=None):
        super().__init__(k0, W)
        self.pol = pol
        self.samples, self.slots, self.i = samples, slots, i0
        if stroke:
            self.ts, self.xs, self.ys = list(stroke[0]), list(stroke[1]), list(stroke[2])
            self.lt = stroke[3]
        else:
            self.ts = None
        self.force = force or {}
        self.record = record

    def slot(self, k, sim):
        new = up = False
        while self.i < len(self.samples) and self.slots[self.i] <= k:
            t, x, y, first, last, _ = self.samples[self.i][:6]
            if not self.allow(k, t):
                self.done = True
                break
            self.i += 1
            if first:
                self.ts, self.xs, self.ys, self.lt = [t], [x], [y], 0
            elif self.ts is not None:
                self.ts.append(t)
                self.xs.append(x)
                self.ys.append(y)
            else:
                continue
            new = True
            if last:
                up = True
                break
        if not new:
            return
        n = len(self.ts) - 1
        h = sim.heading
        f = features(self.ts, self.xs, self.ys, n, self.lt, h)
        a = self.force.get(k)
        if a is None:
            a = self.pol.decide(f)
        if self.record is not None:
            self.record.append((k, f, a))
        if a:
            j = bisect.bisect_left(self.ts, self.ts[-1] - 160)
            vec = (self.xs[-1] - self.xs[j], self.ys[-1] - self.ys[j])
            if k >= self.end:
                self.done = True
            yield self.ts[-1], commands(a, h, sim, vec)
            if sim.heading != h:
                self.lt = n
        if up:
            self.ts = None


class Spec:
    """One gesture window of a recorded game: the real state at its start."""
    __slots__ = ("k", "real", "i", "stroke")

    def __init__(self, k, real, i, stroke):
        self.k, self.real, self.i, self.stroke = k, real, i, stroke


class Game:
    """A recorded game prepared for training: samples, their ticks, its
    gesture windows, imitation examples, and S2-FAST's window outcomes."""

    def __init__(self, key, p, Ws):
        self.key = key
        self.samples = WS.samples_of(p)
        self.slots = WS.slots_of(p, self.samples)
        times = [s[0] for s in self.samples]
        starts = set()
        for s in p.strokes:
            for t in WS.gesture_starts(s):
                starts.add(self.slots[bisect.bisect_left(times, t)])
        ih = {}
        for s in p.strokes:
            for (t, _, _), c in zip(s.samples, s.ih):
                ih[t] = E.LET.index(c)
        cmds = list(zip(p.cmds, E.held_flags(p.cmds)))
        real = E.start_sim(p)
        last_tk = p.death["tk"] if p.death else max([s["tk"] for s in p.steps] + [0])
        self.specs, self.imitate = [], []
        cur = None                      # current stroke: [ts, xs, ys, last turn]
        i = j = 0
        for k in range(0, last_tk + 1):
            if k in starts:
                st = (list(cur[0]), list(cur[1]), list(cur[2]), cur[3]) if cur else None
                self.specs.append(Spec(k, real.copy(), i, st))
            h0 = real.heading
            f = None                    # features at this tick's latest sample
            while True:
                ns = self.samples[i][0] if i < len(self.samples) and self.slots[i] == k else None
                nc = cmds[j][0]["t"] if j < len(cmds) and cmds[j][0]["tk"] == k else None
                if ns is None and nc is None:
                    break
                if nc is None or (ns is not None and ns <= nc):
                    t, x, y, first, last, _ = self.samples[i]
                    i += 1
                    if first:
                        cur = [[t], [x], [y], 0]
                    elif cur:
                        cur[0].append(t)
                        cur[1].append(x)
                        cur[2].append(y)
                    if cur:
                        f = features(cur[0], cur[1], cur[2], len(cur[0]) - 1, cur[3], h0)
                        if last:
                            cur = None
                else:
                    c, hold = cmds[j]
                    j += 1
                    real.swipe(E.LET.index(c["dir"]), hold)
            if f is not None:           # what the live game did this tick, as an answer
                hd = real.heading
                a = 1 if hd == (h0 + 1) % 4 else 2 if hd == (h0 + 3) % 4 else 3 if hd == (h0 + 2) % 4 else 0
                self.imitate.append((f, a))
            if cur and real.heading != h0:
                cur[3] = len(cur[0]) - 1
            if real.lost:
                break
            real.tick()
            if real.lost:
                break
        base = WS.evaluate_play(p, Ws, models=["S2F"])
        self.base = {W: [w.score for w in base[(W, "S2F")]] for W in Ws}
        self.played = {W: [w.score for w in base[(W, "as played")]] for W in Ws}
        assert all(len(self.base[W]) == len(self.specs) for W in Ws), (key, len(self.specs))


def run(g, spec, W, pol, force=None, record=None):
    inp = PolicyInputs(spec.k, W, pol, g.samples, g.slots, spec.i, spec.stroke, force, record)
    return WS.run_window(spec.real, spec.k, W, inp).score


def examples(g, W, pol):
    """Learning-to-search examples from one game: (features, scores of the
    four answers) at every tick the model decided."""
    out = []
    for spec in g.specs:
        rec = []
        base = run(g, spec, W, pol, record=rec)
        for k, f, a in rec:
            q = [0.0] * 4
            q[a] = base
            for b in range(4):
                if b != a:
                    q[b] = run(g, spec, W, pol, force={k: b})
            if max(q) - min(q) > 1e-9:
                out.append((f, q))
    return out


def evaluate(g, W, pol):
    return [run(g, spec, W, pol) for spec in g.specs]


def mats(F):
    F = np.asarray(F, dtype=float)
    Fm = F.copy()
    Fm[:, SIDE] *= -1
    Fs = F.copy()
    Fs[:, SIDE] = 0
    return F, Fm, Fs


def all_scores(w, F, Fm, Fs):
    ws, wb = w[:NF], w[NF:]
    return np.stack([np.zeros(len(F)), F @ ws, Fm @ ws, Fs @ wb], 1)


def softmax(S):
    S = S - S.max(1, keepdims=True)
    P = np.exp(S)
    return P / P.sum(1, keepdims=True)


def fit_imitation(ex, l2=L2, iters=400):
    """Weighted softmax regression on the live game's own turns."""
    F, Fm, Fs = mats([f for f, _ in ex])
    Y = np.eye(4)[[a for _, a in ex]]
    wt = np.where(Y[:, 0] > 0, 1.0, 3.0)[:, None]
    w = np.zeros(2 * NF)
    m = v = np.zeros_like(w)
    for it in range(1, iters + 1):
        P = softmax(all_scores(w, F, Fm, Fs))
        G = (P - Y) * wt / len(F)
        g = np.concatenate([F.T @ G[:, 1] + Fm.T @ G[:, 2], Fs.T @ G[:, 3]]) + l2 * w
        w, m, v = adam(w, g, m, v, it)
    return Policy(w[:NF], w[NF:])


def fit_regret(ex, w0=None, l2=L2, iters=600):
    """Minimize the expected loss against the best answer: sum over
    examples of sum_a P(a|f) (best - score(a)), softmax P, plus L2."""
    F, Fm, Fs = mats([f for f, _ in ex])
    Q = np.asarray([q for _, q in ex])
    Rg = Q.max(1, keepdims=True) - Q
    w = np.zeros(2 * NF) if w0 is None else np.asarray(w0, dtype=float)
    m = v = np.zeros_like(w)
    for it in range(1, iters + 1):
        P = softmax(all_scores(w, F, Fm, Fs))
        L = (P * Rg).sum(1, keepdims=True)
        G = P * (Rg - L) / len(F)
        g = np.concatenate([F.T @ G[:, 1] + Fm.T @ G[:, 2], Fs.T @ G[:, 3]]) + l2 * w
        w, m, v = adam(w, g, m, v, it)
    return Policy(w[:NF], w[NF:])


def adam(w, g, m, v, it, lr=0.05, b1=0.9, b2=0.999):
    m = b1 * m + (1 - b1) * g
    v = b2 * v + (1 - b2) * g * g
    w = w - lr * (m / (1 - b1 ** it)) / (np.sqrt(v / (1 - b2 ** it)) + 1e-8)
    return w, m, v


def per1000(xs):
    return 1000.0 * sum(xs) / max(1, len(xs))


# --- parallel helpers (games live in each worker)
GAMES = {}


def _init(paths, Ws):
    for path in paths:
        _, plays = build(load(path))
        for p in plays:
            if p.spears and not p.aborted:
                key = (os.path.basename(path), p.n)
                GAMES[key] = Game(key, p, Ws)


def _examples(args):
    key, W, w = args
    return examples(GAMES[key], W, Policy(w[:NF], w[NF:]))


def _evaluate(args):
    key, W, w = args
    return key, evaluate(GAMES[key], W, Policy(w[:NF], w[NF:]))


def _info(key):
    g = GAMES[key]
    return key, g.imitate, g.base, g.played


def wvec(pol):
    return list(pol.ws) + list(pol.wb)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = {}
    for name in ("--w", "--iters", "--export"):
        if name in sys.argv:
            val = sys.argv[sys.argv.index(name) + 1]
            opt[name] = val
            args = [a for a in args if a != val]
    Ws = [int(x) for x in opt.get("--w", "2,3").split(",")]
    iters = int(opt.get("--iters", "4"))
    keys = []
    for path in args:
        _, plays = build(load(path))
        keys += [(os.path.basename(path), p.n) for p in plays if p.spears and not p.aborted]
    with Pool(os.cpu_count(), initializer=_init, initargs=(args, Ws)) as pool:
        info = {k: (im, b, pl) for k, im, b, pl in pool.map(_info, keys)}
        if "--export" in opt:
            W = int(opt["--export"])
            pol, _ = train(pool, keys, W, iters, info, None)
            print(json.dumps(pol.to_json()))
            return
        for W in Ws:
            print(f"\n== W = {W} steps of input: {len(keys)} games, {FOLDS} folds by game", flush=True)
            res = []
            for fold in range(FOLDS):
                test = [k for i, k in enumerate(keys) if i % FOLDS == fold]
                trn = [k for k in keys if k not in test]
                print(f"  fold {fold + 1}: train {len(trn)} games, test {len(test)}", flush=True)
                res.append(train(pool, trn, W, iters, info, test)[1])
            print(f"  all folds (per 1000 gestures, vs S2-FAST on the same games; test pools every game once):")
            for it in range(iters + 1):
                tr = sum(r[it][0] for r in res) / FOLDS
                te_model = sum(r[it][2] for r in res)
                te_base = sum(r[it][3] for r in res)
                n = sum(r[it][4] for r in res)
                print(f"    {'copy of live' if it == 0 else f'model {it}':13s} train gain {tr:+6.1f}   "
                      f"test gain {1000 * (te_model - te_base) / n:+6.1f}", flush=True)


def train(pool, trn, W, iters, info, test):
    """Returns the final model and, per round, (train gain, test sums...)."""
    pol = fit_imitation([e for k in trn for e in info[k][0]])
    data, rounds = [], []
    for it in range(iters + 1):
        if test is not None:
            rounds.append(report(pool, trn, test, W, pol, info, "copy of live" if it == 0 else f"model {it}"))
        if it == iters:
            break
        res = pool.map(_examples, [(k, W, wvec(pol)) for k in trn])
        data += [e for r in res for e in r]
        pol = fit_regret(data, wvec(pol))
    return pol, rounds


def report(pool, trn, test, W, pol, info, name):
    sc = dict(pool.map(_evaluate, [(k, W, wvec(pol)) for k in trn + test]))
    def total(ks, src):
        if src == "model":
            return [x for k in ks for x in sc[k]]
        return [x for k in ks for x in info[k][1][W]]
    tr, btr = total(trn, "model"), total(trn, "s2f")
    te, bte = total(test, "model"), total(test, "s2f")
    gtr = per1000(tr) - per1000(btr)
    gte = per1000(te) - per1000(bte)
    print(f"    {name:13s} train {per1000(tr):6.1f} ({gtr:+6.1f} vs S2-FAST)   "
          f"test {per1000(te):6.1f} ({gte:+6.1f})", flush=True)
    return gtr, gte, sum(te), sum(bte), len(te)


if __name__ == "__main__":
    main()

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

Games are split by game into training and test games: a seeded random
share (--holdout) of the games is held out, drawn only from files named
in --never-test's complement (e.g. the files an earlier model was trained
on stay in training, so the test games are new to it too), the same share
from each input method played. Reported per 1000 gestures, against
S2-FAST on the same games. The last line printed is the trained model's
weights (JSON), trained on the training games only.

Usage: python3 tools/simlearn.py FILE... [--iters N] [--holdout FRAC] [--seed S]
                                 [--never-test FILE,FILE...] [--tolerance T] [--alpha A]
                                 [--init MODEL.json] [--export]
  --tolerance T  learn from the Final score: the window's turns moved -T..T
                 ticks together, the best counting --alpha A (default 0.9),
                 the plain run 1 - A (tools/windowsim.py run_window_tolerant)
  --init         start from a trained model instead of copying the live game
  --export       train and print the weights only (no per-round test report)"""
import bisect, hashlib, json, math, os, random, sys
from collections import defaultdict
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


class Layout:
    """Which inputs a model reads: the 18 of ML-1, or 17 without the speed."""
    __slots__ = ("nf", "side", "speed")

    def __init__(self, speed=True):
        self.speed = speed
        self.nf = NF if speed else NF - 1
        self.side = SIDE if speed else [1, 4, 7, 10, 14]


LAYOUT_SPEED, LAYOUT_NOSPEED = Layout(True), Layout(False)
ACTIONS = ("none", "right", "left", "back")
L2 = 1e-3


def features(ts, xs, ys, k, lt, h, speed=True):
    """Finger features at sample k, heading h, last turn at sample lt (without
    the speed input when speed is False)."""
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
    if speed:
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
    """The linear turn model: none = 0, right = ws . f, left = ws . mirror(f),
    back = wb . sym(f) (never, with no_back); the highest wins."""

    layout = LAYOUT_SPEED

    def __init__(self, ws=None, wb=None, no_back=False):
        self.ws = list(ws) if ws is not None else [0.0] * NF
        self.wb = list(wb) if wb is not None else [0.0] * NF
        self.no_back = no_back

    def scores(self, f):
        ws, wb = self.ws, self.wb
        r = sum(a * b for a, b in zip(ws, f))
        m = f[:]
        for i in SIDE:
            m[i] = -m[i]
        l = sum(a * b for a, b in zip(ws, m))
        for i in SIDE:
            m[i] = 0.0
        b = float("-inf") if self.no_back else sum(a * c for a, c in zip(wb, m))
        return (0.0, r, l, b)

    def decide(self, f):
        s = self.scores(f)
        return max(range(4), key=lambda a: s[a])

    def to_json(self):
        d = {"features": NF, "w_side": self.ws, "w_back": self.wb}
        if self.no_back:
            d["no_back"] = True
        return d

    @staticmethod
    def from_spec(spec):
        """The model a game logged (its model line's recognizer spec, as
        tools/export_model.py writes it), checked against this code."""
        ws, wb = spec["w_side"], spec["w_back"]
        sha = hashlib.sha256(json.dumps({"w_side": ws, "w_back": wb}, separators=(",", ":")).encode()).hexdigest()
        assert sha == spec["weights_sha256"], "weights do not match their hash"
        assert len(ws) == len(wb) == NF and spec["side_features"] == SIDE, "different features"
        assert len(spec["features"]) == NF and all(f"over the last {w} ms" in " ".join(spec["features"]) for w in WIN_MS)
        return Policy(ws, wb, bool(spec.get("no_back", False)))


def relu(x):
    return x if x > 0 else 0.0


class MlpPolicy:
    """The non-linear turn model: one hidden layer of H units, h(f) = relu(W1 f
    + b1); none = 0, right = v . h(f), left = v . h(mirror(f)), and, unless
    no_back, back = vb . h(sym(f)); the highest wins. The same hidden layer
    serves both sides, so left and right stay mirror images. speed False:
    the 17 inputs without the finger speed."""

    def __init__(self, w1, b1, v, vb=None, no_back=True, speed=False):
        self.w1 = [list(r) for r in w1]
        self.b1, self.v = list(b1), list(v)
        self.vb = None if no_back else list(vb)
        self.no_back = no_back
        self.layout = LAYOUT_SPEED if speed else LAYOUT_NOSPEED

    @property
    def hidden(self):
        return len(self.b1)

    @property
    def n_weights(self):
        return sum(len(r) for r in self.w1) + len(self.b1) + len(self.v) + (0 if self.no_back else len(self.vb))

    def _hidden(self, f):
        return [relu(sum(a * b for a, b in zip(row, f)) + c) for row, c in zip(self.w1, self.b1)]

    def scores(self, f):
        r = sum(a * b for a, b in zip(self.v, self._hidden(f)))
        m = f[:]
        for i in self.layout.side:
            m[i] = -m[i]
        l = sum(a * b for a, b in zip(self.v, self._hidden(m)))
        if self.no_back:
            return (0.0, r, l, float("-inf"))
        for i in self.layout.side:
            m[i] = 0.0
        b = sum(a * c for a, c in zip(self.vb, self._hidden(m)))
        return (0.0, r, l, b)

    def decide(self, f):
        s = self.scores(f)
        return max(range(4), key=lambda a: s[a])

    def weights(self):
        w = {"w1": self.w1, "b1": self.b1, "v": self.v}
        if not self.no_back:
            w["vb"] = self.vb
        return w

    def to_json(self):
        d = {"type": "mlp", "features": self.layout.nf, "hidden": self.hidden, "no_back": self.no_back,
             "speed": self.layout.speed}
        d.update(self.weights())
        return d

    @staticmethod
    def from_spec(spec):
        no_back, speed = bool(spec.get("no_back", False)), bool(spec.get("speed", True))
        layout = LAYOUT_SPEED if speed else LAYOUT_NOSPEED
        w = {k: spec[k] for k in (("w1", "b1", "v") if no_back else ("w1", "b1", "v", "vb"))}
        sha = hashlib.sha256(json.dumps(w, separators=(",", ":")).encode()).hexdigest()
        assert sha == spec["weights_sha256"], "weights do not match their hash"
        assert all(len(r) == layout.nf for r in w["w1"]) and spec["side_features"] == layout.side, "different features"
        assert len(spec["features"]) == layout.nf and all(f"over the last {x} ms" in " ".join(spec["features"]) for x in WIN_MS)
        return MlpPolicy(w["w1"], w["b1"], w["v"], w.get("vb"), no_back, speed)


def policy_from_json(d):
    """A policy from its to_json() dict (what the pool workers receive)."""
    if "w1" in d:
        return MlpPolicy(d["w1"], d["b1"], d["v"], d.get("vb"), bool(d.get("no_back", False)), bool(d.get("speed", True)))
    return Policy(d["w_side"], d["w_back"], bool(d.get("no_back", False)))


def policy_from_spec(spec):
    """A policy from a model description (tools/models/*.json or a game's
    model line), its weights checked against their hash."""
    return MlpPolicy.from_spec(spec) if "w1" in spec else Policy.from_spec(spec)


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

    def __init__(self, t0, pol, samples, slots, i0, stroke, force=None, record=None):
        super().__init__(t0)
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
            if not self.allow(t):
                if not new:
                    self.done = True
                break                       # else asked again next tick, after this tick's decision
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
        f = features(self.ts, self.xs, self.ys, n, self.lt, h, self.pol.layout.speed)
        a = self.force.get(k)
        if a is None:
            a = self.pol.decide(f)
        if self.record is not None:
            self.record.append((k, f, a, h, self.ts[-1]))
        if a:
            j = bisect.bisect_left(self.ts, self.ts[-1] - 160)
            vec = (self.xs[-1] - self.xs[j], self.ys[-1] - self.ys[j])
            if self.finishing(self.ts[-1]):
                self.done = True
            yield self.ts[-1], commands(a, h, sim, vec)
            if sim.heading != h:
                self.lt = n
        if up:
            self.ts = None


PRE_TICKS = 4           # real states kept before each window, for turns placed earlier


class Spec:
    """One gesture window of a recorded game: the real state at its start,
    the slot of the live game's first command in it (anchor), if any, and
    prev = [(slot, the real state at that slot, the live game's commands in
    it), ...] for the PRE_TICKS slots before the start, nearest first, for
    a turn placed before the window."""
    __slots__ = ("k", "t0", "real", "i", "stroke", "anchor", "prev")

    def __init__(self, k, t0, real, i, stroke, prev=()):
        self.k, self.t0, self.real, self.i, self.stroke = k, t0, real, i, stroke
        self.anchor = None
        self.prev = prev


class Game:
    """A recorded game prepared for training: samples, their ticks, its
    gesture windows, imitation examples, and S2-FAST's window outcomes."""

    def __init__(self, key, p):
        self.key = key
        self.arm = p.arm
        self.samples = WS.samples_of(p)
        self.slots = WS.slots_of(p, self.samples)
        starts = dict(WS.window_starts(p, self.samples, self.slots))
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
        recent = []                     # (slot, the real state at its start, its live commands), newest last
        for k in range(0, last_tk + 1):
            if k in starts:
                st = (list(cur[0]), list(cur[1]), list(cur[2]), cur[3]) if cur else None
                self.specs.append(Spec(k, starts[k], real.copy(), i, st, tuple(reversed(recent))))
            recent.append((k, real.copy(), [(E.LET.index(c["dir"]), h) for c, h in cmds if c["tk"] == k]))
            del recent[:-PRE_TICKS]
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
        cmd_tk = [c["tk"] for c, _ in cmds]
        for spec in self.specs:                     # the live game's first command in each window
            rec = WS.Recorded(WS.LogInputs(spec.t0, cmds, bisect.bisect_left(cmd_tk, spec.k)))
            WS.run_window(spec.real, spec.k, rec)
            spec.anchor = rec.log[0][0] if rec.log else None
        base = WS.evaluate_play(p, models=["S2F"])
        self.base_w = base["S2F"]                   # S2-FAST's windows (outcome by cause)
        self.base = [w.score for w in self.base_w]
        self.played = [w.score for w in base["as played"]]
        assert len(self.base) == len(self.specs), (key, len(self.specs))


TOLERANCE = 0           # ticks of timing tolerance in the training score (0: plain): placements -T..T
ALPHA = 0.9             # the tolerant score: ALPHA * the best placement + (1 - ALPHA) * the plain run
ANCHORED = False        # placements around the live game's turn instead of the model's own


def run_w(g, spec, pol, force=None, record=None):
    """The window's outcome with the model reading it."""
    inp = PolicyInputs(spec.t0, pol, g.samples, g.slots, spec.i, spec.stroke, force, record)
    return WS.run_window(spec.real, spec.k, inp)


def run(g, spec, pol, force=None, record=None, tol=None):
    """The window's score; with tolerance, the best of the turns moved by
    -tol..tol ticks together (windowsim.run_window_tolerant)."""
    tol = TOLERANCE if tol is None else tol
    if not tol:
        return run_w(g, spec, pol, force, record).score
    inp = PolicyInputs(spec.t0, pol, g.samples, g.slots, spec.i, spec.stroke, force, record)
    plain, outs = WS.run_window_tolerant(spec.real, spec.k, spec.t0, inp, tuple(range(-tol, tol + 1)),
                                         spec.anchor if ANCHORED else None, prev=spec.prev)
    return WS.tolerant_score(plain, outs, ALPHA)[0]


def examples(g, pol):
    """Learning-to-search examples from one game: (features, scores of the
    four answers) at every tick the model decided."""
    out = []
    for spec in g.specs:
        rec = []
        base = run(g, spec, pol, record=rec)
        for k, f, a, *_ in rec:
            q = [0.0] * 4
            q[a] = base
            for b in range(4):
                if b != a:
                    q[b] = run(g, spec, pol, force={k: b})
            if any(math.isnan(x) for x in q):
                continue                            # an answer without a score: no example
            if max(q) - min(q) > 1e-9:
                out.append((f, q))
    return out


def evaluate(g, pol):
    return [run(g, spec, pol) for spec in g.specs]


def mats(F, side=SIDE):
    F = np.asarray(F, dtype=float)
    Fm = F.copy()
    Fm[:, side] *= -1
    Fs = F.copy()
    Fs[:, side] = 0
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
    xs = [x for x in xs if not math.isnan(x)]       # windows without a score are left out
    return 1000.0 * sum(xs) / max(1, len(xs))


# --- parallel helpers (games live in each worker)
GAMES = {}


def _init(paths, tolerance=0, alpha=ALPHA):
    global TOLERANCE, ALPHA
    TOLERANCE, ALPHA = tolerance, alpha
    for path in paths:
        _, plays = build(load(path))
        for p in plays:
            if p.spears and not p.aborted:
                key = (os.path.basename(path), p.n)
                GAMES[key] = Game(key, p)


def _examples(args):
    key, d = args
    return examples(GAMES[key], policy_from_json(d))


def _evaluate(args):
    key, d = args
    return key, evaluate(GAMES[key], policy_from_json(d))


def _info(key):
    g = GAMES[key]
    return key, g.imitate, g.base, g.played, g.arm


def wvec(pol):
    return list(pol.ws) + list(pol.wb)


def split(keys, arms, holdout, seed, never_test):
    """(training games, test games): a seeded random share `holdout` of all
    games, drawn from files not in never_test, the same share from each
    input method played (arm)."""
    if not holdout:
        return list(keys), []
    pool = sorted(k for k in keys if k[0] not in never_test)
    n_test = round(holdout * len(keys))
    by_arm = defaultdict(list)
    for k in pool:
        by_arm[arms[k]].append(k)
    rnd = random.Random(seed)
    test, left = [], n_test
    groups = sorted(by_arm.items())
    for i, (arm, ks) in enumerate(groups):
        rnd.shuffle(ks)
        rest = sum(len(g) for _, g in groups[i + 1:])
        take = left if i == len(groups) - 1 else min(left, round(n_test * len(ks) / len(pool)))
        take = max(take, left - rest)
        test += ks[:take]
        left -= take
    test = set(test)
    return [k for k in keys if k not in test], [k for k in keys if k in test]


def options(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    opt = {}
    for name in ("--iters", "--holdout", "--seed", "--never-test", "--tolerance", "--alpha", "--init"):
        if name in argv:
            val = argv[argv.index(name) + 1]
            opt[name] = val
            args = [a for a in args if a != val]
    return args, {"iters": int(opt.get("--iters", "4")), "holdout": float(opt.get("--holdout", "0")),
                  "seed": int(opt.get("--seed", "1")),
                  "never_test": set(x for x in opt.get("--never-test", "").split(",") if x),
                  "tolerance": int(opt.get("--tolerance", "0")), "alpha": float(opt.get("--alpha", str(ALPHA))),
                  "init": opt.get("--init"),
                  "export": "--export" in argv}


def main():
    global TOLERANCE, ALPHA
    args, o = options(sys.argv)
    TOLERANCE, ALPHA = o["tolerance"], o["alpha"]
    init = policy_from_spec(json.load(open(o["init"]))) if o["init"] else None
    keys = []
    for path in args:
        _, plays = build(load(path))
        keys += [(os.path.basename(path), p.n) for p in plays if p.spears and not p.aborted]
    with Pool(os.cpu_count(), initializer=_init, initargs=(args, TOLERANCE, ALPHA)) as pool:
        info = {k: (im, b, pl, arm) for k, im, b, pl, arm in pool.map(_info, keys)}
        trn, test = split(keys, {k: info[k][3] for k in keys}, o["holdout"], o["seed"], o["never_test"])
        if o["export"]:
            pol, _ = train(pool, trn, o["iters"], info, None, init)
            print(json.dumps(pol.to_json()))
            return
        print(f"{len(keys)} games: {len(trn)} for training, {len(test)} for testing", flush=True)
        pol, _ = train(pool, trn, o["iters"], info, test, init)
        print(json.dumps(pol.to_json()))


def train(pool, trn, iters, info, test, init=None):
    """Returns the final model and, per round, (train gain, test gain). The
    first model copies the live game's turns, or is `init`."""
    pol = init or fit_imitation([e for k in trn for e in info[k][0]])
    data, rounds = [], []
    for it in range(iters + 1):
        if test is not None:
            rounds.append(report(pool, trn, test, pol, info, "copy of live" if it == 0 else f"model {it}"))
        if it == iters:
            break
        assert isinstance(pol, Policy), "learning to search fits the linear model only"
        res = pool.map(_examples, [(k, pol.to_json()) for k in trn])
        data += [e for r in res for e in r]
        pol = fit_regret(data, wvec(pol))
    return pol, rounds


def report(pool, trn, test, pol, info, name):
    sc = dict(pool.map(_evaluate, [(k, pol.to_json()) for k in trn + test]))
    def total(ks, src):
        if src == "model":
            return [x for k in ks for x in sc[k]]
        return [x for k in ks for x in info[k][1]]
    tr, btr = total(trn, "model"), total(trn, "s2f")
    te, bte = total(test, "model"), total(test, "s2f")
    gtr = per1000(tr) - per1000(btr)
    gte = per1000(te) - per1000(bte) if te else float("nan")
    print(f"    {name:13s} train {per1000(tr):6.1f} ({gtr:+6.1f} vs S2-FAST)   "
          f"test {per1000(te):6.1f} ({gte:+6.1f})", flush=True)
    return gtr, gte


if __name__ == "__main__":
    main()

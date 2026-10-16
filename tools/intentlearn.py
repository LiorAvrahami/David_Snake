#!/usr/bin/env python3
"""Trains the turn model (tools/simlearn.py's Policy and features) to answer
each gesture with the turn the player meant, as early as the finger shows
it, instead of learning the timing from the window score.

Why: the window score (tools/windowsim.py) is calibrated to the live
recognizer's latency. The player timed every swipe for the recognizer that
was playing, so a model that turns one tick earlier OR later lands David in
another cell and loses about 3% of harps either way (measured). The score
can judge directions and mistakes, but not teach timing; learning timing
from it only copies the live recognizer plus noise.

The oracle: in every gesture window the live game's turns (which agree with
the finger's own direction in all but 1 of ~5000 gestures) give the
intended screen directions, each asked for by a finger movement. At any
tick the right answer is the first intended direction not yet reached,
relative to David's heading now, from the moment that movement is clear:
when the gesture detector counts it (GESTURE_DP of travel at V_MOVE); a
second turn answering the same movement is asked from the moment the first
was clear and is clear at the live turn's time. Between a movement's start
and the moment it is clear the answer is unknown (no example);
otherwise it is none (before the movement, once David heads the intended
way, when nothing is asked). A wrong turn is corrected the same way. A
target not reached within TIMEOUT_MS of its movement's start is dropped.

Training (DAgger): examples are (features, oracle answer) at every tick of
the live games (round 0: the finger resting or not), then at every tick
the model itself decides while it plays the windows; the softmax policy is
refitted on everything so far each round, turns weighted W_TURN against
none (the speed/mistake trade-off: a higher weight turns sooner).

Evaluation per window, the model playing it: the window score, latency
(ticks from the window start to the first intended turn), wrong first
turn, missed turn, extra turns (the oracle said none); and, over the whole
game, turns the model would start where the oracle says none (open loop,
on the live game). The same for S2-FAST and the live game.

Usage: python3 tools/intentlearn.py FILE... [--folds N | --holdout FRAC --seed S --never-test F,..]
                                    [--rounds N] [--w-turn W] [--w-reject W] [--w-hold W]
                                    [--model linear|mlp] [--hidden H] [--no-back]
                                    [--eval MODEL.json,...] [--save ROWS.pkl] [--export]
  --model mlp   the one-hidden-layer model (simlearn.MlpPolicy, H units): no back
                answer, no speed input (the MNL models)
  --no-back     the linear model without the 'back' answer"""
import bisect, json, os, random, sys
from collections import defaultdict
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import enginesim as E
import recognizers as R
import simlearn as L
import windowsim as WS

TIMEOUT_MS = 500
MODEL = "linear"        # or "mlp": one hidden layer of HIDDEN units (simlearn.MlpPolicy), no back
HIDDEN = 16             # answer and no speed input (the MNL models)
NO_BACK = False         # no 'back' answer: a U-turn is two side turns
LAYOUT = L.LAYOUT_SPEED # the inputs of the model being trained
W_TURN = 3.0            # weight of turn answers against none
W_REJECT = 1.0          # weight of none answers during a movement the live game did not answer
W_HOLD = 1.0            # weight of none answers within HOLD_MS after a turn
HOLD_MS = 400
ROUNDS = 3
TURN_OF = {0: 0, 1: 1, 3: 2, 2: 3}      # (D - h) % 4 -> action: none, right, left, back


def relative(d, h):
    return TURN_OF[(d - h) % 4]


def gesture_spans(stroke):
    """Per gesture of a stroke (tools/windowsim.py gesture_starts' rule): (start
    time, the time the movement is clear: GESTURE_DP of travel with a peak speed
    of V_MOVE)."""
    import math
    sm = stroke.samples
    out, j, moving = [], 0, False
    for i, (t, x, y) in enumerate(sm):
        while sm[j][0] < t - WS.SPEED_MS:
            j += 1
        dt = t - sm[j][0]
        vx = (x - sm[j][1]) * 1000.0 / dt if dt > 0 else 0.0
        vy = (y - sm[j][2]) * 1000.0 / dt if dt > 0 else 0.0
        v = math.hypot(vx, vy)
        if not moving:
            if v >= WS.V_REST:
                moving, start, peak, ref, counted, corner = True, max(i - 1, 0), v, None, False, None
            continue
        if v < WS.V_REST:
            moving = False
            continue
        peak = max(peak, v)
        _, sx, sy = sm[start]
        if not counted and peak >= WS.V_MOVE and math.hypot(x - sx, y - sy) >= WS.GESTURE_DP:
            counted = True
            out.append((sm[start][0], t))
        if ref is None:
            if math.hypot(x - sx, y - sy) >= 8.0:
                ref = (x - sx, y - sy)
            continue
        if corner is None or v < corner[0]:
            corner = (v, i)
        cross = ref[0] * vy - ref[1] * vx
        dot = ref[0] * vx + ref[1] * vy
        if v >= WS.V_MOVE and abs(math.degrees(math.atan2(cross, dot))) >= WS.SPLIT_DEG:
            start, peak, ref, counted, corner = corner[1], v, None, False, None
    return out


UNKNOWN = -1


class Oracle:
    """The intended turns of one window (or game), consumed in time order."""
    __slots__ = ("targets", "p", "rejected", "r")

    def __init__(self, targets, rejected=()):
        self.targets = targets          # [(t_move, t_clear, screen dir)]
        self.p = 0
        self.rejected = rejected        # [(t_clear, t_end)] movements the live game did not answer
        self.r = 0

    def is_rejected(self, t):
        """Is t inside a movement the live game did not answer (after it was clear)?"""
        while self.r < len(self.rejected) and t > self.rejected[self.r][1]:
            self.r += 1
        return self.r < len(self.rejected) and self.rejected[self.r][0] <= t <= self.rejected[self.r][1]

    def answer(self, t, h):
        """The right action at time t with heading h (UNKNOWN between a movement's
        start and the moment it is clear). Call in time order."""
        T = self.targets
        while self.p < len(T) and t >= T[self.p][0] and h == T[self.p][2]:
            self.p += 1                 # reached
        while self.p < len(T) and t > T[self.p][0] + TIMEOUT_MS:
            self.p += 1                 # given up
        if self.p < len(T) and t >= T[self.p][0]:
            return relative(T[self.p][2], h) if t >= T[self.p][1] else UNKNOWN
        return 0


class IGame(L.Game):
    """A game with, per window, its intended turns (targets) and S2-FAST's
    state at its start, and the live game's features, heading and time at
    every tick."""

    def __init__(self, key, p):
        super().__init__(key, p)
        self.cmds = list(zip(p.cmds, E.held_flags(p.cmds)))
        self.cmd_tk = [c["tk"] for c, _ in self.cmds]
        self.targets, self.rejected = [], []
        spans = sorted(sp for s in p.strokes for sp in gesture_spans(s))
        for spec in self.specs:
            subs = [sp for sp in spans if spec.t0 <= sp[0] < spec.t0 + WS.JOIN_MS]   # the window's movements
            turns = []                  # the live game's turns in the window: (time, heading after)

            def watch(ev, sim, k, t, h0, turns=turns):
                if ev == "cmd" and sim.heading != h0:
                    turns.append((t, sim.heading))
            WS.run_window(spec.real, spec.k, WS.LogInputs(spec.t0, self.cmds, bisect.bisect_left(self.cmd_tk, spec.k)), watch)
            targets, seen = [], {}
            sub_t = [sp[0] for sp in subs]
            answered = set()
            for t, hd in turns:
                i = max(0, bisect.bisect_right(sub_t, t) - 1)      # the movement this turn answers
                answered.add(i)
                n = seen.get(i, 0)
                if n == 0:
                    targets.append((subs[i][0], min(subs[i][1], t), hd))
                else:                   # its 2nd turn: asked from when the 1st was clear, clear at the live turn
                    targets.append((targets[-1][1], t, hd))
                seen[i] = n + 1
            self.targets.append(targets)
            self.rejected.append([(sp[1], sp[1] + 300) for i, sp in enumerate(subs) if i not in answered])
        # the live game tick by tick: (sample times, xs, ys, latest index, last-turn index, heading, latest time);
        # S2-FAST's shadow state at window starts
        self.live, self.shadow = [], {}
        starts = {spec.k for spec in self.specs}
        real = E.start_sim(p)
        cls, shadow = R.ALL["S2F"], None
        last_tk = p.death["tk"] if p.death else max([s["tk"] for s in p.steps] + [0])
        cur, i, j = None, 0, 0
        for k in range(0, last_tk + 1):
            if k in starts:
                self.shadow[k] = WS.clone(shadow)
            h0 = real.heading
            f = None
            while True:
                ns = self.samples[i][0] if i < len(self.samples) and self.slots[i] == k else None
                nc = self.cmds[j][0]["t"] if j < len(self.cmds) and self.cmds[j][0]["tk"] == k else None
                if ns is None and nc is None:
                    break
                if nc is None or (ns is not None and ns <= nc):
                    smp = self.samples[i]
                    t, x, y, first, last, _ = smp
                    i += 1
                    shadow, _ = WS.feed(cls, shadow, smp, real)
                    if first:
                        cur = [[t], [x], [y], 0]
                    elif cur:
                        cur[0].append(t)
                        cur[1].append(x)
                        cur[2].append(y)
                    if cur:
                        f = (cur[0], cur[1], cur[2], len(cur[0]) - 1, cur[3], h0, t)
                        if last:
                            cur = None
                else:
                    c, hold = self.cmds[j]
                    j += 1
                    real.swipe(E.LET.index(c["dir"]), hold)
            if f is not None:
                self.live.append(f)
            if cur and real.heading != h0:
                cur[3] = len(cur[0]) - 1
            if real.lost:
                break
            real.tick()
            if real.lost:
                break
        self.minutes = last_tk * 45 / 60000.0

    def live_features(self, layout):
        """The live game tick by tick as (features of the layout, heading, latest
        sample time)."""
        for ts, xs, ys, k, lt, h0, t in self.live:
            yield L.features(ts, xs, ys, k, lt, h0, layout.speed), h0, t


def weight(lab, t, f, orc, w_reject, w_hold):
    """The example's weight: turns W_TURN (applied in fit); none answers during a
    rejected movement w_reject, within HOLD_MS after a turn w_hold."""
    if lab != 0:
        return 1.0
    w = w_reject if orc.is_rejected(t) else 1.0
    if f[-5] * 400.0 < HOLD_MS:                  # the 'since the last turn' feature, ms / 400
        w = max(w, w_hold)
    return w


def live_examples(g, w_reject=W_REJECT, w_hold=W_HOLD):
    """Oracle answers along the live game (all its targets in time order)."""
    orc = Oracle(sorted(tg for ts in g.targets for tg in ts), sorted(r for rs in g.rejected for r in rs))
    out = []
    for f, h, t in g.live_features(LAYOUT):
        lab = orc.answer(t, h)
        if lab != UNKNOWN and not (NO_BACK and lab == 3):
            out.append((f, lab, weight(lab, t, f, orc, w_reject, w_hold)))
    return out


TOL = 1                 # ticks of timing tolerance in the final score
ALPHA = 0.9             # the final score: ALPHA * the best placement + (1 - ALPHA) * the plain run


def traced(g, spec, inputs):
    """Run a window; returns (outcome, turns [(tick from the start, heading after,
    time)], final score: the best of the turns moved -TOL..TOL ticks from where
    the model made them times ALPHA, plus the plain run times 1 - ALPHA)."""
    turns = []

    def watch(ev, sim, k, t, h0):
        if ev == "cmd" and sim.heading != h0:
            turns.append((k - spec.k, sim.heading, t))
    plain, outs = WS.run_window_tolerant(spec.real, spec.k, spec.t0, inputs, tuple(range(-TOL, TOL + 1)),
                                         None, watch, spec.prev)
    return plain, turns, WS.tolerant_score(plain, outs, ALPHA)[0]


def rollout(g, spec, pol, targets, rejected=(), w_reject=W_REJECT, w_hold=W_HOLD):
    """The model plays the window: (outcome, examples, turns, extra turns as
    (spurious: after every target was reached or none was asked, other: before a
    later target's movement))."""
    rec = []
    inp = L.PolicyInputs(spec.t0, pol, g.samples, g.slots, spec.i, spec.stroke, record=rec)
    o, turns, acc = traced(g, spec, inp)
    o.acc = acc
    orc = Oracle(list(targets), list(rejected))
    turned_at = {k for k, _, _ in turns}
    ex, spurious, other = [], 0, 0
    for k, f, a, h, t in rec:
        lab = orc.answer(t, h)
        if lab != UNKNOWN and not (NO_BACK and lab == 3):
            ex.append((f, lab, weight(lab, t, f, orc, w_reject, w_hold)))
        if lab == 0 and k - spec.k in turned_at:
            if orc.p >= len(targets):
                spurious += 1
            else:
                other += 1
    return o, ex, turns, (spurious, other)


def metrics(targets, turns):
    """(latency ticks or None, wrong first turn, missed) for one window."""
    if not targets:
        return None, False, False
    want = targets[0][2]
    for k, hd, _ in turns:
        return (k, False, False) if hd == want else (None, True, False)
    return None, False, True


def row(o, targets, turns, extra):
    lat, wrong, missed = metrics(targets, turns)
    return (o.harp, o.death, o.why, lat, wrong, missed, extra[0], extra[1], o.acc)


def fit(ex, w_turn=W_TURN, w0=None, l2=L.L2, iters=400):
    """Fits the model (MODEL: linear or mlp) to weighted examples (features,
    answer, weight), warm-started from w0 (a policy)."""
    if MODEL == "mlp":
        return fit_mlp(ex, w_turn, w0, l2, iters)
    assert LAYOUT.speed, "the linear model reads all 18 inputs"
    F, Fm, Fs = L.mats([f for f, _, _ in ex])
    Y = np.eye(4)[[a for _, a, _ in ex]]
    wt = (np.where(Y[:, 0] > 0, 1.0, w_turn) * np.asarray([w for _, _, w in ex]))[:, None]
    w = np.zeros(2 * L.NF) if w0 is None else np.asarray(L.wvec(w0), dtype=float)
    m = v = np.zeros_like(w)
    for it in range(1, iters + 1):
        P = L.softmax(L.all_scores(w, F, Fm, Fs))
        G = (P - Y) * wt / len(F)
        g = np.concatenate([F.T @ G[:, 1] + Fm.T @ G[:, 2], Fs.T @ G[:, 3]]) + l2 * w
        w, m, v = L.adam(w, g, m, v, it)
    return L.Policy(w[:L.NF], w[L.NF:], NO_BACK)


def fit_mlp(ex, w_turn=W_TURN, w0=None, l2=L.L2, iters=600, seed=1):
    """Softmax fit of the one-hidden-layer model (simlearn.MlpPolicy) by Adam
    with back-propagation: none = 0, right = v . relu(W1 f + b1), left the same
    on mirror(f), back = vb . relu(W1 sym(f) + b1)."""
    nf = LAYOUT.nf
    F, Fm, Fs = L.mats([f for f, _, _ in ex], LAYOUT.side)
    Y = np.eye(4)[[a for _, a, _ in ex]]
    wt = (np.where(Y[:, 0] > 0, 1.0, w_turn) * np.asarray([w for _, _, w in ex]))[:, None]
    H = HIDDEN if w0 is None else w0.hidden
    if w0 is None:
        rng = np.random.default_rng(seed)
        W1 = rng.normal(0, 1.0 / np.sqrt(nf), (H, nf))
        b1 = np.zeros(H)
        v = rng.normal(0, 0.1, H)
        vb = rng.normal(0, 0.1, H)
    else:
        W1, b1, v = np.asarray(w0.w1, float), np.asarray(w0.b1, float), np.asarray(w0.v, float)
        vb = np.asarray(w0.vb, float) if w0.vb is not None else np.zeros(H)
    params = [W1, b1, v, vb]
    ms = [np.zeros_like(x) for x in params]
    vs = [np.zeros_like(x) for x in params]
    n = len(F)
    for it in range(1, iters + 1):
        Hr = np.maximum(F @ W1.T + b1, 0.0)
        Hl = np.maximum(Fm @ W1.T + b1, 0.0)
        Hb = np.maximum(Fs @ W1.T + b1, 0.0)
        S = np.stack([np.zeros(n), Hr @ v, Hl @ v, Hb @ vb if not NO_BACK else np.full(n, -30.0)], 1)
        P = L.softmax(S)
        G = (P - Y) * wt / n
        dr, dl, db = G[:, 1], G[:, 2], (G[:, 3] if not NO_BACK else np.zeros(n))
        gv = Hr.T @ dr + Hl.T @ dl
        gvb = Hb.T @ db
        dHr = (dr[:, None] * v[None, :]) * (Hr > 0)
        dHl = (dl[:, None] * v[None, :]) * (Hl > 0)
        dHb = (db[:, None] * vb[None, :]) * (Hb > 0)
        gW1 = dHr.T @ F + dHl.T @ Fm + dHb.T @ Fs
        gb1 = dHr.sum(0) + dHl.sum(0) + dHb.sum(0)
        grads = [gW1 + l2 * W1, gb1 + l2 * b1, gv + l2 * v, gvb + l2 * vb]
        for i, (x, g) in enumerate(zip(params, grads)):
            params[i], ms[i], vs[i] = L.adam(x, g, ms[i], vs[i], it, lr=0.01)
        W1, b1, v, vb = params
    return L.MlpPolicy(W1.tolist(), b1.tolist(), v.tolist(), None if NO_BACK else vb.tolist(), NO_BACK, LAYOUT.speed)


# --- parallel helpers (games live in each worker)
GAMES = {}


def _init(paths, no_back=False, speed=True):
    global NO_BACK, LAYOUT
    NO_BACK = no_back
    LAYOUT = L.LAYOUT_SPEED if speed else L.LAYOUT_NOSPEED
    for path in paths:
        _, plays = build(load(path))
        for p in plays:
            if p.spears and not p.aborted:
                key = (os.path.basename(path), p.n)
                GAMES[key] = IGame(key, p)


def _info(key):
    g = GAMES[key]
    return key, g.arm, g.minutes


def _live(args):
    key, w_reject, w_hold = args
    return live_examples(GAMES[key], w_reject, w_hold)


def _rollouts(args):
    key, d, w_reject, w_hold = args
    g = GAMES[key]
    pol = L.policy_from_json(d)
    return [e for spec, targets, rej in zip(g.specs, g.targets, g.rejected)
            for e in rollout(g, spec, pol, targets, rej, w_reject, w_hold)[1]]


def _evaluate(args):
    """Per window, per model: (harp, death, why, latency, wrong, missed, extra);
    plus S2-FAST and the live game. And per model the open-loop false starts
    over the whole game."""
    key, models = args
    g = GAMES[key]
    out = defaultdict(list)
    false = {}
    pols = {name: L.policy_from_json(d) for name, d in models}
    for spec, targets in zip(g.specs, g.targets):
        for name, pol in pols.items():
            o, _, turns, extra = rollout(g, spec, pol, targets)
            out[name].append(row(o, targets, turns, extra))
        for name, inputs in (("S2-FAST", WS.RecInputs(spec.t0, R.ALL["S2F"], g.shadow[spec.k], g.samples, g.slots, spec.i)),
                             ("live", WS.LogInputs(spec.t0, g.cmds, bisect.bisect_left(g.cmd_tk, spec.k)))):
            o, turns, acc = traced(g, spec, inputs)
            o.acc = acc
            orc, extra = Oracle(list(targets)), [0, 0]
            heads = [spec.real.heading] + [hd for _, hd, _ in turns]
            for (k, hd, t), h0 in zip(turns, heads):
                if orc.answer(t, h0) == 0:
                    extra[0 if orc.p >= len(targets) else 1] += 1
            out[name].append(row(o, targets, turns, extra))
    # open loop over the live game: turn starts where the oracle says none
    targets_all = sorted(tg for ts in g.targets for tg in ts)
    for name, pol in pols.items():
        orc, prev, n = Oracle(list(targets_all)), 0, 0
        for f, h, t in g.live_features(pol.layout):
            a = pol.decide(f)
            lab = orc.answer(t, h)
            if a and a != prev and lab == 0:
                n += 1
            prev = a
        false[name] = n
    return key, dict(out), false


def split_folds(keys, arms, folds, seed):
    rnd = random.Random(seed)
    by_arm = defaultdict(list)
    for k in keys:
        by_arm[arms[k]].append(k)
    fold_of = {}
    for arm, ks in sorted(by_arm.items()):
        rnd.shuffle(ks)
        for i, k in enumerate(ks):
            fold_of[k] = i % folds
    return fold_of


def train(pool, trn, rounds, w_turn, w_reject=W_REJECT, w_hold=W_HOLD, log=None):
    data = [e for r in pool.map(_live, [(k, w_reject, w_hold) for k in trn]) for e in r]
    pol = fit(data, w_turn)
    if log:
        log(f"    round 0: {len(data)} live examples, {sum(1 for _, a, _ in data if a)} turns")
    for r in range(1, rounds + 1):
        res = pool.map(_rollouts, [(k, pol.to_json(), w_reject, w_hold) for k in trn])
        data += [e for rr in res for e in rr]
        pol = fit(data, w_turn, pol)
        if log:
            log(f"    round {r}: {len(data)} examples")
    return pol


def summarize(rows, names, false, minutes, arms=None):
    """arms: {key: arm} to break the table down by the recognizer the games were
    played with (on its own games the live recognizer defines the oracle)."""
    if arms:
        for arm in sorted(set(arms[k] for k in rows)):
            sub = {k: v for k, v in rows.items() if arms[k] == arm}
            print(f"  -- games played with {arm}: {len(sub)}")
            summarize(sub, names, false, minutes)
        return
    keys = [k for k in rows if rows[k]]         # games with windows
    n = sum(len(rows[k][names[0]]) for k in keys)
    print(f"  {'':10s} {'score':>7s} {'final':>7s} {'harp':>6s} {'wall':>6s} {'spear':>6s} {'tail':>6s} | {'latency':>7s} {'late':>6s} {'wrong':>6s} "
          f"{'missed':>6s} {'spur.':>6s} {'early':>6s} {'false/min':>9s}")
    for m in names:
        ws = [w for k in keys for w in rows[k][m]]
        sc = sum(h - d for h, d, *_ in ws)
        acc = sum(w[8] for w in ws)
        hp = sum(h for h, *_ in ws)
        why = defaultdict(float)
        for h, d, y, *_ in ws:
            if y:
                why[y] += d
        lats = [w[3] for w in ws if w[3] is not None]
        late = sum(1 for x in lats if x >= 2)
        wrong = sum(1 for w in ws if w[4]); missed = sum(1 for w in ws if w[5]); spur = sum(w[6] for w in ws); early = sum(w[7] for w in ws)
        fm = sum(false[k].get(m, 0) for k in keys) / max(1e-9, sum(minutes[k] for k in keys)) if m in false.get(keys[0], {}) else float("nan")
        print(f"  {m:10s} {1000*sc/n:7.1f} {1000*acc/n:7.1f} {1000*hp/n:6.1f} {-1000*why['hit the wall']/n:6.1f} {-1000*why['speared']/n:6.1f} "
              f"{-1000*why['ran into the tail']/n:6.1f} | {sum(lats)/max(1,len(lats)):7.3f} {1000*late/max(1,len(lats)):6.1f} {1000*wrong/n:6.1f} "
              f"{1000*missed/n:6.1f} {1000*spur/n:6.1f} {1000*early/n:6.1f} {fm:9.2f}")
    print(f"  ({n} windows; score columns per 1000 windows, final = {ALPHA:g} times the best score of the turns moved -{TOL}..{TOL} ticks "
          f"plus {1 - ALPHA:g} times the plain score (timing counts for little); latency = mean ticks from the window start to the "
          f"intended turn, late = per 1000 of them at 2 ticks or more; per 1000 windows: wrong first turn, missed turn, "
          f"spur. = turns after every asked turn was made or where none was asked, early = a later asked turn made "
          f"before its finger movement; false/min = open-loop turn starts where the oracle says none)")


def boot(rows, keys, a, b, n=2000, seed=1, acc=False):
    keys = [k for k in keys if rows[k]]
    val = (lambda w: w[8]) if acc else (lambda w: w[0] - w[1])
    per = {k: (sum(val(w) for w in rows[k][a]) - sum(val(w) for w in rows[k][b]), len(rows[k][a])) for k in keys}
    rnd = random.Random(seed)
    ds = []
    for _ in range(n):
        pick = [rnd.choice(keys) for _ in keys]
        ds.append(1000 * sum(per[k][0] for k in pick) / max(1, sum(per[k][1] for k in pick)))
    ds.sort()
    tot = 1000 * sum(per[k][0] for k in keys) / sum(per[k][1] for k in keys)
    return tot, ds[int(0.05 * n)], ds[int(0.95 * n)]


def main():
    argv = list(sys.argv)
    global MODEL, HIDDEN, NO_BACK, LAYOUT
    opt = {"--folds": None, "--rounds": str(ROUNDS), "--w-turn": str(W_TURN), "--w-reject": str(W_REJECT),
           "--w-hold": str(W_HOLD), "--eval": "", "--save": "", "--model": MODEL, "--hidden": str(HIDDEN)}
    NO_BACK = "--no-back" in argv or "--model" in argv and argv[argv.index("--model") + 1] == "mlp"
    argv = [a for a in argv if a != "--no-back"]
    for name in list(opt):
        if name in argv:
            i = argv.index(name)
            opt[name] = argv[i + 1]
            del argv[i:i + 2]
    export = "--export" in argv
    argv = [a for a in argv if a != "--export"]
    MODEL, HIDDEN = opt["--model"], int(opt["--hidden"])
    assert MODEL in ("linear", "mlp"), MODEL
    LAYOUT = L.LAYOUT_NOSPEED if MODEL == "mlp" else L.LAYOUT_SPEED
    args, o = L.options(argv)
    rounds, w_turn = int(opt["--rounds"]), float(opt["--w-turn"])
    w_reject, w_hold = float(opt["--w-reject"]), float(opt["--w-hold"])
    models = []
    for path in [x for x in opt["--eval"].split(",") if x]:
        spec = json.load(open(path))
        models.append((spec["name"], L.policy_from_spec(spec).to_json()))
    keys = []
    for path in args:
        _, plays = build(load(path))
        keys += [(os.path.basename(path), p.n) for p in plays if p.spears and not p.aborted]
    with Pool(os.cpu_count(), initializer=_init, initargs=(args, NO_BACK, LAYOUT.speed)) as pool:
        info = dict((k, (arm, mins)) for k, arm, mins in pool.map(_info, keys))
        arms = {k: info[k][0] for k in keys}
        minutes = {k: info[k][1] for k in keys}
        if export:
            trn, _ = L.split(keys, arms, o["holdout"], o["seed"], o["never_test"])
            pol = train(pool, trn, rounds, w_turn, w_reject, w_hold)
            print(json.dumps(pol.to_json()))
            return
        if opt["--folds"]:
            folds = int(opt["--folds"])
            fold_of = split_folds(keys, arms, folds, o["seed"])
            sets = [([k for k in keys if fold_of[k] != f], [k for k in keys if fold_of[k] == f]) for f in range(folds)]
        else:
            sets = [L.split(keys, arms, o["holdout"], o["seed"], o["never_test"])]
        rows_test, rows_train, false = {}, {}, {}
        for i, (trn, test) in enumerate(sets):
            print(f"fold {i + 1}/{len(sets)}: train {len(trn)} games, test {len(test)}", flush=True)
            pol = train(pool, trn, rounds, w_turn, w_reject, w_hold, log=lambda s: print(s, flush=True))
            ev = models + [("new", pol.to_json())]
            for key, res, fl in pool.map(_evaluate, [(k, ev) for k in (test if opt["--folds"] else trn + test)]):
                (rows_test if key in test else rows_train)[key] = res
                false[key] = fl
            if len(sets) == 1:
                print(json.dumps(pol.to_json()))
        names = [m for m, _ in models] + ["new", "S2-FAST", "live"]
        if opt["--save"]:
            import pickle
            with open(opt["--save"], "wb") as f:
                pickle.dump({"test": rows_test, "train": rows_train, "false": false, "minutes": minutes, "arms": arms, "names": names}, f)
        for title, rows in (("Training games", rows_train), ("Test games" + (" (every game once)" if opt["--folds"] else ""), rows_test)):
            if rows:
                print(f"\n{title}: {len(rows)} games")
                summarize(rows, names, false, minutes)
                print()
                summarize(rows, names, false, minutes, arms)
        if rows_test:
            keys_t = list(rows_test)
            print("\nTest, new minus each (per 1000 windows, 90% range over games): score | final score")
            for m in names[:-2]:
                tot, lo, hi = boot(rows_test, keys_t, "new", m)
                ta, la, ha = boot(rows_test, keys_t, "new", m, acc=True)
                print(f"  vs {m:10s} {tot:+6.1f} [{lo:+.1f}, {hi:+.1f}] | {ta:+6.1f} [{la:+.1f}, {ha:+.1f}]")


if __name__ == "__main__":
    main()

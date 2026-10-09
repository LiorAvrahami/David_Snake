#!/usr/bin/env python3
"""Scores input recognizers by simulating the game forward from the real
situation, as the player defined it:

  A window starts at every gesture: the finger starts moving (its speed
  rises from under V_REST to over V_MOVE, and it travels GESTURE_DP), or,
  while moving, turns by SPLIT_DEG or more from where it was going. A
  finger resting on the screen starts nothing.
  From David's real state at that moment (enginesim, an exact port of the
  engine), a recognizer reads the recorded finger input for W steps and
  the engine executes its turns; then David goes straight, with no more
  input, until he eats the harp or dies. The game runs on all along:
  David steps, the tail follows and grows, spears fly.

  harp   +1 for eating the harp, whenever it happens (only the first: the
         next one appears at random); the run ends there
  death  -DEATH, less the later it is: times exp(-(s - 1) / DECAY), s the
         steps from the window start to the death, DECAY by cause (wall
         2 steps, spear 3.7, tail 4.2; fitted to the earlier linear
         penalty with its cut-offs)

A turn that comes within COMPLETE_MS of the window's last turn, after the
input ended, still counts: a side step or U-turn is one move, never cut
in half by the window's end.

Spears are the recorded ones, but only those an attacker aimed before the
window started (attackers aim AIM_TICKS before the spear flies, at where
David is): later ones were aimed at the path David really took.

The "as played" row runs the game's own logged commands through the same
windows; S2F (the recognizer that was live) should come out close to it.

Usage: python3 tools/windowsim.py FILE... [--w 2,3]"""
import bisect, copy, math, os, random, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import enginesim as E
import recognizers as R

V_REST, V_MOVE = 60.0, 150.0     # finger speed, dp/s
GESTURE_DP = 10.0       # a gesture travels at least this far
SPLIT_DEG = 60.0        # a turn this sharp while moving starts a new gesture
SPEED_MS = 40           # finger speed is measured over this long
MAX_STEPS = 200         # safety cap on a run (going straight, a wall comes within 21)
DECAY = {"hit the wall": 2.0, "speared": 3.7, "ran into the tail": 4.2}
DEATH = 0.5
AIM_TICKS = 18
COMPLETE_MS = 250
SLOT_LAG_MS = 5         # a touch sample is handled ~5 ms after its time stamp
TURNED = {"turn", "queued", "step", "flush", "re-aim", "rotate"}
MODELS = ["S2F", "S2", "S", "P28", "P", "O"]


def samples_of(p):
    """Every finger sample of the game in time order:
    (t, x, y, first, last, up)."""
    out = []
    for s in p.strokes:
        n = len(s.samples)
        for i, (t, x, y) in enumerate(s.samples):
            out.append((t, x, y, i == 0, i == n - 1, i == n - 1 and s.end == "up"))
    out.sort(key=lambda a: a[0])
    return out


def gesture_starts(stroke):
    """Times the finger starts a gesture within one stroke (see top)."""
    sm = stroke.samples
    out = []
    j = 0
    moving = False
    for i, (t, x, y) in enumerate(sm):
        while sm[j][0] < t - SPEED_MS:
            j += 1
        dt = t - sm[j][0]
        vx = (x - sm[j][1]) * 1000.0 / dt if dt > 0 else 0.0
        vy = (y - sm[j][2]) * 1000.0 / dt if dt > 0 else 0.0
        v = math.hypot(vx, vy)
        if not moving:
            if v >= V_REST:     # starts moving: the gesture starts at the last rest
                moving, start, peak, ref, counted, corner = True, max(i - 1, 0), v, None, False, None
            continue
        if v < V_REST:
            moving = False
            continue
        peak = max(peak, v)
        _, sx, sy = sm[start]
        if not counted and peak >= V_MOVE and math.hypot(x - sx, y - sy) >= GESTURE_DP:
            counted = True
            out.append(sm[start][0])
        if ref is None:         # where this gesture goes, once it is clear
            if math.hypot(x - sx, y - sy) >= 8.0:
                ref = (x - sx, y - sy)
            continue
        if corner is None or v < corner[0]:
            corner = (v, i)
        cross = ref[0] * vy - ref[1] * vx
        dot = ref[0] * vx + ref[1] * vy
        if v >= V_MOVE and abs(math.degrees(math.atan2(cross, dot))) >= SPLIT_DEG:
            # a sharp turn: a new gesture from the slowest point of the corner
            start, peak, ref, counted, corner = corner[1], v, None, False, None
    return out


def slots_of(p, samples):
    """Game ticks done before each sample was handled: logged with every
    sample from v1.15 on. For older recordings, estimated: tick times are
    known at steps (interpolated between); samples that fired a logged
    command take its tick count, and the rest are kept in order around
    them."""
    strokes = sorted(p.strokes, key=lambda s: s.t0)
    if strokes and all(s.tks is not None for s in strokes):
        exact = [k for s in strokes for k in s.tks]
        if len(exact) == len(samples):
            return exact
    pts = [(0, p.t)] + [(s["tk"], s["t"]) for s in p.steps if not s.get("flush")]
    ks, ts = [], []
    for (k0, t0), (k1, t1) in zip(pts, pts[1:]):
        for k in range(k0, k1):
            ks.append(k)
            ts.append(t0 + (t1 - t0) * (k - k0) / max(1, k1 - k0))
    k1, t1 = pts[-1]
    for j in range(0, 200):
        ks.append(k1 + j)
        ts.append(t1 + 45 * j)
    est = []
    for smp in samples:
        i = bisect.bisect_right(ts, smp[0] + SLOT_LAG_MS) - 1
        est.append(ks[i] if i >= 0 else 0)
    known = {}
    for c in p.cmds:
        known.setdefault(c["t"], c["tk"])
    fixed = [known.get(smp[0]) for smp in samples]
    lo = 0
    for i in range(len(samples)):
        if fixed[i] is not None:
            lo = fixed[i]
        est[i] = max(est[i], lo)
    hi = 10 ** 9
    for i in range(len(samples) - 1, -1, -1):
        if fixed[i] is not None:
            hi = fixed[i]
            est[i] = fixed[i]
        est[i] = min(est[i], hi)
    return est


def clone(rec):
    if rec is None:
        return None
    c = copy.copy(rec)
    for a in ("ts", "xs", "ys"):
        if hasattr(rec, a):
            setattr(c, a, list(getattr(rec, a)))
    return c


def feed(cls, rec, smp, g):
    """One sample through a recognizer; returns (recognizer, commands).
    The recognizer is None between strokes."""
    t, x, y, first, last, up = smp
    cmds = []
    if first:
        rec = cls()
        rec.down(t, x, y)
    elif rec is not None:
        cmds = rec.up(t, x, y, g) if up else rec.move(t, x, y, g)
    return (None if last else rec), cmds


class Window:
    """One window's outcome."""
    __slots__ = ("harp", "death", "why")

    def __init__(self):
        self.harp = 0.0
        self.death = 0.0
        self.why = None

    @property
    def score(self):
        return self.harp - self.death


def outcome(sim, k0, W, eaten_tick):
    w = Window()
    if eaten_tick is not None:
        w.harp = 1.0
    elif sim.lost:
        s = (sim.tick_no - k0) / 4.0
        w.death = DEATH * min(1.0, math.exp(-(s - 1.0) / DECAY[sim.lost]))
        w.why = sim.lost
    return w


def run_window(real, k0, W, inputs):
    """Simulate from the real state after tick k0; inputs.slot(k, sim)
    yields the commands to run before tick k+1, one sample's at a time,
    as (t, [(dir, hold), ...])."""
    sim = real.copy()
    sim.throw_limit = k0 + AIM_TICKS
    sim.harp_next = lambda: None
    for k in range(k0, k0 + 4 * MAX_STEPS):
        for t, cmds in inputs.slot(k, sim):
            turned = False
            for d, hold in cmds:
                if sim.swipe(d, hold) in TURNED:
                    turned = True
                if sim.lost or sim.eaten:
                    break
            if turned:
                inputs.last_turn = t
            if sim.lost or sim.eaten:
                break
        if sim.lost or sim.eaten:
            break
        sim.tick()
        if sim.lost or sim.eaten:
            break
    return outcome(sim, k0, W, sim.eaten[0] if sim.eaten else None)


class Inputs:
    """Input for one window: W steps of it, then only a turn completing a
    side step or U-turn (within COMPLETE_MS of the last turn)."""

    def __init__(self, k0, W):
        self.end = k0 + 4 * W
        self.last_turn = None
        self.done = False

    def allow(self, k, t):
        if self.done:
            return False
        if k < self.end:
            return True
        return self.last_turn is not None and t <= self.last_turn + COMPLETE_MS


class RecInputs(Inputs):
    """A recognizer reading the recorded finger samples."""

    def __init__(self, k0, W, cls, rec, samples, slots, i0):
        super().__init__(k0, W)
        self.cls, self.rec = cls, clone(rec)
        self.samples, self.slots, self.i = samples, slots, i0

    def slot(self, k, sim):
        while self.i < len(self.samples) and self.slots[self.i] <= k:
            smp = self.samples[self.i]
            if not self.allow(k, smp[0]):
                self.done = True
                return
            self.i += 1
            self.rec, cmds = feed(self.cls, self.rec, smp, sim)
            if cmds:
                if k >= self.end:
                    self.done = True        # the completing turn ends the input
                yield smp[0], [(d, kind == "uturn" and j == 1) for j, (d, kind) in enumerate(cmds)]


class LogInputs(Inputs):
    """The commands the game logged (with their exact tick)."""

    def __init__(self, k0, W, cmds, j0):
        super().__init__(k0, W)
        self.cmds, self.j = cmds, j0

    def slot(self, k, sim):
        while self.j < len(self.cmds) and self.cmds[self.j][0]["tk"] <= k:
            t = self.cmds[self.j][0]["t"]
            if not self.allow(k, t):
                self.done = True
                return
            group = []
            while self.j < len(self.cmds) and self.cmds[self.j][0]["t"] == t and self.cmds[self.j][0]["tk"] <= k:
                c, h = self.cmds[self.j]
                group.append((E.LET.index(c["dir"]), h))
                self.j += 1
            if k >= self.end:
                self.done = True
            yield t, group


def evaluate_play(p, Ws, models=MODELS):
    """{(W, model): [Window, ...]} for one recorded game."""
    samples = samples_of(p)
    slots = slots_of(p, samples)
    cmds = list(zip(p.cmds, E.held_flags(p.cmds)))
    cmd_tk = [c["tk"] for c, _ in cmds]
    real = E.start_sim(p)
    classes = {m: R.ALL[m] for m in models}
    shadow = {m: None for m in models}
    out = defaultdict(list)
    last_tk = p.death["tk"] if p.death else max([s["tk"] for s in p.steps] + [0])
    i = j = 0                           # next sample, next logged command
    times = [smp[0] for smp in samples]
    starts = set()
    for s in p.strokes:
        for t in gesture_starts(s):
            starts.add(slots[bisect.bisect_left(times, t)])
    for k in range(0, last_tk + 1):
        if k in starts:
            for W in Ws:
                out[(W, "as played")].append(
                    run_window(real, k, W, LogInputs(k, W, cmds, bisect.bisect_left(cmd_tk, k))))
                for m in models:
                    out[(W, m)].append(run_window(
                        real, k, W, RecInputs(k, W, classes[m], shadow[m], samples, slots, i)))
        # the real game: this slot's samples (shadow recognizers read them
        # against the real game) and logged commands, in time order
        while True:
            ns = samples[i][0] if i < len(samples) and slots[i] == k else None
            nc = cmds[j][0]["t"] if j < len(cmds) and cmds[j][0]["tk"] == k else None
            if ns is None and nc is None:
                break
            if nc is None or (ns is not None and ns <= nc):
                for m in models:
                    shadow[m], _ = feed(classes[m], shadow[m], samples[i], real)
                i += 1
            else:
                c, h = cmds[j]
                real.swipe(E.LET.index(c["dir"]), h)
                j += 1
        while i < len(samples) and slots[i] <= k:
            i += 1
        if real.lost:
            break
        real.tick()
        if real.lost:
            break
    return out


def summarize(per_game, Ws, rows, ref="as played", boots=2000, seed=1):
    games = list(per_game)
    for W in Ws:
        n = sum(len(per_game[g][(W, ref)]) for g in games)
        print(f"\nW = {W} steps of input: {n} gestures, {len(games)} games")
        print(f"  {'':10s} {'score':>7s} {'harp':>6s} {'wall':>6s} {'tail':>6s} {'spear':>6s}"
              f"   vs {ref} [90% range over games]   (per 1000 gestures; deaths = their penalty)")
        tot = {}
        for m in rows:
            ws = [w for g in games for w in per_game[g][(W, m)]]
            sc = sum(w.score for w in ws)
            tot[m] = sc
            hp = sum(w.harp for w in ws)
            why = defaultdict(float)
            for w in ws:
                if w.why:
                    why[w.why] += w.death
            line = (f"  {m:10s} {1000 * sc / n:7.1f} {1000 * hp / n:6.1f} "
                    f"{-1000 * why['hit the wall'] / n:6.1f} {-1000 * why['ran into the tail'] / n:6.1f} "
                    f"{-1000 * why['speared'] / n:6.1f}")
            if m != ref:
                rnd = random.Random(seed)
                per = {g: (sum(w.score for w in per_game[g][(W, m)]) - sum(w.score for w in per_game[g][(W, ref)]),
                           len(per_game[g][(W, ref)])) for g in games}
                ds = []
                for _ in range(boots):
                    pick = [rnd.choice(games) for _ in games]
                    a = sum(per[g][0] for g in pick)
                    b = sum(per[g][1] for g in pick) or 1
                    ds.append(1000 * a / b)
                ds.sort()
                d = 1000 * (sc - sum(w.score for g in games for w in per_game[g][(W, ref)])) / n
                line += f"   {d:+6.1f} [{ds[int(0.05 * boots)]:+.1f}, {ds[int(0.95 * boots)]:+.1f}]"
            print(line)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    Ws = [2, 3]
    if "--w" in sys.argv:
        Ws = [int(x) for x in sys.argv[sys.argv.index("--w") + 1].split(",")]
        args = [a for a in args if a != sys.argv[sys.argv.index("--w") + 1]]
    per_game = {}
    for path in args:
        _, plays = build(load(path))
        for p in plays:
            if not p.spears or p.aborted:
                continue                # spears unknown before v1.7
            per_game[(os.path.basename(path), p.n)] = evaluate_play(p, Ws)
    summarize(per_game, Ws, ["as played"] + MODELS)

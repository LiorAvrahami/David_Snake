#!/usr/bin/env python3
"""Scores input recognizers by simulating the game forward from the real
situation, as the player defined it:

  A window starts at every game tick in which the finger moves: its
  samples in that tick get MOVE_DP or more away from where it was at the
  end of the tick before (sample noise is about 0.3 dp). So every
  decision a recognizer makes on a moving finger is scored, from the real
  situation at that tick; a resting finger, or none, starts nothing.
  (Until v1.18 a window started only at a gesture the detector counted,
  the finger reaching V_MOVE with GESTURE_DP of travel, which left every
  smaller movement, and the turns made on it, unscored; gestures within
  JOIN_MS of a window's start joined it. gesture_starts is still the
  detector, now counting START_DP of travel after a rest; the oracle of
  tools/intentlearn.py uses the old criterion to say when a movement is
  clear.)
  From David's real state at the window's start (enginesim, an exact port
  of the engine), a recognizer reads the recorded finger input of the
  first READ_MS and the engine executes its turns. If it turned David, it
  may make one more turn before David's next regular step after its
  latest turn (the second turn of a one-cell side step or U-turn); that
  turn ends the input. Then David goes straight, with no more input,
  until he dies, eating the harp on the way or not. The game runs on all
  along: David steps, the tail follows and grows, spears fly.

  harp   +1 for eating the harp, whenever it happens (only the first: the
         next one appears at random); the run goes on, to see whether a
         spear gets him afterwards; a wall or tail death after the harp
         costs nothing
  death  -DEATH, less the later it is: times exp(-(s - 1) / DECAY), s the
         steps from the window start to the death, DECAY by cause (wall
         and tail 1 step, spear 3.7)
  excluded  a run in which David is speared from the side (the spear
         flying across the way he moves) or speared after eating the harp
         has no score (NaN) and is left out of every average: the recorded
         throws were aimed at the path he took in the real game, so on
         another path such a hit says nothing about the turn. So has a run
         in which David enters the phantom tail: the PHANTOM cells behind
         the tail's end, moving as if the tail were that much longer (with
         no tail, the cells he has just left)

Why only READ_MS: the finger was recorded while the player watched the
live recognizer's turns, and from about 225 ms after a turn it can be a
reaction to it; a recognizer reading that would be judged with help meant
for another. A second turn after David's next regular step is a separate
decision, made after seeing him move on.

Spears are the recorded ones, but only those an attacker aimed before the
window started (attackers aim AIM_TICKS before the spear flies, at where
David is): later ones were aimed at the path David really took.

The "as played" row runs the game's own logged commands through the same
windows; S2F (the recognizer that was live in most games) should come out
close to it on the games it played.

Usage: python3 tools/windowsim.py FILE..."""
import bisect, copy, math, os, random, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import enginesim as E
import recognizers as R

V_REST, V_MOVE = 60.0, 150.0
START_DP = 3.0                            # travel after a rest that makes a gesture (gesture_starts)
MOVE_DP = 1.0                             # finger travel within a tick that counts as movement (a window)     # finger speed, dp/s
GESTURE_DP = 10.0       # a gesture travels at least this far
SPLIT_DEG = 60.0        # a turn this sharp while moving starts a new gesture
SPEED_MS = 40           # finger speed is measured over this long
MAX_STEPS = 200         # safety cap on a run (going straight, a wall comes within 21)
DECAY = {"hit the wall": 1.0, "speared": 3.7, "ran into the tail": 1.0}
DEATH = 0.5
AIM_TICKS = 18
PHANTOM = 2             # cells of phantom tail behind the real tail's end (David entering one: no score)
JOIN_MS = 250           # a gesture starting this soon after a window's start belongs to it
READ_MS = 250           # the input read from the window's start
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
                moving, start, ref, counted, corner = True, max(i - 1, 0), None, False, None
            continue
        if v < V_REST:
            moving = False
            continue
        _, sx, sy = sm[start]
        if not counted and math.hypot(x - sx, y - sy) >= START_DP:
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
            start, ref, counted, corner = corner[1], None, False, None
    return out


def window_starts(p, samples, slots):
    """The game's windows, [(k0, t0)]: one at every game tick in which the
    finger moved MOVE_DP or more (the farthest of the tick's samples from the
    latest sample of the tick before, or from the touch-down in a stroke's
    first tick); t0 the time of the tick's first sample, k0 the game ticks
    done before it was handled."""
    out, ref, i, n = [], None, 0, len(samples)
    while i < n:
        k, j, d = slots[i], i, 0.0
        while j < n and slots[j] == k:
            _, x, y, first, _, _ = samples[j][:6]
            if first or ref is None:
                ref = (x, y)
            d = max(d, math.hypot(x - ref[0], y - ref[1]))
            j += 1
        if d >= MOVE_DP and (not out or k > out[-1][0]):
            out.append((k, samples[i][0]))
        _, x, y, _, last, _ = samples[j - 1][:6]
        ref = None if last else (x, y)          # a lifted finger: the next stroke starts fresh
        i = j
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


EXCLUDED = ("speared from the side", "speared after the harp", "hit the phantom tail")   # runs without a score (NaN)


class Window:
    """One window's outcome (acc: a timing-tolerant score, when computed;
    harp_at: ticks from the start to the harp, if eaten). A run whose cause is
    in EXCLUDED has no score (NaN) and is left out of every average."""
    __slots__ = ("harp", "death", "why", "acc", "harp_at")

    def __init__(self):
        self.harp = 0.0
        self.death = 0.0
        self.why = None
        self.acc = None
        self.harp_at = None

    @property
    def excluded(self):
        return self.why in EXCLUDED

    @property
    def score(self):
        return float("nan") if self.why in EXCLUDED else self.harp - self.death


def outcome(sim, k0, eaten_tick, phantom_hit=False):
    w = Window()
    if eaten_tick is not None:
        w.harp = 1.0
        w.harp_at = eaten_tick - k0
    if phantom_hit:
        w.why = "hit the phantom tail"          # excluded: no score
    elif eaten_tick is not None:
        if sim.lost == "speared":
            w.why = "speared after the harp"    # excluded: no score
    elif sim.lost == "speared" and sim.spear_dir % 2 != sim.last % 2:
        w.why = "speared from the side"         # excluded: no score
    elif sim.lost:
        s = (sim.tick_no - k0) / 4.0
        w.death = DEATH * min(1.0, math.exp(-(s - 1.0) / DECAY[sim.lost]))
        w.why = sim.lost
    return w


def run_window(real, k0, inputs, watch=None, ref=None):
    """Simulate from the real state after tick k0 until David dies (or MAX_STEPS
    steps): the harp eaten on the way counts, and the run goes on past it to see
    whether a spear gets him after it. A phantom tail of PHANTOM cells follows
    the real one: the cells its end has left most recently (the cells David
    left, with no tail), taken from his path in the real game at the start and
    moving as if the tail were PHANTOM cells longer; David entering one ends
    the run without a score. inputs.slot(k, sim) yields the
    commands to run before tick k+1, one
    sample's at a time, as (t, [(dir, hold), ...]). watch(event, sim, k, t,
    heading before), if given, sees every command ("cmd") and every tick
    ("tick"). ref is the window's start when the run begins earlier (the spears
    taken and the death decay are counted from it)."""
    ref = k0 if ref is None else ref
    sim = real.copy()
    sim.throw_limit = ref + AIM_TICKS
    sim.harp_next = lambda: None
    # the phantom tail's cells, nearest the real tail first: at the start, the cells David
    # occupied just before the tail's end, from his path in the real game
    n = len(sim.tail)
    older = sim.hist[:len(sim.hist) - n] if n else list(sim.hist)
    phantom = list(reversed(older[-PHANTOM:]))

    def tail_end():
        return sim.tail[-1] if sim.tail else (sim.hx, sim.hy)

    def stepped(end_before):
        """After a step: the cell the tail's end left becomes the phantom's first
        cell and the rest move back (nothing moves if the tail grew); True if
        David is on the phantom."""
        if tail_end() != end_before:
            phantom.insert(0, end_before)
            del phantom[PHANTOM:]
        return (sim.hx, sim.hy) in phantom

    hit = False
    for k in range(k0, k0 + 4 * MAX_STEPS):
        for t, cmds in inputs.slot(k, sim):
            h0, pos, turned = sim.heading, (sim.hx, sim.hy), False
            end0 = tail_end()
            for d, hold in cmds:
                if sim.swipe(d, hold) in TURNED:
                    turned = True
                if sim.lost:
                    break
            if turned:
                inputs.turned(t)
            elif (sim.hx, sim.hy) != pos:
                inputs.stepped()            # a flushed step, with no turn after it
            if (sim.hx, sim.hy) != pos and stepped(end0):
                hit = True
            if watch:
                watch("cmd", sim, k, t, h0)
            if sim.lost or hit:
                break
        if sim.lost or hit:
            break
        pos = (sim.hx, sim.hy)
        end0 = tail_end()
        sim.tick()
        if (sim.hx, sim.hy) != pos:
            inputs.stepped()
            if stepped(end0):
                hit = True
        if watch:
            watch("tick", sim, k, None, None)
        if sim.lost or hit:
            break
    return outcome(sim, ref, sim.eaten[0] if sim.eaten else None, hit)


class Inputs:
    """Input for one window: the first READ_MS of it; then, if David
    turned, until his next regular step after the latest turn, and only
    one more turn (which ends the input)."""

    def __init__(self, t0):
        self.t_end = t0 + READ_MS
        self.last_turn = None       # time of the sample behind the latest turn
        self.steps = 0              # David's steps since then
        self.done = False

    def turned(self, t):
        self.last_turn, self.steps = t, 0

    def stepped(self):
        self.steps += 1

    def allow(self, t):
        """May the recognizer read the sample (or command) of time t?"""
        if self.done:
            return False
        if t <= self.t_end:
            return True
        return self.last_turn is not None and self.steps <= 1

    def finishing(self, t):
        """A turn from time t is the one more turn, which ends the input."""
        return t > self.t_end


class Recorded:
    """Wraps an Inputs: runs it and keeps the commands it gave, by slot."""

    def __init__(self, inner):
        self.inner = inner
        self.log = []                   # [(slot, t, [(dir, hold), ...])]

    def slot(self, k, sim):
        for t, cmds in self.inner.slot(k, sim):
            self.log.append((k, t, cmds))
            yield t, cmds

    def turned(self, t):
        self.inner.turned(t)

    def stepped(self):
        self.inner.stepped()


class Schedule(Inputs):
    """Replays recorded commands, every slot moved by `shift` ticks (no
    deciding, no reading rules); `first` = [(slot, t, cmds)] run before them."""

    def __init__(self, t0, log, shift, first=()):
        super().__init__(t0)
        self.by_slot = defaultdict(list)
        for k, t, cmds in first:
            self.by_slot[k].append((t, cmds))
        for k, t, cmds in log:
            self.by_slot[k + shift].append((t, cmds))

    def slot(self, k, sim):
        yield from self.by_slot.get(k, ())


def run_window_tolerant(real, k0, t0, inputs, shifts=(-1, 0, 1), anchor=None, watch=None, prev=()):
    """Runs the window, then replays the commands it gave moved together by
    each of `shifts` ticks; with `anchor` (a slot), moved so that the first
    command lands at anchor + shift instead, the same placements for every
    recognizer. A placement before the window's start runs from an earlier
    real state: `prev` = [(slot, state, the live game's commands in that
    slot), ...] for the slots before k0, nearest first; a placement earlier
    than the earliest given is left out. Returns (the plain run's Window,
    {shift: Window} of the placements; the plain run is among them when its
    timing is one of them)."""
    rec = Recorded(inputs)
    plain = run_window(real, k0, rec, watch)
    if not rec.log:
        return plain, {0: plain}
    first = rec.log[0][0]
    out = {}
    for d in shifts:
        move = d if anchor is None else anchor + d - first
        at = first + move
        if move == 0:
            out[d] = plain
        elif at >= k0:
            out[d] = run_window(real, k0, Schedule(t0, rec.log, move))
        elif k0 - at <= len(prev):
            j = k0 - at                 # start j slots early, the live game's commands of those slots first
            state = prev[j - 1][1]
            before = [(slot, t0, cmds) for slot, _, cmds in reversed(prev[:j]) if cmds]
            out[d] = run_window(state, k0 - j, Schedule(t0, rec.log, move, before), ref=k0)
    return plain, out or {0: plain}


def tolerant_score(plain, placements, alpha=1.0):
    """The timing-tolerant score: alpha * (the best placement's score) + (1 -
    alpha) * (the plain run's score); and the best placement's Window (the
    plain run's on a tie)."""
    valid = {d: w for d, w in placements.items() if not w.excluded}
    if plain.excluded or not valid:
        return float("nan"), plain             # no score: left out of every average
    best = max(valid, key=lambda d: (valid[d].score, valid[d] is plain))
    return alpha * valid[best].score + (1 - alpha) * plain.score, valid[best]


class RecInputs(Inputs):
    """A recognizer reading the recorded finger samples."""

    def __init__(self, t0, cls, rec, samples, slots, i0):
        super().__init__(t0)
        self.cls, self.rec = cls, clone(rec)
        self.samples, self.slots, self.i = samples, slots, i0

    def slot(self, k, sim):
        while self.i < len(self.samples) and self.slots[self.i] <= k:
            smp = self.samples[self.i]
            if not self.allow(smp[0]):
                self.done = True
                return
            self.i += 1
            self.rec, cmds = feed(self.cls, self.rec, smp, sim)
            if cmds:
                if self.finishing(smp[0]):
                    self.done = True
                yield smp[0], [(d, kind == "uturn" and j == 1) for j, (d, kind) in enumerate(cmds)]


class LogInputs(Inputs):
    """The commands the game logged (with their exact tick)."""

    def __init__(self, t0, cmds, j0):
        super().__init__(t0)
        self.cmds, self.j = cmds, j0

    def slot(self, k, sim):
        while self.j < len(self.cmds) and self.cmds[self.j][0]["tk"] <= k:
            t = self.cmds[self.j][0]["t"]
            if not self.allow(t):
                self.done = True
                return
            group = []
            while self.j < len(self.cmds) and self.cmds[self.j][0]["t"] == t and self.cmds[self.j][0]["tk"] <= k:
                c, h = self.cmds[self.j]
                group.append((E.LET.index(c["dir"]), h))
                self.j += 1
            if self.finishing(t):
                self.done = True
            yield t, group


def evaluate_play(p, models=MODELS):
    """{model: [Window, ...]} for one recorded game, one per window."""
    samples = samples_of(p)
    slots = slots_of(p, samples)
    cmds = list(zip(p.cmds, E.held_flags(p.cmds)))
    cmd_tk = [c["tk"] for c, _ in cmds]
    real = E.start_sim(p)
    classes = {m: R.ALL[m] for m in models}
    shadow = {m: None for m in models}
    out = defaultdict(list)
    last_tk = p.death["tk"] if p.death else max([s["tk"] for s in p.steps] + [0])
    wins = dict(window_starts(p, samples, slots))
    i = j = 0                           # next sample, next logged command
    for k in range(0, last_tk + 1):
        if k in wins:
            t0 = wins[k]
            out["as played"].append(run_window(real, k, LogInputs(t0, cmds, bisect.bisect_left(cmd_tk, k))))
            for m in models:
                out[m].append(run_window(real, k, RecInputs(t0, classes[m], shadow[m], samples, slots, i)))
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


def summarize(per_game, rows, ref="as played", boots=2000, seed=1):
    games = list(per_game)
    n_all = sum(len(per_game[g][ref]) for g in games)
    print(f"\n{n_all} windows, {len(games)} games (per 1000 windows with a score; excl. = windows left out: speared "
          f"from the side or after the harp, or the phantom tail; differences over the windows both have a score for)")
    print(f"  {'':10s} {'score':>7s} {'harp':>6s} {'wall':>6s} {'spear':>6s} {'tail':>6s} {'excl.':>6s}"
          f"   vs {ref} [90% range over games]")
    for m in rows:
        ws = [w for g in games for w in per_game[g][m]]
        ok = [w for w in ws if not w.excluded]
        n = max(1, len(ok))
        sc = sum(w.score for w in ok)
        hp = sum(w.harp for w in ok)
        why = defaultdict(float)
        for w in ok:
            if w.why:
                why[w.why] += w.death
        line = (f"  {m:10s} {1000 * sc / n:7.1f} {1000 * hp / n:6.1f} "
                f"{-1000 * why['hit the wall'] / n:6.1f} {-1000 * why['speared'] / n:6.1f} "
                f"{-1000 * why['ran into the tail'] / n:6.1f} {len(ws) - len(ok):6d}")
        if m != ref:
            rnd = random.Random(seed)
            per = {}
            for g in games:
                pairs = [(a, b) for a, b in zip(per_game[g][m], per_game[g][ref]) if not a.excluded and not b.excluded]
                per[g] = (sum(a.score - b.score for a, b in pairs), len(pairs))
            ds = []
            for _ in range(boots):
                pick = [rnd.choice(games) for _ in games]
                a = sum(per[g][0] for g in pick)
                b = sum(per[g][1] for g in pick) or 1
                ds.append(1000 * a / b)
            ds.sort()
            d = 1000 * sum(per[g][0] for g in games) / max(1, sum(per[g][1] for g in games))
            line += f"   {d:+6.1f} [{ds[int(0.05 * boots)]:+.1f}, {ds[int(0.95 * boots)]:+.1f}]"
        print(line)


if __name__ == "__main__":
    per_game = {}
    for path in sys.argv[1:]:
        _, plays = build(load(path))
        for p in plays:
            if not p.spears or p.aborted:
                continue                # spears unknown before v1.7
            per_game[(os.path.basename(path), p.n)] = evaluate_play(p)
    summarize(per_game, ["as played"] + MODELS)

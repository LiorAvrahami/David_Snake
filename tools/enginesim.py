"""A Python port of GameEngine's movement for offline simulation: David's
steps, turns (STEP_SAFE with the U-turn hold, the mode recordings from
v1.7 on were played in, and SCHED, the steady beat), the tail, the harp,
flying spears and the HARD difficulty's wall grace. Attackers are not simulated: spears enter
from a list of throws (the recorded ones).

replay(play) re-runs a recorded game from its logged commands and spear
throws; it must land every step exactly where the game logged it, which
checks the port against the real engine.

With a Timing (the scoring's "replayed timing"), David steps only when he
stepped in the recorded game, at the same ticks (and between ticks where a
quick second turn flushed a step), whatever turns are made: turns change
his heading, and the next of those steps goes that way. Past the recorded
game's end he steps every 4 ticks.

Directions use the engine's numbering: UP=0, RIGHT=1, DOWN=2, LEFT=3."""
import bisect

COLS, ROWS = 21, 13
UP, RIGHT, DOWN, LEFT = 0, 1, 2, 3
DX = (0, 1, 0, -1)
DY = (-1, 0, 1, 0)
LET = "URDL"
GRACE = -1          # HARD: a step into the wall waits until the counter reaches -1


def inb(x, y):
    return 0 <= x < COLS and 0 <= y < ROWS


class Sim:
    """Engine state between ticks. `harp_next` gives the harp's next cell
    after one is eaten (None: unknown, no more harps)."""

    def __init__(self):
        self.hx, self.hy, self.hd = 10, 6, UP
        self.last = UP                  # direction of the last actual step
        self.tail = []                  # head-first
        self.tails = set()
        self.harp = (3, 5)
        self.score = 0
        self.counter = 4                # engine stepCounter
        self.key = False                # engine keyCommand: a turn's step is armed
        self.pending = -1
        self.mode = "STEP_SAFE"         # or "SCHED": moves only on the beat
        self.rotated = False            # SCHED / timing: the head turned since the last step
        self.timing = None              # Timing: step only when the recorded game did
        self.wall_wait = False          # timing: facing the wall at a step, one tick of grace
        self.last_step_tick = 0
        self.lost = None                # reason once dead
        self.spear_dir = -1             # flight direction of the spear that killed him
        self.spears = []                # [x, y, dir]
        self.throws = {}                # tick -> [(x, y, dir)]
        self.throw_limit = float("inf") # throws after this tick are left out
        self.harp_next = lambda: None
        self.tick_no = 0
        self.steps = []                 # (tick, x, y, flush) for checks
        self.eaten = []                 # ticks a harp was eaten

    def copy(self):
        s = Sim.__new__(Sim)
        s.__dict__.update(self.__dict__)
        s.tail = list(self.tail)
        s.tails = set(self.tails)
        s.spears = [list(p) for p in self.spears]
        s.steps = []
        s.eaten = []
        return s

    # --- what a recognizer may ask
    @property
    def heading(self):
        return self.pending if self.pending >= 0 else self.hd

    @property
    def has_tail(self):
        return bool(self.tail)

    def room(self, d, cap=6):
        x, y, n = self.hx, self.hy, 0
        while n < cap:
            x += DX[d]
            y += DY[d]
            if not inb(x, y) or (x, y) in self.tails:
                break
            n += 1
        return n

    # --- engine
    def speared_by(self, d):
        if self.lost is None:
            self.spear_dir = d
        self.lose("speared")

    def lose(self, why):
        if self.lost is None:
            self.lost = why

    def step(self, flush=False):
        self.key = False
        if self.lost:
            return
        self.counter = 4
        self.last = self.hd
        ox, oy = self.hx, self.hy
        if self.tail:
            self.tails.add((ox, oy))
        self.hx += DX[self.hd]
        self.hy += DY[self.hd]
        self.steps.append((self.tick_no, self.hx, self.hy, flush))
        if not inb(self.hx, self.hy):
            self.lose("hit the wall")
            return
        grow = (self.hx, self.hy) == self.harp
        if grow:
            self.score += 1
            self.eaten.append(self.tick_no)
            self.harp = self.harp_next()
        if (self.hx, self.hy) in self.tails:
            self.lose("ran into the tail")
            return
        if grow:
            self.tail.insert(0, (ox, oy))
            self.tails.add((ox, oy))
        elif self.tail:
            lx, ly = self.tail.pop()
            self.tails.discard((lx, ly))
            self.tail.insert(0, (ox, oy))
            self.tails.add((ox, oy))
        self.rotated = False
        d = self.pending                # promotePendingDir
        if d >= 0:
            self.pending = -1
            nx, ny = self.hx + DX[d], self.hy + DY[d]
            if d != self.hd and not (self.tail and d == (self.hd + 2) % 4) \
                    and inb(nx, ny) and (nx, ny) not in self.tails:
                self.hd = d
                self.rotated = True

    def swipe(self, d, hold=False):
        """Engine onSwipe (STEP_SAFE or SCHED); returns the engine's result tag."""
        if self.lost:
            return "off"
        if self.timing is not None:
            return self.swipe_timed(d, hold)
        if self.mode == "SCHED":
            if not self.rotated:
                if d == self.hd:
                    return "same"
                if self.tail and d == (self.hd + 2) % 4:
                    return "rev-block"
                nx, ny = self.hx + DX[d], self.hy + DY[d]
                if not inb(nx, ny):
                    return "wall-block"
                if (nx, ny) in self.tails:
                    return "tail-block"
                self.hd = d
                self.rotated = True
                return "turn"
            if d != self.hd:
                self.pending = d
                return "queued"
            return "same"
        if d == self.hd:
            return "same"
        if hold:
            if self.key:
                self.pending = d
                return "queued"
            if self.tail and d == (self.last + 2) % 4:
                return "rev-block"
            nx, ny = self.hx + DX[d], self.hy + DY[d]
            if inb(nx, ny) and (nx, ny) in self.tails:
                return "tail-block"
            self.hd = d
            return "rotate"
        pinned = self.key and self.room(self.hd, 1) == 0
        ref = self.last if pinned else self.hd
        if self.tail and d == (ref + 2) % 4:
            return "rev-block"
        nx, ny = self.hx + DX[d], self.hy + DY[d]
        if inb(nx, ny) and (nx, ny) in self.tails and not (self.key and not pinned):
            return "tail-block"
        tag = "step"
        if self.key:
            if pinned:
                tag = "re-aim"
            else:
                c = self.counter
                self.step(flush=True)
                self.counter = c
                if self.lost:
                    return "flush-died"
                tag = "flush"
                if self.room(d, 1) == 0 and inb(self.hx + DX[d], self.hy + DY[d]):
                    self.hd = self.last
                    return "flush-tail-block"
        self.hd = d
        self.key = True
        return tag

    def swipe_timed(self, d, hold):
        """A turn when steps follow a Timing: it only changes the heading
        (STEP_SAFE's checks); the second half of a U-turn waits for the
        next step if the first half has not stepped yet."""
        if d == self.hd:
            return "same"
        if hold and self.rotated:
            self.pending = d
            return "queued"
        if self.tail and d == (self.last + 2) % 4:
            return "rev-block"
        nx, ny = self.hx + DX[d], self.hy + DY[d]
        if inb(nx, ny) and (nx, ny) in self.tails:
            return "tail-block"
        self.hd = d
        self.rotated = True
        return "turn"

    def timed_step(self):
        """A step the Timing calls for; facing the wall, it waits one tick."""
        if not inb(self.hx + DX[self.hd], self.hy + DY[self.hd]) and not self.wall_wait:
            self.wall_wait = True
            return
        self.wall_wait = False
        self.step()
        self.last_step_tick = self.tick_no

    def tick(self):
        """One base tick: movement, spears, then this tick's throws."""
        self.tick_no += 1
        if self.timing is not None:
            if not self.lost and (self.wall_wait or self.timing.due(self.tick_no, self.last_step_tick)):
                self.timed_step()
        else:
            due = self.counter <= 0 or (self.key and self.mode != "SCHED")
            if due and not self.lost:
                if inb(self.hx + DX[self.hd], self.hy + DY[self.hd]) or self.counter <= GRACE:
                    self.step()
        self.counter -= 1
        keep = []
        for s in self.spears:
            if (s[0], s[1]) != (self.hx, self.hy):
                s[0] += DX[s[2]]
                s[1] += DY[s[2]]
                if (s[0], s[1]) == (self.hx, self.hy):
                    self.speared_by(s[2])
                if not inb(s[0] + DX[s[2]], s[1] + DY[s[2]]):
                    continue            # stuck in the wall
            else:
                self.speared_by(s[2])
            keep.append(s)
        self.spears = keep
        if self.tick_no <= self.throw_limit:
            for x, y, d in self.throws.get(self.tick_no, ()):
                self.spears.append([x, y, d])


class Timing:
    """When David moved in a recorded game: steps in the movement of ticks,
    and flush steps between ticks (slot -> the times they happened)."""

    def __init__(self, play):
        self.ticks, self.flush, every = set(), {}, []
        for st in play.steps:
            if st.get("flush"):
                self.flush.setdefault(st["tk"], []).append(st["t"])
            else:
                self.ticks.add(st["tk"])
            every.append(st["tk"])
        self.sorted = sorted(every)
        self.end = play.death["tk"] if play.death else (self.sorted[-1] if every else 0)

    def due(self, tick, last_step_tick):
        if tick <= self.end:
            return tick in self.ticks
        return tick - last_step_tick >= 4       # past the recording: the plain beat

    def last_step_before(self, k):
        i = bisect.bisect_right(self.sorted, k) - 1
        return self.sorted[i] if i >= 0 else 0


def held_flags(cmds):
    """The engine holds the second half of a U-turn (InputSession: kind
    uturn, second of the pair); logged cmd lines carry no index, so pairs
    are found by time."""
    out = []
    first = None
    for c in cmds:
        hold = False
        if c["kind"] == "uturn":
            if first is not None and first["t"] == c["t"]:
                hold = True
                first = None
            else:
                first = c
        else:
            first = None
        out.append(hold)
    return out


def start_sim(play):
    """A fresh game for `play` with its recorded throws and harps."""
    s = Sim()
    s.mode = getattr(play, "mode", None) or "STEP_SAFE"
    for th in play.spears:
        s.throws.setdefault(th["tk"], []).append((th["at"][0], th["at"][1], LET.index(th["d"])))
    nexts = iter([tuple(h["next"]) for h in play.harps if "next" in h])
    s.harp_next = lambda: next(nexts, None)
    return s


def replay(play, on_slot=None):
    """Re-run a recorded game: before tick k+1, the commands the game
    logged after tick k. on_slot(k, sim, cmds) is called at each slot
    before its commands run. Returns (sim, problems)."""
    s = start_sim(play)
    by_tk = {}
    for c, h in zip(play.cmds, held_flags(play.cmds)):
        by_tk.setdefault(c["tk"], []).append((c, h))
    last_tk = play.death["tk"] if play.death else max([st["tk"] for st in play.steps] + [0])
    problems = []
    for k in range(0, last_tk + 1):
        cs = by_tk.get(k, [])
        if on_slot:
            on_slot(k, s, cs)
        for c, h in cs:
            r = s.swipe(LET.index(c["dir"]), h)
            if r != c["res"] and len(problems) < 20:
                problems.append(f"tk {k}: cmd {c['dir']} hold={h} -> {r}, game said {c['res']}")
        if s.lost or k == last_tk:
            break
        s.tick()
        if s.lost:
            break
    return s, problems


def check(play):
    """Replay `play` from its log and compare with what the game logged."""
    s, problems = replay(play)
    logged = [(st["tk"], st["head"][0], st["head"][1], bool(st.get("flush"))) for st in play.steps]
    got = list(s.steps)
    if got[:len(logged)] != logged:
        for i, (a, b) in enumerate(zip(got, logged)):
            if a != b:
                problems.append(f"step {i}: sim {a} game {b}")
                break
        else:
            problems.append(f"steps: sim {len(got)} game {len(logged)}")
    if play.death:
        want = (play.death["tk"], play.death["reason"])
        if (s.tick_no, s.lost) != want:
            problems.append(f"death: sim tick {s.tick_no} {s.lost}, game {want}")
    return problems

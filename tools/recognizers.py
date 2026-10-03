"""Python ports of the in-game recognizers (Recognizers.kt), for offline
replay of logged strokes. Keep them in step with the Kotlin code:
tools/check_parity.py replays the labeled trajectories through both and
diffs the results.

Directions use the engine's numbering: UP=0, RIGHT=1, DOWN=2, LEFT=3.
Positions are dp, times ms, screen y grows downward."""
import math

UP, RIGHT, DOWN, LEFT = 0, 1, 2, 3
LETTER = "URDL"
VEC = {UP: (0.0, -1.0), RIGHT: (1.0, 0.0), DOWN: (0.0, 1.0), LEFT: (-1.0, 0.0)}


def angle_deg(fx, fy, vx, vy):
    """Signed angle of v from f; positive is clockwise on screen (right)."""
    return math.degrees(math.atan2(fx * vy - fy * vx, fx * vx + fy * vy))


class Game:
    """What a recognizer may ask: heading (a queued turn counts), whether
    there is a tail, and free cells ahead in a direction."""
    def __init__(self, heading, has_tail=True, room=None):
        self.heading = heading
        self.has_tail = has_tail
        self._room = room or (lambda d: 6)

    def room(self, d):
        return self._room(d)


class Original:
    """The first commit: 42dp from the anchor on either axis, dominant axis
    wins, anchor jumps to the finger; nothing on lift."""
    name = "O"
    threshold = 42.0

    def down(self, t, x, y):
        self.ax, self.ay = x, y

    def move(self, t, x, y, g):
        dx, dy = x - self.ax, y - self.ay
        if abs(dx) < self.threshold and abs(dy) < self.threshold:
            return []
        if abs(dx) > abs(dy):
            d = RIGHT if dx > 0 else LEFT
        else:
            d = DOWN if dy > 0 else UP
        self.ax, self.ay = x, y
        return [(d, "orig")]

    def up(self, t, x, y, g):
        return []


class Smart:
    """SmartRecognizer: heading-relative, speed-scaled threshold, sliding
    anchor, follow-through cooldown, corner rule for chains, lift rule,
    U-turn for backward swipes with a tail. See Recognizers.kt."""
    name = "S"
    fastDp, slowDp = 8.0, 16.0
    fastSpeed, slowSpeed = 400.0, 100.0
    speedWinMs = 50
    anchorAgeMs = 300
    forwardDeg, backDeg, backFactor = 30.0, 160.0, 1.5
    cooldownMs = 150
    chainDp, cornerDeg = 14.0, 50.0
    liftDp, liftMaxMs = 10.0, 300

    def __init__(self, **over):
        for k, v in over.items():
            setattr(self, k, v)

    def down(self, t, x, y):
        self.ts, self.xs, self.ys = [t], [x], [y]
        self.anchor = 0
        self.fired = 0
        self.fireT = 0
        self.mx = self.my = 0.0

    def move(self, t, x, y, g):
        if not getattr(self, "ts", None):
            self.down(t, x, y)
            return []
        self.ts.append(t); self.xs.append(x); self.ys.append(y)
        j = len(self.ts) - 1
        if self.fired > 0 and t - self.fireT < self.cooldownMs:
            self.anchor = j
            return []
        while self.anchor < j and t - self.ts[self.anchor] > self.anchorAgeMs:
            self.anchor += 1
        vx = x - self.xs[self.anchor]
        vy = y - self.ys[self.anchor]
        thr = self.threshold(j)
        if self.fired > 0:
            thr = max(thr, self.chainDp)
            if abs(angle_deg(self.mx, self.my, vx, vy)) < self.cornerDeg:
                return []
        cmds = self.classify(vx, vy, thr, g, "chain" if self.fired > 0 else "swipe")
        if not cmds:
            return []
        ln = math.hypot(vx, vy)
        self.mx, self.my = vx / ln, vy / ln
        self.fired += 1
        self.fireT = t
        self.anchor = j
        return cmds

    def up(self, t, x, y, g):
        cmds = self.move(t, x, y, g)
        if cmds or self.fired > 0 or len(self.ts) < 2 or t - self.ts[0] > self.liftMaxMs:
            return cmds
        return self.classify(self.xs[-1] - self.xs[0], self.ys[-1] - self.ys[0],
                             self.liftDp, g, "lift")

    def threshold(self, j):
        ts, xs, ys = self.ts, self.xs, self.ys
        i = j
        while i > 0 and ts[j] - ts[i - 1] <= self.speedWinMs:
            i -= 1
        dt = ts[j] - ts[i]
        sp = math.hypot(xs[j] - xs[i], ys[j] - ys[i]) * 1000.0 / dt if dt > 0 else 0.0
        k = min(1.0, max(0.0, (sp - self.slowSpeed) / (self.fastSpeed - self.slowSpeed)))
        return self.slowDp + (self.fastDp - self.slowDp) * k

    def classify(self, vx, vy, thr, g, kind):
        ln = math.hypot(vx, vy)
        if ln < thr:
            return []
        h = g.heading
        fx, fy = VEC[h]
        a = angle_deg(fx, fy, vx, vy)
        absA = abs(a)
        if absA <= self.forwardDeg:
            return []
        right, left = (h + 1) % 4, (h + 3) % 4
        if absA < self.backDeg:
            if ln * math.sin(math.radians(absA)) < thr:
                return []
            return [(right if a > 0 else left, kind)]
        if ln < thr * self.backFactor:
            return []
        back = (h + 2) % 4
        if not g.has_tail:
            return [(back, "reverse")]
        side = right if a > 0 else left
        other = left if a > 0 else right
        if ln * math.sin(math.radians(absA)) < 3.0 and g.room(other) > g.room(side):
            side = other
        if g.room(side) == 0 and g.room(other) > 0:
            side = left if side == right else right
        return [(side, "uturn"), (back, "uturn")]


class Anchor:
    """The previous shipped recognizer (absolute directions, 14dp from the
    anchor along an axis dominating 1.5:1, no repeats within a stroke)."""
    name = "A"
    swipe, ratio = 14.0, 1.5

    def down(self, t, x, y):
        self.ax, self.ay, self.last = x, y, None

    def move(self, t, x, y, g):
        dx, dy = x - self.ax, y - self.ay
        if max(abs(dx), abs(dy)) < self.swipe:
            return []
        if abs(dx) >= self.ratio * abs(dy):
            d = RIGHT if dx > 0 else LEFT
        elif abs(dy) >= self.ratio * abs(dx):
            d = DOWN if dy > 0 else UP
        else:
            return []
        self.ax, self.ay = x, y
        if d == self.last:
            return []
        self.last = d
        return [(d, "swipe")]

    def up(self, t, x, y, g):
        return self.move(t, x, y, g)


ALL = {"O": Original, "S": Smart, "A": Anchor}


def replay(rec, samples, game, apply=None):
    """Feed one stroke [(t, x, y), ...] (first = down, last = up) through
    `rec`. `apply(cmd)` updates `game` after each command; by default every
    command is assumed accepted (heading becomes its direction).
    Returns [(t, dir, kind), ...]."""
    if apply is None:
        def apply(c):
            game.heading = c[0]
    out = []
    t0, x0, y0 = samples[0]
    rec.down(t0, x0, y0)
    for k, (t, x, y) in enumerate(samples[1:], 1):
        last = k == len(samples) - 1
        cmds = rec.up(t, x, y, game) if last else rec.move(t, x, y, game)
        for c in cmds:
            out.append((t, c[0], c[1]))
            apply(c)
    if len(samples) == 1:
        for c in rec.up(t0, x0, y0, game):
            out.append((t0, c[0], c[1]))
            apply(c)
    return out

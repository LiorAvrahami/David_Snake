#!/usr/bin/env python3
"""Reads an input-test file written by the game in debug mode
(Downloads/DavidSnake_InputLab_v*.txt) and reports, per arm:

  games      score, survival, death causes
  input      strokes, commands and their engine results per minute
  failures   flags (double taps) and what preceded them, silent strokes
             (a real swipe that fired nothing), blocked commands, deaths
             right after a silent or blocked input
  latency    finger down to first command
  replay     every recorded stroke through every recognizer (O, S, A):
             the active one must reproduce its own logged commands
             (parity), the others show what they would have done

Usage: python3 tools/lab_report.py FILE [--strokes] [--play N]"""
import json, math, os, statistics, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recognizers as R

FLAG_WINDOW_MS = 1500     # a flag blames inputs this far back
SILENT_DP = 15.0          # a stroke this long that fired nothing is "silent"
DEATH_WINDOW_MS = 1000    # input this close before a death may have caused it
TURNED = {"turn", "queued", "step", "flush", "re-aim"}
BLOCKED = {"rev-block", "wall-block", "tail-block", "flush-tail-block"}
ARM_REC = {"O-ORIGINAL": "O", "O-PLUS": "P", "S-STEP": "S", "S-SCHED": "S"}
DIR = {c: i for i, c in enumerate(R.LETTER)}


def load(path):
    recs = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"line {n}: unreadable ({e}); skipped", file=sys.stderr)
    return recs


class Stroke:
    def __init__(self, r):
        self.play = r["play"]
        self.t0 = r["t0"]
        self.end = r["end"]
        self.samples = []
        t = r["t0"]
        for p in r["pts"].split(";"):
            dt, x, y = p.split(",")
            t += int(dt)
            self.samples.append((t, int(x) / 10.0, int(y) / 10.0))
        self.ih = r.get("ih", "")
        self.cmds = []          # logged cmd records within this stroke

    @property
    def t1(self):
        return self.samples[-1][0]

    @property
    def dur(self):
        return self.t1 - self.t0

    def length(self):
        (_, x0, y0), (_, x1, y1) = self.samples[0], self.samples[-1]
        return math.hypot(x1 - x0, y1 - y0)

    def path(self):
        s = self.samples
        return sum(math.hypot(s[i][1] - s[i - 1][1], s[i][2] - s[i - 1][2]) for i in range(1, len(s)))


class Play:
    def __init__(self, r):
        self.n = r["n"]
        self.arm = r["arm"]
        self.arm_n = r.get("arm_n")
        self.t = r["t"]
        self.end = None
        self.death = None
        self.aborted = False
        self.strokes, self.cmds, self.steps, self.flags, self.harps = [], [], [], [], []

    @property
    def rec(self):
        return ARM_REC.get(self.arm, "S")

    @property
    def dur(self):
        return (self.end["dur_ms"] if self.end else
                (max([s.t1 for s in self.strokes] + [self.t]) - self.t))


def build(recs):
    session, plays = None, {}
    for r in recs:
        k = r.get("k")
        if k == "session":
            session = r
        elif k == "play":
            plays[r["n"]] = Play(r)
        elif k in ("play_end", "play_abort"):
            p = plays.get(r.get("n", r.get("play")))
            if p:
                if k == "play_end":
                    p.end = r
                else:
                    p.aborted = True
        else:
            p = plays.get(r.get("play"))
            if p is None:
                continue
            if k == "stroke":
                p.strokes.append(Stroke(r))
            elif k == "cmd":
                p.cmds.append(r)
            elif k == "step":
                p.steps.append(r)
            elif k == "flag":
                p.flags.append(r)
            elif k == "harp":
                p.harps.append(r)
            elif k == "death":
                p.death = r
    # attach commands to strokes by time
    for p in plays.values():
        p.strokes.sort(key=lambda s: s.t0)
        for c in p.cmds:
            for s in p.strokes:
                if s.t0 <= c["t"] <= s.t1:
                    s.cmds.append(c)
                    break
    return session, [plays[k] for k in sorted(plays)]


def score_at(p, t):
    return sum(1 for h in p.harps if h["t"] <= t)


def replay_stroke(rec_name, p, s, use_logged_heading):
    """Replay one stroke. With use_logged_heading (parity), every sample is
    judged against the heading the game logged for it; otherwise the
    stroke's starting heading is used and each command is assumed taken."""
    rec = R.ALL[rec_name]()
    tail = score_at(p, s.t0) > 0
    if use_logged_heading and len(s.ih) == len(s.samples):
        g = R.Game(DIR.get(s.ih[0], 0), tail)
        out = []
        for k, (t, x, y) in enumerate(s.samples):
            g.heading = DIR.get(s.ih[k], g.heading)
            if k == 0:
                rec.down(t, x, y)
                continue
            last = k == len(s.samples) - 1 and s.end == "up"
            cmds = rec.up(t, x, y, g) if last else rec.move(t, x, y, g)
            for c in cmds:
                out.append((t, c[0], c[1]))
                g.heading = c[0]    # until the next sample's logged heading
        return out
    g = R.Game(DIR.get(s.ih[:1] or "U", 0), tail)
    samples = s.samples if s.end == "up" else s.samples + [s.samples[-1]]
    return R.replay(rec, samples, g)


def pct(a, b):
    return f"{100.0 * a / b:.0f}%" if b else "-"


def med(xs):
    return f"{statistics.median(xs):.0f}" if xs else "-"


def report(path, show_strokes=False, only_play=None):
    session, plays = build(load(path))
    if session:
        print(f"file: {os.path.basename(path)}  app {session.get('ver')}  "
              f"{session.get('device')}  sdk {session.get('sdk')}  started {session.get('wall')}")
    plays = [p for p in plays if only_play is None or p.n == only_play]
    by_arm = defaultdict(list)
    for p in plays:
        by_arm[p.arm].append(p)
    print(f"games: {len(plays)} ({sum(1 for p in plays if p.end)} finished, "
          f"{sum(1 for p in plays if p.aborted)} aborted)\n")

    parity_bad = 0
    parity_n = 0
    for arm in sorted(by_arm):
        ps = by_arm[arm]
        mins = sum(p.dur for p in ps) / 60000.0 or 1e-9
        strokes = [s for p in ps for s in p.strokes]
        cmds = [c for p in ps for c in p.cmds]
        res = Counter(c["res"] for c in cmds)
        kinds = Counter(c["kind"] for c in cmds)
        flags = [f for p in ps for f in p.flags]
        silent = [s for s in strokes if not s.cmds and s.length() >= SILENT_DP]
        lat = [s.cmds[0]["t"] - s.t0 for s in strokes if s.cmds]
        print(f"== {arm}  ({len(ps)} games, {mins:.1f} min)")
        print("  scores " + " ".join(str(p.end['score']) if p.end else '?' for p in ps) +
              "   survival s " + " ".join(f"{p.dur / 1000:.0f}" for p in ps) +
              "   deaths " + ", ".join(Counter(p.end["reason"] for p in ps if p.end).elements()))
        print(f"  strokes {len(strokes)} ({len(strokes) / mins:.0f}/min)  cmds {len(cmds)} "
              f"({len(cmds) / mins:.0f}/min)  kinds {dict(kinds)}")
        print(f"  results {dict(res)}  blocked {pct(sum(res[b] for b in BLOCKED), len(cmds))}")
        print(f"  first command after touch-down: median {med(lat)} ms")
        print(f"  silent strokes (>= {SILENT_DP:.0f}dp, no command) {len(silent)} "
              f"({pct(len(silent), len(strokes))} of strokes)   multi-command strokes "
              f"{sum(1 for s in strokes if len(s.cmds) > 1)}")
        print(f"  flags {len(flags)} ({len(flags) / mins:.1f}/min)")
        for p in ps:
            for f in p.flags:
                lo = f["t"] - FLAG_WINDOW_MS
                near = [s for s in p.strokes if s.t1 >= lo and s.t0 <= f["t"]]
                desc = "; ".join(
                    f"{s.t0 - f['t']:+d}ms {s.length():.0f}dp/{s.dur}ms -> " +
                    (",".join(f"{c['kind']}:{c['dir']}:{c['res']}" for c in s.cmds) or "nothing")
                    for s in near) or "no stroke in window"
                print(f"    flag g{p.n} {f['phase']}: {desc}")
        for p in ps:
            if not p.end or not p.death:
                continue
            td = p.death["t"]
            near = [s for s in p.strokes if td - DEATH_WINDOW_MS <= s.t1 <= td + 50]
            bad = [s for s in near if not s.cmds or all(c["res"] not in TURNED for c in s.cmds)]
            if bad:
                print(f"    death g{p.n} ({p.end['reason']}) after silent/blocked input: " +
                      "; ".join(f"{s.t1 - td:+d}ms {s.length():.0f}dp -> " +
                                (",".join(c['res'] for c in s.cmds) or "nothing") for s in bad))
        # replay: parity of the active recognizer, and what the others do
        alt = Counter()
        for p in ps:
            for s in p.strokes:
                logged = [(c["t"], DIR[c["dir"]], c["kind"]) for c in s.cmds]
                dead = p.death["t"] if p.death else float("inf")
                mine = [m for m in replay_stroke(p.rec, p, s, True) if m[0] < dead]
                parity_n += 1
                # directions must match; times may shift a sample, since
                # positions are logged rounded to 0.1dp
                if [d for _, d, _ in mine] != [d for _, d, _ in logged]:
                    parity_bad += 1
                    if parity_bad <= 5:
                        print(f"    PARITY g{p.n} stroke@{s.t0}: logged {logged} replay {mine}")
                for name in ("O", "P", "S", "A"):
                    n = len(replay_stroke(name, p, s, False))
                    alt[(name, "fires")] += n
                    alt[(name, "silent-fires")] += 1 if (s in silent and n) else 0
        print("  replay over these strokes (commands / silent strokes it would have fired on): " +
              "  ".join(f"{n} {alt[(n, 'fires')]}/{alt[(n, 'silent-fires')]}" for n in ("O", "P", "S", "A")))
        if show_strokes:
            for p in ps:
                for s in p.strokes:
                    print(f"    g{p.n} {s.t0 - p.t:>7}ms {s.length():5.0f}dp {s.dur:4}ms "
                          f"{s.end:6} ih={s.ih[:1]} -> " +
                          (",".join(f"{c['kind']}:{c['dir']}:{c['res']}" for c in s.cmds) or "-"))
        print()
    print(f"parity (active recognizer re-run on its own strokes): {parity_n - parity_bad}/{parity_n} identical")
    return parity_bad


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    play = None
    if "--play" in sys.argv:
        play = int(sys.argv[sys.argv.index("--play") + 1])
        args = [a for a in args if a != str(play)]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(1 if report(args[0], "--strokes" in sys.argv, play) else 0)

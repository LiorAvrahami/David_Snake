#!/usr/bin/env python3
"""Builds viewer/finishing_rules.html: recorded moves where two ways of
finishing a move disagree, replayed as the game screen.

The proposed gesture windows (not the scoring yet): a window starts at a
gesture start, and gestures starting less than JOIN_MS after a window's start
join it. David is turned by the recorded game's own commands (the moves you
made, as the live model turned them) whose time is at most READ_MS after the
window start. After that, one more command may finish a move, if David turned
in the window:

  300 ms rule           the command comes at most 300 ms after the window start
  regular-step rule     David has not taken his next regular step since the
                        latest turn (only the step the turn itself caused)

The command ends the reading; then David goes straight until he eats the harp
or dies, scored as tools/windowsim.py scores a window.

Usage: python3 tools/make_rules_viewer.py [--per N]   (N examples per kind, default 8)"""
import base64, bisect, json, os, random, re, sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import enginesim as E
import windowsim as WS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(ROOT, "training_recordings")
SPRITES = os.path.join(ROOT, "app", "src", "main", "res", "drawable-nodpi")
JOIN_MS = 250
READ_MS = 250
CLOCK_MS = 300
PRE_TICKS = 24
RULES = ("A", "B")                  # A: 300 ms rule, B: regular-step rule
WORD = {"U": "up", "R": "right", "D": "down", "L": "left"}


def frame(sim):
    return [sim.hx, sim.hy, sim.hd, [c for xy in sim.tail for c in xy], [c for sp in sim.spears for c in sp]]


def tick_clock(p):
    """Time (ms, the samples' clock) of tick k, from the logged steps."""
    pts = [(0, p.t)] + [(s["tk"], s["t"]) for s in p.steps if not s.get("flush")]
    ks = [k for k, _ in pts]

    def at(k):
        i = bisect.bisect_right(ks, k) - 1
        if i < 0:
            return pts[0][1] + 45.0 * (k - pts[0][0])
        if i + 1 < len(pts):
            (k0, t0), (k1, t1) = pts[i], pts[i + 1]
            return t0 + (t1 - t0) * (k - k0) / max(1, k1 - k0)
        k1, t1 = pts[-1]
        return t1 + 45.0 * (k - k1)
    return at


def run_rule(real, k0, t0, cmds, j0, rule, T):
    """One window under one finishing rule, with every tick recorded."""
    sim = real.copy()
    sim.throw_limit = k0 + WS.AIM_TICKS
    sim.harp_next = lambda: None
    frames = [frame(sim)]
    j, done, turned = j0, False, False
    since = None                    # David's steps since the latest turn
    since_r, limit = None, None     # the same for the reading's latest turn, and when it reached 2
    cmd_log, steps, turns = [], [], []
    for k in range(k0, k0 + 4 * WS.MAX_STEPS):
        while not done and j < len(cmds) and cmds[j][0]["tk"] <= k:
            t = cmds[j][0]["t"]
            group = []
            while j < len(cmds) and cmds[j][0]["t"] == t and cmds[j][0]["tk"] <= k:
                group.append(cmds[j])
                j += 1
            dirs = "".join(c["dir"] for c, _ in group)
            finishing = t > t0 + READ_MS
            if finishing:
                why = None
                if not turned:
                    why = "no turn in the window"
                elif rule == "A" and t > t0 + CLOCK_MS:
                    why = "after 300 ms"
                elif rule == "B" and since > 1:
                    why = "after David's next regular step"
                if why:
                    cmd_log.append({"t": t - t0, "dirs": dirs, "read": False, "why": why})
                    done = True
                    break
            h0, pos = sim.heading, (sim.hx, sim.hy)
            tr = False
            for c, hold in group:
                if sim.swipe(E.LET.index(c["dir"]), hold) in WS.TURNED:
                    tr = True
                if sim.lost or sim.eaten:
                    break
            if (sim.hx, sim.hy) != pos:                     # a flushed step
                steps.append(t - t0)
                if since_r is not None and not finishing:
                    since_r += 1
            cmd_log.append({"t": t - t0, "dirs": dirs, "read": True, "finishing": finishing,
                            "turned": tr, "h0": h0, "h1": sim.heading, "since": since})
            if tr:
                turned = True
                since = 0
                turns.append([k - k0, t - t0, sim.heading])
                if not finishing:
                    since_r, limit = 0, None
            if finishing:
                done = True
            if sim.lost or sim.eaten:
                break
        if sim.lost or sim.eaten:
            break
        pos = (sim.hx, sim.hy)
        sim.tick()
        if (sim.hx, sim.hy) != pos:
            ts = T(k + 1) - t0
            steps.append(ts)
            if since is not None:
                since += 1
            if since_r is not None:
                since_r += 1
                if since_r == 2 and limit is None:
                    limit = ts
        frames.append(frame(sim))
        if sim.lost or sim.eaten:
            break
    w = WS.outcome(sim, k0, None, sim.eaten[0] if sim.eaten else None)
    # the rest of the commands (neither read nor checked): shown grayed on the timeline
    return {"frames": frames, "turns": turns, "cmds": cmd_log, "steps": steps, "limit": limit,
            "score": round(w.score, 4), "why": "harp" if w.harp > 0 else w.why, "end": sim.tick_no - k0}


def kind_of(r, last):
    """What the finishing command did with the move: side step or U-turn, how many cells between."""
    rot = (last["h1"] - last["h0"]) % 4
    prev = None
    for c in r["cmds"]:
        if c["read"] and c is not last and c.get("turned"):
            prev = c
    if prev is None:
        return "move"
    r1 = (prev["h1"] - prev["h0"]) % 4
    if rot == 0:
        return "command that changes nothing"
    if rot == 2 or r1 == 2:
        return "reversal"
    return "U-turn" if rot == r1 else "side step"


def game_examples(job):
    path, n = job
    _, plays = build(load(path))
    p = [q for q in plays if q.n == n][0]
    samples = WS.samples_of(p)
    slots = WS.slots_of(p, samples)
    times = [s[0] for s in samples]
    starts = sorted(t for s in p.strokes for t in WS.gesture_starts(s))
    wins, w0 = {}, None
    for t in starts:
        if w0 is None or t - w0 > JOIN_MS:
            w0 = t
            wins.setdefault(slots[bisect.bisect_left(times, t)], t)
    T = tick_clock(p)
    cmds = list(zip(p.cmds, E.held_flags(p.cmds)))
    cmd_tk = [c["tk"] for c, _ in cmds]
    real = E.start_sim(p)
    last_tk = p.death["tk"] if p.death else max([s["tk"] for s in p.steps] + [0])

    def rframe(sim):
        return frame(sim) + [list(sim.harp) if sim.harp else None]

    hist = [rframe(real)]
    out, stats = [], {"windows": 0}
    j = 0
    for k in range(0, last_tk + 1):
        if k in wins:
            t0 = wins[k]
            stats["windows"] += 1
            res = {r: run_rule(real, k, t0, cmds, bisect.bisect_left(cmd_tk, k), r, T) for r in RULES}
            la = [c for c in res["A"]["cmds"] if c.get("finishing") or not c["read"]]
            lb = [c for c in res["B"]["cmds"] if c.get("finishing") or not c["read"]]
            a_fin = la[0] if la and la[0]["read"] else None
            b_fin = lb[0] if lb and lb[0]["read"] else None
            if (a_fin is None) != (b_fin is None):
                cat = "clock reads a later decision" if a_fin else "clock cuts a tight move"
                fin = a_fin or b_fin
                move = kind_of(res["A" if a_fin else "B"], fin)
                cells = fin["since"]
                stats[cat] = stats.get(cat, 0) + 1
                differs = abs(res["A"]["score"] - res["B"]["score"]) > 0.05
                if differs:
                    stats[cat + ", score differs"] = stats.get(cat + ", score differs", 0) + 1
                end = max(len(res[r]["frames"]) for r in RULES)
                fin_samples = []
                for x in range(len(samples)):
                    t, fx, fy, first, last, up = samples[x]
                    if t > t0 + 1200:
                        break
                    if t >= t0 - PRE_TICKS * 45:
                        fin_samples.append([round(t - t0), fx, fy, slots[x] - k, int(first), int(last)])
                att = [[tk - k, sx, sy, d] for tk, lst in real.throws.items()
                       if k - PRE_TICKS - 20 < tk <= k + WS.AIM_TICKS for sx, sy, d in lst]
                later = [c for c, _ in cmds[bisect.bisect_left(cmd_tk, k):] if c["t"] <= t0 + 1200]
                out.append({"file": os.path.basename(path), "game": n, "arm": p.arm, "k": k, "t0": t0,
                            "category": cat, "move": move, "cells": cells, "differs": differs,
                            "harp": list(real.harp) if real.harp else None, "score0": real.score,
                            "pre": hist[-(PRE_TICKS + 1):-1], "attackers": att, "finger": fin_samples,
                            "gstarts": [round(s - t0) for s in starts if -400 <= s - t0 <= 1200],
                            "recorded": [[round(c["t"] - t0), c["dir"]] for c in later],
                            "tick_ms": [round(T(k + i) - t0, 1) for i in range(-PRE_TICKS, end + 1)],
                            "res": res})
        while j < len(cmds) and cmds[j][0]["tk"] == k:
            c, h = cmds[j]
            real.swipe(E.LET.index(c["dir"]), h)
            j += 1
        if real.lost:
            break
        real.tick()
        hist.append(rframe(real))
        if len(hist) > PRE_TICKS + 2:
            del hist[0]
        if real.lost:
            break
    return out, stats


def file_label(f):
    m = re.search(r"_(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})", f)
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(m.group(2)) - 1]} {int(m.group(3))}, {m.group(4)}:{m.group(5)} file" if m else f


def story(w):
    """The example in words."""
    A, B = w["res"]["A"], w["res"]["B"]
    r = A if w["category"].startswith("clock reads") else B
    fin = [c for c in r["cmds"] if c.get("finishing")][0]
    prev = [c for c in r["cmds"] if c["read"] and not c.get("finishing") and c.get("turned")][-1]
    d1 = WORD[prev["dirs"][-1]]
    d2 = " then ".join(WORD[x] for x in fin["dirs"])
    own = [s for s in r["steps"] if s > prev["t"]][0]
    lim = B["limit"]
    if w["category"].startswith("clock reads"):
        return (f"In the recorded game you turned David {d1} at {prev['t']:.0f} ms. He stepped at {own:.0f} ms "
                f"(the turn's own step) and at {lim:.0f} ms (his next regular step), so he had moved two cells. "
                f"Then you turned {d2} at {fin['t']:.0f} ms: a separate decision, made after he moved on. "
                f"The 300 ms rule reads it ({fin['t']:.0f} ms is before 300 ms). The regular-step rule doesn't, "
                f"because David had already taken his next regular step.")
    return (f"In the recorded game you turned David {d1} at {prev['t']:.0f} ms. He took the turn's own step at "
            f"{own:.0f} ms, and his next regular step was due at {lim:.0f} ms. You turned {d2} at {fin['t']:.0f} ms, "
            f"before that step: one move, with one cell between the turns. The 300 ms rule refuses it "
            f"({fin['t']:.0f} ms is after 300 ms), so David keeps going {d1}. The regular-step rule reads it.")


def main():
    per = 8
    if "--per" in sys.argv:
        per = int(sys.argv[sys.argv.index("--per") + 1])
    files = []
    for name in sorted(os.listdir(REC)):
        if name.endswith(".txt"):
            _, plays = build(load(os.path.join(REC, name)))
            first = min((q.wall for q in plays if q.wall), default="")
            files.append((first, os.path.join(REC, name)))
    jobs = []
    for _, path in sorted(files):
        _, plays = build(load(path))
        jobs += [(path, q.n) for q in plays if q.spears and not q.aborted and q.strokes]
    with Pool(os.cpu_count()) as pool:
        got = pool.map(game_examples, jobs)
    wins = [w for r, _ in got for w in r]
    stats = {}
    for _, s in got:
        for key, v in s.items():
            stats[key] = stats.get(key, 0) + v
    rnd = random.Random(7)
    scen = []
    titles = {"clock reads a later decision": "The 300 ms rule also reads a later decision",
              "clock cuts a tight move": "The 300 ms rule cuts a one-cell move in half"}
    for cat in ("clock reads a later decision", "clock cuts a tight move"):
        pool_ = [w for w in wins if w["category"] == cat and w["differs"]]
        rest = [w for w in wins if w["category"] == cat and not w["differs"]]
        rnd.shuffle(pool_)
        rnd.shuffle(rest)
        pick = []
        for move in ("side step", "U-turn"):            # both kinds of move, then anything
            pick += [w for w in pool_ if w["move"] == move][:per // 2]
        pick += [w for w in pool_ if w not in pick][:max(0, per - len(pick))]
        pick += rest[:max(0, per - len(pick))]
        for w in sorted(pick, key=lambda w: (w["file"], w["game"], w["k"])):
            w["title"] = titles[cat]
            w["label"] = f"game {w['game']}, {file_label(w['file'])}, tick {w['k']}"
            w["story"] = story(w)
            scen.append(w)
    sprites = {}
    for name in ("background", "davide", "note1", "note2", "note3", "harp1", "harp2", "harp3", "spear",
                 "spear_stuck", "attacker_steady", "attacker_throw"):
        with open(os.path.join(SPRITES, name + ".png"), "rb") as f:
            sprites[name] = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    data = {"games": len(jobs), "stats": stats, "scenarios": scen, "tick_ms": 45, "pre_ticks": PRE_TICKS,
            "read_ms": READ_MS, "clock_ms": CLOCK_MS, "join_ms": JOIN_MS, "decay": WS.DECAY, "death": WS.DEATH}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules_viewer_template.html")) as f:
        html = f.read()
    html = html.replace("/*SPRITES*/{}", json.dumps(sprites)).replace("/*DATA*/{}", json.dumps(data, separators=(",", ":")))
    out = os.path.join(ROOT, "viewer", "finishing_rules.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(html)
    print(f"wrote {out}: {len(scen)} examples; {stats}; {os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    main()

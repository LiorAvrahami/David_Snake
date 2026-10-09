#!/usr/bin/env python3
"""Builds viewer/decisions.html: a page to open in a browser that replays
example gestures from the training recordings as the game screen, once per
input method (S2-FAST, ML-1, ML-2), next to the finger movement.

Every gesture window of every recording is simulated for the three methods
(tools/windowsim.py scoring; spears as the scorer sees them); example
windows are picked by kind (different turns, timing only, ML-1 vs ML-2,
all the same), and the page also shows how often the methods differ. Each
example starts PRE_TICKS before the gesture, with the real game, for
context.

Usage: python3 tools/make_viewer.py [--per N]   (N examples per kind, default 8)"""
import base64, json, os, random, re, sys
from collections import Counter
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import enginesim as E
import recognizers as R
import simlearn as L
import windowsim as WS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(ROOT, "training_recordings")
SPRITES = os.path.join(ROOT, "app", "src", "main", "res", "drawable-nodpi")
W = 3
MODELS = ("S2-FAST", "ML-1", "ML-2")
PRE_TICKS = 24          # the real game shown before the gesture (about 1.1 s)


def policies():
    out = {}
    for name in ("ML-1", "ML-2"):
        spec = json.load(open(os.path.join(ROOT, "tools", "models", name.lower() + ".json")))
        out[name] = L.Policy.from_spec(spec)
    return out


def traced(real, k0, inputs):
    """A gesture window as tools/windowsim.py runs it, recording every tick:
    David (cell, heading), his tail, the spears, and the turns made."""
    sim = real.copy()
    sim.throw_limit = k0 + WS.AIM_TICKS
    sim.harp_next = lambda: None

    def frame():
        return [sim.hx, sim.hy, sim.hd, [c for xy in sim.tail for c in xy], [c for s in sim.spears for c in s]]

    frames = [frame()]
    turns = []
    for k in range(k0, k0 + 4 * WS.MAX_STEPS):
        for t, cmds in inputs.slot(k, sim):
            h0, turned = sim.heading, False
            for d, hold in cmds:
                if sim.swipe(d, hold) in WS.TURNED:
                    turned = True
                if sim.lost or sim.eaten:
                    break
            if turned:
                inputs.last_turn = t
            if sim.heading != h0:
                turns.append([k - k0, t, sim.heading])
            if sim.lost or sim.eaten:
                break
        if sim.lost or sim.eaten:
            frames.append(frame())
            break
        sim.tick()
        frames.append(frame())
        if sim.lost or sim.eaten:
            break
    w = WS.outcome(sim, k0, W, sim.eaten[0] if sim.eaten else None)
    why = "harp" if sim.eaten else w.why
    return {"frames": frames, "turns": turns, "score": round(w.score, 4), "why": why,
            "end": sim.tick_no - k0}


def game_windows(job):
    path, n, pols = job
    _, plays = build(load(path))
    p = [q for q in plays if q.n == n][0]
    g = L.Game(n, p, [W])
    if not g.specs:
        return []
    samples, slots = g.samples, g.slots
    starts = {s.k: s for s in g.specs}
    cmds = list(zip(p.cmds, E.held_flags(p.cmds)))
    real = E.start_sim(p)
    cls = R.ALL["S2F"]
    shadow = None
    last_tk = p.death["tk"] if p.death else max([s["tk"] for s in p.steps] + [0])
    def frame(sim):
        return [sim.hx, sim.hy, sim.hd, [c for xy in sim.tail for c in xy],
                [c for sp in sim.spears for c in sp], list(sim.harp) if sim.harp else None]

    hist = [frame(real)]                # the real game after each tick
    out, i, j = [], 0, 0
    for k in range(0, last_tk + 1):
        if k in starts:
            s = starts[k]
            res = {"S2-FAST": traced(real, k, WS.RecInputs(k, W, cls, shadow, samples, slots, s.i))}
            for m in ("ML-1", "ML-2"):
                res[m] = traced(real, k, L.PolicyInputs(k, W, pols[m], samples, slots, s.i, s.stroke))
            end = k + 4 * W + 8
            fin = []
            for x in range(len(samples)):
                t, fx, fy, first, last, up = samples[x]
                if slots[x] > end:
                    break
                if slots[x] >= k - PRE_TICKS:
                    fin.append([t, fx, fy, slots[x] - k, int(first), int(last)])
            # attackers: those on screen before the gesture and at its start (their
            # spears come by AIM_TICKS after it); an attacker shows AIM_TICKS before
            # its throw and lingers a while after
            att = [[tk - k, sx, sy, d] for tk, lst in real.throws.items()
                   if k - PRE_TICKS - 20 < tk <= k + WS.AIM_TICKS for sx, sy, d in lst]
            out.append({"file": os.path.basename(path), "game": n, "arm": p.arm, "k": k,
                        "harp": list(real.harp) if real.harp else None, "score0": real.score,
                        "pre": hist[-(PRE_TICKS + 1):-1], "attackers": att, "finger": fin, "res": res})
        while True:
            ns = samples[i][0] if i < len(samples) and slots[i] == k else None
            nc = cmds[j][0]["t"] if j < len(cmds) and cmds[j][0]["tk"] == k else None
            if ns is None and nc is None:
                break
            if nc is None or (ns is not None and ns <= nc):
                shadow, _ = WS.feed(cls, shadow, samples[i], real)
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
        hist.append(frame(real))
        if len(hist) > PRE_TICKS + 2:
            del hist[0]
        if real.lost:
            break
    return out


def kind(a, b):
    if [(x[0], x[2]) for x in a["turns"]] == [(x[0], x[2]) for x in b["turns"]]:
        return "same"
    return "timing" if [x[2] for x in a["turns"]] == [x[2] for x in b["turns"]] else "turns"


def file_label(f):
    m = re.search(r"_(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})", f)
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(m.group(2)) - 1]} {int(m.group(3))}, {m.group(4)}:{m.group(5)} file" if m else f


def main():
    per = 8
    if "--per" in sys.argv:
        per = int(sys.argv[sys.argv.index("--per") + 1])
    files = []
    for name in sorted(os.listdir(REC)):
        if name.endswith(".txt"):
            session, plays = build(load(os.path.join(REC, name)))
            first = min((q.wall for q in plays if q.wall), default="")
            files.append((first, os.path.join(REC, name)))
    files = [f for _, f in sorted(files)]
    pols = policies()
    jobs = []
    for path in files:
        _, plays = build(load(path))
        jobs += [(path, q.n, pols) for q in plays if q.spears and not q.aborted and q.strokes]
    with Pool(os.cpu_count()) as pool:
        wins = [w for r in pool.map(game_windows, jobs) for w in r]
    n = len(wins)
    stats = []
    for a, b in (("S2-FAST", "ML-1"), ("S2-FAST", "ML-2"), ("ML-1", "ML-2")):
        c = Counter(kind(w["res"][a], w["res"][b]) for w in wins)
        sc = sum(1 for w in wins if abs(w["res"][a]["score"] - w["res"][b]["score"]) > 0.01)
        stats.append({"pair": f"{a} vs {b}", "same": c["same"] / n, "timing": c["timing"] / n,
                      "turns": c["turns"] / n, "outcome": sc / n})
    allsame = sum(1 for w in wins if kind(w["res"]["S2-FAST"], w["res"]["ML-1"]) == "same"
                  and kind(w["res"]["ML-1"], w["res"]["ML-2"]) == "same") / n
    S, M1, M2 = MODELS
    better = lambda w, a, b: w["res"][a]["score"] > w["res"][b]["score"] + 0.05
    cats = [
        ("Different turns: ML-1 does better", lambda w: kind(w["res"][S], w["res"][M1]) == "turns" and better(w, M1, S)),
        ("Different turns: S2-FAST does better", lambda w: kind(w["res"][S], w["res"][M1]) == "turns" and better(w, S, M1)),
        ("Same turns, different timing (outcome differs)",
         lambda w: kind(w["res"][S], w["res"][M1]) == "timing" and abs(w["res"][S]["score"] - w["res"][M1]["score"]) > 0.05),
        ("ML-1 and ML-2 differ", lambda w: kind(w["res"][M1], w["res"][M2]) != "same"
         and abs(w["res"][M1]["score"] - w["res"][M2]["score"]) > 0.05),
        ("All three decide the same", lambda w: kind(w["res"][S], w["res"][M1]) == "same"
         and kind(w["res"][M1], w["res"][M2]) == "same" and w["res"][S]["turns"]),
    ]
    rnd = random.Random(11)
    scen = []
    for title, cond in cats:
        pool_ = [w for w in wins if cond(w)]
        rnd.shuffle(pool_)
        for w in sorted(pool_[:per if not title.startswith("All") else max(2, per // 2)],
                        key=lambda w: (w["file"], w["game"], w["k"])):
            w = dict(w)
            w["category"] = title
            w["label"] = f"game {w['game']}, {file_label(w['file'])}, tick {w['k']}"
            scen.append(w)
    sprites = {}
    for name in ("background", "davide", "note1", "note2", "note3", "harp1", "harp2", "harp3", "spear",
                 "spear_stuck", "attacker_steady", "attacker_throw"):
        with open(os.path.join(SPRITES, name + ".png"), "rb") as f:
            sprites[name] = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    data = {"windows": n, "games": len(jobs), "stats": stats, "allsame": allsame, "scenarios": scen,
            "tick_ms": 45, "input_ticks": 4 * W, "pre_ticks": PRE_TICKS, "decay": WS.DECAY, "death": WS.DEATH}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer_template.html")) as f:
        html = f.read()
    html = html.replace("/*SPRITES*/{}", json.dumps(sprites)).replace("/*DATA*/{}", json.dumps(data, separators=(",", ":")))
    out = os.path.join(ROOT, "viewer", "decisions.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(html)
    print(f"wrote {out}: {len(scen)} examples from {n} gesture windows in {len(jobs)} games, "
          f"{os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    main()

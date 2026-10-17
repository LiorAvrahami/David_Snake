#!/usr/bin/env python3
"""Builds viewer/bad_turns.html: the turns a model makes in the training
windows that score lower than making no turn at all, drawn one by one.

For every window of the training games (tools/windowsim.py: a game tick with
finger movement) two courses are simulated as the judge does: no turn at all,
and the model's own play. A turn is 'bad' when the no-turn course's Final
score (tolerance 4, alpha 0.9) is higher. The page shows the worst N: the
board at the window's start with both paths, the finger movement that made
the model turn, and the scores (--fragment PATH also writes the page without the
document wrapper, for publishing).

Usage: python3 tools/make_badturns_viewer.py FILE... --model tools/models/mnw1.json
                                           [--holdout 0.3 --seed 1 --never-test F,...] [--top 40] [--fragment PATH]"""
import html, json, os, re, sys
from collections import Counter
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import enginesim as E
import intentlearn as I
import simlearn as L
import windowsim as WS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DELTA, ALPHA = 4, 0.9
SHIFTS = tuple(range(-DELTA, DELTA + 1))
PRE_TICKS, POST_TICKS = 12, 10          # finger shown before the window and after its start
CELL = 20
POL = None


def _init(paths, model):
    global POL
    I._init(paths, True, False)
    POL = L.policy_from_spec(json.load(open(model)))


def outcome(w):
    why = w.why or "nothing"
    return "harp" if w.harp > 0 else (why if why in OUT else "nothing")


def _scan(key):
    """Per window: (tick, none Final, none plain, none outcome, model Final, model plain,
    model outcome, model turns [(tick offset, heading after, sample time)])."""
    g = I.GAMES[key]
    out = []
    for spec in g.specs:
        pn, outs = WS.run_window_tolerant(spec.real, spec.k, spec.t0, WS.Schedule(spec.t0, [], 0), SHIFTS, None, None, spec.prev)
        fn, _ = WS.tolerant_score(pn, outs, ALPHA)
        turns = []

        def watch(ev, sim, k, t, h0):
            if ev == "cmd" and sim.heading != h0:
                turns.append((k - spec.k, sim.heading, t))
        inp = L.PolicyInputs(spec.t0, POL, g.samples, g.slots, spec.i, spec.stroke)
        pm, mouts = WS.run_window_tolerant(spec.real, spec.k, spec.t0, inp, SHIFTS, None, watch, spec.prev)
        fm, _ = WS.tolerant_score(pm, mouts, ALPHA)
        out.append((spec.k, fn, pn.score, outcome(pn), fm, pm.score, outcome(pm), turns))
    return key, out


def _paths(real, k0, inputs):
    """David's cell after every tick of a run, and the run's outcome."""
    cells = [(real.hx, real.hy)]

    def watch(ev, sim, k, t, h0):
        if ev == "tick":
            cells.append((sim.hx, sim.hy))
    w = WS.run_window(real, k0, inputs, watch)
    return cells, outcome(w), w.score


def _render(args):
    """Everything the page needs to draw one window."""
    key, k = args
    g = I.GAMES[key]
    spec = next(s for s in g.specs if s.k == k)
    real = spec.real
    none_cells, none_out, _ = _paths(real, k, WS.Schedule(spec.t0, [], 0))
    rec = []
    inp = L.PolicyInputs(spec.t0, POL, g.samples, g.slots, spec.i, spec.stroke, record=rec)
    model_cells, model_out, _ = _paths(real, k, inp)
    turn_times = [t for kk, f, a, h, t in rec if a]
    fin = [[t, x, y, g.slots[i] - k, int(first), int(last)] for i, (t, x, y, first, last, _) in enumerate(g.samples)
           if k - PRE_TICKS <= g.slots[i] <= k + POST_TICKS]
    att = [[sx, sy, d] for tk, lst in real.throws.items() if k - 40 < tk <= k + WS.AIM_TICKS for sx, sy, d in lst]
    return (key, k), {"head": (real.hx, real.hy, real.heading), "tail": list(real.tail), "harp": real.harp,
                      "spears": [list(s) for s in real.spears], "attackers": att, "t0": spec.t0,
                      "none": (none_cells, none_out), "model": (model_cells, model_out),
                      "turn_times": turn_times, "finger": fin}


def file_label(f):
    m = re.search(r"_(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})", f)
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(m.group(2)) - 1]} {int(m.group(3))} {m.group(4)}:{m.group(5)}" if m else f


LET = "URDL"
NAMES = {"U": "up", "R": "right", "D": "down", "L": "left"}
OUT = {"harp": "ate the harp", "hit the wall": "hit the wall", "speared": "speared", "ran into the tail": "ran into the tail",
       "speared from the side": "speared from the side (costs nothing)", "nothing": "no harp, no death"}


def board_svg(d):
    W, H = E.COLS * CELL, E.ROWS * CELL
    c = lambda xy: (xy[0] * CELL + CELL / 2, xy[1] * CELL + CELL / 2)
    s = [f'<svg viewBox="-6 -6 {W + 12} {H + 12}" class="board" role="img" aria-label="the board at the window start with both paths">',
         f'<rect x="0" y="0" width="{W}" height="{H}" class="field"/>']
    for i in range(1, E.COLS):
        s.append(f'<line x1="{i * CELL}" y1="0" x2="{i * CELL}" y2="{H}" class="grid"/>')
    for j in range(1, E.ROWS):
        s.append(f'<line x1="0" y1="{j * CELL}" x2="{W}" y2="{j * CELL}" class="grid"/>')
    for x, y in d["tail"]:
        s.append(f'<rect x="{x * CELL + 2}" y="{y * CELL + 2}" width="{CELL - 4}" height="{CELL - 4}" class="tail"/>')
    if d["harp"]:
        hx, hy = c(d["harp"])
        s.append(f'<circle cx="{hx}" cy="{hy}" r="7" class="harp"/>')
    for x, y, dd in d["spears"]:
        cx, cy = c((x, y))
        dx, dy = E.DX[dd] * 8, E.DY[dd] * 8
        s.append(f'<line x1="{cx - dx}" y1="{cy - dy}" x2="{cx + dx}" y2="{cy + dy}" class="spear"/>')
    for x, y, dd in d["attackers"]:
        cx, cy = c((x, y))
        s.append(f'<rect x="{cx - 6}" y="{cy - 6}" width="12" height="12" class="attacker"/>')
    for name, (cells, out), off in (("none", d["none"], -2.5), ("model", d["model"], 2.5)):
        pts = " ".join(f"{c(xy)[0] + off},{c(xy)[1] + off}" for xy in cells)
        s.append(f'<polyline points="{pts}" class="path {name}"/>')
        ex, ey = c(cells[-1])
        if out == "harp":
            s.append(f'<circle cx="{ex + off}" cy="{ey + off}" r="6" class="end harp-end {name}"/>')
        else:
            s.append(f'<line x1="{ex + off - 5}" y1="{ey + off - 5}" x2="{ex + off + 5}" y2="{ey + off + 5}" class="end {name}"/>'
                     f'<line x1="{ex + off - 5}" y1="{ey + off + 5}" x2="{ex + off + 5}" y2="{ey + off - 5}" class="end {name}"/>')
    hx, hy, h = d["head"]
    cx, cy = c((hx, hy))
    s.append(f'<rect x="{hx * CELL + 1}" y="{hy * CELL + 1}" width="{CELL - 2}" height="{CELL - 2}" class="david"/>')
    s.append(f'<line x1="{cx}" y1="{cy}" x2="{cx + E.DX[h] * 8}" y2="{cy + E.DY[h] * 8}" class="heading"/>')
    s.append("</svg>")
    return "".join(s)


def finger_svg(d):
    f = d["finger"]
    if not f:
        return ""
    xs, ys = [p[1] for p in f], [p[2] for p in f]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0, 60.0)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    size = 240
    sc = (size - 40) / span
    X = lambda x: (x - mx) * sc + size / 2
    Y = lambda y: (y - my) * sc + size / 2
    t0 = d["t0"]
    s = [f'<svg viewBox="0 0 {size} {size}" class="finger" role="img" aria-label="the finger movement">',
         f'<rect x="0" y="0" width="{size}" height="{size}" class="field"/>']
    # 10 dp grid
    step = 10 * sc
    k = 0
    while k * step < size:
        s.append(f'<line x1="{k * step}" y1="0" x2="{k * step}" y2="{size}" class="grid"/>')
        s.append(f'<line x1="0" y1="{k * step}" x2="{size}" y2="{k * step}" class="grid"/>')
        k += 1
    def seg(cls, pts):
        if len(pts) >= 2:
            s.append('<polyline class="trace ' + cls + '" points="' + " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts) + '"/>')
    before = [(x, y) for t, x, y, *_ in f if t < t0]
    during = [(x, y) for t, x, y, *_ in f if t0 <= t <= t0 + WS.READ_MS]
    after = [(x, y) for t, x, y, *_ in f if t > t0 + WS.READ_MS]
    # join the segments so the line is continuous
    if before and during:
        during = [before[-1]] + during
    if during and after:
        after = [during[-1]] + after
    seg("before", before)
    seg("after", after)
    seg("during", during)
    for t, x, y, *_ in f:
        if t in d["turn_times"]:
            s.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="7" class="turnmark"/>')
    # David's heading at the window start, as a compass
    h = d["head"][2]
    cx, cy = size - 26, 26
    s.append(f'<circle cx="{cx}" cy="{cy}" r="14" class="compass"/>')
    s.append(f'<line x1="{cx}" y1="{cy}" x2="{cx + E.DX[h] * 11}" y2="{cy + E.DY[h] * 11}" class="heading"/>')
    rh = (h + 1) % 4
    s.append(f'<text x="{cx + E.DX[rh] * 22}" y="{cy + E.DY[rh] * 22 + 4}" class="lab" text-anchor="middle">R</text>')
    s.append(f'<text x="8" y="{size - 8}" class="lab">grid 10 dp</text>')
    s.append("</svg>")
    return "".join(s)


def card(i, key, row, d):
    k, fn, pn, on, fm, pm, om, turns = row
    hx, hy, h = d["head"]
    tl = ", ".join(f"{NAMES[LET[hd]]} at tick +{tt}" for tt, hd, _ in turns)
    none_cells, _ = d["none"]
    model_cells, _ = d["model"]
    return (f'<article class="case"><h3>{i}. Game {key[1]} of {html.escape(file_label(key[0]))}, tick {k}</h3>'
            f'<div class="pics">{board_svg(d)}{finger_svg(d)}</div>'
            f'<table class="scores"><tr><th></th><th>what happened</th><th>plain</th><th>Final</th></tr>'
            f'<tr><td><span class="sw none"></span>no turn</td><td>{OUT[on]} after {len(none_cells) - 1} ticks</td><td>{pn:+.3f}</td><td>{fn:+.3f}</td></tr>'
            f'<tr><td><span class="sw model"></span>MNW1: {html.escape(tl)}</td><td>{OUT[om]} after {len(model_cells) - 1} ticks</td><td>{pm:+.3f}</td><td>{fm:+.3f}</td></tr>'
            f'</table><p class="loss">David heading {NAMES[LET[h]]}. The turn cost {fn - fm:.3f} on the Final score.</p></article>')


STYLE = """
<style>
/* layout: summary first, then one card per bad turn; board and finger side by side, stacked on a phone */
:root { --bg: #f7f5f0; --fg: #23211d; --muted: #6b665c; --line: #d9d4c8; --card: #fffdf8;
        --field: #efece4; --grid: #ddd8cc; --tail: #b9b3a4; --david: #2b2824; --harp: #d9a000;
        --spear: #8a2f1c; --attacker: #b5402a; --none: #2a6fd6; --model: #e2702a; --turnmark: #e2702a; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg: #1b1a17; --fg: #ece8df; --muted: #a39d90;
        --line: #3a372f; --card: #23211d; --field: #2a2823; --grid: #3a372f; --tail: #5a564c; --david: #f0ece2;
        --harp: #f0b323; --spear: #e0735a; --attacker: #e0735a; --none: #6ea2ff; --model: #ff9a5c; --turnmark: #ff9a5c; color-scheme: dark } }
:root[data-theme="dark"] { --bg: #1b1a17; --fg: #ece8df; --muted: #a39d90; --line: #3a372f; --card: #23211d;
        --field: #2a2823; --grid: #3a372f; --tail: #5a564c; --david: #f0ece2; --harp: #f0b323; --spear: #e0735a;
        --attacker: #e0735a; --none: #6ea2ff; --model: #ff9a5c; --turnmark: #ff9a5c; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
       padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 20px; }
main { max-width: 980px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 6px; text-wrap: balance; }
h2 { font-size: 1.1rem; margin: 28px 0 8px; }
h3 { font-size: 1rem; margin: 0 0 8px; }
p { max-width: 68ch; }
.lead { color: var(--muted); margin: 0 0 16px; }
table { border-collapse: collapse; font-variant-numeric: tabular-nums; }
.summary td, .summary th, .scores td, .scores th { padding: 4px 10px 4px 0; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
.summary th { font-weight: 600; }
.cases { display: grid; gap: 18px; margin-top: 12px; }
.case { background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: 12px 14px; min-width: 0; }
.pics { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }
svg.board { width: 440px; max-width: 100%; height: auto; }
svg.finger { width: 240px; max-width: 100%; height: auto; }
.field { fill: var(--field); stroke: var(--line); }
.grid { stroke: var(--grid); stroke-width: 0.6; }
.tail { fill: var(--tail); }
.david { fill: var(--david); }
.heading { stroke: var(--bg); stroke-width: 2.5; stroke-linecap: round; }
svg.finger .heading { stroke: var(--fg); }
.harp { fill: var(--harp); }
.spear { stroke: var(--spear); stroke-width: 3; stroke-linecap: round; }
.attacker { fill: var(--attacker); }
.path { fill: none; stroke-width: 3; stroke-linejoin: round; stroke-linecap: round; opacity: 0.9; }
.path.none, .end.none { stroke: var(--none); }
.path.model, .end.model { stroke: var(--model); }
.end { stroke-width: 3; fill: none; }
.harp-end { fill: var(--harp); }
.trace { fill: none; stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
.trace.before { stroke: var(--tail); }
.trace.during { stroke: var(--fg); }
.trace.after { stroke: var(--tail); stroke-dasharray: 3 3; }
.turnmark { fill: none; stroke: var(--turnmark); stroke-width: 2.5; }
.compass { fill: var(--card); stroke: var(--line); }
.lab { font: 11px system-ui, sans-serif; fill: var(--muted); }
.sw { display: inline-block; width: 12px; height: 12px; border-radius: 2px; vertical-align: -1px; margin-right: 6px; }
.sw.none { background: var(--none); } .sw.model { background: var(--model); }
.scores { margin-top: 10px; font-size: 14px; }
.loss { color: var(--muted); margin: 8px 0 0; font-size: 14px; }
.legend { color: var(--muted); font-size: 14px; }
</style>
"""


def main():
    argv = list(sys.argv)
    model, top, fragment = os.path.join(ROOT, "tools", "models", "mnw1.json"), 40, ""
    for opt in ("--model", "--top", "--fragment"):
        if opt in argv:
            i = argv.index(opt)
            if opt == "--model":
                model = argv[i + 1]
            elif opt == "--top":
                top = int(argv[i + 1])
            else:
                fragment = argv[i + 1]
            del argv[i:i + 2]
    args, o = L.options(argv)
    name = json.load(open(model))["name"]
    keys = []
    for path in args:
        _, plays = build(load(path))
        keys += [(os.path.basename(path), p.n) for p in plays if p.spears and not p.aborted]
    with Pool(os.cpu_count(), initializer=_init, initargs=(args, model)) as pool:
        arms = {k: arm for k, arm, _ in pool.map(I._info, keys)}
        trn, _ = L.split(keys, arms, o["holdout"], o["seed"], o["never_test"])
        rows = dict(pool.map(_scan, trn))
        allw = [(key, r) for key in trn for r in rows[key]]
        turned = [(key, r) for key, r in allw if r[7]]
        bad = [(key, r) for key, r in turned if r[1] > r[4] + 1e-9]
        good = [(key, r) for key, r in turned if r[4] > r[1] + 1e-9]
        bad.sort(key=lambda kr: kr[1][1] - kr[1][4], reverse=True)
        shown = bad[:top]
        pics = dict(pool.map(_render, [(key, r[0]) for key, r in shown]))
    n, nt = len(allw), len(turned)
    loss = sum(r[1] - r[4] for _, r in bad)
    gain = sum(r[4] - r[1] for _, r in good)
    pairs = Counter((r[3], r[6]) for _, r in bad)
    buckets = [("a harp lost (0.9 or more)", sum(1 for _, r in bad if r[1] - r[4] >= 0.9)),
               ("0.5 to 0.9", sum(1 for _, r in bad if 0.5 <= r[1] - r[4] < 0.9)),
               ("0.1 to 0.5", sum(1 for _, r in bad if 0.1 <= r[1] - r[4] < 0.5)),
               ("under 0.1", sum(1 for _, r in bad if r[1] - r[4] < 0.1))]
    body = [f"<title>{name} bad turns</title>", '<meta name="claude:viewport-fit-sides" content="cover">', STYLE, "<main>",
            f"<h1>{name}: turns that score below no turn</h1>",
            f'<p class="lead">Every window of the {len(trn)} training games, simulated twice as the judge does: with no turn at all, '
            f'and with {name} playing. A turn is counted here when the no-turn course scores higher on the Final score '
            f'(tolerance {DELTA}, alpha {ALPHA:g}). The worst {len(shown)} are drawn below.</p>',
            '<table class="summary">',
            f"<tr><th>Training windows</th><td>{n}</td></tr>",
            f"<tr><th>Windows where {name} turns</th><td>{nt} ({100 * nt / n:.0f}%)</td></tr>",
            f"<tr><th>Turns scoring below no turn</th><td>{len(bad)} ({100 * len(bad) / max(1, nt):.0f}% of its turns), total loss {loss:.1f}, mean {loss / max(1, len(bad)):.3f}</td></tr>",
            f"<tr><th>Turns scoring above no turn</th><td>{len(good)} ({100 * len(good) / max(1, nt):.0f}%), total gain {gain:.1f}, mean {gain / max(1, len(good)):.3f}</td></tr>",
            f"<tr><th>Turns scoring the same</th><td>{nt - len(bad) - len(good)}</td></tr>",
            "</table>",
            "<h2>How much the bad turns cost</h2><table class=\"summary\">"
            + "".join(f"<tr><th>{t}</th><td>{c}</td></tr>" for t, c in buckets) + "</table>",
            "<h2>What happened instead</h2><table class=\"summary\"><tr><th>no turn</th><th>with the turn</th><th>windows</th></tr>"
            + "".join(f"<tr><td>{OUT[a]}</td><td>{OUT[b]}</td><td>{c}</td></tr>" for (a, b), c in pairs.most_common()) + "</table>",
            f"<h2>The worst {len(shown)}</h2>",
            '<p class="legend">Board: David is the dark square with a short line for his heading, his tail grey, the harp gold, spears in flight dark red, attackers red squares at the walls. '
            '<span class="sw none"></span>blue path: no turn. <span class="sw model"></span>orange path: ' + name + '. A gold dot ends a path at the harp, a cross at a death. '
            'Finger: grey before the window, black during its 250 ms of reading, dashed after; the orange ring is the sample at which ' + name + ' turned. '
            'The compass shows David\'s heading at the window start and his right-hand side.</p>',
            '<div class="cases">']
    for i, (key, r) in enumerate(shown, 1):
        body.append(card(i, key, r, pics[(key, r[0])]))
    body.append("</div></main>")
    frag = "\n".join(body)
    os.makedirs(os.path.join(ROOT, "viewer"), exist_ok=True)
    out = os.path.join(ROOT, "viewer", "bad_turns.html")
    with open(out, "w") as f:
        f.write('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1"></head><body>'
                + frag.replace('<meta name="claude:viewport-fit-sides" content="cover">', "") + "</body></html>")
    if fragment:
        with open(fragment, "w") as f:
            f.write(frag)
    print(f"wrote {out}; {n} windows, {name} turns in {nt}, below no turn {len(bad)} (loss {loss:.1f}), "
          f"above {len(good)} (gain {gain:.1f}); shown {len(shown)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Builds viewer/bad_turns.html: the turns a model makes in the training
windows that score lower than making no turn at all, each replayable.

For every window of the training games (tools/windowsim.py: a game tick with
finger movement) two courses are simulated as the judge does: no turn at all,
and the model's own play. A turn is 'bad' when the no-turn course's Final
score (tolerance 4, alpha 0.9) is higher. The page shows the worst N: for each,
the two boards played side by side from PRE ticks before the window at a
chosen speed, with the finger drawn as it moved (lifts break the trace), the
scores, and what happened. --fragment PATH also writes the page without the
document wrapper, for publishing.

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
PRE = 20                # real-game ticks shown before the window (0.9 s: the previous gesture too)
POST_TICKS = 60         # finger samples kept after the window start
TICK_MS = 45
POL = None
PLAYS = {}
OUT = {"harp": "ate the harp", "hit the wall": "hit the wall", "speared": "speared", "ran into the tail": "ran into the tail",
       "speared from the side": "speared from the side (costs nothing)", "nothing": "no harp, no death"}


def _init(paths, model):
    global POL
    I._init(paths, True, False)
    POL = L.policy_from_spec(json.load(open(model)))
    for path in paths:
        _, plays = build(load(path))
        for p in plays:
            PLAYS[(os.path.basename(path), p.n)] = p


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


def frame(sim):
    return [sim.hx, sim.hy, sim.heading, [list(xy) for xy in sim.tail], [list(s) for s in sim.spears],
            list(sim.harp) if sim.harp else None]


def _course(real, k0, inputs):
    """Frames after every tick of a run (frame 0: the start), the turns and the outcome."""
    frames, turns = [frame(real)], []

    def watch(ev, sim, k, t, h0):
        if ev == "tick":
            frames.append(frame(sim))
        elif ev == "cmd" and sim.heading != h0:
            turns.append([k - k0, sim.heading])
    w = WS.run_window(real, k0, inputs, watch)
    return {"frames": frames, "turns": turns, "end": outcome(w), "plain": round(w.score, 4)}


def _render(args):
    """Everything the page needs to replay one window."""
    key, k = args
    g = I.GAMES[key]
    spec = next(s for s in g.specs if s.k == k)
    real = spec.real
    pre = {}

    def on_slot(kk, sim, cmds):
        if k - PRE <= kk < k:
            pre[kk] = frame(sim)
    E.replay(PLAYS[key], on_slot)
    rec = []
    none = _course(real, k, WS.Schedule(spec.t0, [], 0))
    model = _course(real, k, L.PolicyInputs(spec.t0, POL, g.samples, g.slots, spec.i, spec.stroke, record=rec))
    t0 = spec.t0
    marks = [round((t - t0) / TICK_MS, 3) for kk, f, a, h, t in rec if a]
    fin = [[round((t - t0) / TICK_MS, 3), x, y, int(first), int(last)] for i, (t, x, y, first, last, _) in enumerate(g.samples)
           if k - PRE <= g.slots[i] <= k + POST_TICKS]
    att = [[tk - k, sx, sy, d] for tk, lst in real.throws.items() if k - PRE - 20 < tk <= k + WS.AIM_TICKS for sx, sy, d in lst]
    return (key, k), {"pre": [pre.get(kk) for kk in range(k - PRE, k)], "courses": {"none": none, "model": model},
                      "attackers": att, "finger": fin, "marks": marks}


def file_label(f):
    m = re.search(r"_(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})", f)
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(m.group(2)) - 1]} {int(m.group(3))} {m.group(4)}:{m.group(5)}" if m else f


LET = "URDL"
NAMES = {"U": "up", "R": "right", "D": "down", "L": "left"}

TEMPLATE = r"""<title>__NAME__ bad turns</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
/* layout: summary first, then one replayable card per bad turn; two boards and the finger side by side, stacking on a phone */
:root { --bg: #f7f5f0; --fg: #23211d; --muted: #6b665c; --line: #d9d4c8; --card: #fffdf8; --field: #efece4; --grid: #ddd8cc;
        --tail: #b9b3a4; --david: #2b2824; --harp: #d9a000; --spear: #8a2f1c; --attacker: #b5402a; --none: #2a6fd6; --model: #e2702a;
        --trace: #23211d; --tracepast: #9c968a; --btn: #e9e5da; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg: #1b1a17; --fg: #ece8df; --muted: #a39d90; --line: #3a372f;
        --card: #23211d; --field: #2a2823; --grid: #3a372f; --tail: #5a564c; --david: #f0ece2; --harp: #f0b323; --spear: #e0735a;
        --attacker: #e0735a; --none: #6ea2ff; --model: #ff9a5c; --trace: #ece8df; --tracepast: #6f6a5f; --btn: #33302a; color-scheme: dark } }
:root[data-theme="dark"] { --bg: #1b1a17; --fg: #ece8df; --muted: #a39d90; --line: #3a372f; --card: #23211d; --field: #2a2823;
        --grid: #3a372f; --tail: #5a564c; --david: #f0ece2; --harp: #f0b323; --spear: #e0735a; --attacker: #e0735a; --none: #6ea2ff;
        --model: #ff9a5c; --trace: #ece8df; --tracepast: #6f6a5f; --btn: #33302a; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
       padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 20px; }
main { max-width: 1040px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 6px; text-wrap: balance; }
h2 { font-size: 1.1rem; margin: 28px 0 8px; }
h3 { font-size: 1rem; margin: 0 0 8px; }
p { max-width: 70ch; }
.lead, .legend, .loss { color: var(--muted); }
table { border-collapse: collapse; font-variant-numeric: tabular-nums; }
.summary td, .summary th, .scores td, .scores th { padding: 4px 10px 4px 0; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
.summary th { font-weight: 600; }
.cases { display: grid; gap: 18px; margin-top: 12px; }
.case { background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: 12px 14px; min-width: 0; }
.pics { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }
.pic { min-width: 0; }
.pic figcaption { font-size: 13px; color: var(--muted); margin-bottom: 4px; }
canvas { display: block; max-width: 100%; height: auto; background: var(--field); border: 1px solid var(--line); }
canvas.board { width: 420px; }
canvas.finger { width: 240px; }
.controls { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; margin-top: 10px; font-size: 14px; }
.controls input[type=range] { flex: 1 1 160px; min-width: 120px; }
button, select { font: inherit; color: var(--fg); background: var(--btn); border: 1px solid var(--line); border-radius: 4px; padding: 4px 10px; }
button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid var(--none); outline-offset: 2px; }
.sw { display: inline-block; width: 12px; height: 12px; border-radius: 2px; vertical-align: -1px; margin-right: 6px; }
.sw.none { background: var(--none); } .sw.model { background: var(--model); }
.scores { margin-top: 10px; font-size: 14px; }
.loss { margin: 8px 0 0; font-size: 14px; }
.legend { font-size: 14px; }
.tick { font-variant-numeric: tabular-nums; min-width: 9ch; }
</style>
<main>
__SUMMARY__
<div class="cases" id="cases"></div>
</main>
<script>
const DATA = /*DATA*/{};
const CELL = 20, COLS = 21, ROWS = 13, TICK_MS = 45, PRE = DATA.pre, DX = [0, 1, 0, -1], DY = [-1, 0, 1, 0];
const NAME = DATA.name, OUT = DATA.out, LET = "URDL", NAMES = {U: "up", R: "right", D: "down", L: "left"};
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
const cx = x => x * CELL + CELL / 2, cy = y => y * CELL + CELL / 2;

class Player {
  constructor(el, d) {
    this.d = d; this.t = -PRE; this.playing = false; this.speed = 0.25; this.last = null;
    this.endTick = Math.max(d.courses.none.frames.length, d.courses.model.frames.length) - 1;
    this.boards = {none: el.querySelector("canvas.none"), model: el.querySelector("canvas.model")};
    this.fingerCv = el.querySelector("canvas.finger");
    this.range = el.querySelector("input[type=range]"); this.label = el.querySelector(".tick");
    this.btn = el.querySelector("button.play");
    this.range.min = -PRE; this.range.max = this.endTick; this.range.step = 0.25; this.range.value = this.t;
    this.range.addEventListener("input", () => { this.pause(); this.t = parseFloat(this.range.value); this.draw(); });
    this.btn.addEventListener("click", () => this.playing ? this.pause() : this.play());
    el.querySelector("select").addEventListener("change", e => { this.speed = parseFloat(e.target.value); });
    const f = d.finger, xs = f.map(s => s[1]), ys = f.map(s => s[2]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    this.span = Math.max(x1 - x0, y1 - y0, 60); this.mx = (x0 + x1) / 2; this.my = (y0 + y1) / 2;
    this.draw();
  }
  play() { if (this.t >= this.endTick) this.t = -PRE; this.playing = true; this.btn.textContent = "Pause"; this.last = null;
           if (ACTIVE && ACTIVE !== this) ACTIVE.pause(); ACTIVE = this; requestAnimationFrame(ts => this.step(ts)); }
  pause() { this.playing = false; this.btn.textContent = "Play"; }
  step(ts) {
    if (!this.playing) return;
    if (this.last !== null) this.t = Math.min(this.endTick, this.t + (ts - this.last) / TICK_MS * this.speed);
    this.last = ts; this.draw();
    if (this.t >= this.endTick) { this.pause(); return; }
    requestAnimationFrame(t2 => this.step(t2));
  }
  frameAt(course, tick) {
    if (tick < 0) return this.d.pre[PRE + tick];
    const fr = course.frames; return fr[Math.min(tick, fr.length - 1)];
  }
  draw() {
    this.range.value = this.t; const tick = Math.floor(this.t);
    this.label.textContent = (tick < 0 ? "tick " + tick + " (before)" : "tick +" + tick) + ", " + Math.round(this.t * TICK_MS) + " ms";
    for (const name of ["none", "model"]) this.drawBoard(name, tick);
    this.drawFinger();
  }
  drawBoard(name, tick) {
    const course = this.d.courses[name], cv = this.boards[name], ctx = cv.getContext("2d"), col = css("--" + name);
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.fillStyle = css("--field"); ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.strokeStyle = css("--grid"); ctx.lineWidth = 0.6; ctx.beginPath();
    for (let i = 1; i < COLS; i++) { ctx.moveTo(i * CELL, 0); ctx.lineTo(i * CELL, ROWS * CELL); }
    for (let j = 1; j < ROWS; j++) { ctx.moveTo(0, j * CELL); ctx.lineTo(COLS * CELL, j * CELL); }
    ctx.stroke();
    const fr = this.frameAt(course, tick);
    if (!fr) return;
    const [hx, hy, hd, tail, spears, harp] = fr;
    // attackers: visible from 18 ticks before their throw to 6 after
    ctx.fillStyle = css("--attacker");
    for (const [T, sx, sy, d] of this.d.attackers) if (T - 18 <= tick && tick <= T + 6) ctx.fillRect(cx(sx) - 6, cy(sy) - 6, 12, 12);
    ctx.fillStyle = css("--tail");
    for (const [x, y] of tail) ctx.fillRect(x * CELL + 2, y * CELL + 2, CELL - 4, CELL - 4);
    if (harp) { ctx.fillStyle = css("--harp"); ctx.beginPath(); ctx.arc(cx(harp[0]), cy(harp[1]), 7, 0, 7); ctx.fill(); }
    ctx.strokeStyle = css("--spear"); ctx.lineWidth = 3; ctx.lineCap = "round";
    for (const [x, y, d] of spears) { ctx.beginPath(); ctx.moveTo(cx(x) - DX[d] * 8, cy(y) - DY[d] * 8); ctx.lineTo(cx(x) + DX[d] * 8, cy(y) + DY[d] * 8); ctx.stroke(); }
    // the course's path: solid up to now, dashed ahead
    const cells = course.frames.map(f => [cx(f[0]), cy(f[1])]);
    const now = Math.max(0, Math.min(tick, cells.length - 1));
    ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.strokeStyle = col;
    if (tick >= 0 && now > 0) { ctx.beginPath(); ctx.moveTo(...cells[0]); for (let i = 1; i <= now; i++) ctx.lineTo(...cells[i]); ctx.stroke(); }
    ctx.setLineDash([4, 5]); ctx.globalAlpha = 0.6; ctx.beginPath();
    const from = tick < 0 ? 0 : now; ctx.moveTo(...cells[from]); for (let i = from + 1; i < cells.length; i++) ctx.lineTo(...cells[i]);
    ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
    for (const [tt, h] of course.turns) if (tt < cells.length) { ctx.beginPath(); ctx.arc(cells[tt][0], cells[tt][1], 7, 0, 7); ctx.stroke(); }
    // David, with his heading
    ctx.fillStyle = css("--david"); ctx.fillRect(hx * CELL + 1, hy * CELL + 1, CELL - 2, CELL - 2);
    ctx.strokeStyle = css("--bg"); ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(cx(hx), cy(hy)); ctx.lineTo(cx(hx) + DX[hd] * 8, cy(hy) + DY[hd] * 8); ctx.stroke();
    // the end
    const endTick = course.frames.length - 1;
    if (tick >= endTick) {
      const [ex, ey] = cells[endTick];
      if (course.end === "harp") { ctx.fillStyle = css("--harp"); ctx.beginPath(); ctx.arc(ex, ey, 6, 0, 7); ctx.fill(); }
      else { ctx.strokeStyle = col; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(ex - 6, ey - 6); ctx.lineTo(ex + 6, ey + 6); ctx.moveTo(ex - 6, ey + 6); ctx.lineTo(ex + 6, ey - 6); ctx.stroke(); }
      const text = course.end === "harp" ? "ate the harp  +1" : OUT[course.end] + "  " + course.plain.toFixed(3);
      ctx.font = "bold 13px system-ui, sans-serif"; const w = ctx.measureText(text).width + 14;
      const bx = Math.min(Math.max(ex - w / 2, 4), COLS * CELL - w - 4), by = ey < 40 ? ey + 14 : ey - 32;
      ctx.fillStyle = css("--card"); ctx.fillRect(bx, by, w, 20); ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.strokeRect(bx, by, w, 20);
      ctx.fillStyle = css("--fg"); ctx.fillText(text, bx + 7, by + 14);
    } else if (tick < 0) {
      ctx.font = "12px system-ui, sans-serif"; ctx.fillStyle = css("--muted"); ctx.fillText("the real game, before the window", 6, ROWS * CELL - 6);
    }
  }
  drawFinger() {
    const cv = this.fingerCv, ctx = cv.getContext("2d"), size = cv.width, sc = (size - 40) / this.span;
    const X = x => (x - this.mx) * sc + size / 2, Y = y => (y - this.my) * sc + size / 2;
    ctx.clearRect(0, 0, size, size); ctx.fillStyle = css("--field"); ctx.fillRect(0, 0, size, size);
    ctx.strokeStyle = css("--grid"); ctx.lineWidth = 0.6; ctx.beginPath();
    for (let k = 0; k * 10 * sc < size; k++) { ctx.moveTo(k * 10 * sc, 0); ctx.lineTo(k * 10 * sc, size); ctx.moveTo(0, k * 10 * sc); ctx.lineTo(size, k * 10 * sc); }
    ctx.stroke();
    const f = this.d.finger, t = this.t;
    // strokes: a touch-down (first) starts one, a lift (last) ends it; nothing is drawn between strokes
    ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.lineWidth = 2.5;
    let pos = null, lifted = false;
    for (let i = 1; i < f.length; i++) {
      const a = f[i - 1], b = f[i];
      if (b[3]) continue;                       // b is a touch-down: no line from the previous stroke
      const past = b[0] <= t;
      ctx.strokeStyle = past ? css("--trace") : css("--tracepast");
      ctx.setLineDash(past ? [] : [3, 3]);
      ctx.beginPath(); ctx.moveTo(X(a[1]), Y(a[2])); ctx.lineTo(X(b[1]), Y(b[2])); ctx.stroke();
      if (a[0] <= t && t < b[0]) { const u = (t - a[0]) / (b[0] - a[0]); pos = [a[1] + u * (b[1] - a[1]), a[2] + u * (b[2] - a[2])]; }
    }
    ctx.setLineDash([]);
    if (pos === null) {                          // between strokes, or before the first sample / after the last
      for (const s of f) if (s[0] <= t && s[4]) lifted = true;
      for (const s of f) if (s[0] <= t && s[3]) lifted = false;
      const on = f.filter(s => s[0] <= t); const lastS = on.length ? on[on.length - 1] : null;
      if (lastS && !lifted) pos = [lastS[1], lastS[2]];
    }
    for (const m of this.d.marks) { const s = f.reduce((best, s) => Math.abs(s[0] - m) < Math.abs(best[0] - m) ? s : best, f[0]);
      ctx.strokeStyle = css("--model"); ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(X(s[1]), Y(s[2]), 7, 0, 7); ctx.stroke(); }
    if (pos) { ctx.fillStyle = css("--fg"); ctx.beginPath(); ctx.arc(X(pos[0]), Y(pos[1]), 5, 0, 7); ctx.fill(); }
    else { ctx.font = "12px system-ui, sans-serif"; ctx.fillStyle = css("--muted"); ctx.fillText("finger up", 8, 18); }
    // compass: David's heading at the window start and his right-hand side
    const h = this.d.courses.none.frames[0][2], px = size - 26, py = 26, rh = (h + 1) % 4;
    ctx.fillStyle = css("--card"); ctx.strokeStyle = css("--line"); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(px, py, 14, 0, 7); ctx.fill(); ctx.stroke();
    ctx.strokeStyle = css("--fg"); ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px + DX[h] * 11, py + DY[h] * 11); ctx.stroke();
    ctx.font = "11px system-ui, sans-serif"; ctx.fillStyle = css("--muted"); ctx.textAlign = "center";
    ctx.fillText("R", px + DX[rh] * 22, py + DY[rh] * 22 + 4); ctx.textAlign = "left"; ctx.fillText("grid 10 dp", 8, size - 8);
  }
}
let ACTIVE = null;
function fingerNote(c) {
  const downs = c.finger.filter(s => s[3] && s[0] <= 0), lifts = c.finger.filter(s => s[4] && s[0] < 0);
  if (!downs.length) return "The finger was already down more than " + PRE + " ticks before the window.";
  const d = downs[downs.length - 1][0];
  const prev = lifts.length ? ", the previous stroke lifted " + (-lifts[lifts.length - 1][0]).toFixed(1) + " ticks before it" : "";
  return "The finger touched down " + (-d).toFixed(1) + " ticks before the window" + prev + ".";
}
const root = document.getElementById("cases");
DATA.cases.forEach((c, i) => {
  const el = document.createElement("article"); el.className = "case";
  const turns = c.courses.model.turns.map(([tt, h]) => NAMES[LET[h]] + " at tick +" + tt).join(", ") || "no turn";
  const row = (name, label, co) => `<tr><td><span class="sw ${name}"></span>${label}</td><td>${OUT[co.end]} after ${co.frames.length - 1} ticks</td><td>${co.plain.toFixed(3)}</td><td>${(name === "none" ? c.final_none : c.final_model).toFixed(3)}</td></tr>`;
  el.innerHTML = `<h3>${i + 1}. ${c.title}</h3>
    <div class="pics">
      <figure class="pic"><figcaption><span class="sw none"></span>no turn</figcaption><canvas class="board none" width="${COLS * CELL}" height="${ROWS * CELL}"></canvas></figure>
      <figure class="pic"><figcaption><span class="sw model"></span>${NAME}</figcaption><canvas class="board model" width="${COLS * CELL}" height="${ROWS * CELL}"></canvas></figure>
      <figure class="pic"><figcaption>your finger</figcaption><canvas class="finger" width="240" height="240"></canvas></figure>
    </div>
    <div class="controls"><button class="play" type="button">Play</button><input type="range" id="r${i}" aria-label="time"><span class="tick"></span>
      <label>speed <select id="s${i}"><option value="0.25" selected>x0.25</option><option value="0.5">x0.5</option><option value="1">x1</option></select></label></div>
    <table class="scores"><tr><th></th><th>what happened</th><th>plain</th><th>Final</th></tr>${row("none", "no turn", c.courses.none)}${row("model", NAME + ": " + turns, c.courses.model)}</table>
    <p class="loss">David heading ${NAMES[LET[c.courses.none.frames[0][2]]]} at the window start. ${fingerNote(c)} The turn cost ${(c.final_none - c.final_model).toFixed(3)} on the Final score.</p>`;
  root.appendChild(el);
  new Player(el, c);
});
</script>
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
    summary = "\n".join([
        f"<h1>{name}: turns that score below no turn</h1>",
        f'<p class="lead">Every window of the {len(trn)} training games, simulated twice as the judge does: with no turn at all, '
        f'and with {name} playing. A turn is counted here when the no-turn course scores higher on the Final score '
        f'(tolerance {DELTA}, alpha {ALPHA:g}). The worst {len(shown)} are below; press Play to watch one from {PRE} ticks before the window.</p>',
        '<table class="summary">',
        f"<tr><th>Training windows</th><td>{n}</td></tr>",
        f"<tr><th>Windows where {name} turns</th><td>{nt} ({100 * nt / n:.0f}%)</td></tr>",
        f"<tr><th>Turns scoring below no turn</th><td>{len(bad)} ({100 * len(bad) / max(1, nt):.0f}% of its turns), total loss {loss:.1f}, mean {loss / max(1, len(bad)):.3f}</td></tr>",
        f"<tr><th>Turns scoring above no turn</th><td>{len(good)} ({100 * len(good) / max(1, nt):.0f}%), total gain {gain:.1f}, mean {gain / max(1, len(good)):.3f}</td></tr>",
        f"<tr><th>Turns scoring the same</th><td>{nt - len(bad) - len(good)}</td></tr>",
        "</table>",
        '<h2>How much the bad turns cost</h2><table class="summary">'
        + "".join(f"<tr><th>{t}</th><td>{c}</td></tr>" for t, c in buckets) + "</table>",
        '<h2>What happened instead</h2><table class="summary"><tr><th>no turn</th><th>with the turn</th><th>windows</th></tr>'
        + "".join(f"<tr><td>{OUT[a]}</td><td>{OUT[b]}</td><td>{c}</td></tr>" for (a, b), c in pairs.most_common()) + "</table>",
        f"<h2>The worst {len(shown)}</h2>",
        '<p class="legend">Boards: David is the dark square with a short line for his heading, his tail grey, the harp gold, spears in flight dark red, '
        'attackers red squares at the walls from 18 ticks before their throw. The path is solid where David has been and dashed where he is going; '
        'rings mark the turns; a gold dot ends a path at the harp, a cross at a death. '
        'Finger: the line is drawn as the finger moved, dashed where it has not yet been; the dot is the finger now, interpolated between samples; '
        'a lift breaks the line, and the dot is gone while the finger is up. The orange ring is the sample at which ' + name + ' turned. '
        'The compass shows David\'s heading at the window start and his right-hand side.</p>',
    ])
    cases = []
    for key, r in shown:
        d = pics[(key, r[0])]
        d["title"] = f"Game {key[1]} of {html.escape(file_label(key[0]))}, tick {r[0]}"
        d["final_none"], d["final_model"] = round(r[1], 4), round(r[4], 4)
        cases.append(d)
    data = {"name": name, "pre": PRE, "out": OUT, "cases": cases}
    frag = (TEMPLATE.replace("__NAME__", html.escape(name)).replace("__SUMMARY__", summary)
            .replace("/*DATA*/{}", json.dumps(data, separators=(",", ":"))))
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
          f"above {len(good)} (gain {gain:.1f}); shown {len(shown)}, {os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    main()

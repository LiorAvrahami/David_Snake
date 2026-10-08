#!/usr/bin/env python3
"""Puts a turn model trained by tools/simlearn.py into the game.

Writes tools/models/<name>.json, the model's complete description (inputs,
features, decision rule, weights, and how it was trained, down to the
code commit and the training files' hashes), and
app/src/main/java/com/davidsnake/game/MlModel.kt with the same weights and
description; the game logs that description with every recorded game.

Usage: python3 tools/export_model.py NAME WEIGHTS.json COMMIT FILE=PATH...
  WEIGHTS.json  output of `simlearn.py ... --export W`
  COMMIT        the commit of tools/simlearn.py that trained it
  FILE=PATH     each training recording, in training order: the name to
                record and where it is"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import simlearn as L
import windowsim as WS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W_INPUT = 3                 # steps of input the model was trained with
ITERS = 6


def describe_data(name, path):
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    session, plays = build(load(path))
    games = [p for p in plays if p.spears and not p.aborted]
    return {"file": name, "sha256": sha, "app": session.get("ver") if session else None,
            "session_start": session.get("wall") if session else None, "games": len(games)}


def spec_of(name, weights, commit, data):
    ws, wb = weights["w_side"], weights["w_back"]
    assert len(ws) == len(wb) == L.NF
    wsha = hashlib.sha256(json.dumps({"w_side": ws, "w_back": wb}, separators=(",", ":")).encode()).hexdigest()
    windows = list(L.WIN_MS)
    feats = []
    for w in windows:
        feats += [f"fwd{w}: finger travel along David's heading over the last {w} ms, per 20 dp",
                  f"side{w}: finger travel to David's right over the last {w} ms, per 20 dp",
                  f"|side{w}|: its size"]
    feats += ["speed: finger travel over the last 40 ms, per 500 dp/s",
              "since: ms since the last-turn sample, at most 400, per 400",
              "fwdSince: finger travel along the heading since the last-turn sample, per 40 dp",
              "sideSince: finger travel to David's right since the last-turn sample, per 40 dp",
              "|sideSince|: its size",
              "bias: 1"]
    return {
        "name": name,
        "type": "linear turn policy over finger movement only, trained on the game simulation",
        "recognizer": "LearnedRecognizer (app/src/main/java/com/davidsnake/game/Recognizers.kt); "
                      "Python twin: tools/simlearn.py Policy + features",
        "turn_mode": "STEP_SAFE",
        "hold_uturn": True,
        "input": "the current stroke's finger samples (time ms; x, y in dp rounded to 0.1 dp, as logged) "
                 "and David's heading (a queued turn counts)",
        "decides": "once per game tick, just before it (the engine acts on a turn at the next tick), "
                   "on the stroke's latest sample, if samples arrived since its last decision; a lifted "
                   "or replaced stroke's last samples are decided at the next tick, and then a new "
                   "stroke's samples wait one more tick",
        "window_travel": "from the first sample at or after (t - window) to the latest sample t",
        "features": feats,
        "side_features": list(L.SIDE),
        "scores": "none = 0; right = w_side . f; left = w_side . mirror(f), mirror negating the "
                  "side features; back = w_back . sym(f), sym zeroing them; the highest wins, ties "
                  "going to the first of none, right, left, back",
        "commands": {
            "right": "turn to David's right (kind ml)",
            "left": "turn to David's left (kind ml)",
            "back": "with a tail, a U-turn: first to the side the finger's travel over the last "
                    "160 ms leans to by 3 dp or more, else the side with more free cells ahead "
                    "(never a blocked side if the other is open), then back, which the engine holds "
                    "until the first turn's step; without a tail, a reversal",
        },
        "last_turn": "after a decision's commands run, if David's heading changed, the latest sample "
                     "becomes the last-turn sample; it starts at the stroke's first sample",
        "w_side": ws,
        "w_back": wb,
        "weights_sha256": wsha,
        "training": {
            "code": f"tools/simlearn.py at commit {commit}",
            "command": "python3 tools/simlearn.py " + " ".join(d["file"] for d in data)
                       + f" --iters {ITERS} --export {W_INPUT}",
            "deterministic": "re-running the command on the same files gives identical weights",
            "data": data,
            "method": "learning to search on the window simulation (tools/windowsim.py): in every "
                      "gesture window of the training games the model plays the window; at each tick "
                      "it decided, each of the four answers is tried, the model deciding the rest; the "
                      "four window scores are that tick's example; weights minimize the expected loss "
                      "against the best answer (softmax, L2) by Adam; examples accumulate over rounds; "
                      "round 0 copies the live game's own turns",
            "settings": {"input_steps": W_INPUT, "rounds": ITERS, "l2": L.L2, "adam_lr": 0.05,
                         "fit_steps": 600, "copy_fit_steps": 400, "copy_turn_weight": 3.0},
            "objective": {
                "windows": "one per gesture start: finger speed rising from under "
                           f"{WS.V_REST:g} to over {WS.V_MOVE:g} dp/s with {WS.GESTURE_DP:g} dp of travel, "
                           f"or a turn of {WS.SPLIT_DEG:g} degrees or more while moving (speed over "
                           f"{WS.SPEED_MS} ms)",
                "input_steps": W_INPUT, "horizon_steps": WS.HORIZON,
                "harp": "+1 for the first harp eaten, linearly less the later it is (1/horizon at the last step)",
                "death": f"-{WS.DEATH:g}, linearly less the later it is; counted only up to this many "
                         "steps after the input ends",
                "death_cut_steps": WS.CUT,
                "completion_ms": WS.COMPLETE_MS,
                "spears": f"recorded throws aimed before the window started (throw tick <= start + {WS.AIM_TICKS})",
            },
            "result": "3-fold cross-validation by game (game i of the files in order is in fold i % 3): "
                      "held-out gain over S2-FAST +18.1 per 1000 gestures (90% range +5.6 to +32.3); "
                      "this model is then trained on all the games",
        },
    }


def kotlin(spec):
    js = json.dumps(spec, separators=(",", ":"), ensure_ascii=True)
    assert "$" not in js and '"""' not in js
    def arr(v):
        return ",\n        ".join(", ".join(repr(float(x)) for x in v[i:i + 6]) for i in range(0, len(v), 6))
    return f'''package com.davidsnake.game

// Generated by tools/export_model.py from tools/models/{spec["name"].lower()}.json. Do not edit.

/** The trained turn model {spec["name"]}: weights for [LearnedRecognizer], and
 *  its complete description, logged with every game it plays. */
object MlModel {{
    const val NAME = "{spec["name"]}"

    val W_SIDE = doubleArrayOf(
        {arr(spec["w_side"])}
    )

    val W_BACK = doubleArrayOf(
        {arr(spec["w_back"])}
    )

    const val SPEC = """{js}"""
}}
'''


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(2)
    name, wpath, commit = sys.argv[1:4]
    data = []
    for a in sys.argv[4:]:
        fname, path = a.split("=", 1)
        data.append(describe_data(fname, path))
    spec = spec_of(name, json.load(open(wpath)), commit, data)
    out = os.path.join(ROOT, "tools", "models", name.lower() + ".json")
    with open(out, "w") as f:
        json.dump(spec, f, indent=1)
        f.write("\n")
    kt = os.path.join(ROOT, "app", "src", "main", "java", "com", "davidsnake", "game", "MlModel.kt")
    with open(kt, "w") as f:
        f.write(kotlin(spec))
    print("wrote", out, "and", kt)

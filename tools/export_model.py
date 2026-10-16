#!/usr/bin/env python3
"""Records a turn model trained by tools/simlearn.py, and puts it into the
game on request.

Writes tools/models/<name>.json, the model's complete description (inputs,
features, decision rule, weights, and how it was trained, down to the
code commit and the training files' hashes); with --game also
app/src/main/java/com/davidsnake/game/MlModel.kt with the same weights and
description, which the game logs with every recorded game.

Usage: python3 tools/export_model.py NAME WEIGHTS.json COMMIT RESULT FILE=PATH... [SPLIT] [TRAINER] [--game]
  WEIGHTS.json  output of the trainer's `--export`
  COMMIT        the commit of tools/ that trained it
  RESULT        how it tested (recorded as is)
  FILE=PATH     each recording given to the training, in order: the name to
                record and where it is
  SPLIT         the options that held out test games, as given to the
                trainer: --holdout FRAC --seed S --never-test FILE,...
  TRAINER       --trainer simlearn (default: learning to search on the window
                score) or intent (tools/intentlearn.py: the intended turn as
                early as the finger shows it), with its --rounds N --w-turn W"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import intentlearn as I
import simlearn as L
import windowsim as WS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ITERS = 6


def describe_data(name, path):
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    session, plays = build(load(path))
    games = [p for p in plays if p.spears and not p.aborted]
    return {"file": name, "sha256": sha, "app": session.get("ver") if session else None,
            "session_start": session.get("wall") if session else None, "games": len(games)}


def spec_of(name, weights, commit, data, result, split_args=(), test_games=(), trainer="simlearn", trainer_args=()):
    mlp = "w1" in weights
    no_back = bool(weights.get("no_back", False))
    speed = bool(weights.get("speed", True))
    layout = L.LAYOUT_SPEED if speed else L.LAYOUT_NOSPEED
    if mlp:
        wd = {k: weights[k] for k in (("w1", "b1", "v") if no_back else ("w1", "b1", "v", "vb"))}
        assert all(len(r) == layout.nf for r in wd["w1"])
    else:
        ws, wb = weights["w_side"], weights["w_back"]
        assert len(ws) == len(wb) == L.NF
        wd = {"w_side": ws, "w_back": wb}
    wsha = hashlib.sha256(json.dumps(wd, separators=(",", ":")).encode()).hexdigest()
    windows = list(L.WIN_MS)
    feats = []
    for w in windows:
        feats += [f"fwd{w}: finger travel along David's heading over the last {w} ms, per 20 dp",
                  f"side{w}: finger travel to David's right over the last {w} ms, per 20 dp",
                  f"|side{w}|: its size"]
    feats += (["speed: finger travel over the last 40 ms, per 500 dp/s"] if speed else []) + [
              "since: ms since the last-turn sample, at most 400, per 400",
              "fwdSince: finger travel along the heading since the last-turn sample, per 40 dp",
              "sideSince: finger travel to David's right since the last-turn sample, per 40 dp",
              "|sideSince|: its size",
              "bias: 1"]
    spec = {
        "name": name,
        "type": ("non-linear turn policy (one hidden layer) " if mlp else "linear turn policy ")
                + "over finger movement only" + (", without the back answer" if no_back else ""),
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
        "side_features": list(layout.side),
        "scores": ("none = 0; h(x) = relu(w1 x + b1); right = v . h(f); left = v . h(mirror(f)), mirror "
                   "negating the side features"
                   + ("" if no_back else "; back = vb . h(sym(f)), sym zeroing them")
                   if mlp else
                   "none = 0; right = w_side . f; left = w_side . mirror(f), mirror negating the "
                   "side features" + ("" if no_back else "; back = w_back . sym(f), sym zeroing them"))
                  + ("; no back answer (a U-turn is two side turns)" if no_back else "")
                  + "; the highest wins, ties going to the first of none, right, left"
                  + ("" if no_back else ", back"),
        "commands": {
            "right": "turn to David's right (kind ml)",
            "left": "turn to David's left (kind ml)",
            **({} if no_back else {
                "back": "with a tail, a U-turn: first to the side the finger's travel over the last "
                        "160 ms leans to by 3 dp or more, else the side with more free cells ahead "
                        "(never a blocked side if the other is open), then back, which the engine holds "
                        "until the first turn's step; without a tail, a reversal"}),
        },
        "last_turn": "after a decision's commands run, if David's heading changed, the latest sample "
                     "becomes the last-turn sample; it starts at the stroke's first sample",
        "weights_sha256": wsha,
        "training": TRAINING[trainer](commit, data, split_args, test_games, trainer_args, result),
    }
    spec.update(wd)
    if mlp:
        spec["hidden"] = len(wd["b1"])
        spec["speed"] = speed
    if no_back:
        spec["no_back"] = True
    return spec


def objective():
    return {
        "windows": "one per gesture start: finger speed rising from under "
                   f"{WS.V_REST:g} to over {WS.V_MOVE:g} dp/s with {WS.GESTURE_DP:g} dp of travel, "
                   f"or a turn of {WS.SPLIT_DEG:g} degrees or more while moving (speed over "
                   f"{WS.SPEED_MS} ms); a gesture starting less than {WS.JOIN_MS} ms after a "
                   "window's start belongs to that window",
        "input": f"the finger samples of the first {WS.READ_MS} ms from the window's start; then, "
                 "if David turned, one more turn before his next regular step after the latest "
                 "turn, which ends the input",
        "run": "after the input David goes straight until he eats the harp or dies",
        "harp": "+1 for eating the harp, whenever it happens (only the first); the run ends there",
        "death": f"-{WS.DEATH:g} times exp(-(s - 1) / decay), s = steps from the window start "
                 "to the death, decay by cause; a spear hitting David from the side (flying "
                 "across the direction of his last step) costs nothing",
        "death_decay_steps": WS.DECAY,
        "spears": f"recorded throws aimed before the window started (throw tick <= start + {WS.AIM_TICKS})",
    }


def split_text(split_args):
    return "the games listed in test_games were held out (not trained on): " + (
        " ".join(split_args) + " in tools/simlearn.py split()" if split_args else "none")


def training_simlearn(commit, data, split_args, test_games, trainer_args, result):
    args = dict(zip(trainer_args[::2], trainer_args[1::2]))
    iters = int(args.get("--iters", ITERS))
    tol, alpha, init = int(args.get("--tolerance", 0)), float(args.get("--alpha", L.ALPHA)), args.get("--init")
    extra = (f" --tolerance {tol} --alpha {alpha:g}" if tol else "") + (f" --init {init}" if init else "")
    return {
        "code": f"tools/simlearn.py at commit {commit}",
        "command": "python3 tools/simlearn.py " + " ".join(d["file"] for d in data)
                   + f" --iters {iters}" + extra + "".join(" " + a for a in split_args) + " --export",
        "deterministic": "re-running the command on the same files gives identical weights",
        "data": data,
        "test_games": [{"file": f, "game": n} for f, n in test_games],
        "method": "learning to search on the window simulation (tools/windowsim.py): in every "
                  "gesture window of the training games the model plays the window; at each tick "
                  "it decided, each of the four answers is tried, the model deciding the rest; the "
                  "four window scores are that tick's example; weights minimize the expected loss "
                  "against the best answer (softmax, L2) by Adam; examples accumulate over rounds; "
                  + (f"round 0 is the model {init}" if init else "round 0 copies the live game's own turns")
                  + (f". The score is the Final score: the window's turns moved together -{tol}..{tol} "
                     f"ticks (from earlier real states when before the window), the best counting {alpha:g}, "
                     f"the plain run {1 - alpha:g}" if tol else ""),
        "settings": {"rounds": iters, "tolerance_ticks": tol, "alpha": alpha if tol else None, "init": init,
                     "l2": L.L2, "adam_lr": 0.05, "fit_steps": 600, "copy_fit_steps": 400, "copy_turn_weight": 3.0},
        "split": split_text(split_args),
        "objective": objective(),
        "result": result,
    }


def training_intent(commit, data, split_args, test_games, trainer_args, result):
    flags = [a for a in trainer_args if a == "--no-back"]
    pairs = [a for a in trainer_args if a != "--no-back"]
    args = dict(zip(pairs[::2], pairs[1::2]))
    rounds, w_turn = int(args.get("--rounds", I.ROUNDS)), float(args.get("--w-turn", I.W_TURN))
    w_reject, w_hold = float(args.get("--w-reject", I.W_REJECT)), float(args.get("--w-hold", I.W_HOLD))
    model, hidden = args.get("--model", "linear"), int(args.get("--hidden", I.HIDDEN))
    return {
        "code": f"tools/intentlearn.py at commit {commit}",
        "command": "python3 tools/intentlearn.py " + " ".join(d["file"] for d in data)
                   + f" --rounds {rounds} --w-turn {w_turn:g} --w-reject {w_reject:g} --w-hold {w_hold:g}"
                   + (f" --model mlp --hidden {hidden}" if model == "mlp" else "") + ("".join(" " + a for a in flags))
                   + "".join(" " + a for a in split_args) + " --export",
        "deterministic": "re-running the command on the same files gives identical weights",
        "data": data,
        "test_games": [{"file": f, "game": n} for f, n in test_games],
        "method": "the intended turn as early as the finger shows it (DAgger): in every gesture "
                  "window of the training games the live game's turns give the intended screen "
                  "directions; the right answer at a tick is the first intended direction not yet "
                  "reached, relative to David's heading, from the moment the finger movement asking "
                  f"for it is clear ({WS.GESTURE_DP:g} dp of travel at {WS.V_MOVE:g} dp/s; a second turn "
                  "answering the same movement: clear at the live turn, asked from when the first "
                  "was clear), unknown between the movement's start and that moment (no example), "
                  f"none otherwise; a target not reached within {I.TIMEOUT_MS} ms of its movement's "
                  "start is dropped. Examples (features, answer) at every tick of the live games, "
                  "then at every tick the model decides while playing the windows (one round of "
                  "rollouts per round); the softmax policy is refitted on all examples each round "
                  "(cross-entropy, L2, Adam) with turn answers weighted w_turn, none answers during "
                  "a clear finger movement the live game did not answer (for 300 ms) w_reject, and "
                  f"none answers within {I.HOLD_MS} ms after a turn w_hold",
        "settings": {"rounds": rounds, "w_turn": w_turn, "w_reject": w_reject, "w_hold": w_hold,
                     "timeout_ms": I.TIMEOUT_MS, "hold_ms": I.HOLD_MS, "l2": L.L2, "model": model,
                     "hidden": hidden if model == "mlp" else None, "no_back": model == "mlp" or "--no-back" in flags, "speed_input": model != "mlp",
                     "adam_lr": 0.01 if model == "mlp" else 0.05, "fit_steps": I.MLP_ITERS if model == "mlp" else 400},
        "split": split_text(split_args),
        "objective": objective(),
        "note": "the window score only judges the model; it is calibrated to the live recognizer's "
                "latency (a turn one tick earlier or later than it loses about 3% of harps either way)",
        "result": result,
    }


TRAINING = {"simlearn": training_simlearn, "intent": training_intent}


def kotlin(spec):
    assert "w1" not in spec, "the game has no code for the one-hidden-layer model yet"
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
    argv = [a for a in sys.argv if a != "--game"]
    split_args, trainer_args, trainer = [], [], "simlearn"
    for opt in ("--holdout", "--seed", "--never-test"):
        if opt in argv:
            i = argv.index(opt)
            split_args += argv[i:i + 2]
            del argv[i:i + 2]
    for opt in ("--rounds", "--w-turn", "--w-reject", "--w-hold", "--iters", "--tolerance", "--alpha", "--init",
                "--model", "--hidden"):
        if opt in argv:
            i = argv.index(opt)
            trainer_args += argv[i:i + 2]
            del argv[i:i + 2]
    if "--no-back" in argv:
        trainer_args.append("--no-back")
        argv.remove("--no-back")
    if "--trainer" in argv:
        i = argv.index("--trainer")
        trainer = argv[i + 1]
        del argv[i:i + 2]
    args = argv[1:]
    if len(args) < 5:
        print(__doc__)
        sys.exit(2)
    name, wpath, commit, result = args[:4]
    data, keys, arms = [], [], {}
    for a in args[4:]:
        fname, path = a.split("=", 1)
        data.append(describe_data(fname, path))
        _, plays = build(load(path))
        for q in plays:
            if q.spears and not q.aborted:
                keys.append((fname, q.n))
                arms[(fname, q.n)] = q.arm
    _, o = L.options(["simlearn.py"] + split_args)
    _, test = L.split(keys, arms, o["holdout"], o["seed"], o["never_test"])
    spec = spec_of(name, json.load(open(wpath)), commit, data, result, split_args, test, trainer, trainer_args)
    out = os.path.join(ROOT, "tools", "models", name.lower() + ".json")
    with open(out, "w") as f:
        json.dump(spec, f, indent=1)
        f.write("\n")
    print("wrote", out)
    if "--game" in sys.argv:
        kt = os.path.join(ROOT, "app", "src", "main", "java", "com", "davidsnake", "game", "MlModel.kt")
        with open(kt, "w") as f:
            f.write(kotlin(spec))
        print("wrote", kt)

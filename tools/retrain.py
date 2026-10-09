#!/usr/bin/env python3
"""Retrains a trained turn model from its description alone and checks that
the weights come out identical.

The description is tools/models/<name>.json, or the model line of any game
the model played (give the recording). The training recordings it names
are found in training_recordings/ and checked against their sha256; then the
training command it names is run, with tools/ as of the commit it names
(the scoring may have changed since), and the weights compared.

Usage: python3 tools/retrain.py [tools/models/ml-1.json | RECORDING]"""
import hashlib, json, os, shlex, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "training_recordings")


def spec_from(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        return json.loads(text)                     # a model description
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():                  # a recording: its first trained model
        if '"k":"model"' in line:
            rec = json.loads(line)["spec"]["recognizer"]
            if "w_side" in rec:
                return rec
    raise SystemExit(f"{path}: no trained model described in it")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "tools", "models", "ml-1.json")
    spec = spec_from(path)
    tr = spec["training"]
    args = shlex.split(tr["command"])
    assert args[:2] == ["python3", "tools/simlearn.py"], tr["command"]
    files = {d["file"]: d for d in tr["data"]}
    commit = tr["code"].rsplit(" ", 1)[-1]
    tmp = tempfile.mkdtemp(prefix="retrain_")
    tar = subprocess.run(["git", "-C", ROOT, "archive", commit, "tools"], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", tmp], input=tar, check=True)
    cmd = [sys.executable, "-I", os.path.join(tmp, "tools", "simlearn.py")]
    for a in args[2:]:
        if a in files:
            p = os.path.join(DATA, a)
            if not os.path.exists(p):
                raise SystemExit(f"missing training recording {p}")
            with open(p, "rb") as f:
                sha = hashlib.sha256(f.read()).hexdigest()
            if sha != files[a]["sha256"]:
                raise SystemExit(f"{a}: sha256 {sha} is not the one the model was trained on")
            cmd.append(p)
        else:
            cmd.append(a)
    print(f"{spec['name']}: training files found and verified; trained with {tr['code']}")
    print("running: " + tr["command"])
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    got = json.loads(out.strip().splitlines()[-1])
    same = got["w_side"] == spec["w_side"] and got["w_back"] == spec["w_back"]
    print("weights identical to the model's" if same else "weights DIFFER from the model's")
    sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()

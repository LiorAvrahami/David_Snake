#!/usr/bin/env python3
"""Adds exported game recordings to training_recordings/, the folder of
recordings the models are trained on, skipping duplicates, and rewrites
that folder's README.md (the list of what is in it).

A file is a duplicate when its bytes equal a file already there; a game is
a duplicate when a game with the same start (uptime ms) and score is
already there. New files are copied under their own name (an upload's
8-character prefix dropped). A file that mixes new and known games is not
added; it is reported. Only recordings that log every spear throw (v1.7 and
later) are taken, and every game must replay exactly (tools/enginesim.py).

Usage: python3 tools/add_recordings.py FILE...   (no FILE: rewrite the README)"""
import hashlib, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_report import load, build
import enginesim as E

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "training_recordings")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def games_of(path):
    session, plays = build(load(path))
    return session, plays


def known():
    """sha256 -> file, and game key -> file, for what is in the folder."""
    files, games = {}, {}
    for name in sorted(os.listdir(DIR)):
        if not name.endswith(".txt"):
            continue
        p = os.path.join(DIR, name)
        files[sha(p)] = name
        for g in games_of(p)[1]:
            games[(g.t, g.end and g.end.get("score"))] = name
    return files, games


def add(path, files, games):
    base = os.path.basename(path)
    name = re.sub(r"^[0-9a-f]{8}-", "", base)
    h = sha(path)
    if h in files:
        return f"{base}: duplicate of {files[h]} (same bytes), skipped"
    session, plays = games_of(path)
    if not plays:
        return f"{base}: no games in it, skipped"
    if any(not p.spears for p in plays if p.end):
        return f"{base}: recorded before spear throws were logged (v1.7), skipped"
    keys = [(p.t, p.end and p.end.get("score")) for p in plays]
    dup = [games[k] for k in keys if k in games]
    if len(dup) == len(keys):
        return f"{base}: all {len(keys)} games already in {sorted(set(dup))}, skipped"
    if dup:
        return f"{base}: {len(dup)} of its {len(keys)} games are already in {sorted(set(dup))}; not added"
    bad = [p.n for p in plays if E.check(p)]
    if bad:
        return f"{base}: games {bad} do not replay exactly; not added"
    if os.path.exists(os.path.join(DIR, name)):
        return f"{base}: a different file named {name} is already there; not added"
    shutil.copyfile(path, os.path.join(DIR, name))
    files[h] = name
    for k in keys:
        games[k] = name
    return f"{base}: added as {name} ({len(keys)} games)"


def readme():
    rows, total, touched = [], 0, 0
    for name in sorted(os.listdir(DIR)):
        if not name.endswith(".txt"):
            continue
        p = os.path.join(DIR, name)
        session, plays = games_of(p)
        arms = ", ".join(sorted({g.arm for g in plays}))
        n = len(plays)
        t = sum(1 for g in plays if g.strokes)
        total += n
        touched += t
        played = min((g.wall for g in plays if getattr(g, "wall", None)), default="?")
        rows.append((played, name, session.get("ver", "?") if session else "?", arms, n, t, sha(p)[:12]))
    rows.sort()
    lines = [
        "# Training recordings",
        "",
        "Game recordings exported from the app in debug mode, used to score input",
        "methods (`tools/windowsim.py`) and to train models (`tools/simlearn.py`).",
        "Only recordings that log every spear throw (app v1.7 and later) belong here.",
        "Add new exports with `python3 tools/add_recordings.py FILE...`: it skips",
        "files and games that are already here, checks that every game replays",
        "exactly, and rewrites this list. Files keep the name the app gave them.",
        "",
        "| First game | File | App | Input | Games | With touches | sha256 |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} |" for r in rows]
    lines += ["", f"{total} games, {touched} of them with touches (a game started by accident and",
              "never touched has no gestures, so it adds nothing to scoring or training).", ""]
    with open(os.path.join(DIR, "README.md"), "w") as f:
        f.write("\n".join(lines))
    return total, touched


if __name__ == "__main__":
    os.makedirs(DIR, exist_ok=True)
    files, games = known()
    for path in sys.argv[1:]:
        print(add(path, files, games))
    total, touched = readme()
    print(f"training_recordings: {total} games ({touched} with touches)")

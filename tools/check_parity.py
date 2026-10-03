#!/usr/bin/env python3
"""Checks that tools/recognizers.py matches the Kotlin recognizers.

    python3 tools/check_parity.py dump cases.txt     # write replay input
    (run tools/sim/Parity.kt on cases.txt -> kotlin.txt, see its header)
    python3 tools/check_parity.py diff kotlin.txt    # compare

Input lines: "gid heading dt,dx,dy;..." (integer dt ms, dp deltas) from the
labeled trajectories; every case starts at heading UP rotated to its logged
heading, and every command is assumed accepted."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_candidate import load
import recognizers as R

DIRS = {(0, -1): R.UP, (1, 0): R.RIGHT, (0, 1): R.DOWN, (-1, 0): R.LEFT}

def cases():
    from eval_candidate import trajs
    for s in load():
        yield s.gid, DIRS[s.fwd], trajs[s.gid]

def python_lines():
    out = []
    for gid, h, pts in cases():
        t = x = y = 0.0
        samples = [(0, 0.0, 0.0)]
        for dt, dx, dy in pts:
            t += int(dt); x += dx; y += dy
            samples.append((int(t), x, y))
        for name in ("O", "S"):
            g = R.Game(h)
            fired = R.replay(R.ALL[name](), samples, g)
            out.append(f"{gid} {name} " + " ".join(f"{t}:{d}:{k}" for t, d, k in fired))
    return out

if __name__ == "__main__":
    if sys.argv[1] == "dump":
        with open(sys.argv[2], "w") as f:
            for gid, h, pts in cases():
                f.write(f"{gid} {h} " + ";".join(f"{int(dt)},{dx},{dy}" for dt, dx, dy in pts) + "\n")
    else:
        kt = [l.rstrip("\n") for l in open(sys.argv[2]) if l.strip()]
        py = python_lines()
        bad = [(a, b) for a, b in zip(py, kt) if a.strip() != b.strip()]
        for a, b in bad:
            print("python:", a); print("kotlin:", b)
        print(f"{len(py)} replays, {len(bad)} differ" + ("" if len(py) == len(kt) else f" (count {len(py)} vs {len(kt)})"))
        sys.exit(1 if bad or len(py) != len(kt) else 0)

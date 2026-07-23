#!/usr/bin/env python3
"""Consistency check: does each labeled gesture's trajectory actually match
its debug-line summary? Verifies (a) the trajectory's net displacement
against the line's length and (b) its total duration against the line's
time, and prints derived stats (path length, average speed) useful for
tuning. Run from the repo root: python3 tools/check_gesture_data.py"""
import csv, math, re, sys, os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cases = {}
with open(os.path.join(root, "docs", "gesture-cases.csv")) as f:
    for row in csv.DictReader(f):
        cases[row["id"]] = row

trajs = {}
with open(os.path.join(root, "docs", "gesture-trajectories.txt")) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        gid, rest = line.split(":", 1)
        pts = re.findall(r"\(([-\d.]+),([-\d.]+),([-\d.]+)\)", rest)
        trajs[gid.strip()] = [(float(a), float(b), float(c)) for a, b, c in pts]

def parse_line(dl):
    """reason, angle_deg, len_dp, ms (or None), outcome, score (or None)"""
    toks = dl.split()
    reason = toks[0]
    angle = int(toks[1].rstrip("\u00b0"))
    length = int(toks[2].rstrip("dp"))
    ms = None
    rest = toks[3:]
    if rest and rest[0].endswith("ms"):
        ms = int(rest[0][:-2]); rest = rest[1:]
    outcome = rest[0] if rest else "-"
    score = float(rest[1]) if len(rest) > 1 else None
    return reason, angle, length, ms, outcome, score

bad = 0
for gid, row in cases.items():
    reason, angle, length, ms, outcome, score = parse_line(row["debug_line"])
    label = f"{gid} [{row['debug_line']}] intended={row['intended_gesture']}"
    tr = trajs.get(gid)
    if not tr:
        print(f"{label}: no trajectory on file")
        continue
    T = sum(dt for dt, _, _ in tr)
    disp = math.hypot(sum(dx for _, dx, _ in tr), sum(dy for _, _, dy in tr))
    path = sum(math.hypot(dx, dy) for _, dx, dy in tr)
    speed = path / (T / 1000) if T > 0 else float("inf")
    print(f"{label}: disp={disp:.1f}dp path={path:.1f}dp T={T:.0f}ms avg={speed:.0f}dp/s")
    tol = max(4.0, 0.25 * length)
    hard = max(12.0, 0.25 * length)  # pre-clean-split cases: boundary-born
    # gestures inherited up to ~12dp recorded in the previous trajectory
    if abs(disp - length) > hard:
        print(f"  MISMATCH: displacement {disp:.1f}dp vs logged {length}dp (tol {hard:.1f})")
        bad += 1
    elif abs(disp - length) > tol:
        print(f"  note: {abs(disp - length):.1f}dp short of logged length -- consistent"
              f" with a boundary-born gesture inheriting pre-birth movement")
    if ms is not None:
        # allowances for cases recorded before the clean vertex split
        # (trajectories and clocks now split at the same boundary vertex,
        # so new cases should not need them)
        slop = max(60, 0.3 * ms)
        if outcome == "-":
            if abs(T - ms) > slop + 250:
                print(f"  MISMATCH: duration {T:.0f}ms vs logged {ms}ms (unfired: should match)")
                bad += 1
            elif abs(T - ms) > slop:
                which = "boundary-birth inheritance" if T < ms else "recorded dwell tail"
                print(f"  note: duration {T:.0f}ms vs logged {ms}ms -- consistent with {which}")
        elif T + slop + 250 < ms:
            print(f"  MISMATCH: trajectory ends ({T:.0f}ms) before logged recognition ({ms}ms)")
            bad += 1
        elif T + slop < ms:
            print(f"  note: trajectory {T:.0f}ms vs recognition {ms}ms -- consistent"
                  f" with boundary-birth inheritance")
print("all consistent" if bad == 0 else f"{bad} mismatch(es)", file=sys.stderr)
sys.exit(1 if bad else 0)

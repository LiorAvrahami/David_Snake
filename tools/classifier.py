#!/usr/bin/env python3
"""A small trained turn classifier (multinomial logistic regression).

Every finger sample is described by how far the finger moved over the
last WINDOWS ms, split into forward / sideways relative to David's
heading, plus its current speed. Classes: none, left, right, back.
Training labels come from tools/intent.py: samples from 20 ms to
LABEL_MS after a turn event's onset carry that turn; doubtful windows are
skipped; everything else is none. Left/right are mirrored to double the
data and keep the model symmetric.

Online use: fire the most likely turn once its probability passes THETA.

Usage:
  python3 tools/classifier.py FILE...           leave-one-file-out test
  python3 tools/classifier.py --export FILE...  train on all, print Kotlin weights"""
import math, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intent
from lab_report import score_at, DIR

WINDOWS = (40, 80, 160, 300)
LABEL_MS = 200
CLASSES = ("none", "left", "right", "back")
V = {0: (0, -1), 1: (1, 0), 2: (0, 1), 3: (-1, 0)}
LET = "URDL"


def features(ts, xs, ys, k, h, lt):
    """Feature vector at sample k for heading h (0..3); lt = index of the
    sample where the last turn of this stroke happened (0 if none)."""
    fx, fy = V[h]
    rx, ry = -fy, fx                      # David's right, screen y down
    f = []
    for w in WINDOWS:
        j = k
        while j > 0 and ts[k] - ts[j - 1] <= w:
            j -= 1
        dx, dy = xs[k] - xs[j], ys[k] - ys[j]
        fwd, side = dx * fx + dy * fy, dx * rx + dy * ry
        f += [fwd / 20.0, side / 20.0, abs(side) / 20.0]
    j = k
    while j > 0 and ts[k] - ts[j - 1] <= 40:
        j -= 1
    dt = max(1, ts[k] - ts[j])
    f.append(math.hypot(xs[k] - xs[j], ys[k] - ys[j]) / dt * 1000 / 500.0)
    # since the last turn (or touch-down): time, and finger travel
    since = min(ts[k] - ts[lt], 400) / 400.0
    dx, dy = xs[k] - xs[lt], ys[k] - ys[lt]
    f += [since, (dx * fx + dy * fy) / 40.0, (dx * rx + dy * ry) / 40.0, abs(dx * rx + dy * ry) / 40.0]
    return f


def mirror(f):
    g = list(f)
    for i in range(len(WINDOWS)):
        g[3 * i + 1] = -g[3 * i + 1]
    g[3 * len(WINDOWS) + 3] = -g[3 * len(WINDOWS) + 3]
    return g


def rel_class(d, h):
    """Class of turning to screen direction d from heading h."""
    if d == h:
        return 0
    if d == (h + 1) % 4:
        return 2
    if d == (h + 3) % 4:
        return 1
    return 3


def samples(data):
    X, Y = [], []
    for _, p, s, evs in data:
        ts = [a for a, _, _ in s.samples]
        xs = [b for _, b, _ in s.samples]
        ys = [c for _, _, c in s.samples]
        lt = 0
        for k in range(1, len(ts)):
            t = ts[k]
            h = DIR.get(s.ih[k], 0) if k < len(s.ih) else 0
            if k < len(s.ih) and s.ih[k] != s.ih[k - 1]:
                lt = k                     # the logged heading changed here
            if any(e.kind == "doubt" and e.onset - 50 <= t <= e.onset + intent.WIN_MS for e in evs):
                continue
            y = 0
            for e in evs:
                if e.kind == "turn" and e.onset + 20 <= t <= e.onset + LABEL_MS:
                    y = rel_class(DIR[e.dir], h)
            f = features(ts, xs, ys, k, h, lt)
            X.append(f); Y.append(y)
            X.append(mirror(f)); Y.append({1: 2, 2: 1}.get(y, y))
    return np.array(X), np.array(Y)


def train(X, Y, l2=1e-3, iters=600, lr=0.5):
    n, d = X.shape
    Xb = np.hstack([X, np.ones((n, 1))])
    W = np.zeros((d + 1, len(CLASSES)))
    Yh = np.eye(len(CLASSES))[Y]
    w = np.ones(len(CLASSES))
    w[1:] = 3.0                            # turns are rare: weight them up
    sw = (Yh * w).sum(1, keepdims=True)
    for _ in range(iters):
        Z = Xb @ W
        Z -= Z.max(1, keepdims=True)
        P = np.exp(Z); P /= P.sum(1, keepdims=True)
        G = Xb.T @ ((P - Yh) * sw) / n + l2 * W
        W -= lr * G
    return W


def probs(W, f):
    z = np.array(f + [1.0]) @ W
    z -= z.max()
    e = np.exp(z)
    return e / e.sum()


def make_fires(W, theta, refractory=60, hold=1):
    def fires_of(p, s):
        tail = score_at(p, s.t0) > 0
        h = DIR.get(s.ih[:1] or "U", 0)
        ts = [a for a, _, _ in s.samples]
        xs = [b for _, b, _ in s.samples]
        ys = [c for _, _, c in s.samples]
        out = []
        last = -10 ** 9
        lt = 0
        run, runc = 0, 0
        for k in range(1, len(ts)):
            if ts[k] - last < refractory:
                continue
            pr = probs(W, features(ts, xs, ys, k, h, lt))
            c = int(np.argmax(pr[1:]) + 1)
            if pr[c] < theta:
                run = 0
                continue
            run = run + 1 if c == runc else 1
            runc = c
            if run < hold:
                continue
            run = 0
            d = {1: (h + 3) % 4, 2: (h + 1) % 4, 3: (h + 2) % 4}[c]
            h = d                          # a back turn is a U-turn when there is a tail
            out.append((ts[k], LET[d]))
            last = ts[k]
            lt = k
        return out
    return fires_of


if __name__ == "__main__":
    import recognizers as R
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--export" in sys.argv:
        X, Y = samples(intent.dataset(args))
        W = train(X, Y)
        print("// rows: features (" + ", ".join(f"fwd{w} side{w} |side|{w}" for w in WINDOWS) + ", speed, since, fwdSince, sideSince, |sideSince|), bias; columns: " + ", ".join(CLASSES))
        print("val W = arrayOf(")
        for row in W:
            print("    floatArrayOf(" + ", ".join(f"{v:.5f}f" for v in row) + "),")
        print(")")
        sys.exit(0)
    for test in args:
        trainset = [a for a in args if a != test]
        X, Y = samples(intent.dataset(trainset))
        W = train(X, Y)
        data = intent.dataset([test])
        print(f"test {os.path.basename(test)}  (trained on {len(Y)} samples, {int((Y > 0).sum())} turn samples)")
        for name in ("P", "P28", "S2"):
            print(f"   {name:6s}", intent.score(intent.replay_fires(R.ALL[name]), data))
        for theta in (0.5, 0.6, 0.7, 0.8):
            print(f"   ML {theta:.1f}", intent.score(make_fires(W, theta), data))

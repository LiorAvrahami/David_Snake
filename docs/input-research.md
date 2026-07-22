# Input research — gesture failure tracking

Ongoing log of misdetected gestures, to tune the recognizer against real data.
Thresholds at time of each case are noted. Angle is signed degrees from
David's forward; length in dp.

## Current tuning (2026-07-22)
- fireDist 12dp (first gesture of a touch), confirmDist 20dp (successor
  gestures born from elbow/stop), sampleDist 6dp, estDist 10dp,
  elbow > 60 deg vs established direction, stop 150 ms, jitterEps 3dp.

## Case log

### FP-1 — liftoff fragment fired (session 1)
`elbow -77 122 R!` then `lift -44 14 Uq`
- 14dp post-elbow fragment during finger liftoff fired U into the queue.
- Not an intended gesture. Led to confirmDist=20dp for successor gestures.

### FN-1 — small successor leg ignored (session 2)
`elbow +111 16 -` (followed by `lift -169 118 U!` / `reverse to north`)
- Intended elbow leg of 16dp did not fire: below the 20dp confirmDist.

### FN-2 — small fragment ignored (session 2)
`stop -103 7 -` after `elbow -127 174 R!`
- Intended (per player) but only 7dp — below even the first-gesture 12dp.

## Analysis
- FP-1 (14dp) sits inside the FN range (7-16dp): a pure length gate cannot
  separate these. 20dp confirmDist trades FP-1 for FN-1.
- Hypothesis: the discriminator is continuation, not length. FP-1 was the
  final fragment immediately before ACTION_UP; FN legs continue into more
  trajectory or a deliberate dwell.
- Candidate fixes to evaluate:
  1. Fire successor legs at ~10-12dp but only after the direction persists
     for one extra polyline sample (continuation confirm).
  2. Suppress firing for movement within the last ~40-60 ms before lift.
  3. On lift, retroactively fire a pending successor leg only if its length
     exceeds a stricter bar (e.g. 25dp), since lift can no longer confirm.

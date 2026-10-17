# Training on the score

The player's definition of how the turn model should be trained. No answer
is ever taken from what the live game did; the only teacher is the score.

## The decision points

Every game tick in which the finger moved (1 dp or more within the tick) is a
decision point. At that tick the model sees the finger features (the stroke
so far, relative to David's heading, finger movement only), exactly what it
would see in the game.

## The score of an action at a decision point

Three actions are possible: none, turn right, turn left (relative to David's
heading at that tick). Each is tried by simulation from the real game state at
that tick (tools/enginesim.py, the exact engine), with the recorded spear
throws (those thrown up to 18 ticks after the decision tick):

- none: David goes straight until he eats the harp or dies.
- a turn: David turns once, then goes straight until he eats the harp or dies.

A run scores +1 for eating the harp (whenever it happens), and for a death
-0.5 times exp(-(s - 1) / decay), s the steps from the decision tick to the
death, decay 1 step for the wall and the tail and 3.7 for a spear (a spear
from the side costs nothing).

Timing is forgiven with the lags and weights defined earlier for the Final
score, delta = 4 ticks and alpha = 0.9: a turn is simulated at every lag
d = -4 .. +4 ticks from the decision tick (a turn before the decision tick runs
from the real state that many ticks earlier, with the live game's commands
in between), nine runs per direction, and

    score(turn) = 0.9 * max over d of score(turn at lag d) + 0.1 * score(turn at lag 0)
    score(none) = score of going straight

So every decision point costs 1 + 2 * 9 = 19 simulations.

## The training target

The right action at a decision point is the one with the highest score (a tie
goes to none). The model is trained to pick it: softmax over the three actions,
cross-entropy with that label, each decision point weighted by how much the
choice matters, the gap between the best score and the second best (a tie
teaches nothing). Adam, 600 steps, L2 as before.

## The model

The MNL architecture: one hidden layer of 16 units over the 17 finger inputs
(no speed input), no back answer, left and right by mirroring.

## Judging

As always: tools/compare_models.py on the held-out games, plain score and
Final score (--tolerance 4 --alpha 0.9), against S2-FAST, ML-1 and MNL1.

# Training on the window score

The player's definition: the model's weights are those that maximize the
window score itself. No labels of any kind; nothing is taken from what the
live game did.

## The objective

The mean, over all windows of the training games (a window at every game tick
with finger movement, tools/windowsim.py), of the Final score of what the model
does in the window, exactly as `tools/compare_models.py --tolerance 4 --alpha
0.9` computes it: the model reads the finger for 250 ms, deciding none, right
or left at every tick with a fresh sample, may make one more turn before
David's second step after a turn, then David goes straight to the harp or a
death; its turns are shifted together by -4..+4 ticks and 0.9 times the best
shift plus 0.1 times the unshifted run counts. A run in which David is speared
from the side, or speared after eating the harp, or enters the phantom tail
(the cell one behind the tail's end, moving as if the tail were one cell
longer), has no score (NaN) and is left out: a window's value is the expected
score given a course with a score. The held-out games are never used for
training or for choosing between runs; they judge the result.

## How the maximum is found

1. `tools/windowtable.py` enumerates every course of action in every window
   (no turn; a first turn at any decision tick, either way; after it a second
   turn at any remaining decision tick, either way; a third turn is not
   enumerated) and scores each once with the Final score, keeping the finger
   features the model sees at every decision tick along the way. A turn the
   engine blocks (into the tail) is the same course as none at that tick.
2. `tools/windowlearn.py` reads the model (simlearn.MlpPolicy: 17 finger
   inputs, 16 hidden units, outputs none / right / left) as a softmax policy
   over its three scores at each decision node. A window's expected Final
   score is then the sum over its courses of the course's probability times
   its score, computed exactly on the enumerated tree, and so is its gradient
   with respect to the weights. Adam climbs the mean expected score of the
   training windows (learning rate 0.01, L2 1e-4), from random initial
   weights. This is policy gradient with the expectation taken exactly, so
   there is no sampling noise.
3. The model put in the game is the argmax of the three scores, as always; its
   deterministic score is reported next to the expected one. Several random
   starts; the one with the highest training score is kept.

## Judging

`tools/compare_models.py` on the held-out games, plain and Final score,
against S2-FAST, ML-1 and MNL1, and the recorded lift-off flick.

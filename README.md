# David Snake — Android

A faithful Android port of *david_snake*, a 2012 C# WinForms game. It looks
like Snake, but David is being ambushed: while he collects golden harps and
trails a line of musical notes behind him, spear-throwers appear along the
walls, take aim just ahead of where he's going, and let fly.

![Gameplay](docs/screenshot.png)

## Get the APK on your phone

1. Create a new **public** GitHub repository.
2. Extract this zip and upload **all of its contents to the repository root** —
   including the `.github` folder. The file `.github/workflows/build.yml` must
   end up at exactly that path, or the build won't trigger.
3. Commit. GitHub Actions builds the app automatically (2–4 minutes — watch
   the **Actions** tab).
4. Open **Releases** → **David Snake — latest build** → download
   **david-snake.apk** on your phone and open it. Android will ask you to
   allow installs from your browser ("install unknown apps") the first time.

Every later commit rebuilds the APK and refreshes the same `latest` release.
The debug signing key is committed with the project, so new builds install
straight over the old one — no uninstalling.

## How to play

Swipe anywhere to steer. A swipe is read relative to the way David is
going: any clearly sideways move turns him that way, and a turn moves him
a step right away. Fast flicks count after a few dp, slow drags need a
little more, and a quick flick counts even when the finger lifts at once.
Keep dragging and bend sharply to make the next turn, so you can carve
zigzags in one continuous drag. Swiping straight back the way he came
makes a U-turn (a step to the side, then, on his next regular step,
back). Tap to start, and to retry after a loss. The build number is in
the top-right corner.
Collect harps to grow your trail of notes. Spears kill only on a head hit;
they pass over your tail and stick into the far wall (six at most — the
oldest falls out). When you're pressed against a wall you get a beat to
swipe away before it's over. Attackers always throw from the wall on your far side, aiming one
cell ahead of you. Waves come faster and larger the longer you survive.

And yes — after you fall, they keep throwing. The original did that too.

## Input test (debug mode)

Debug mode is on when the app starts (for now): games are recorded, with
the normal input (`ML-1`, the best so far). Dragging along the very top
edge of the screen from one side to the other turns it off, and on again. The top-right corner shows how many games are stored.

- Every recorded game is saved as it ends, in the app's own storage, and
  kept across app restarts and debug mode on/off. *Export games* (start
  and lose screens, in debug mode) joins all stored games into one file,
  `Downloads/DavidSnake_Games_v<version>_<date>_<time>_<N>games.txt`,
  reads it back to check it is complete, and only then clears them.
  Analyze with `python3 tools/lab_report.py FILE`; score input methods
  with `python3 tools/windowsim.py FILE...`: at every gesture start
  (the finger starts moving after resting, or turns sharply while
  moving; a resting finger starts nothing), each method reads the next
  2 (or 3) steps of input from the real situation, an exact port of the
  engine (`tools/enginesim.py`, checked by replaying every recorded
  game) plays its turns, and David goes on straight until he eats the
  harp (+1, whenever it comes) or dies (-0.5, times exp(-(s-1)/d) for a
  death s steps after the gesture start; d = 2 steps for a wall, 3.7 for
  a spear, 4.2 for the tail; a spear hitting him from the side costs
  nothing). Needs v1.7+ recordings (every
  spear throw logged); model choices and training use only those, older
  ones are for sanity checks.
  `python3 tools/simlearn.py FILE...` trains a small weighted-formula turn
  model on that score (finger movement only; at each tick it tries all
  four answers, none/left/right/back, in the simulation and learns from
  the outcomes), cross-validated by game; `--export 3` prints the weights
  trained on all games, and `tools/export_model.py` records them with a
  complete description (`tools/models/<name>.json`), and with `--game`
  puts them in the game (`MlModel.kt`).
- The input is `ML-1`, that model trained on 41 games (it beat S2-FAST on
  held-out games). Every recorded game starts with a `model` line holding
  the input in full: recognizer, settings, weights, features, and how it
  was trained (code commit, training files with sha256, settings), so it
  can be rebuilt from the file alone; every finger sample carries its game
  tick. `python3 tools/mlparity.py FILE` rebuilds the model from each
  game's model line, replays the game, and checks every decision.
- `tools/models/ml-2.json`: ML-2, trained like ML-1 but with the current
  scoring, on 121 games; not in the game, since on held-out games it did
  not beat ML-1.
- `training_recordings/` holds the recordings for scoring and training,
  as the app exported them, with a list of what is in it (its README).
  Add new exports with `python3 tools/add_recordings.py FILE...`, which
  skips duplicate files and games and checks every game replays exactly.
  The models' descriptions name the files they were trained on, with hashes. `python3 tools/retrain.py` retrains it from
  its description (`tools/models/ml-1.json`, or any recorded game's model
  line) and checks the weights come out identical.


Earlier tests: test 1 (v1.1.50) found the original input best for long
continuous drags and beat-timed movement worst; the original's only
flagged failures were blocked backward swipes, now U-turns. Test 2
(`O-HOLD-UTURN`, `O-HOLD-ALL`, `O-PLUS-28`, `S2-STEP`) and a replay of
all recordings against intents inferred from the game
(`tools/intent.py`; a trained classifier, `tools/classifier.py`, did not
beat the hand-built readers) led to test 3: `S2-FAST` (S2 with a 0.12 s
blind spot after a turn) scored best in replay but `O-HOLD-UTURN` did
better in play.

## Project notes

Plain Kotlin with zero dependencies: one Activity, a custom Canvas view,
and all UI built in code (no layout XML). The game logic lives in
`GameEngine.kt`, a line-faithful port of the original's `Form1.cs`,
`figure.cs`, `attaker.cs` and `math.cs`, preserving its tick structure
(the snake steps every 4th tick and spears move every tick; a turn
executes either as the original's immediate step or in a metronome mode
with one instant rotation per movement plus one queued turn, see
`TurnMode`) at a relaxed
mobile pace, locked to the original's hard difficulty, and its quirks —
including the difficulty-scaled wall-grace window and the post-death spear
rain. One genuine bug in the original was fixed (the harp could briefly
respawn under the first tail segment) and is flagged with a comment. The
sprites are the original 48-pixel bitmaps, converted to transparent PNGs
and drawn with nearest-neighbor scaling so they stay crisp. The engine was
validated headlessly with ~600k simulated ticks (1.8M assertions) across
all difficulties — the simulator is in `tools/sim/Sim.kt`.

There is intentionally no Gradle wrapper: the workflow installs Gradle 8.7
on JDK 17 (AGP 8.5.2, compileSdk 34, minSdk 26). To build locally instead,
install Gradle 8.7 and the Android SDK, then run `gradle assembleDebug`.

Original game and artwork by the original author, 2012. This port keeps
both intact.

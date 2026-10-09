# Training recordings

Game recordings exported from the app in debug mode, used to score input
methods (`tools/windowsim.py`) and to train models (`tools/simlearn.py`).
Only recordings that log every spear throw (app v1.7 and later) belong here.
Add new exports with `python3 tools/add_recordings.py FILE...`: it skips
files and games that are already here, checks that every game replays
exactly, and rewrites this list. Files keep the name the app gave them.

| First game | File | App | Input | Games | With touches | sha256 |
|---|---|---|---|---|---|---|
| 2026-10-03 23:58:24 | DavidSnake_InputLab_v1.10.63_2026-10-03_235821.txt | 1.10.63 | S2-FAST | 19 | 19 | ce3aa1d5c28b |
| 2026-10-04 00:39:42 | DavidSnake_InputLab_v1.13.66_2026-10-04_003849.txt | 1.13.66 | S2-FAST | 8 | 8 | ae2bfb32ae31 |
| 2026-10-08 13:10:03 | zDavidSnake_InputLab_v1.13.66_2026-10-08_130959.txt | 1.13.66 | S2-FAST | 14 | 14 | 2c987b86e918 |
| 2026-10-08 20:49:44 | DavidSnake_Games_v1.15.72_2026-10-08_210016_12games.txt | 1.15.72 | ML-1 | 12 | 11 | 40b601854dd2 |
| 2026-10-08 21:00:18 | DavidSnake_Games_v1.15.73_2026-10-09_085006_24games.txt | 1.15.73 | ML-1 | 24 | 22 | d6d60de321b5 |
| 2026-10-09 08:50:08 | DavidSnake_Games_v1.15.73_2026-10-09_092805_15games.txt | 1.15.73 | ML-1 | 15 | 12 | 4c5df9aa5674 |
| 2026-10-09 09:32:26 | DavidSnake_Games_v1.15.73_2026-10-09_095916_29games.txt | 1.15.73 | ML-1 | 29 | 29 | 980ec6293a81 |

121 games, 115 of them with touches (a game started by accident and
never touched has no gestures, so it adds nothing to scoring or training).

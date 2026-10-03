"""Rebuilds the game state of a recorded game at any game tick:
David's head, heading and tail, the harp, and (for recordings from
v1.6 on, which log every spear throw) the flying spears.

Old recordings have spears only in the board snapshots taken at each
turn and at death; GameState.spears_known(tk) says whether spears can be
trusted at a tick."""
V = {"U": (0, -1), "R": (1, 0), "D": (0, 1), "L": (-1, 0)}
COLS, ROWS = 21, 13


class GameState:
    def __init__(self, play):
        self.p = play
        self.exact = any(True for _ in getattr(play, "spears", []))
        self.steps = sorted(play.steps, key=lambda s: s["t"])

    # --- David
    def head_at_time(self, t):
        """(x, y, heading letter, step index) of David at time t."""
        cur = (10, 6, "U", -1)
        for i, s in enumerate(self.steps):
            if s["t"] > t:
                break
            cur = (s["head"][0], s["head"][1], s.get("hd", s["d"]), i)
        return cur

    def tail_at_step(self, i, score):
        """Tail cells (head-first) right after step i: the previous `score`
        head positions."""
        cells = [tuple(s["head"]) for s in self.steps[max(0, i - score):i]]
        cells.reverse()
        if i - score < 0:
            cells.append((10, 6))
        return cells[:score]

    def score_at(self, t):
        return sum(1 for h in self.p.harps if h["t"] <= t)

    def harp_at(self, t):
        """Harp cell at time t, or None if unknown (old recordings: between
        a harp being eaten and the next board snapshot)."""
        best_t, best = -1, (3, 5) if not self.p.harps or self.p.harps[0]["t"] > t else None
        for h in self.p.harps:
            if h["t"] <= t and "next" in h:
                best_t, best = h["t"], tuple(h["next"])
            elif h["t"] <= t:
                best_t, best = h["t"], None
        for c in self.p.cmds:
            if best_t <= c["t"] <= t:
                best_t, best = c["t"], tuple(c["board"]["harp"])
        return best

    # --- spears (exact for v1.6+ recordings)
    def spears_at_tick(self, tk):
        """[(x, y, dirLetter)] flying at the end of tick tk."""
        out = []
        for s in getattr(self.p, "spears", []):
            if s["tk"] > tk:
                continue
            x, y = s["at"]
            vx, vy = V[s["d"]]
            alive = True
            for _ in range(tk - s["tk"]):
                x, y = x + vx, y + vy
                # engine: after moving, a spear whose next cell is off the
                # board sticks in the wall and stops flying
                if not (0 <= x + vx < COLS and 0 <= y + vy < ROWS):
                    alive = False
                    break
            if alive:
                out.append((x, y, s["d"]))
        return out

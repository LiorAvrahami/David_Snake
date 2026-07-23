package com.davidsnake.game

import kotlin.math.sqrt
import kotlin.random.Random

/**
 * Faithful port of the original 2012 "david_snake" WinForms game logic
 * (Form1.cs / figure.cs / attaker.cs / math.cs), with the original's
 * timing structure preserved:
 *
 *  - one base tick ~= one WinForms timer tick (~16 ms at the original's
 *    default "speed molt" of 0.1, which floored both game timers to the
 *    ~15.6 ms system timer resolution)
 *  - the snake steps every 4th base tick (the "counter" cycle)
 *  - spears move 1 cell every base tick (5x snake speed)
 *  - attackers wind up 7 ticks, hold the throw pose for 10 ticks, then
 *    linger for `attackerCountGoal` ticks before vanishing
 *  - a new wave spawns every `attackerCountGoal` ticks; the goal ramps
 *    from 60 down to 19 and the wave size is (200 / goal - 2)
 *
 * Deliberately preserved quirks of the original:
 *  - deviating from the original's step-on-keypress: a turn intent only
 *    rotates the head instantly, movement happens strictly on the step
 *    schedule, turns queue at depth two (one instant rotation per
 *    movement -- a hard rule -- plus one queued turn applied at the
 *    step), and a turn can never aim at a wall or into the tail
 *  - at a wall the snake presses against it for a small, difficulty-based
 *    grace window (easy 3 / medium 2 / hard 1 extra ticks) before dying
 *  - attackers aim one cell ahead of you with +/-1 jitter, and always
 *    reflect to the wall on your far side
 *  - wall room claims ("bool2") are never released during a run
 *  - after you die, attackers keep spawning and hurling spears at the
 *    spot where you fell; spears that reach the corpse freeze in it
 */
class GameEngine(private val rng: Random = Random.Default) {

    companion object {
        const val COLS = 21
        const val ROWS = 13

        const val EMPTY = 0
        const val HEAD = 1
        const val TAIL = 2
        const val HARP = 3

        const val UP = 0
        const val RIGHT = 1
        const val DOWN = 2
        const val LEFT = 3

        const val MAX_WALL_SPEARS = 6
        const val MAX_ATTACKERS = 69
        const val ANIM_INTERVAL_MS = 110L   // original timer1.Interval
        const val TICK_MS = 45L             // mobile pace: 1.5x slower than the old Chill preset
    }

    enum class Phase { READY, PLAYING, LOST }

    /** idx matches the original get_dificolty() mapping (200/320/400 -> 0/1/2). */
    enum class Difficulty(val idx: Int) { EASY(0), MEDIUM(1), HARD(2) }

    /** Shared 0->1->2->1->0 sparkle animation state (original toggle_fig_pics). */
    open class Anim {
        var frame = 0
        var frameUp = true

        fun toggleFrame() {
            when (frame) {
                2 -> { frame = 1; frameUp = false }
                0 -> { frame = 1; frameUp = true }
                1 -> frame += if (frameUp) 1 else -1
            }
        }
    }

    class Segment(var x: Int, var y: Int) : Anim()

    class Spear(var x: Int, var y: Int, var dir: Int) {
        var stuck = false
    }

    class Attacker(var wall: Int, var pos: Int) {
        var throwing = false
        var vanishing = false
        var count = 7               // original attaker.count initial value
    }

    // ------------------------------------------------------------------ state

    val blocks = Array(COLS) { IntArray(ROWS) }

    var headX = 10; private set
    var headY = 6; private set
    var headDir = UP; private set

    /** tail[0] is the segment right behind the head. */
    val tail = ArrayList<Segment>()

    var harpX = 3; private set
    var harpY = 5; private set
    val harpAnim = Anim()

    /** Flying spears (plus any frozen in the corpse after a loss). */
    val spears = ArrayList<Spear>()

    /** Spears stuck in the walls, oldest first, capped at [MAX_WALL_SPEARS]. */
    val wallSpears = ArrayList<Spear>()

    val attackers = ArrayList<Attacker>()

    // "bool2" wall room claims -- intentionally never cleared during a run.
    private val roomTop = BooleanArray(COLS)
    private val roomBottom = BooleanArray(COLS)
    private val roomLeft = BooleanArray(ROWS)
    private val roomRight = BooleanArray(ROWS)

    var difficulty = Difficulty.HARD

    var phase = Phase.READY; private set
    var score = 0; private set

    /** Notified on every phase change (READY / PLAYING / LOST). */
    var listener: ((Phase) -> Unit)? = null

    private var stepCounter = 4         // original 'counter'
    private var rotatedSinceStep = false // the instant turn of this window is used
    private var pendingDir = -1         // one queued turn, applied after the step
    private var pendingId = -1          // effect id of the queued turn
    private var rotId = -1              // effect id of the live UNCOMMITTED rotation
    private var rotPrevDir = 0          // heading to restore if it is canceled
    private var effectSeq = 0           // effect id source
    private var attackerCount = 60      // ticks until the next wave
    private var attackerCountGoal = 60  // ramps 60 -> 19
    private var cont3 = 0               // original 'timer_2_cont_to_3'

    init {
        reset()
    }

    // ------------------------------------------------------------- lifecycle

    /** Original Form1.Reset(): fresh board, harp at (3,5), head at (10,6)
     *  facing up, and the opening attacker on the right wall at row 6. */
    fun reset() {
        for (col in blocks) col.fill(EMPTY)
        tail.clear()
        spears.clear()
        wallSpears.clear()
        attackers.clear()
        roomTop.fill(false); roomBottom.fill(false)
        roomLeft.fill(false); roomRight.fill(false)

        attackerCountGoal = 60
        attackerCount = attackerCountGoal
        stepCounter = 4
        rotatedSinceStep = false
        pendingDir = -1
        pendingId = -1
        rotId = -1
        cont3 = 0
        score = 0

        harpX = 3; harpY = 5
        harpAnim.frame = 0; harpAnim.frameUp = true
        blocks[harpX][harpY] = HARP

        headX = 10; headY = 6
        headDir = UP
        blocks[headX][headY] = HEAD

        attackers.add(Attacker(RIGHT, 6))

        setPhase(Phase.READY)
    }

    /** Tap: start on the title screen, retry after a loss. */
    fun tapAction() {
        when (phase) {
            Phase.LOST -> reset()
            Phase.READY -> setPhase(Phase.PLAYING)
            Phase.PLAYING -> Unit  // no pause on mobile
        }
    }

    private fun setPhase(p: Phase) {
        phase = p
        listener?.invoke(p)
    }

    // ----------------------------------------------------------------- input

    /**
     * HARD RULE: the head physically rotates at most once per movement,
     * with no exceptions. The first turn intent of an inter-step window
     * rotates the head at once; every further intent before David moves
     * goes to the single queue slot (last one wins) and becomes the
     * heading right after the next step lands. A turn that would face a
     * wall or the snake's own tail -- except its last, vacating bit -- is
     * disregarded, so a rotation can never aim at certain death; the same
     * checks run again when a queued turn is promoted. Same-direction
     * input is ignored and reversals are blocked while there is a tail
     * (original rule). Returns a tag for the debug log plus an effect id
     * that can revoke the intent via cancelSwipe while it is uncommitted.
     *
     * CAUSALITY AND CANCELLATION: an accepted gesture's effect is either a
     * queued turn or a rotation. It stays revocable until David actually
     * moves BECAUSE OF it -- i.e. until a step is taken in its direction.
     * A step taken while the effect still sits in the queue was caused by
     * the old heading and does not commit it. Canceling a queued turn
     * clears the slot; canceling an uncommitted rotation restores the
     * previous heading and refunds the window's rotation, keeping the
     * hard rule as "at most one NET rotation per movement".
     */
    fun onSwipe(dir: Int): SwipeResult {
        if (phase != Phase.PLAYING) return SwipeResult("off", -1)
        if (!rotatedSinceStep) {
            if (dir == headDir) return SwipeResult("same", -1)
            if (tail.isNotEmpty() && dir == (headDir + 2) % 4) return SwipeResult("rev-block", -1)
            val nx = nextX(headX, dir)
            val ny = nextY(headY, dir)
            if (!inBounds(nx, ny)) return SwipeResult("wall-block", -1)
            if (isTailBlock(nx, ny)) return SwipeResult("tail-block", -1)
            rotPrevDir = headDir
            headDir = dir
            rotatedSinceStep = true
            rotId = ++effectSeq
            return SwipeResult("turn", rotId)
        }
        if (dir != headDir) {
            pendingDir = dir
            pendingId = ++effectSeq
            return SwipeResult("queued", pendingId)
        }
        return SwipeResult("same", -1)
    }

    data class SwipeResult(val tag: String, val id: Int)

    /** Revoke a gesture's effect if it has not yet caused a movement.
     *  Returns "cancel-q" (queue slot cleared), "cancel-rot" (heading
     *  restored, rotation refunded) or "stale" (already committed,
     *  overwritten, or unknown). */
    fun cancelSwipe(id: Int): String {
        if (id < 0 || phase != Phase.PLAYING) return "stale"
        if (id == pendingId && pendingDir >= 0) {
            pendingDir = -1
            pendingId = -1
            return "cancel-q"
        }
        if (id == rotId) {
            headDir = rotPrevDir
            rotatedSinceStep = false
            rotId = -1
            return "cancel-rot"
        }
        return "stale"
    }

    /** A cell blocks a turn if it holds the tail, unless it is the very
     *  last tail bit, which will have vacated by the time the step lands. */
    private fun isTailBlock(nx: Int, ny: Int): Boolean {
        if (blocks[nx][ny] != TAIL) return false
        val last = tail.lastOrNull() ?: return true
        return !(last.x == nx && last.y == ny)
    }

    /** Promote the queued turn to the heading, if legal right now. */
    private fun promotePendingDir() {
        val d = pendingDir
        val pid = pendingId
        if (d < 0) return
        pendingDir = -1
        pendingId = -1
        if (d != headDir &&
            !(tail.isNotEmpty() && d == (headDir + 2) % 4) &&
            inBounds(nextX(headX, d), nextY(headY, d)) &&
            !isTailBlock(nextX(headX, d), nextY(headY, d))
        ) {
            rotPrevDir = headDir
            headDir = d
            rotatedSinceStep = true
            rotId = pid  // the queued effect lives on as an uncommitted rotation
        }
    }

    // ----------------------------------------------------------------- ticks

    /** One base tick. Runs while PLAYING, and keeps running after a loss
     *  (spears keep flying and attackers keep pelting the corpse). */
    fun tick() {
        if (phase != Phase.PLAYING && phase != Phase.LOST) return
        movementTick()
        attackerTick()
    }

    /** Original timer1 (110 ms): sparkle animation for the notes and harp. */
    fun animTick() {
        for (seg in tail) seg.toggleFrame()
        harpAnim.toggleFrame()
    }

    /** Original movment_Tick(sender, e). */
    private fun movementTick() {
        if (stepCounter <= 0 && phase == Phase.PLAYING) {
            val nx = nextX(headX, headDir)
            val ny = nextY(headY, headDir)
            // Wall grace: past the wall, the step is withheld until the
            // counter sinks to -(3 - difficulty), giving a last-moment out.
            // Facing a wall by rotation is impossible, so this state only
            // arises from travel and the instant rotation is always free
            // to steer out of it.
            if (inBounds(nx, ny) || stepCounter <= -(3 - difficulty.idx)) {
                step(true)
            }
        }
        stepCounter--
        moveSpears()
    }

    /** Original movment_Tick(bool delet_last): one snake step. */
    private fun step(deleteLastIn: Boolean) {
        if (phase == Phase.LOST) return
        stepCounter = 4
        rotId = -1  // this step moves in headDir: its rotation is now committed
        var deleteLast = deleteLastIn

        val lastX = headX
        val lastY = headY
        blocks[headX][headY] = if (tail.isNotEmpty()) TAIL else EMPTY

        headX = nextX(headX, headDir)
        headY = nextY(headY, headDir)
        if (!inBounds(headX, headY)) {
            lose("hit the wall")
            return
        }
        if (blocks[headX][headY] == HARP) {
            deleteLast = false
            score++
            respawnHarp()
        }
        if (blocks[headX][headY] == TAIL) {
            lose("ran into the tail")
            return
        }
        blocks[headX][headY] = HEAD

        if (deleteLast) {
            if (tail.isNotEmpty()) {
                val last = tail.removeAt(tail.size - 1)
                blocks[last.x][last.y] = EMPTY
                val seg = Segment(lastX, lastY)
                tail.add(0, seg)
                if (tail.size > 1) {
                    // The new neck segment inherits its neighbor's animation
                    // frame, advanced once (original snake_tail[0] copy).
                    seg.frame = tail[1].frame
                    seg.frameUp = tail[1].frameUp
                    seg.toggleFrame()
                }
            }
        } else {
            tail.add(0, Segment(lastX, lastY))
            // The original leaves this cell marked EMPTY when the very first
            // segment grows (it tests the tail length before inserting),
            // briefly letting the harp respawn under the tail tip. Marking it
            // TAIL here fixes that without changing anything else.
            blocks[lastX][lastY] = TAIL
        }

        // A fresh inter-step window begins; the queued turn (if any) becomes
        // the new heading now, one step after the instant turn.
        rotatedSinceStep = false
        promotePendingDir()
    }

    /** Original harp respawn: a free cell strictly farther than 4 from the
     *  head. Guarded against the (near-impossible) full-board case. */
    private fun respawnHarp() {
        var attempts = 0
        while (attempts < 5000) {
            val x = rng.nextInt(COLS)
            val y = rng.nextInt(ROWS)
            val dx = (x - headX).toDouble()
            val dy = (y - headY).toDouble()
            if (blocks[x][y] == EMPTY && sqrt(dx * dx + dy * dy) > 4.0) {
                harpX = x; harpY = y
                blocks[x][y] = HARP
                return
            }
            attempts++
        }
        // Fallback: any free cell at all.
        for (x in 0 until COLS) {
            for (y in 0 until ROWS) {
                if (blocks[x][y] == EMPTY) {
                    harpX = x; harpY = y
                    blocks[x][y] = HARP
                    return
                }
            }
        }
        harpX = -1; harpY = -1  // board is full -- nothing left to place
    }

    /** Original move_progectiles / figure.move_progectil. */
    private fun moveSpears() {
        var i = 0
        while (i < spears.size) {
            val s = spears[i]
            var removed = false
            if (blocks[s.x][s.y] != HEAD) {
                s.x = nextX(s.x, s.dir)
                s.y = nextY(s.y, s.dir)
                if (blocks[s.x][s.y] == HEAD && phase != Phase.LOST) lose("speared")
                if (!inBounds(nextX(s.x, s.dir), nextY(s.y, s.dir))) {
                    stickSpear(s)
                    spears.removeAt(i)
                    removed = true
                }
            } else if (phase != Phase.LOST) {
                lose("speared")
            }
            // else: the spear sits frozen in the corpse cell, as in the
            // original -- the fallen player slowly becomes a porcupine.
            if (!removed) i++
        }
    }

    private fun stickSpear(s: Spear) {
        s.stuck = true
        if (wallSpears.size >= MAX_WALL_SPEARS) {
            wallSpears.removeAt(0)
        }
        wallSpears.add(s)
    }

    /** Original timer2_Tick: wave scheduling + per-attacker state machine. */
    private fun attackerTick() {
        cont3 = if (cont3 < 3) cont3 + 1 else 0
        if (attackerCount == 0) {
            if (attackerCountGoal >= 20 && cont3 % (3 - difficulty.idx) == 0) {
                attackerCountGoal--
            }
            attackerCount = attackerCountGoal
            spawnWave(200 / attackerCountGoal - 2)
        } else {
            attackerCount--
        }

        var i = 0
        while (i < attackers.size) {
            val a = attackers[i]
            var removed = false
            if (a.count > 0) {
                a.count--
            } else {
                if (!a.vanishing && !a.throwing) {
                    a.throwing = true
                    a.count = 10
                } else if (!a.vanishing) {
                    a.throwing = false
                    a.vanishing = true
                    a.count = attackerCountGoal
                    spears.add(spearFrom(a))
                } else {
                    attackers.removeAt(i)
                    removed = true
                }
            }
            if (!removed) i++
        }
    }

    /** Original attaker.To_fig(): the spear starts on the attacker's edge
     *  cell, flying into the board. */
    private fun spearFrom(a: Attacker): Spear = when (a.wall) {
        UP -> Spear(a.pos, 0, DOWN)
        RIGHT -> Spear(COLS - 1, a.pos, LEFT)
        DOWN -> Spear(a.pos, ROWS - 1, UP)
        else -> Spear(0, a.pos, RIGHT)
    }

    /** Original ceat_random_attakers: aim ahead of the player with jitter,
     *  reflect to the far wall, claim a wall slot, avoid facing the wave's
     *  first attacker head-on -- with the original's retry relaxations. */
    private fun spawnWave(n: Int) {
        var waveFirstWall = 10  // original dir_1 sentinel
        for (round in 1..n) {
            var wall = 0
            var point = 0
            if (attackers.size < MAX_ATTACKERS) {
                var trys = 0
                while (true) {
                    wall = rng.nextInt(4)
                    var aimX = nextX(headX, headDir)
                    var aimY = nextY(headY, headDir)
                    if (phase == Phase.LOST) {
                        var inB = true
                        if (headX <= -1) { aimX = 0; inB = false }
                        if (headX >= COLS) { aimX = COLS - 1; inB = false }
                        if (headY <= -1) { aimY = 0; inB = false }
                        if (headY >= ROWS) { aimY = ROWS - 1; inB = false }
                        if (inB) { aimX = headX; aimY = headY }
                    }
                    point = if (wall == UP || wall == DOWN) {
                        if (headX != aimX) aimX + rng.nextInt(-1, 2) else aimX
                    } else {
                        if (headY != aimY) aimY + rng.nextInt(-1, 2) else aimY
                    }
                    if (trys >= 600) {
                        point = 5
                        wall = DOWN
                    }
                    if (isPointLegal(point, wall)) {
                        if (phase != Phase.LOST) wall = reflectIfNeeded(wall)
                        if ((!claimRoom(point, wall) && (wall + 2) % 4 != waveFirstWall) ||
                            trys >= 30
                        ) {
                            if (round == 1) waveFirstWall = wall
                            break
                        }
                    }
                    trys++
                }
            }
            attackers.add(Attacker(wall, point))
        }
    }

    /** Original math.Reflect_dir_if_needed: if the player is in this wall's
     *  half of the board, attack from the opposite wall instead. */
    private fun reflectIfNeeded(wall: Int): Int = when (wall) {
        UP -> {
            var py = headY
            if (headDir == UP) py--
            if (py < ROWS / 2) DOWN else UP
        }
        RIGHT -> {
            var px = headX
            if (headDir == RIGHT) px++
            if (px > COLS / 2) LEFT else RIGHT
        }
        DOWN -> {
            var py = headY
            if (headDir == DOWN) py++
            if (py > ROWS / 2) UP else DOWN
        }
        LEFT -> {
            var px = headX
            if (headDir == LEFT) px--
            if (px < COLS / 2) RIGHT else LEFT
        }
        else -> UP
    }

    /** Original is_attacker_room_tacken: claim-on-check, never released. */
    private fun claimRoom(point: Int, wall: Int): Boolean {
        val arr = when (wall) {
            UP -> roomTop
            RIGHT -> roomRight
            DOWN -> roomBottom
            else -> roomLeft
        }
        return if (arr[point]) {
            true
        } else {
            arr[point] = true
            false
        }
    }

    private fun isPointLegal(point: Int, wall: Int): Boolean =
        if (wall == UP || wall == DOWN) point in 0 until COLS
        else point in 0 until ROWS

    /** Why the last game ended ("hit the wall" / "ran into the tail" /
     *  "speared"); empty while no game has been lost. */
    var lostReason = ""
        private set

    private fun lose(reason: String) {
        if (phase == Phase.LOST) return
        lostReason = reason
        // The original wipes the board bitmap on death; pre-death wall spears
        // are never redrawn, so they vanish with the player.
        wallSpears.clear()
        setPhase(Phase.LOST)
    }

    // --------------------------------------------------------------- helpers

    private fun nextX(x: Int, dir: Int): Int = when (dir) {
        RIGHT -> x + 1
        LEFT -> x - 1
        else -> x
    }

    private fun nextY(y: Int, dir: Int): Int = when (dir) {
        DOWN -> y + 1
        UP -> y - 1
        else -> y
    }

    private fun inBounds(x: Int, y: Int): Boolean =
        x in 0 until COLS && y in 0 until ROWS
}

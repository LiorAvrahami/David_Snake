package com.davidsnake.game

import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.sin

/** What a recognizer may ask about the game while reading a stroke. */
interface GameInfo {
    /** Heading new input is judged against (a queued turn counts). */
    val heading: Int
    val hasTail: Boolean
    /** Free cells straight ahead of the head in a direction. */
    fun room(dir: Int): Int
}

/** One command for the engine: a screen direction plus why it fired. */
data class Cmd(val dir: Int, val kind: String)

/**
 * Turns one finger's samples (positions in dp, times in ms) into turn
 * commands. One instance per arm; [down] starts a stroke, [move] feeds
 * every sample (historical ones included), [up] ends it.
 */
interface Recognizer {
    fun down(t: Long, x: Float, y: Float)
    fun move(t: Long, x: Float, y: Float, g: GameInfo): List<Cmd>
    fun up(t: Long, x: Float, y: Float, g: GameInfo): List<Cmd>

    /** For recognizers that decide once per game tick: the commands to run
     *  just before the next tick, decided on the finger sample at [tickT]. */
    fun tick(g: GameInfo): List<Cmd> = NONE
    val tickT: Long get() = 0L
    /** Called once [tick]'s commands have run. */
    fun ticked(g: GameInfo) {}

    /** Everything needed to rebuild this recognizer, as a JSON object. */
    fun describe(): String
}

private val NONE = emptyList<Cmd>()

/** Direction vectors in screen space (y grows downward). */
private fun dirX(d: Int) = when (d) { GameEngine.RIGHT -> 1f; GameEngine.LEFT -> -1f; else -> 0f }
private fun dirY(d: Int) = when (d) { GameEngine.DOWN -> 1f; GameEngine.UP -> -1f; else -> 0f }

/** Signed angle (degrees) of (vx, vy) from (fx, fy); positive is clockwise
 *  on screen, i.e. to the right of the reference direction. */
private fun angleDeg(fx: Float, fy: Float, vx: Float, vy: Float): Double =
    Math.toDegrees(atan2((fx * vy - fy * vx).toDouble(), (fx * vx + fy * vy).toDouble()))

/**
 * A U-turn for a backward swipe while there is a tail (a plain reversal
 * would be blocked): turn to the side the swipe leans to by at least
 * [leanDp], else to the roomier side, never into a wall/tail when the
 * other side is open; then reverse.
 */
private fun uTurn(h: Int, vx: Float, vy: Float, g: GameInfo, leanDp: Float): List<Cmd> {
    val cross = dirX(h) * vy - dirY(h) * vx      // > 0: leans to David's right
    val right = (h + 1) % 4
    val left = (h + 3) % 4
    var side = if (cross > 0) right else left
    val other = if (cross > 0) left else right
    if (abs(cross) < leanDp && g.room(other) > g.room(side)) side = other
    if (g.room(side) == 0 && g.room(other) > 0) side = if (side == right) left else right
    return listOf(Cmd(side, "uturn"), Cmd((h + 2) % 4, "uturn"))
}

/**
 * The first commit's recognizer: once the finger is 42dp from the anchor
 * on either axis, the dominant axis is the command and the anchor jumps
 * to the finger. With [plus] (O-PLUS; the first commit had it off):
 *  - a clearly backward command while there is a tail becomes a U-turn
 *    instead of being blocked;
 *  - a flick, a stroke of at most [flickMs] that fired nothing, counts on
 *    lift once it moved [flickDp] (taps move under 10dp).
 */
class OriginalRecognizer(
    private val plus: Boolean = false,
    private val threshold: Float = 42f
) : Recognizer {
    private val flickDp = 12f
    private val flickMs = 250L
    private var ax = 0f
    private var ay = 0f
    private var x0 = 0f
    private var y0 = 0f
    private var t0 = 0L
    private var fired = false

    override fun down(t: Long, x: Float, y: Float) {
        ax = x; ay = y
        x0 = x; y0 = y; t0 = t
        fired = false
    }

    override fun describe(): String = Json().s("recognizer", "OriginalRecognizer")
        .s("source", "app/src/main/java/com/davidsnake/game/Recognizers.kt")
        .raw("plus", plus.toString()).f("threshold_dp", threshold)
        .f("flick_dp", flickDp).n("flick_ms", flickMs).toString()

    override fun move(t: Long, x: Float, y: Float, g: GameInfo): List<Cmd> {
        val dx = x - ax
        val dy = y - ay
        if (abs(dx) < threshold && abs(dy) < threshold) return NONE
        ax = x; ay = y
        fired = true
        return command(dx, dy, g, "orig")
    }

    override fun up(t: Long, x: Float, y: Float, g: GameInfo): List<Cmd> {
        if (!plus) return NONE
        val cmds = move(t, x, y, g)
        if (cmds.isNotEmpty() || fired || t - t0 > flickMs) return cmds
        val dx = x - x0
        val dy = y - y0
        if (abs(dx) < flickDp && abs(dy) < flickDp) return NONE
        return command(dx, dy, g, "flick")
    }

    /** The dominant axis of (dx, dy) as a command (or a U-turn, see above). */
    private fun command(dx: Float, dy: Float, g: GameInfo, kind: String): List<Cmd> {
        val dir = if (abs(dx) > abs(dy)) {
            if (dx > 0) GameEngine.RIGHT else GameEngine.LEFT
        } else {
            if (dy > 0) GameEngine.DOWN else GameEngine.UP
        }
        val h = g.heading
        if (plus && g.hasTail && dir == (h + 2) % 4) {
            // only a clearly backward swipe (within 35 degrees): a diagonal
            // half-backward one stays blocked, as in the original
            val back = -(dirX(h) * dx + dirY(h) * dy)
            if (abs(dirX(h) * dy - dirY(h) * dx) <= 0.7f * back) return uTurn(h, dx, dy, g, 8f)
        }
        return listOf(Cmd(dir, kind))
    }
}

/**
 * Reads swipes relative to David's heading, since only turns mean
 * anything: forward is a no-op and a reversal is impossible with a tail.
 *
 *  - Distance threshold scales with finger speed: [fastDp] for a flick
 *    (>= [fastSpeed] dp/s over the last [speedWinMs]), [slowDp] for a slow
 *    drag, linear in between. Displacement only counts from an anchor at
 *    most [anchorAgeMs] old, so resting jitter and slow drift never add up.
 *  - Within [forwardDeg] of the heading is "still forward"; past
 *    [backDeg] is "backward"; anything between turns to that side, once
 *    the sideways part alone reaches the threshold.
 *  - Backward with a tail is a U-turn (turn to the side the swipe leans
 *    to, or the roomier side, then reverse); without a tail, a reversal.
 *  - After a command, the next [cooldownMs] of motion is follow-through
 *    and ignored. A further command in the same stroke needs a real
 *    corner: at least [chainDp], bent [cornerDeg] from the last command.
 *    With [v2] (S2, after test 1 showed slow-drift fires and missed bends
 *    after a diagonal) it instead needs the finger moving at least
 *    [chainSpeed] and at least [chainSectorDeg] off the current heading.
 *  - On lift, a short stroke ([liftMaxMs]) that fired nothing is read
 *    whole at [liftDp], so quick flicks still count.
 */
class SmartRecognizer(
    private val v2: Boolean = false,
    val cooldownMs: Long = 150L
) : Recognizer {
    val fastDp = 8f
    val slowDp = 16f
    val fastSpeed = 400f
    val slowSpeed = 100f
    val speedWinMs = 50L
    val anchorAgeMs = 300L
    val forwardDeg = 30.0
    val backDeg = 160.0
    val backFactor = 1.5f
    val chainDp = 14f
    val cornerDeg = 50.0
    val chainSpeed = 200f
    val chainSectorDeg = 60.0
    val liftDp = 10f
    val liftMaxMs = 300L

    private var n = 0
    private var ts = LongArray(256)
    private var xs = FloatArray(256)
    private var ys = FloatArray(256)
    private var anchor = 0
    private var fired = 0
    private var fireT = 0L
    private var mx = 0f            // unit motion direction of the last command
    private var my = 0f

    override fun down(t: Long, x: Float, y: Float) {
        n = 0
        anchor = 0
        fired = 0
        add(t, x, y)
    }

    override fun describe(): String = Json().s("recognizer", "SmartRecognizer")
        .s("source", "app/src/main/java/com/davidsnake/game/Recognizers.kt")
        .raw("v2", v2.toString()).n("cooldown_ms", cooldownMs)
        .f("fast_dp", fastDp).f("slow_dp", slowDp).f("fast_speed", fastSpeed).f("slow_speed", slowSpeed)
        .n("speed_win_ms", speedWinMs).n("anchor_age_ms", anchorAgeMs)
        .f("forward_deg", forwardDeg.toFloat()).f("back_deg", backDeg.toFloat()).f("back_factor", backFactor)
        .f("chain_dp", chainDp).f("corner_deg", cornerDeg.toFloat()).f("chain_speed", chainSpeed)
        .f("chain_sector_deg", chainSectorDeg.toFloat()).f("lift_dp", liftDp).n("lift_max_ms", liftMaxMs)
        .toString()

    private fun add(t: Long, x: Float, y: Float) {
        if (n == ts.size) {
            ts = ts.copyOf(n * 2); xs = xs.copyOf(n * 2); ys = ys.copyOf(n * 2)
        }
        ts[n] = t; xs[n] = x; ys[n] = y
        n++
    }

    override fun move(t: Long, x: Float, y: Float, g: GameInfo): List<Cmd> {
        if (n == 0) { add(t, x, y); return NONE }
        add(t, x, y)
        val j = n - 1
        if (fired > 0 && t - fireT < cooldownMs) {
            anchor = j          // follow-through: the anchor rides along
            return NONE
        }
        while (anchor < j && t - ts[anchor] > anchorAgeMs) anchor++
        val vx = x - xs[anchor]
        val vy = y - ys[anchor]
        var thr = threshold(j)
        if (fired > 0) {
            thr = maxOf(thr, chainDp)
            if (v2) {
                if (speed(j) < chainSpeed) return NONE
                val h = g.heading
                if (abs(angleDeg(dirX(h), dirY(h), vx, vy)) < chainSectorDeg) return NONE
            } else if (abs(angleDeg(mx, my, vx, vy)) < cornerDeg) {
                return NONE
            }
        }
        val cmds = classify(vx, vy, thr, g, if (fired > 0) "chain" else "swipe")
        if (cmds.isEmpty()) return NONE
        val len = hypot(vx, vy)
        mx = vx / len; my = vy / len
        fired++
        fireT = t
        anchor = j
        return cmds
    }

    override fun up(t: Long, x: Float, y: Float, g: GameInfo): List<Cmd> {
        val cmds = move(t, x, y, g)
        if (cmds.isNotEmpty() || fired > 0 || n < 2 || t - ts[0] > liftMaxMs) return cmds
        return classify(xs[n - 1] - xs[0], ys[n - 1] - ys[0], liftDp, g, "lift")
    }

    /** Finger speed (dp/s) over the last [speedWinMs] at sample [j]. */
    private fun speed(j: Int): Float {
        var i = j
        while (i > 0 && ts[j] - ts[i - 1] <= speedWinMs) i--
        val dt = ts[j] - ts[i]
        return if (dt > 0) hypot(xs[j] - xs[i], ys[j] - ys[i]) * 1000f / dt else 0f
    }

    /** Speed-scaled distance threshold at sample [j]. */
    private fun threshold(j: Int): Float {
        val sp = speed(j)
        val k = ((sp - slowSpeed) / (fastSpeed - slowSpeed)).coerceIn(0f, 1f)
        return slowDp + (fastDp - slowDp) * k
    }

    private fun classify(vx: Float, vy: Float, thr: Float, g: GameInfo, kind: String): List<Cmd> {
        val len = hypot(vx, vy)
        if (len < thr) return NONE
        val h = g.heading
        val a = angleDeg(dirX(h), dirY(h), vx, vy)
        val absA = abs(a)
        if (absA <= forwardDeg) return NONE
        val right = (h + 1) % 4
        val left = (h + 3) % 4
        if (absA < backDeg) {
            if (len * sin(Math.toRadians(absA)) < thr) return NONE
            return listOf(Cmd(if (a > 0) right else left, kind))
        }
        if (len < thr * backFactor) return NONE
        val back = (h + 2) % 4
        if (!g.hasTail) return listOf(Cmd(back, "reverse"))
        return uTurn(h, vx, vy, g, 3f)
    }
}

/**
 * A trained model ([MlModel], tools/simlearn.py): a weighted formula over
 * finger movement only, relative to David's heading. It decides once per
 * game tick, just before it (the engine acts on a turn at the next tick
 * anyway), on the latest finger sample: none, right, left or back.
 * Positions are rounded to 0.1 dp as the test file logs them, and all math
 * is in doubles as in the Python twin, so a recorded game replays exactly
 * (tools/mlparity.py). The full rule is in [MlModel.SPEC].
 */
class LearnedRecognizer(
    private val wSide: DoubleArray = MlModel.W_SIDE,
    private val wBack: DoubleArray = MlModel.W_BACK,
    private val spec: String = MlModel.SPEC
) : Recognizer {

    private class Stroke {
        var n = 0
        var ts = LongArray(256)
        var xs = DoubleArray(256)
        var ys = DoubleArray(256)
        var lt = 0              // sample of the last turn (or touch-down)
        var fresh = false       // samples arrived since the last decision

        fun add(t: Long, x: Float, y: Float) {
            if (n == ts.size) {
                ts = ts.copyOf(n * 2); xs = xs.copyOf(n * 2); ys = ys.copyOf(n * 2)
            }
            ts[n] = t
            xs[n] = Math.round(x * 10f) / 10.0
            ys[n] = Math.round(y * 10f) / 10.0
            n++
            fresh = true
        }

        /** First sample in [0, end) at or after time [t]. */
        fun firstAtOrAfter(t: Long, end: Int): Int {
            var lo = 0
            var hi = end
            while (lo < hi) {
                val mid = (lo + hi) ushr 1
                if (ts[mid] < t) lo = mid + 1 else hi = mid
            }
            return lo
        }
    }

    private var cur: Stroke? = null         // the finger on the screen
    private val ended = ArrayDeque<Stroke>() // lifted or replaced, last samples undecided
    private var decided: Stroke? = null     // stroke of this tick's commands
    private var decidedH = 0
    private var decidedK = 0
    override var tickT = 0L
        private set

    override fun down(t: Long, x: Float, y: Float) {
        cur?.let { if (it.fresh) ended.addLast(it) }
        cur = Stroke().also { it.add(t, x, y) }
    }

    override fun move(t: Long, x: Float, y: Float, g: GameInfo): List<Cmd> {
        cur?.add(t, x, y)
        return NONE
    }

    override fun up(t: Long, x: Float, y: Float, g: GameInfo): List<Cmd> {
        cur?.let {
            it.add(t, x, y)
            ended.addLast(it)
        }
        cur = null
        return NONE
    }

    override fun tick(g: GameInfo): List<Cmd> {
        decided = null
        // a lifted stroke's last samples first, one stroke per tick; the
        // finger now on the screen waits for the next tick
        val s = ended.removeFirstOrNull() ?: cur ?: return NONE
        if (!s.fresh) return NONE
        s.fresh = false
        val k = s.n - 1
        val h = g.heading
        val a = best(features(s, k, h))
        if (a == 0) return NONE
        tickT = s.ts[k]
        decided = s
        decidedH = h
        decidedK = k
        return when (a) {
            1 -> listOf(Cmd((h + 1) % 4, "ml"))
            2 -> listOf(Cmd((h + 3) % 4, "ml"))
            else -> if (g.hasTail) {
                val j = s.firstAtOrAfter(s.ts[k] - 160, s.n)
                uTurnD(h, s.xs[k] - s.xs[j], s.ys[k] - s.ys[j], g)
            } else {
                listOf(Cmd((h + 2) % 4, "reverse"))
            }
        }
    }

    override fun ticked(g: GameInfo) {
        val s = decided ?: return
        if (g.heading != decidedH) s.lt = decidedK
        decided = null
    }

    override fun describe(): String = spec

    private fun features(s: Stroke, k: Int, h: Int): DoubleArray {
        val f = DoubleArray(NF)
        val fx = dirXd(h)
        val fy = dirYd(h)
        val rx = -fy
        val ry = fx
        val t = s.ts[k]
        val x = s.xs[k]
        val y = s.ys[k]
        var i = 0
        var j40 = k
        for (w in WIN_MS) {
            val j = s.firstAtOrAfter(t - w, k + 1)
            if (w == 40L) j40 = j
            val dx = x - s.xs[j]
            val dy = y - s.ys[j]
            val side = (dx * rx + dy * ry) / 20.0
            f[i++] = (dx * fx + dy * fy) / 20.0
            f[i++] = side
            f[i++] = abs(side)
        }
        val dt = t - s.ts[j40]
        f[i++] = if (dt > 0) hypot(x - s.xs[j40], y - s.ys[j40]) * 2.0 / dt else 0.0
        val lt = s.lt
        val dx = x - s.xs[lt]
        val dy = y - s.ys[lt]
        val side = (dx * rx + dy * ry) / 40.0
        f[i++] = minOf(t - s.ts[lt], 400L) / 400.0
        f[i++] = (dx * fx + dy * fy) / 40.0
        f[i++] = side
        f[i++] = abs(side)
        f[i] = 1.0
        return f
    }

    /** 0 none, 1 right, 2 left, 3 back: the highest score, ties to the first. */
    private fun best(f: DoubleArray): Int {
        var r = 0.0
        var l = 0.0
        var b = 0.0
        for (i in 0 until NF) {
            val side = IS_SIDE[i]
            r += wSide[i] * f[i]
            l += wSide[i] * (if (side) -f[i] else f[i])
            b += wBack[i] * (if (side) 0.0 else f[i])
        }
        var a = 0
        var v = 0.0
        if (r > v) { a = 1; v = r }
        if (l > v) { a = 2; v = l }
        if (b > v) a = 3
        return a
    }

    /** [uTurn] in doubles, as tools/recognizers.py u_turn (lean 3 dp). */
    private fun uTurnD(h: Int, vx: Double, vy: Double, g: GameInfo): List<Cmd> {
        val cross = dirXd(h) * vy - dirYd(h) * vx
        val right = (h + 1) % 4
        val left = (h + 3) % 4
        var side = if (cross > 0) right else left
        val other = if (cross > 0) left else right
        if (abs(cross) < 3.0 && g.room(other) > g.room(side)) side = other
        if (g.room(side) == 0 && g.room(other) > 0) side = if (side == right) left else right
        return listOf(Cmd(side, "uturn"), Cmd((h + 2) % 4, "uturn"))
    }

    private companion object {
        val WIN_MS = longArrayOf(40L, 80L, 160L, 300L)
        const val NF = 18
        val IS_SIDE = BooleanArray(NF).also { for (i in intArrayOf(1, 4, 7, 10, 15)) it[i] = true }

        fun dirXd(d: Int) = when (d) { GameEngine.RIGHT -> 1.0; GameEngine.LEFT -> -1.0; else -> 0.0 }
        fun dirYd(d: Int) = when (d) { GameEngine.DOWN -> 1.0; GameEngine.UP -> -1.0; else -> 0.0 }
    }
}

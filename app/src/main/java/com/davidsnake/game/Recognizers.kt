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
class OriginalRecognizer(private val plus: Boolean = false) : Recognizer {
    private val threshold = 42f
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
 *  - On lift, a short stroke ([liftMaxMs]) that fired nothing is read
 *    whole at [liftDp], so quick flicks still count.
 */
class SmartRecognizer : Recognizer {
    val fastDp = 8f
    val slowDp = 16f
    val fastSpeed = 400f
    val slowSpeed = 100f
    val speedWinMs = 50L
    val anchorAgeMs = 300L
    val forwardDeg = 30.0
    val backDeg = 160.0
    val backFactor = 1.5f
    val cooldownMs = 150L
    val chainDp = 14f
    val cornerDeg = 50.0
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
            if (abs(angleDeg(mx, my, vx, vy)) < cornerDeg) return NONE
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

    /** Speed-scaled distance threshold at sample [j]. */
    private fun threshold(j: Int): Float {
        var i = j
        while (i > 0 && ts[j] - ts[i - 1] <= speedWinMs) i--
        val dt = ts[j] - ts[i]
        val sp = if (dt > 0) hypot(xs[j] - xs[i], ys[j] - ys[i]) * 1000f / dt else 0f
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

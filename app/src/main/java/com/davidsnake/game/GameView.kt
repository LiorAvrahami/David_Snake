package com.davidsnake.game

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Typeface
import android.view.Choreographer
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.min

/**
 * Renders the fixed 1110x726 virtual board of the original game, letterboxed
 * and scaled to the screen with nearest-neighbor filtering so the 2012 pixel
 * art stays crisp. Also owns the frame loop (Choreographer, fixed-step ticks)
 * and swipe/tap input.
 */
class GameView(context: Context) : View(context), Choreographer.FrameCallback {

    companion object {
        private const val VIRTUAL_W = 1110f     // background.png dimensions
        private const val VIRTUAL_H = 726f
        private const val CELL = 48
        private const val BOARD_OFF = 51f       // original x*48 + 48 + 3
        private const val NO_SWIPE = -1
        private val FIELD_COLOR = Color.rgb(166, 202, 240)
        private val HUD_COLOR = Color.rgb(40, 60, 90)
    }

    val engine = GameEngine()
    var bestScore = 0

    private val sprites = Sprites(context)

    private val bitmapPaint = Paint().apply {
        isAntiAlias = false
        isFilterBitmap = false
        isDither = false
    }
    private val hudPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = HUD_COLOR
        typeface = Typeface.DEFAULT_BOLD
        textSize = 30f
    }
    private val hudPaintSmall = Paint(hudPaint).apply { textSize = 22f }

    // fixed-step frame loop
    private var lastFrameNanos = 0L
    private var tickAccMs = 0L
    private var animAccMs = 0L

    // touch state. Input is split into GESTURES; one gesture produces at
    // most ONE direction change. A gesture ends (and a new one begins) on
    // exactly three events: the finger lifting, the finger stopping in
    // place, or a clear elbow in the trajectory. Elbows are measured on a
    // speed-independent polyline against the gesture's ESTABLISHED
    // direction (frozen from its first few dp), so a rounded thumb corner
    // still reads as one clean bend.
    private val density = context.resources.displayMetrics.density
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop
    private val minFloor = 6f * density         // noise floor to classify at all
    private val estDist = 10f * density         // freezes the established dir
    private val sampleDist = 6f * density       // polyline decimation step
    private val jitterEps = 3f * density        // below this is "in place"
    private val stopMs = 150L                   // dwell that ends a gesture
    private val fireThreshold = 0.30f           // score to act on a gesture
    private val cancelThreshold = 0.25f         // final score below this revokes
    private var anchorX = 0f                    // gesture start
    private var anchorY = 0f
    private var sampX = 0f                      // last polyline sample
    private var sampY = 0f
    private var sampT = 0L                      // when that sample was set
    private var estX = 0f                       // established direction
    private var estY = 0f
    private var estSet = false
    private var spent = false                   // this gesture already fired
    private var swiped = false                  // anything fired this touch
    private var stopRefX = 0f                   // stop-in-place watchdog
    private var stopRefY = 0f
    private var lastProgressT = 0L
    private var topBand = false                 // debug-toggle drag tracking
    private var downX = 0f
    private var gOutcome = ""                   // what this gesture fired
    private var gAngleAtFire = 0                // angle when it fired
    private var gFiredDir = NO_SWIPE            // direction it fired
    private var gId = -1                        // engine effect id, for cancel
    private var gScore = 0f                     // latest score, for the log
    private var gRotLine = ""                   // rotation line, logged after
    private var gCancel = ""                    // cancel line, logged after
    private var firstGesture = true             // no elbow/stop yet this touch
    private var gWasLive = false                // game was playing during it
    private var gStartT = 0L                    // gesture start time (ms)
    private var gFireT = 0L                     // when it was recognized

    // decisive-motion and momentum gates (values chosen from the labeled
    // gesture dataset; see tools/eval_candidate.py)
    private val speedLo = 150f                  // dp/s: no confidence below
    private val speedHi = 450f                  // dp/s: full confidence above
    private val launchLo = 150f
    private val launchHi = 400f
    private val peakWinMs = 80L                 // peak-speed window
    private val launchAgeMs = 50L               // opening-speed span
    private var gPeakSpeed = 0f                 // best windowed speed so far
    private var gBoundaryBorn = false           // born at a boundary vertex
    private var gLaunchFactor = 1f              // momentum discount
    private var gLaunchSet = false              // frozen at 50ms of age
    private var gFwdX = 0f                      // heading frame at fire time
    private var gFwdY = -1f

    // raw finger trajectories (dp deltas per touch event) of the current
    // and the last 3 completed gestures; clipboard-only, never on screen
    private var lastEvX = 0f
    private var lastEvY = 0f
    private var lastEvT = 0L
    private val curTraj = ArrayList<Triple<Long, Float, Float>>()  // (dt,dx,dy)
    private val recentTrajs = ArrayDeque<List<Triple<Long, Float, Float>>>()

    // debug overlay (toggled by dragging across the top edge of the title
    // screen); tap the panel to copy the whole log to the clipboard
    private var debugMode = false
    private val dbg = ArrayDeque<String>()
    private var panelLeft = 0f
    private var panelTop = 0f
    private val dbgBg = Paint().apply { color = Color.argb(170, 0, 0, 0) }
    private val dbgText = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(130, 255, 130)
        typeface = Typeface.MONOSPACE
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        lastFrameNanos = 0L
        Choreographer.getInstance().postFrameCallback(this)
    }

    override fun onDetachedFromWindow() {
        Choreographer.getInstance().removeFrameCallback(this)
        super.onDetachedFromWindow()
    }

    override fun doFrame(frameTimeNanos: Long) {
        if (lastFrameNanos != 0L) {
            var dtMs = (frameTimeNanos - lastFrameNanos) / 1_000_000L
            if (dtMs > 100L) dtMs = 100L  // don't fast-forward after long stalls
            val phase = engine.phase
            if (phase == GameEngine.Phase.PLAYING || phase == GameEngine.Phase.LOST) {
                tickAccMs += dtMs
                val tickMs = GameEngine.TICK_MS
                while (tickAccMs >= tickMs) {
                    val pd = engine.headDir
                    val pp = engine.phase
                    engine.tick()
                    if (debugMode && engine.headDir != pd) {
                        logRotation(pd, engine.headDir, deq = true)
                    }
                    if (debugMode && pp == GameEngine.Phase.PLAYING &&
                        engine.phase == GameEngine.Phase.LOST
                    ) {
                        dlog("GAME END: ${engine.lostReason}")
                    }
                    tickAccMs -= tickMs
                }
            } else {
                tickAccMs = 0L
            }
            animAccMs += dtMs
            while (animAccMs >= GameEngine.ANIM_INTERVAL_MS) {
                engine.animTick()
                animAccMs -= GameEngine.ANIM_INTERVAL_MS
            }
        }
        lastFrameNanos = frameTimeNanos
        invalidate()
        Choreographer.getInstance().postFrameCallback(this)
    }

    override fun onDraw(canvas: Canvas) {
        canvas.drawColor(FIELD_COLOR)
        val scale = min(width / VIRTUAL_W, height / VIRTUAL_H)
        canvas.save()
        // Board shifted left by up to 20% of the screen so the right side
        // stays vacant for gestures, without ever clipping the play area.
        val ox = maxOf(0f, (width - VIRTUAL_W * scale) / 2f - width * 0.2f)
        canvas.translate(ox, (height - VIRTUAL_H * scale) / 2f)
        canvas.scale(scale, scale)

        canvas.drawBitmap(sprites.background, 0f, 0f, bitmapPaint)

        for (a in engine.attackers) drawAttacker(canvas, a)
        for (s in engine.wallSpears) drawCellSprite(canvas, sprites.spearStuck[s.dir], s.x, s.y)
        for (seg in engine.tail) drawCellSprite(canvas, sprites.note[seg.frame], seg.x, seg.y)
        if (engine.phase != GameEngine.Phase.LOST &&
            engine.headX in 0 until GameEngine.COLS &&
            engine.headY in 0 until GameEngine.ROWS
        ) {
            drawCellSprite(canvas, sprites.head[engine.headDir], engine.headX, engine.headY)
        }
        if (engine.harpX >= 0) {
            drawCellSprite(canvas, sprites.harp[engine.harpAnim.frame], engine.harpX, engine.harpY)
        }
        for (s in engine.spears) drawCellSprite(canvas, sprites.spear[s.dir], s.x, s.y)

        if (engine.phase != GameEngine.Phase.READY) {
            canvas.drawBitmap(sprites.hudNote, 8f, 3f, bitmapPaint)
            canvas.drawText(engine.score.toString(), 56f, 34f, hudPaint)
            if (bestScore > 0) {
                val label = context.getString(R.string.best, bestScore)
                val w = hudPaintSmall.measureText(label)
                canvas.drawText(label, VIRTUAL_W - w - 10f, 30f, hudPaintSmall)
            }
        }
        canvas.restore()

        if (debugMode) drawDebugPanel(canvas)
    }

    private fun drawCellSprite(canvas: Canvas, bmp: Bitmap, cx: Int, cy: Int) {
        canvas.drawBitmap(bmp, cx * CELL + BOARD_OFF, cy * CELL + BOARD_OFF, bitmapPaint)
    }

    /** Original attaker.draw(): exact margin positions and rotation indices. */
    private fun drawAttacker(canvas: Canvas, a: GameEngine.Attacker) {
        val set = if (a.throwing) sprites.attackerThrow else sprites.attackerSteady
        when (a.wall) {
            GameEngine.UP -> canvas.drawBitmap(
                set[2], (a.pos * CELL + 48).toFloat(), 0f, bitmapPaint
            )
            GameEngine.RIGHT -> canvas.drawBitmap(
                set[3], (22 * CELL + 6).toFloat(), (a.pos * CELL + 48).toFloat(), bitmapPaint
            )
            GameEngine.DOWN -> canvas.drawBitmap(
                set[0], (a.pos * CELL + 54).toFloat(), (14 * CELL + 6).toFloat(), bitmapPaint
            )
            else -> canvas.drawBitmap(
                set[1], 0f, (a.pos * CELL + 48).toFloat(), bitmapPaint
            )
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                firstGesture = true
                newGesture(event.x, event.y)
                gStartT = event.eventTime
                sampT = event.eventTime
                lastEvX = event.x
                lastEvY = event.y
                lastEvT = event.eventTime
                curTraj.clear()
                stopRefX = event.x; stopRefY = event.y
                lastProgressT = event.eventTime
                swiped = false
                topBand = event.y < height * 0.1f
                downX = event.x
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                if (event.y >= height * 0.1f) topBand = false

                // (2) stop-in-place: if the finger made no real progress for
                // a while and then moves again, the dwell ended the gesture.
                if (hypot(event.x - stopRefX, event.y - stopRefY) >= jitterEps) {
                    if (event.eventTime - lastProgressT >= stopMs) {
                        // the dead wait belongs to neither gesture: the old
                        // one ends where progress stopped, its trajectory is
                        // trimmed there and the dwell deltas are dropped, and
                        // the new one starts when the finger moves again (the
                        // last touch sample before the confirming movement)
                        endGesture("stop", stopRefX, stopRefY, lastProgressT)
                        splitTraj(lastProgressT, keepTail = false)
                        newGesture(stopRefX, stopRefY)
                        firstGesture = false
                        gStartT = lastEvT
                        sampT = lastEvT
                        gBoundaryBorn = true
                    }
                    stopRefX = event.x; stopRefY = event.y
                    lastProgressT = event.eventTime
                }

                // (3) elbow: on the decimated polyline, a stroke bending
                // more than 60 degrees away from the gesture's established
                // direction ends it -- the corner radius does not matter,
                // because the reference direction is frozen, not a running
                // average that drifts around the bend.
                val mx = event.x - sampX
                val my = event.y - sampY
                if (hypot(mx, my) >= sampleDist) {
                    if (estSet &&
                        mx * estX + my * estY < 0.5f * hypot(mx, my) * hypot(estX, estY)
                    ) {
                        endGesture("elbow", sampX, sampY, sampT)
                        splitTraj(sampT)
                        newGesture(sampX, sampY)
                        firstGesture = false
                        gStartT = sampT
                        gBoundaryBorn = true
                        recomputePeak()
                    }
                    sampX = event.x; sampY = event.y
                    sampT = event.eventTime
                }

                // record the raw per-event finger delta for the trajectory
                if (curTraj.size < 500) {
                    curTraj.add(Triple(
                        event.eventTime - lastEvT,
                        (event.x - lastEvX) / density,
                        (event.y - lastEvY) / density
                    ))
                }
                lastEvX = event.x
                lastEvY = event.y
                lastEvT = event.eventTime
                updatePeak()

                if (engine.phase == GameEngine.Phase.PLAYING) gWasLive = true

                val gx = event.x - anchorX
                val gy = event.y - anchorY
                if (!estSet && hypot(gx, gy) >= estDist) {
                    estX = gx; estY = gy; estSet = true
                }
                // One gesture, one direction change: after firing, the rest
                // of this gesture is ignored until something ends it.
                if (!spent && !topBand) {
                    val dir = classifySwipe(gx, gy, minFloor)
                    if (dir != NO_SWIPE) {
                        val a = angleFromForward(gx, gy)
                        val s = angleScore(a) *
                            lengthScore(hypot(gx, gy) / density) *
                            speedScore(gPeakSpeed) *
                            launchFactor(gx / density, gy / density,
                                event.eventTime - gStartT)
                        if (s >= fireThreshold) fire(dir, a, s, event.eventTime)
                    }
                }
                return true
            }
            MotionEvent.ACTION_UP -> {
                // debug toggle, any time: a drag along the top edge of the
                // screen, spanning from one side (<10%) to the other (>90%)
                if (topBand && event.y < height * 0.1f &&
                    minOf(downX, event.x) < width * 0.1f &&
                    maxOf(downX, event.x) > width * 0.9f
                ) {
                    debugMode = !debugMode
                    if (debugMode) dlog("debug on")
                    return true
                }
                // (1) the lift ends the gesture: full verdict, which may
                // late-fire it or revoke its effect.
                endGesture("lift", event.x, event.y, event.eventTime)
                finalizeTraj()
                if (!swiped) {
                    if (debugMode && event.x >= panelLeft && event.y >= panelTop) {
                        copyLog()
                        return true
                    }
                    performClick()
                }
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    /** Begin a fresh gesture at the given point (finger down / elbow / stop). */
    private fun newGesture(ax: Float, ay: Float) {
        anchorX = ax; anchorY = ay
        gWasLive = engine.phase == GameEngine.Phase.PLAYING
        sampX = ax; sampY = ay
        estSet = false
        spent = false
        gOutcome = ""
        gRotLine = ""
        gCancel = ""
        gFiredDir = NO_SWIPE
        gId = -1
        gScore = 0f
        gPeakSpeed = 0f
        gBoundaryBorn = false
        gLaunchFactor = 1f
        gLaunchSet = false
        gFireT = 0L
    }

    /** Act on a gesture: rotate now or queue, and remember the effect id
     *  so the ending verdict can still revoke it. */
    private fun fire(dir: Int, angle: Int, score: Float, atT: Long) {
        val f = forward()
        gFwdX = f.first; gFwdY = f.second
        gAngleAtFire = angle
        gScore = score
        gFireT = atT
        val pre = engine.headDir
        val r = engine.onSwipe(dir)
        if (r.tag == "turn") gRotLine = rotLine(pre, dir, deq = false)
        gOutcome = dirName(dir) + resTag(r.tag)
        gFiredDir = dir
        gId = r.id
        spent = true
        swiped = true
    }

    /**
     * Final verdict for a completed gesture, now that its ending is known:
     *  - if it never fired, this is its last chance (late-fire), with the
     *    ending factored into the score;
     *  - if it fired, re-score it; a score that sank below the cancel
     *    threshold revokes its effect -- but only while the engine still
     *    holds it uncommitted (no movement was caused by it yet).
     * Then its line (plus rotation / cancel lines) goes to the log.
     */
    private fun endGesture(reason: String, endX: Float, endY: Float, endT: Long) {
        val gx = endX - anchorX
        val gy = endY - anchorY
        val lenDp = hypot(gx, gy) / density
        val ef = endFactor(reason)
        val sf = speedScore(gPeakSpeed)
        val lf = launchFactor(gx / density, gy / density, endT - gStartT)
        if (gOutcome.isEmpty()) {
            val a = angleFromForward(gx, gy)
            gScore = angleScore(a) * lengthScore(lenDp) * ef * sf * lf
            val dir = classifySwipe(gx, gy, minFloor)
            if (dir != NO_SWIPE && !topBand && gScore >= fireThreshold) {
                fire(dir, a, gScore, endT)
            }
        } else {
            // full-information verdict: judge the final vector, not the
            // snapshot the gesture happened to fire on
            val aFinal = relAngle(gFwdX, gFwdY, gx, gy)
            gScore = angleScore(aFinal) * lengthScore(lenDp) * ef * sf * lf
            if (gScore < cancelThreshold && gId >= 0) {
                val c = engine.cancelSwipe(gId)
                if (c != "stale") {
                    gCancel = "cancel ${dirName(gFiredDir)} (" +
                        (if (c == "cancel-q") "q" else "rot") + ")"
                }
            }
        }
        if (gWasLive) logGesture(reason, endX, endY, endT)
    }

    /** Decisive-motion gate: no confidence below 250 dp/s of windowed
     *  peak speed, full confidence above 500. Slow drifts and meanders
     *  never contain a fast moment; commands do. */
    private fun speedScore(peak: Float) =
        ((peak - speedLo) / (speedHi - speedLo)).coerceIn(0f, 1f)

    /** Momentum gate for boundary-born gestures: a successor that opens
     *  fast inherited its speed (follow-through of the previous stroke);
     *  a fresh command launches from near rest. Frozen once the gesture
     *  is 50ms old. */
    private fun launchFactor(gxDp: Float, gyDp: Float, age: Long): Float {
        if (!gBoundaryBorn) return 1f
        if (!gLaunchSet && age >= launchAgeMs) {
            gLaunchFactor = launchOf(hypot(gxDp, gyDp), age)
            gLaunchSet = true
        }
        if (gLaunchSet) return gLaunchFactor
        return if (age > 0) launchOf(hypot(gxDp, gyDp), age) else 1f
    }

    private fun launchOf(dispDp: Float, age: Long): Float {
        val v = dispDp / (age / 1000f)
        return ((launchHi - v) / (launchHi - launchLo)).coerceIn(0f, 1f)
    }

    /** Best displacement-over-span speed of any window of at least 80ms
     *  (shorter only at the very head of the gesture) ending at a recorded
     *  event. updatePeak folds in the newest event; recomputePeak rebuilds
     *  after an elbow split carries a trajectory tail over. */
    private fun updatePeak() {
        var span = 0L
        var dx = 0f
        var dy = 0f
        var i = curTraj.size - 1
        while (i >= 0) {
            span += curTraj[i].first
            dx += curTraj[i].second
            dy += curTraj[i].third
            if (span >= peakWinMs) break
            i--
        }
        if (span > 0) gPeakSpeed = maxOf(gPeakSpeed, hypot(dx, dy) / (span / 1000f))
    }

    private fun recomputePeak() {
        gPeakSpeed = 0f
        val n = curTraj.size
        for (e in 0 until n) {
            var span = 0L
            var dx = 0f
            var dy = 0f
            var i = e
            while (i >= 0) {
                span += curTraj[i].first
                dx += curTraj[i].second
                dy += curTraj[i].third
                if (span >= peakWinMs) break
                i--
            }
            if (span > 0) gPeakSpeed = maxOf(gPeakSpeed, hypot(dx, dy) / (span / 1000f))
        }
    }

    /** Confidence that the angle meant a turn or reversal: its distance
     *  from the classification boundaries (30 and 150 degrees from
     *  forward), saturating 30 degrees away. Ambiguity costs points, not
     *  obliqueness -- a 180 gesture is a perfectly clear reversal. */
    private fun angleScore(aDeg: Int): Float {
        val a = kotlin.math.abs(aDeg).toFloat()
        val d = if (a > 150f) a - 150f else minOf(a - 30f, 150f - a)
        return (d / 30f).coerceIn(0f, 1f)
    }

    /** Saturating length confidence: 8dp scores .50, 24dp .75. More length
     *  is never evidence against. */
    private fun lengthScore(lenDp: Float) = lenDp / (lenDp + 8f)

    /** A lift ending a successor gesture is the delicate case: liftoff
     *  flicks fake a direction, so the score is discounted there. */
    private fun endFactor(reason: String) =
        if (reason == "lift" && !firstGesture) 0.6f else 1f

    /** Signed angle (degrees) of a vector relative to David's forward:
     *  positive is to his right, negative to his left, +-180 is backward. */
    private fun forward(): Pair<Float, Float> = when (engine.headDir) {
        GameEngine.RIGHT -> Pair(1f, 0f)
        GameEngine.LEFT -> Pair(-1f, 0f)
        GameEngine.DOWN -> Pair(0f, 1f)
        else -> Pair(0f, -1f)
    }

    private fun relAngle(fx: Float, fy: Float, dx: Float, dy: Float): Int {
        val dot = fx * dx + fy * dy
        val cross = fx * dy - fy * dx
        return Math.toDegrees(atan2(cross.toDouble(), dot.toDouble())).toInt()
    }

    private fun angleFromForward(dx: Float, dy: Float): Int {
        val f = forward()
        return relAngle(f.first, f.second, dx, dy)
    }

    private fun resTag(res: String) = when (res) {
        "turn" -> "!"      // rotated instantly
        "queued" -> "q"    // took the queue slot
        "rev-block" -> "x" // reversal blocked by the tail
        "wall-block" -> "w"// turn faced the wall: disregarded
        "tail-block" -> "t"// turn faced mid-tail: disregarded
        "same" -> "="      // already that heading
        else -> "."        // engine not playing
    }

    private fun compass(d: Int) = when (d) {
        GameEngine.UP -> "north"
        GameEngine.RIGHT -> "east"
        GameEngine.DOWN -> "south"
        else -> "west"
    }

    /** One line per physical rotation of the head: which way it turned,
     *  the compass direction it now faces, and whether it was dequeued.
     *  Instant rotations are emitted right AFTER their gesture's line. */
    private fun rotLine(from: Int, to: Int, deq: Boolean): String {
        val word = when (to) {
            (from + 1) % 4 -> "turn right"
            (from + 3) % 4 -> "turn left"
            else -> "reverse"
        }
        return "$word to ${compass(to)}" + if (deq) " (deq)" else ""
    }

    private fun logRotation(from: Int, to: Int, deq: Boolean) {
        if (!debugMode) return
        dlog(rotLine(from, to, deq))
    }

    /** One line per completed gesture: the reason it completed (elbow /
     *  stop / lift), signed angle from forward, length in dp, and what it
     *  fired ("-" if nothing). Fired gestures report the angle at the
     *  moment they fired, since firing rotates the reference frame. */
    private fun logGesture(reason: String, endX: Float, endY: Float, endT: Long) {
        if (!debugMode) return
        val gx = endX - anchorX
        val gy = endY - anchorY
        val len = (hypot(gx, gy) / density).toInt()
        if (len < 3 && gOutcome.isEmpty()) return  // taps and touch noise
        val a = if (gOutcome.isEmpty()) angleFromForward(gx, gy) else gAngleAtFire
        val sign = if (a >= 0) "+" else ""
        val sc = ".%02d".format((gScore * 100).toInt().coerceIn(0, 99))
        // time from the gesture's start until it was recognized and applied
        // (for gestures that never fired: until it ended)
        val ms = ((if (gFireT > 0L) gFireT else endT) - gStartT).coerceAtLeast(0)
        dlog("$reason $sign$a° ${len}dp ${ms}ms ${gOutcome.ifEmpty { "-" }} $sc p${gPeakSpeed.toInt()}")
        if (gRotLine.isNotEmpty()) {
            dlog(gRotLine)
            gRotLine = ""
        }
        if (gCancel.isNotEmpty()) {
            dlog(gCancel)
            gCancel = ""
        }
    }

    private fun dirName(d: Int) = when (d) {
        GameEngine.UP -> "U"
        GameEngine.DOWN -> "D"
        GameEngine.LEFT -> "L"
        GameEngine.RIGHT -> "R"
        else -> "?"
    }

    private fun dlog(msg: String) {
        dbg.addLast(msg)
        while (dbg.size > 300) dbg.removeFirst()
    }

    private fun drawDebugPanel(canvas: Canvas) {
        dbgText.textSize = 9f * density
        val lh = dbgText.textSize * 1.3f
        val shown = 12
        val w = width * 0.20f
        val h = lh * shown + lh * 0.6f
        panelLeft = width - w
        panelTop = height - h
        canvas.drawRect(panelLeft, panelTop, width.toFloat(), height.toFloat(), dbgBg)
        var y = panelTop + lh
        val start = maxOf(0, dbg.size - shown)
        for (i in start until dbg.size) {
            canvas.drawText(dbg.elementAt(i), panelLeft + 6f * density, y, dbgText)
            y += lh
        }
    }

    /** Split the trajectory at a boundary vertex. The predecessor keeps and
     *  finalizes everything up to the vertex; deltas after it either open
     *  the successor's trajectory (elbow: every delta belongs to one side)
     *  or are dropped (stop: the dead wait belongs to neither gesture). */
    private fun splitTraj(vertexT: Long, keepTail: Boolean = true) {
        var k = curTraj.size
        var tailDur = 0L
        while (k > 0 && lastEvT - tailDur > vertexT) {
            tailDur += curTraj[k - 1].first
            k--
        }
        val tail = ArrayList(curTraj.subList(k, curTraj.size))
        while (curTraj.size > k) curTraj.removeAt(curTraj.size - 1)
        finalizeTraj()
        if (keepTail) curTraj.addAll(tail)
    }

    /** Move the finished gesture's trajectory into the last-3 ring; taps
     *  and touch noise (under 3dp of total path) are not kept. */
    private fun finalizeTraj() {
        var total = 0f
        for ((_, dx, dy) in curTraj) total += hypot(dx, dy)
        if (total >= 3f && gWasLive) {
            recentTrajs.addLast(ArrayList(curTraj))
            while (recentTrajs.size > 3) recentTrajs.removeFirst()
        }
        curTraj.clear()
    }

    private fun copyLog() {
        val sb = StringBuilder(dbg.joinToString("\n"))
        sb.append("\n--- trajectories of the last ")
            .append(recentTrajs.size)
            .append(" gestures, oldest first; (dt_ms,dx_dp,dy_dp) per touch event ---")
        for ((i, traj) in recentTrajs.withIndex()) {
            sb.append("\ng").append(i + 1 - recentTrajs.size).append(":")
            for ((dt, dx, dy) in traj) {
                sb.append(" (").append(dt)
                    .append(",").append("%.1f".format(dx))
                    .append(",").append("%.1f".format(dy)).append(")")
            }
        }
        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("david-snake-debug", sb.toString()))
        dlog("copied ${dbg.size} lines +traj")
    }

    override fun performClick(): Boolean {
        super.performClick()
        engine.tapAction()  // start on the title screen, retry after a loss
        return true
    }

    /**
     * Estimate what the segment direction asks of David, relative to his
     * heading. Within about 30 degrees of straight ahead: nothing to do.
     * More than that off-axis: turn toward the perpendicular component
     * (turn-biased, since diagonals almost always mean a turn). Within
     * about 30 degrees of straight back: a reversal attempt, which the
     * engine honors only while there is no tail (original rule).
     */
    private fun classifySwipe(dx: Float, dy: Float, minDist: Float): Int {
        if (hypot(dx, dy) < minDist) return NO_SWIPE
        val horizontal =
            engine.headDir == GameEngine.LEFT || engine.headDir == GameEngine.RIGHT
        val perp = if (horizontal) dy else dx
        val para = if (horizontal) dx else dy
        if (abs(perp) >= 0.577f * abs(para)) {
            return if (horizontal) {
                if (perp > 0) GameEngine.DOWN else GameEngine.UP
            } else {
                if (perp > 0) GameEngine.RIGHT else GameEngine.LEFT
            }
        }
        val forward =
            engine.headDir == GameEngine.RIGHT || engine.headDir == GameEngine.DOWN
        return if ((para > 0) != forward) (engine.headDir + 2) % 4 else NO_SWIPE
    }
}

/** Loads the original 48px sprites and pre-rotates them like the WinForms build. */
class Sprites(context: Context) {

    private val res = context.resources
    private val opts = BitmapFactory.Options().apply { inScaled = false }

    private fun load(id: Int): Bitmap = BitmapFactory.decodeResource(res, id, opts)

    private fun rotations(base: Bitmap): Array<Bitmap> {
        val m = Matrix()
        return Array(4) { i ->
            if (i == 0) base
            else {
                m.reset()
                m.postRotate(90f * i)
                Bitmap.createBitmap(base, 0, 0, base.width, base.height, m, false)
            }
        }
    }

    val background: Bitmap = load(R.drawable.background)
    val head: Array<Bitmap> = rotations(load(R.drawable.davide))
    val note: Array<Bitmap> = arrayOf(
        load(R.drawable.note1), load(R.drawable.note2), load(R.drawable.note3)
    )
    val harp: Array<Bitmap> = arrayOf(
        load(R.drawable.harp1), load(R.drawable.harp2), load(R.drawable.harp3)
    )
    val spear: Array<Bitmap> = rotations(load(R.drawable.spear))
    val spearStuck: Array<Bitmap> = rotations(load(R.drawable.spear_stuck))
    val attackerSteady: Array<Bitmap> = rotations(load(R.drawable.attacker_steady))
    val attackerThrow: Array<Bitmap> = rotations(load(R.drawable.attacker_throw))
    val hudNote: Bitmap = Bitmap.createScaledBitmap(note[0], 42, 42, false)
}

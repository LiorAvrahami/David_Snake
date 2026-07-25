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
    private val ctrlWinMs = 120L                // sliding control window
    private val ctrlMinSpeed = 250f             // dp/s average over the window
    private val ctrlTurnDeg = 30                // off-forward degrees = a turn
    private var topBand = false                 // debug-toggle drag tracking
    private var downX = 0f
    private var swiped = false                  // anything applied this stroke
    private var gWasLive = false                // game was playing during it
    private var strokeStartT = 0L               // finger-down time
    private var strokeRot = 0                   // commands this stroke caused
    private var strokePeak = 0f                 // best window speed (dp/s)
    private var strokeHead = "?"                // heading letter at its start
    private var lastQueuedDir = NO_SWIPE        // dedup queue log lines

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
                lastEvX = event.x
                lastEvY = event.y
                lastEvT = event.eventTime
                curTraj.clear()
                strokeStartT = event.eventTime
                strokeRot = 0
                strokePeak = 0f
                strokeHead = dirName(engine.headDir)
                lastQueuedDir = NO_SWIPE
                gWasLive = engine.phase == GameEngine.Phase.PLAYING
                swiped = false
                topBand = event.y < height * 0.1f
                downX = event.x
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                if (event.y >= height * 0.1f) topBand = false

                // record the raw per-event finger delta for the trajectory
                if (curTraj.size < 2000) {
                    curTraj.add(Triple(
                        event.eventTime - lastEvT,
                        (event.x - lastEvX) / density,
                        (event.y - lastEvY) / density
                    ))
                }
                lastEvX = event.x
                lastEvY = event.y
                lastEvT = event.eventTime
                if (engine.phase == GameEngine.Phase.PLAYING) gWasLive = true

                // the whole recognizer: look at the last ctrlWinMs of the
                // finger path; a window that is fast enough and clearly
                // sideways or backwards IS a command, applied on the spot.
                // The engine's own rules (one rotation per movement window,
                // the depth-two queue, wall/tail/reversal blocks, ignoring
                // the current direction) absorb repeats harmlessly.
                var span = 0L
                var wx = 0f
                var wy = 0f
                var i = curTraj.size - 1
                while (i >= 0) {
                    span += curTraj[i].first
                    wx += curTraj[i].second
                    wy += curTraj[i].third
                    if (span >= ctrlWinMs) break
                    i--
                }
                if (!topBand && span >= ctrlWinMs) {
                    val sp = hypot(wx, wy) / (span / 1000f)
                    if (sp > strokePeak) strokePeak = sp
                    if (sp >= ctrlMinSpeed) {
                        // one boundary, one number: within ctrlTurnDeg of
                        // forward means "still going forward"; past it the
                        // side of the angle picks the turn, and past 150
                        // it is a reversal (blocked by the engine while
                        // there is a tail)
                        val a = angleFromForward(wx, wy)
                        if (abs(a) >= ctrlTurnDeg) {
                            val dir = if (abs(a) > 150) (engine.headDir + 2) % 4
                                      else turnDir(a > 0)
                            val pre = engine.headDir
                            val r = engine.onSwipe(dir)
                            when (r.tag) {
                                "turn" -> {
                                    logRotation(pre, engine.headDir, deq = false)
                                    strokeRot++
                                    swiped = true
                                    lastQueuedDir = NO_SWIPE
                                }
                                "queued" -> if (dir != lastQueuedDir) {
                                    dlog("queue ${dirName(dir)}")
                                    strokeRot++
                                    swiped = true
                                    lastQueuedDir = dir
                                }
                            }
                        }
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
                    else {
                        dbg.clear()
                        recentTrajs.clear()
                    }
                    return true
                }
                logStroke(event.eventTime)
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

    /** One summary line per stroke (finger down to lift): heading at its
     *  start, net length, duration, how many commands it caused, and its
     *  best window speed. */
    private fun logStroke(endT: Long) {
        if (!gWasLive) return
        var nx = 0f
        var ny = 0f
        for ((_, dx, dy) in curTraj) { nx += dx; ny += dy }
        val len = hypot(nx, ny).toInt()
        val ms = (endT - strokeStartT).coerceAtLeast(0)
        dlog("stroke $strokeHead ${len}dp ${ms}ms x$strokeRot p${strokePeak.toInt()}")
    }





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


    /** Move the finished gesture's trajectory into the last-3 ring; taps
     *  and touch noise (under 3dp of total path) are not kept. */
    private fun finalizeTraj() {
        var total = 0f
        for ((_, dx, dy) in curTraj) total += hypot(dx, dy)
        if (total >= 3f && gWasLive) {
            recentTrajs.addLast(ArrayList(curTraj))
            while (recentTrajs.size > 7) recentTrajs.removeFirst()
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

    /** The screen direction of a turn to David's right (clockwise) or
     *  left (counter-clockwise) of his current heading. */
    private fun turnDir(right: Boolean): Int = when (engine.headDir) {
        GameEngine.RIGHT -> if (right) GameEngine.DOWN else GameEngine.UP
        GameEngine.LEFT -> if (right) GameEngine.UP else GameEngine.DOWN
        GameEngine.DOWN -> if (right) GameEngine.LEFT else GameEngine.RIGHT
        else -> if (right) GameEngine.RIGHT else GameEngine.LEFT
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

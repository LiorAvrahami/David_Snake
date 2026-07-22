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
    private val fireDist = 12f * density        // gesture length to classify
    private val confirmDist = 20f * density     // ...for successor gestures
    private val estDist = 10f * density         // freezes the established dir
    private val sampleDist = 6f * density       // polyline decimation step
    private val jitterEps = 3f * density        // below this is "in place"
    private val stopMs = 150L                   // dwell that ends a gesture
    private val flickMin = touchSlop.toFloat()  // finger-up flick minimum
    private var anchorX = 0f                    // gesture start
    private var anchorY = 0f
    private var sampX = 0f                      // last polyline sample
    private var sampY = 0f
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
    private var gRotLine = ""                   // rotation line, logged after
    private var firstGesture = true             // no elbow/stop yet this touch

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
                    engine.tick()
                    if (debugMode && engine.headDir != pd) {
                        logRotation(pd, engine.headDir, deq = true)
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
                        logGesture("stop", stopRefX, stopRefY)
                        newGesture(stopRefX, stopRefY)
                        firstGesture = false
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
                        logGesture("elbow", sampX, sampY)
                        newGesture(sampX, sampY)
                        firstGesture = false
                    }
                    sampX = event.x; sampY = event.y
                }

                val gx = event.x - anchorX
                val gy = event.y - anchorY
                if (!estSet && hypot(gx, gy) >= estDist) {
                    estX = gx; estY = gy; estSet = true
                }
                // One gesture, one direction change: after firing, the rest
                // of this gesture is ignored until something ends it.
                if (!spent && !topBand) {
                    val need = if (firstGesture) fireDist else confirmDist
                    val dir = classifySwipe(gx, gy, need)
                    if (dir != NO_SWIPE) {
                        gAngleAtFire = angleFromForward(gx, gy)
                        val pre = engine.headDir
                        val res = engine.onSwipe(dir)
                        if (res == "turn") gRotLine = rotLine(pre, dir, deq = false)
                        gOutcome = dirName(dir) + resTag(res)
                        spent = true
                        swiped = true
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
                if (!swiped && debugMode && event.x >= panelLeft && event.y >= panelTop) {
                    copyLog()
                    return true
                }
                // (1) lift ends the gesture; a short directional flick that
                // never reached the fire distance registers here.
                if (!swiped && !topBand && firstGesture) {
                    val dir = classifySwipe(event.x - anchorX, event.y - anchorY, flickMin)
                    if (dir != NO_SWIPE) {
                        gAngleAtFire = angleFromForward(event.x - anchorX, event.y - anchorY)
                        val pre = engine.headDir
                        val res = engine.onSwipe(dir)
                        if (res == "turn") gRotLine = rotLine(pre, dir, deq = false)
                        gOutcome = dirName(dir) + resTag(res)
                    } else if (gOutcome.isEmpty()) performClick()
                }
                logGesture("lift", event.x, event.y)
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    /** Begin a fresh gesture at the given point (finger down / elbow / stop). */
    private fun newGesture(ax: Float, ay: Float) {
        anchorX = ax; anchorY = ay
        sampX = ax; sampY = ay
        estSet = false
        spent = false
        gOutcome = ""
        gRotLine = ""
    }

    /** Signed angle (degrees) of a vector relative to David's forward:
     *  positive is to his right, negative to his left, +-180 is backward. */
    private fun angleFromForward(dx: Float, dy: Float): Int {
        val fx: Float
        val fy: Float
        when (engine.headDir) {
            GameEngine.RIGHT -> { fx = 1f; fy = 0f }
            GameEngine.LEFT -> { fx = -1f; fy = 0f }
            GameEngine.DOWN -> { fx = 0f; fy = 1f }
            else -> { fx = 0f; fy = -1f }
        }
        val dot = fx * dx + fy * dy
        val cross = fx * dy - fy * dx
        return Math.toDegrees(atan2(cross.toDouble(), dot.toDouble())).toInt()
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
    private fun logGesture(reason: String, endX: Float, endY: Float) {
        if (!debugMode) return
        val gx = endX - anchorX
        val gy = endY - anchorY
        val len = (hypot(gx, gy) / density).toInt()
        if (len < 3 && gOutcome.isEmpty()) return  // taps and touch noise
        val a = if (gOutcome.isEmpty()) angleFromForward(gx, gy) else gAngleAtFire
        val sign = if (a >= 0) "+" else ""
        dlog("$reason $sign$a $len ${gOutcome.ifEmpty { "-" }}")
        if (gRotLine.isNotEmpty()) {
            dlog(gRotLine)
            gRotLine = ""
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

    private fun copyLog() {
        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("david-snake-debug", dbg.joinToString("\n")))
        dlog("copied ${dbg.size} lines")
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

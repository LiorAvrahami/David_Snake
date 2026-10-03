package com.davidsnake.game

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Typeface
import android.os.Build
import android.os.SystemClock
import android.view.Choreographer
import android.view.MotionEvent
import android.view.View
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.min

/**
 * Renders the fixed 1110x726 virtual board of the original game, letterboxed
 * and scaled to the screen with nearest-neighbor filtering so the 2012 pixel
 * art stays crisp. Also owns the frame loop (Choreographer, fixed-step ticks),
 * touch input, and the debug-mode input test (see [InputLab]).
 */
class GameView(context: Context) : View(context), Choreographer.FrameCallback {

    companion object {
        private const val VIRTUAL_W = 1110f     // background.png dimensions
        private const val VIRTUAL_H = 726f
        private const val CELL = 48
        private const val BOARD_OFF = 51f       // original x*48 + 48 + 3
        private const val TAP_MS = 300L         // a tap is short...
        private const val TAP_DP = 10f          // ...and nearly still
        private const val DOUBLE_TAP_MS = 300L  // 1st tap's lift to 2nd tap's touch
        private val FIELD_COLOR = Color.rgb(166, 202, 240)
        private val HUD_COLOR = Color.rgb(40, 60, 90)
        private val ALERT_COLOR = Color.rgb(170, 30, 30)
    }

    val engine = GameEngine()
    var bestScore = 0
    val lab = InputLab(context)
    val version: String = try {
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"
    } catch (e: Exception) {
        "?"
    }

    private val density = context.resources.displayMetrics.density
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
    private val cornerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = HUD_COLOR
        textAlign = Paint.Align.RIGHT
    }

    // fixed-step frame loop
    private var lastFrameNanos = 0L
    private var tickAccMs = 0L
    private var animAccMs = 0L

    // the input path: recognizer, engine commands and test-file lines
    val session = InputSession(engine, { lab.line(it) }, { dlog(it) })

    // the one finger being followed (the latest to land), in px
    private var activeId = -1
    private var downX = 0f
    private var downY = 0f
    private var strokeT0 = 0L
    private var topBand = false     // debug-toggle drag along the top edge

    // taps: in debug mode a lone tap waits DOUBLE_TAP_MS for a second one
    private var tapUpT = 0L
    private var tapPending: Runnable? = null
    private var awaitingSecond = false
    private var flagShownUntil = 0L

    // debug mode (toggled by dragging along the top edge, end to end);
    // runs the input test and shows a log panel in the bottom right
    private var debugMode = false   // off at every app start
    private val dbg = ArrayDeque<String>()
    private val dbgBg = Paint().apply { color = Color.argb(170, 0, 0, 0) }
    private val dbgText = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(130, 255, 130)
        typeface = Typeface.MONOSPACE
    }

    init {
        session.debug = debugMode
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
            if (paused) {
                tickAccMs = 0L
                val now = SystemClock.uptimeMillis()
                if (countdownEnd in 1..now) resume(now)
            } else if (phase == GameEngine.Phase.PLAYING || phase == GameEngine.Phase.LOST) {
                tickAccMs += dtMs
                val tickMs = GameEngine.TICK_MS
                while (tickAccMs >= tickMs) {
                    session.tick(SystemClock.uptimeMillis())
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

    /** Left edge of the board: shifted left by up to 20% of the screen so
     *  the right side stays vacant for gestures, never clipping the board. */
    private fun boardOffsetX(scale: Float) =
        maxOf(0f, (width - VIRTUAL_W * scale) / 2f - width * 0.2f)

    override fun onDraw(canvas: Canvas) {
        canvas.drawColor(FIELD_COLOR)
        val scale = min(width / VIRTUAL_W, height / VIRTUAL_H)
        canvas.save()
        // Board shifted left by up to 20% of the screen so the right side
        // stays vacant for gestures, without ever clipping the play area.
        val ox = boardOffsetX(scale)
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

        drawHud(canvas)
        drawCountdown(canvas)
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

    // ---------------------------------------------------------- phases

    /** Called (via MainActivity) on every engine phase change. */
    fun onPhase(phase: GameEngine.Phase) {
        val t = SystemClock.uptimeMillis()
        when (phase) {
            GameEngine.Phase.PLAYING -> {
                if (debugMode && lab.running) {
                    val i = lab.playsDone
                    session.startPlay(Arms.armAt(i), i + 1, Arms.armPlay(i), t, wallClock())
                    lab.flush()
                } else {
                    session.startPlay(Arms.DEFAULT, 0, 0, t, "")
                }
            }
            GameEngine.Phase.LOST -> if (session.endPlay(t)) {
                lab.completePlay()
                lab.flush()
            }
            GameEngine.Phase.READY -> Unit
        }
    }

    /** A recorded test is going on (debug mode on). */
    val recording: Boolean get() = debugMode && lab.running

    /** Lose-screen button: close the test file, later games go to a new
     *  one. Returns where the closed file is. */
    fun saveFile(): String {
        return lab.saveAndRotate(sessionHeader(), version)
    }

    /** App going to the background: write out what we have. */
    fun onPauseApp() {
        lab.flush()
    }

    // ----------------------------------------------------------- touch

    override fun onTouchEvent(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> begin(e, 0)
            MotionEvent.ACTION_POINTER_DOWN -> {
                // the newest finger takes over; the old stroke ends here
                if (activeId >= 0) session.end(e.eventTime, "steal", 0f, 0f)
                begin(e, e.actionIndex)
            }
            MotionEvent.ACTION_MOVE -> {
                val i = e.findPointerIndex(activeId)
                if (i >= 0) {
                    for (h in 0 until e.historySize) {
                        val y = e.getHistoricalY(i, h)
                        if (y >= height * 0.1f) topBand = false
                        session.move(e.getHistoricalEventTime(h), e.getHistoricalX(i, h) / density, y / density)
                    }
                    if (e.getY(i) >= height * 0.1f) topBand = false
                    session.move(e.eventTime, e.getX(i) / density, e.getY(i) / density)
                }
            }
            MotionEvent.ACTION_POINTER_UP, MotionEvent.ACTION_UP -> {
                val i = e.actionIndex
                if (e.getPointerId(i) == activeId) finish(e.eventTime, e.getX(i), e.getY(i))
            }
            MotionEvent.ACTION_CANCEL -> {
                if (activeId >= 0) session.end(e.eventTime, "cancel", 0f, 0f)
                activeId = -1
                if (awaitingSecond) {
                    awaitingSecond = false
                    tapPending?.run()   // the held first tap still counts
                    tapPending = null
                }
            }
        }
        return true
    }

    private fun begin(e: MotionEvent, idx: Int) {
        val t = e.eventTime
        activeId = e.getPointerId(idx)
        downX = e.getX(idx)
        downY = e.getY(idx)
        strokeT0 = t
        topBand = downY < height * 0.1f
        session.debug = debugMode
        session.down(t, downX / density, downY / density)
        // a touch soon after a lone tap may be its second tap: hold the
        // first tap's action until this stroke ends
        val pending = tapPending
        if (pending != null && t - tapUpT <= DOUBLE_TAP_MS) {
            removeCallbacks(pending)
            awaitingSecond = true
        }
    }

    /** The followed finger lifted at (x, y) px. */
    private fun finish(t: Long, x: Float, y: Float) {
        activeId = -1
        session.end(t, "up", x / density, y / density)

        // debug toggle: a drag along the top edge from one side to the other
        if (topBand && y < height * 0.1f &&
            minOf(downX, x) < width * 0.1f && maxOf(downX, x) > width * 0.9f
        ) {
            awaitingSecond = false
            toggleDebug()
            return
        }

        val click = session.strokeCmds == 0
        val tap = click && session.maxDist <= TAP_DP && t - strokeT0 <= TAP_MS
        if (awaitingSecond) {
            awaitingSecond = false
            val first = tapPending
            tapPending = null
            if (tap) {
                requestFlag(t)
                return
            }
            first?.run()        // the first tap was a lone tap after all
        }
        if (!click) return
        if (!debugMode || !tap) {
            performClick()
            return
        }
        tapUpT = t
        val r = Runnable { tapPending = null; performClick() }
        tapPending = r
        postDelayed(r, DOUBLE_TAP_MS)
    }

    // ------------------------------------------------------------ flags

    /** Shows the flag menu (MainActivity): the last turns, newest first. */
    var onFlag: ((List<InputSession.Turn>, Long) -> Unit)? = null
    private var paused = false
    private var pauseStart = 0L
    private var countdownEnd = 0L
    private var flagT = 0L

    /** Double tap: pause a live game and ask what went wrong. */
    private fun requestFlag(t: Long) {
        if (paused) return
        flagT = t
        if (engine.phase == GameEngine.Phase.PLAYING) {
            paused = true
            pauseStart = SystemClock.uptimeMillis()
            countdownEnd = 0L
            session.frozen = true
        }
        val show = onFlag
        if (show != null) show(session.recentTurns(4), t) else submitFlag("none", null, -1)
    }

    /** The menu's answer; a paused game resumes after a 3-2-1 countdown. */
    fun submitFlag(type: String, turn: InputSession.Turn?, want: Int) {
        if (session.flag(flagT, type, turn, want)) lab.flush()
        val now = SystemClock.uptimeMillis()
        flagShownUntil = now + 1200L
        if (paused) countdownEnd = now + 3000L
    }

    private fun resume(now: Long) {
        paused = false
        countdownEnd = 0L
        session.frozen = false
        val ms = now - pauseStart
        session.addPause(ms)
        if (session.playNo > 0) lab.line(LabLog.resume(session.playNo, now, ms))
    }

    private fun toggleDebug() {
        debugMode = !debugMode
        session.debug = debugMode
        if (debugMode) {
            dlog("debug on")
            if (!lab.running) {
                lab.startNew(sessionHeader(), version)
            }
        } else {
            session.abortPlay(SystemClock.uptimeMillis(), "debug off")
            lab.flush()
            dbg.clear()
        }
    }

    private fun wallClock(): String =
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())

    private fun sessionHeader(): String {
        val scale = min(width / VIRTUAL_W, height / VIRTUAL_H)
        val ox = boardOffsetX(scale)
        val oy = (height - VIRTUAL_H * scale) / 2f
        val cells = BOARD_OFF * scale
        return LabLog.session(
            version, "${Build.MANUFACTURER} ${Build.MODEL}", Build.VERSION.SDK_INT, density,
            width / density, height / density,
            (ox + cells) / density, (oy + cells) / density,
            GameEngine.COLS * CELL * scale / density, GameEngine.ROWS * CELL * scale / density,
            wallClock(), SystemClock.uptimeMillis()
        )
    }

    override fun performClick(): Boolean {
        super.performClick()
        engine.tapAction()  // start on the title screen, retry after a loss
        return true
    }

    // ------------------------------------------------------------- HUD

    private val countPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = HUD_COLOR
        typeface = Typeface.DEFAULT_BOLD
        textAlign = Paint.Align.CENTER
    }

    /** 3-2-1 before a flagged game resumes. */
    private fun drawCountdown(canvas: Canvas) {
        if (countdownEnd == 0L) return
        val left = countdownEnd - SystemClock.uptimeMillis()
        if (left <= 0) return
        countPaint.textSize = 96f * density
        canvas.drawText(((left + 999) / 1000).toString(), width / 2f, height / 2f + 32f * density, countPaint)
    }

    /** Top-right corner: version always; in debug mode the test status. */
    private fun drawHud(canvas: Canvas) {
        val x = width - 10f * density
        var y = 16f * density
        cornerPaint.typeface = Typeface.DEFAULT
        cornerPaint.textSize = 12f * density
        cornerPaint.color = HUD_COLOR
        canvas.drawText("v$version", x, y, cornerPaint)
        if (!debugMode) return

        val lines = ArrayList<Pair<String, Boolean>>()  // text, bold
        val now = SystemClock.uptimeMillis()
        if (lab.running) {
            val live = session.playNo > 0
            val i = if (live) session.playNo - 1 else lab.playsDone
            val a = Arms.armAt(i)
            lines.add(Pair((if (live) "" else "next: ") + a.name, true))
            lines.add(Pair("play ${Arms.armPlay(i)}/${Arms.BLOCK} · game ${i + 1}", false))
        }
        for ((text, bold) in lines) {
            cornerPaint.typeface = if (bold) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
            cornerPaint.textSize = (if (bold) 15f else 12f) * density
            y += cornerPaint.textSize * 1.3f
            canvas.drawText(text, x, y, cornerPaint)
        }
        cornerPaint.typeface = Typeface.DEFAULT_BOLD
        cornerPaint.textSize = 13f * density
        cornerPaint.color = ALERT_COLOR
        lab.lastError?.let {
            y += cornerPaint.textSize * 1.3f
            canvas.drawText(it.take(40), x, y, cornerPaint)
        }
        if (now < flagShownUntil) {
            y += cornerPaint.textSize * 1.5f
            canvas.drawText("FLAGGED", x, y, cornerPaint)
        }
    }

    // ----------------------------------------------------------- debug log

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
        val left = width - w
        val top = height - h
        canvas.drawRect(left, top, width.toFloat(), height.toFloat(), dbgBg)
        var y = top + lh
        val start = maxOf(0, dbg.size - shown)
        for (i in start until dbg.size) {
            canvas.drawText(dbg.elementAt(i), left + 6f * density, y, dbgText)
            y += lh
        }
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

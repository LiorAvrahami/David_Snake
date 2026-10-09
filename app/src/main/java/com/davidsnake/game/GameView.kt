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
    val session = InputSession(engine) { lab.line(it) }.also { it.appVersion = version }

    // the one finger being followed (the latest to land), in px
    private var activeId = -1
    private var downX = 0f
    private var downY = 0f
    private var strokeT0 = 0L
    private var topBand = false     // debug-toggle drag along the top edge

    // debug mode (toggled by dragging along the top edge, end to end):
    // records every game to a file
    private var debugMode = true    // on at every app start, for now

    /** David moves on a steady beat (turns never move him sooner); set
     *  from the start screen. */
    var steadyBeat = true
    val arm: Arm get() = Arms.current(steadyBeat)

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
                if (debugMode) {
                    val n = lab.nextGameNo()
                    lab.beginGame(n)
                    session.startPlay(arm, n, 1, t, wallClock())
                    lab.flush()
                } else {
                    session.startPlay(arm, 0, 0, t, "")
                }
            }
            GameEngine.Phase.LOST -> if (session.endPlay(t)) lab.flush()
            GameEngine.Phase.READY -> Unit
        }
    }

    /** Debug mode is on (games are being recorded). */
    val recording: Boolean get() = debugMode

    /** Export button: join all stored games into one file in Downloads,
     *  check it, then clear them. [done] runs on the UI thread. */
    fun exportGames(done: (String) -> Unit) {
        lab.export(sessionHeader(), version) { msg -> post { done(msg) } }
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
        session.down(t, downX / density, downY / density)
    }

    /** The followed finger lifted at (x, y) px. */
    private fun finish(t: Long, x: Float, y: Float) {
        activeId = -1
        session.end(t, "up", x / density, y / density)

        // debug toggle: a drag along the top edge from one side to the other
        if (topBand && y < height * 0.1f &&
            minOf(downX, x) < width * 0.1f && maxOf(downX, x) > width * 0.9f
        ) {
            toggleDebug()
            return
        }

        if (session.strokeCmds == 0) performClick()   // start, or retry after a loss
    }

    /** MainActivity refreshes its buttons when debug mode flips. */
    var onDebugChanged: (() -> Unit)? = null

    private fun toggleDebug() {
        debugMode = !debugMode
        if (!debugMode) {
            session.abortPlay(SystemClock.uptimeMillis(), "debug off")
            lab.flush()
        }
        onDebugChanged?.invoke()
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
        lines.add(Pair("recording · ${arm.name}", true))
        lines.add(Pair("${lab.storedGames} games stored", false))
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

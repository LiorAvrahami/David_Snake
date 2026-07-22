package com.davidsnake.game

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

    // touch state. The gesture is a polyline sampled every few dp so drag
    // speed does not matter; a sharp bend (elbow) starts a new segment.
    // Each segment's average direction is classified against David's
    // heading after a short fire distance, so small deliberate strokes
    // register. A segment's first classification is a new turn intent; if
    // its estimate later shifts, that only refines the same intent.
    private val density = context.resources.displayMetrics.density
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop
    private val fireDist = 12f * density        // segment length to classify
    private val sampleDist = 6f * density       // polyline decimation step
    private val flickMin = touchSlop.toFloat()  // finger-up flick minimum
    private var anchorX = 0f                    // segment start
    private var anchorY = 0f
    private var sampX = 0f                      // last polyline sample
    private var sampY = 0f
    private var firedDir = NO_SWIPE             // classification sent so far
    private var firedThisSegment = false
    private var swiped = false

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
                    engine.tick()
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
        canvas.translate((width - VIRTUAL_W * scale) / 2f, (height - VIRTUAL_H * scale) / 2f)
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
                anchorX = event.x; anchorY = event.y
                sampX = event.x; sampY = event.y
                firedDir = NO_SWIPE
                firedThisSegment = false
                swiped = false
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                val mx = event.x - sampX
                val my = event.y - sampY
                if (hypot(mx, my) >= sampleDist) {
                    // Elbow detection on the decimated polyline: if the
                    // newest stroke bends away sharply (>60 degrees) from
                    // the segment so far, a new segment starts at the bend
                    // regardless of how fast the finger is moving.
                    val ax = sampX - anchorX
                    val ay = sampY - anchorY
                    if (hypot(ax, ay) >= sampleDist &&
                        mx * ax + my * ay < 0.5f * hypot(mx, my) * hypot(ax, ay)
                    ) {
                        anchorX = sampX; anchorY = sampY
                        firedDir = NO_SWIPE
                        firedThisSegment = false
                    }
                    sampX = event.x; sampY = event.y
                }
                val dir = classifySwipe(event.x - anchorX, event.y - anchorY, fireDist)
                if (dir != NO_SWIPE && dir != firedDir) {
                    engine.onSwipe(dir, newIntent = !firedThisSegment)
                    firedDir = dir
                    firedThisSegment = true
                    swiped = true
                }
                return true
            }
            MotionEvent.ACTION_UP -> {
                if (!swiped) {
                    // Short flick: too small to fire mid-drag but clearly
                    // directional -- register it on lift.
                    val dir = classifySwipe(event.x - anchorX, event.y - anchorY, flickMin)
                    if (dir != NO_SWIPE) engine.onSwipe(dir) else performClick()
                }
                return true
            }
        }
        return super.onTouchEvent(event)
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

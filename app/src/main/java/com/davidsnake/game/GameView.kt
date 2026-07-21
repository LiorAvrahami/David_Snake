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

    // touch state. Two thresholds: a deliberate drag must travel a solid
    // distance before it registers (robust against wobble), while a short
    // fast flick is caught on finger-up with a much smaller one.
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop
    private val swipeThreshold = 42f * context.resources.displayMetrics.density
    private val flickMin = touchSlop.toFloat()
    private var downX = 0f
    private var downY = 0f
    private var anchorX = 0f
    private var anchorY = 0f
    private var lastX = 0f
    private var lastY = 0f
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
                downX = event.x; downY = event.y
                anchorX = event.x; anchorY = event.y
                lastX = event.x; lastY = event.y
                swiped = false
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                // Elbow detection: if the finger's current motion bends away
                // sharply (>60 degrees) from the drag so far, that is a new
                // gesture -- measure it from the bend instead of averaging
                // it into the old direction.
                val segX = event.x - lastX
                val segY = event.y - lastY
                val accX = lastX - anchorX
                val accY = lastY - anchorY
                val segLen = hypot(segX, segY)
                val accLen = hypot(accX, accY)
                if (segLen >= flickMin && accLen >= flickMin &&
                    segX * accX + segY * accY < 0.5f * segLen * accLen
                ) {
                    anchorX = lastX; anchorY = lastY
                }
                val dir = classifySwipe(event.x - anchorX, event.y - anchorY, swipeThreshold)
                if (dir != NO_SWIPE) {
                    engine.onSwipe(dir)
                    // re-anchor so a continued drag can chain turns; the
                    // heading has changed, so the rest of the same gesture is
                    // measured against the new perpendicular axis
                    anchorX = event.x; anchorY = event.y
                    swiped = true
                }
                lastX = event.x; lastY = event.y
                return true
            }
            MotionEvent.ACTION_UP -> {
                if (!swiped) {
                    // Short flick: never crossed the drag threshold but is
                    // clearly directional -- register it on lift. Measured
                    // from the last elbow so only the final leg counts.
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
     * Interpret a gesture relative to David's heading. Only two turns are
     * ever meaningful (left or right of travel), so the swipe component
     * perpendicular to the heading decides -- a diagonal "back and a bit to
     * the right" swipe still turns right. The along-heading component is used
     * only when there is no meaningful perpendicular part; the engine ignores
     * it except for the tailless reversal the original allowed.
     */
    private fun classifySwipe(dx: Float, dy: Float, threshold: Float): Int {
        val horizontal =
            engine.headDir == GameEngine.LEFT || engine.headDir == GameEngine.RIGHT
        val perp = if (horizontal) dy else dx
        val para = if (horizontal) dx else dy
        return if (abs(perp) >= threshold) {
            if (horizontal) {
                if (perp > 0) GameEngine.DOWN else GameEngine.UP
            } else {
                if (perp > 0) GameEngine.RIGHT else GameEngine.LEFT
            }
        } else if (abs(para) >= threshold) {
            if (horizontal) {
                if (para > 0) GameEngine.RIGHT else GameEngine.LEFT
            } else {
                if (para > 0) GameEngine.DOWN else GameEngine.UP
            }
        } else {
            NO_SWIPE
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

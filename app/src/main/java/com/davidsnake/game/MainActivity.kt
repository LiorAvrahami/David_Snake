package com.davidsnake.game

import android.app.Activity
import android.content.Context
import android.content.SharedPreferences
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Single fullscreen activity. All UI is built in code (no layout XML):
 * the GameView underneath and a centered overlay panel for the start,
 * pause and lose screens.
 */
class MainActivity : Activity() {

    private lateinit var gameView: GameView
    private lateinit var panel: LinearLayout
    private lateinit var titleView: TextView
    private lateinit var subtitleView: TextView
    private lateinit var saveButton: TextView

    private lateinit var prefs: SharedPreferences

    private val ink = Color.rgb(40, 60, 90)
    private val inkSoft = Color.rgb(70, 90, 120)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("david_snake", Context.MODE_PRIVATE)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            val attrs = window.attributes
            attrs.layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            window.attributes = attrs
        }
        hideSystemBars()

        val root = FrameLayout(this)
        gameView = GameView(this)
        gameView.bestScore = prefs.getInt("best", 0)
        root.addView(
            gameView,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        buildOverlay(root)
        buildFlagMenu(root)
        setContentView(root)
        gameView.onFlag = { turns, t -> showFlagMenu(turns, t) }

        gameView.engine.listener = { phase ->
            gameView.onPhase(phase)
            onPhase(phase)
        }
        onPhase(gameView.engine.phase)
    }

    override fun onPause() {
        super.onPause()
        gameView.onPauseApp()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    @Suppress("DEPRECATION")
    private fun hideSystemBars() {
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            )
    }

    // ------------------------------------------------------------ overlay UI

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun buildOverlay(root: FrameLayout) {
        panel = LinearLayout(this)
        panel.orientation = LinearLayout.VERTICAL
        panel.gravity = Gravity.CENTER_HORIZONTAL
        panel.setPadding(dp(32), dp(22), dp(32), dp(22))
        val bg = GradientDrawable()
        bg.cornerRadius = dp(18).toFloat()
        bg.setColor(Color.argb(235, 255, 255, 255))
        bg.setStroke(dp(2), ink)
        panel.background = bg

        titleView = TextView(this)
        titleView.textSize = 26f
        titleView.setTypeface(Typeface.DEFAULT_BOLD)
        titleView.setTextColor(ink)
        titleView.gravity = Gravity.CENTER
        panel.addView(titleView)

        subtitleView = TextView(this)
        subtitleView.textSize = 15f
        subtitleView.setTextColor(inkSoft)
        subtitleView.gravity = Gravity.CENTER
        subtitleView.setPadding(0, dp(8), 0, 0)
        panel.addView(subtitleView)

        // debug mode: close the test file from the lose screen
        saveButton = TextView(this)
        saveButton.text = getString(R.string.save_file)
        saveButton.textSize = 17f
        saveButton.setTextColor(ink)
        saveButton.gravity = Gravity.CENTER
        saveButton.setPadding(dp(18), dp(10), dp(18), dp(10))
        val sbg = GradientDrawable()
        sbg.cornerRadius = dp(12).toFloat()
        sbg.setColor(Color.rgb(222, 234, 248))
        sbg.setStroke(dp(1), inkSoft)
        saveButton.background = sbg
        saveButton.setOnClickListener {
            val where = gameView.saveFile()
            saveButton.visibility = View.GONE
            subtitleView.text = subtitleView.text.toString() + "\n\n" + getString(R.string.saved_to, where)
        }
        val lp = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        )
        lp.topMargin = dp(12)
        panel.addView(saveButton, lp)

        root.addView(
            panel,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER
            )
        )
    }

    // ------------------------------------------------------------- flag menu
    // Debug-mode double tap: the game pauses and asks what went wrong.

    private lateinit var flagLayer: FrameLayout
    private lateinit var flagPanel: LinearLayout

    private fun buildFlagMenu(root: FrameLayout) {
        flagLayer = FrameLayout(this)
        flagLayer.setBackgroundColor(Color.argb(110, 0, 0, 0))
        flagLayer.isClickable = true          // swallow touches behind the menu
        flagLayer.visibility = View.GONE
        flagPanel = LinearLayout(this)
        flagPanel.orientation = LinearLayout.VERTICAL
        flagPanel.gravity = Gravity.CENTER_HORIZONTAL
        flagPanel.setPadding(dp(24), dp(16), dp(24), dp(16))
        val bg = GradientDrawable()
        bg.cornerRadius = dp(18).toFloat()
        bg.setColor(Color.argb(245, 255, 255, 255))
        bg.setStroke(dp(2), ink)
        flagPanel.background = bg
        flagLayer.addView(
            flagPanel,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER
            )
        )
        root.addView(
            flagLayer,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
    }

    private fun arrow(d: Int) = when (d) {
        GameEngine.UP -> "↑ up"
        GameEngine.RIGHT -> "→ right"
        GameEngine.DOWN -> "↓ down"
        else -> "← left"
    }

    private fun showFlagMenu(turns: List<InputSession.Turn>, t: Long) {
        flagLayer.visibility = View.VISIBLE
        fun done(type: String, turn: InputSession.Turn?, want: Int) {
            flagLayer.visibility = View.GONE
            gameView.submitFlag(type, turn, want)
        }
        fun pickDir(then: (Int) -> Unit) = flagStep(
            "Which way did you want to go?",
            listOf(GameEngine.LEFT, GameEngine.UP, GameEngine.DOWN, GameEngine.RIGHT)
                .map { d -> Pair(arrow(d)) { then(d) } } + Pair("not sure") { then(-1) },
            row = true
        )
        fun pickTurn(then: (InputSession.Turn?) -> Unit) {
            if (turns.isEmpty()) { then(null); return }
            flagStep(
                "Which turn was wrong? (newest first)",
                turns.map { tu ->
                    Pair(arrow(tu.dir) + "   %.1f s ago".format((t - tu.t) / 1000f)) { then(tu) }
                } + Pair("not sure") { then(null) }
            )
        }
        flagStep(
            "What went wrong?",
            listOf(
                Pair("It turned, I didn't want that") { pickTurn { tu -> done("fp", tu, -1) } },
                Pair("It didn't turn, I wanted a turn") { pickDir { d -> done("fn", null, d) } },
                Pair("It turned the wrong way") {
                    pickTurn { tu -> pickDir { d -> done("wrong", tu, d) } }
                },
                Pair("Nothing, cancel") { done("none", null, -1) }
            )
        )
    }

    /** One menu step: a title and buttons (in a row with [row]). */
    private fun flagStep(title: String, options: List<Pair<String, () -> Unit>>, row: Boolean = false) {
        flagPanel.removeAllViews()
        val tv = TextView(this)
        tv.text = title
        tv.textSize = 20f
        tv.setTypeface(Typeface.DEFAULT_BOLD)
        tv.setTextColor(ink)
        tv.gravity = Gravity.CENTER
        tv.setPadding(0, 0, 0, dp(8))
        flagPanel.addView(tv)
        val box = LinearLayout(this)
        box.orientation = if (row) LinearLayout.HORIZONTAL else LinearLayout.VERTICAL
        box.gravity = Gravity.CENTER
        for ((label, action) in options) {
            val b = TextView(this)
            b.text = label
            b.textSize = 17f
            b.setTextColor(ink)
            b.gravity = Gravity.CENTER
            b.setPadding(dp(18), dp(10), dp(18), dp(10))
            val bg = GradientDrawable()
            bg.cornerRadius = dp(12).toFloat()
            bg.setColor(Color.rgb(222, 234, 248))
            bg.setStroke(dp(1), inkSoft)
            b.background = bg
            b.setOnClickListener { action() }
            val lp = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
            lp.setMargins(dp(5), dp(4), dp(5), dp(4))
            if (!row) lp.width = ViewGroup.LayoutParams.MATCH_PARENT
            box.addView(b, lp)
        }
        flagPanel.addView(box)
    }

    // --------------------------------------------------------- phase handling

    private fun onPhase(phase: GameEngine.Phase) {
        when (phase) {
            GameEngine.Phase.PLAYING -> panel.visibility = View.GONE
            GameEngine.Phase.READY -> {
                panel.visibility = View.VISIBLE
                saveButton.visibility = View.GONE
                titleView.text = getString(R.string.app_name)
                subtitleView.text =
                    getString(R.string.swipe_hint) + "\n" + getString(R.string.tap_to_start)
            }
            GameEngine.Phase.LOST -> {
                val score = gameView.engine.score
                if (score > gameView.bestScore) {
                    gameView.bestScore = score
                    prefs.edit().putInt("best", score).apply()
                }
                panel.visibility = View.VISIBLE
                titleView.text = getString(R.string.you_lost)
                var sub = getString(R.string.final_score, score, gameView.bestScore) +
                    "\n" + getString(R.string.try_again)
                subtitleView.text = sub
                saveButton.visibility = if (gameView.recording) View.VISIBLE else View.GONE
            }
        }
    }
}

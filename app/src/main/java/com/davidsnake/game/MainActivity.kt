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
    private lateinit var exportButton: TextView
    private lateinit var beatButton: TextView
    private var panelText = ""

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
        gameView.steadyBeat = prefs.getBoolean("steady_beat", false)
        root.addView(
            gameView,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        buildOverlay(root)
        setContentView(root)

        gameView.onDebugChanged = {
            if (gameView.engine.phase != GameEngine.Phase.PLAYING) {
                exportButton.visibility = if (gameView.recording) View.VISIBLE else View.GONE
            }
        }
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

        // how David moves: on a steady beat, or also right when he turns
        beatButton = TextView(this)
        beatButton.textSize = 16f
        beatButton.setTextColor(ink)
        beatButton.gravity = Gravity.CENTER
        beatButton.setPadding(dp(18), dp(9), dp(18), dp(9))
        val bbg = GradientDrawable()
        bbg.cornerRadius = dp(12).toFloat()
        bbg.setColor(Color.rgb(240, 240, 236))
        bbg.setStroke(dp(1), inkSoft)
        beatButton.background = bbg
        beatButton.setOnClickListener {
            gameView.steadyBeat = !gameView.steadyBeat
            prefs.edit().putBoolean("steady_beat", gameView.steadyBeat).apply()
            showBeat()
        }
        showBeat()
        val blp = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        )
        blp.topMargin = dp(14)
        panel.addView(beatButton, blp)

        // debug mode: close the test file from the lose screen
        exportButton = TextView(this)
        exportButton.text = getString(R.string.export_games)
        exportButton.textSize = 17f
        exportButton.setTextColor(ink)
        exportButton.gravity = Gravity.CENTER
        exportButton.setPadding(dp(18), dp(10), dp(18), dp(10))
        val sbg = GradientDrawable()
        sbg.cornerRadius = dp(12).toFloat()
        sbg.setColor(Color.rgb(222, 234, 248))
        sbg.setStroke(dp(1), inkSoft)
        exportButton.background = sbg
        exportButton.setOnClickListener {
            exportButton.isEnabled = false
            subtitleView.text = panelText + "\n\n" + getString(R.string.exporting)
            gameView.exportGames { msg ->
                exportButton.isEnabled = true
                subtitleView.text = panelText + "\n\n" + msg
            }
        }
        val lp = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        )
        lp.topMargin = dp(12)
        panel.addView(exportButton, lp)

        root.addView(
            panel,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER
            )
        )
    }

    private fun showBeat() {
        beatButton.text = getString(if (gameView.steadyBeat) R.string.movement_beat else R.string.movement_turns)
    }

    // --------------------------------------------------------- phase handling

    private fun onPhase(phase: GameEngine.Phase) {
        when (phase) {
            GameEngine.Phase.PLAYING -> panel.visibility = View.GONE
            GameEngine.Phase.READY -> {
                panel.visibility = View.VISIBLE
                titleView.text = getString(R.string.app_name)
                subtitleView.text =
                    getString(R.string.swipe_hint) + "\n" + getString(R.string.tap_to_start)
                panelText = subtitleView.text.toString()
                exportButton.visibility = if (gameView.recording) View.VISIBLE else View.GONE
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
                panelText = sub
                subtitleView.text = sub
                exportButton.visibility = if (gameView.recording) View.VISIBLE else View.GONE
            }
        }
    }
}

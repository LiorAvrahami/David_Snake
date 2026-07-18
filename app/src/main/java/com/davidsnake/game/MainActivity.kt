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
 * the GameView underneath, and a centered overlay panel for the start,
 * pause and lose screens with difficulty and speed choices.
 */
class MainActivity : Activity() {

    private lateinit var gameView: GameView
    private lateinit var panel: LinearLayout
    private lateinit var titleView: TextView
    private lateinit var subtitleView: TextView
    private lateinit var settingsBlock: LinearLayout
    private val diffButtons = ArrayList<TextView>()
    private val speedButtons = ArrayList<TextView>()

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
        setContentView(root)

        gameView.engine.listener = { phase -> onPhase(phase) }
        onPhase(gameView.engine.phase)
    }

    override fun onPause() {
        super.onPause()
        gameView.engine.pauseIfPlaying()
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
        panel.setPadding(dp(30), dp(20), dp(30), dp(20))
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
        subtitleView.setPadding(0, dp(6), 0, 0)
        panel.addView(subtitleView)

        settingsBlock = LinearLayout(this)
        settingsBlock.orientation = LinearLayout.VERTICAL
        settingsBlock.gravity = Gravity.CENTER_HORIZONTAL
        settingsBlock.setPadding(0, dp(8), 0, 0)
        settingsBlock.addView(makeLabel(R.string.difficulty))
        settingsBlock.addView(
            makeChoiceRow(diffButtons, listOf(R.string.easy, R.string.medium, R.string.hard)) { i ->
                gameView.engine.difficulty = GameEngine.Difficulty.values()[i]
                refreshChoices()
            }
        )
        settingsBlock.addView(makeLabel(R.string.speed))
        settingsBlock.addView(
            makeChoiceRow(
                speedButtons,
                listOf(R.string.speed_original, R.string.speed_slower, R.string.speed_chill)
            ) { i ->
                gameView.engine.speed = GameEngine.Speed.values()[i]
                refreshChoices()
            }
        )
        panel.addView(settingsBlock)

        root.addView(
            panel,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER
            )
        )
    }

    private fun makeLabel(textRes: Int): TextView {
        val v = TextView(this)
        v.text = getString(textRes)
        v.textSize = 13f
        v.setTextColor(inkSoft)
        v.gravity = Gravity.CENTER
        v.setPadding(0, dp(10), 0, dp(4))
        return v
    }

    private fun makeChoiceRow(
        store: ArrayList<TextView>,
        labels: List<Int>,
        onPick: (Int) -> Unit
    ): LinearLayout {
        val row = LinearLayout(this)
        row.orientation = LinearLayout.HORIZONTAL
        row.gravity = Gravity.CENTER
        labels.forEachIndexed { idx, resId ->
            val b = TextView(this)
            b.text = getString(resId)
            b.textSize = 14f
            b.setTypeface(Typeface.DEFAULT_BOLD)
            b.gravity = Gravity.CENTER
            b.setPadding(dp(16), dp(8), dp(16), dp(8))
            b.setOnClickListener { onPick(idx) }
            val lp = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
            if (idx > 0) lp.marginStart = dp(8)
            row.addView(b, lp)
            store.add(b)
        }
        return row
    }

    private fun styleChoice(v: TextView, selected: Boolean) {
        val bg = GradientDrawable()
        bg.cornerRadius = dp(10).toFloat()
        if (selected) {
            bg.setColor(ink)
            v.setTextColor(Color.WHITE)
        } else {
            bg.setColor(Color.TRANSPARENT)
            bg.setStroke(dp(1), ink)
            v.setTextColor(ink)
        }
        v.background = bg
    }

    private fun refreshChoices() {
        for (i in diffButtons.indices) {
            styleChoice(diffButtons[i], gameView.engine.difficulty.ordinal == i)
        }
        for (i in speedButtons.indices) {
            styleChoice(speedButtons[i], gameView.engine.speed.ordinal == i)
        }
    }

    // --------------------------------------------------------- phase handling

    private fun onPhase(phase: GameEngine.Phase) {
        when (phase) {
            GameEngine.Phase.PLAYING -> panel.visibility = View.GONE
            GameEngine.Phase.READY -> {
                panel.visibility = View.VISIBLE
                settingsBlock.visibility = View.VISIBLE
                titleView.text = getString(R.string.app_name)
                subtitleView.text =
                    getString(R.string.swipe_hint) + "\n" + getString(R.string.tap_to_start)
                refreshChoices()
            }
            GameEngine.Phase.PAUSED -> {
                panel.visibility = View.VISIBLE
                settingsBlock.visibility = View.VISIBLE
                titleView.text = getString(R.string.paused)
                subtitleView.text = getString(R.string.resume_hint)
                refreshChoices()
            }
            GameEngine.Phase.LOST -> {
                val score = gameView.engine.score
                if (score > gameView.bestScore) {
                    gameView.bestScore = score
                    prefs.edit().putInt("best", score).apply()
                }
                panel.visibility = View.VISIBLE
                settingsBlock.visibility = View.GONE
                titleView.text = getString(R.string.you_lost)
                subtitleView.text =
                    getString(R.string.final_score, score, gameView.bestScore) +
                        "\n" + getString(R.string.try_again)
            }
        }
    }
}

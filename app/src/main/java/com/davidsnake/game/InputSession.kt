package com.davidsnake.game

import kotlin.math.hypot

/**
 * The input path between the finger and the engine, free of Android so it
 * runs unchanged in desktop checks: feeds one finger's strokes (dp, ms)
 * to the arm's recognizer, applies the commands, runs the engine's ticks,
 * and writes the test-file lines ([lab]) for a recorded game.
 */
class InputSession(
    val engine: GameEngine,
    private val lab: (String) -> Unit
) {

    companion object {
        /** A tap: the finger up [TAP_MIN_MS] to [TAP_MS] after landing, never
         *  [TAP_DP] from where it landed. A double tap, which marks the
         *  moment: a second tap landing [DOUBLE_MIN_MS] to [DOUBLE_MS] after
         *  the first one's lift and within [DOUBLE_DP] of where it landed.
         *  The minimums keep out a light drag whose contact flickers: that
         *  gives touches of 10-20 ms a few tens of ms apart, each a little
         *  further along (seen in a recording). */
        const val TAP_MIN_MS = 30L
        const val TAP_MS = 300L
        const val TAP_DP = 12f
        const val DOUBLE_MIN_MS = 50L
        const val DOUBLE_MS = 300L
        const val DOUBLE_DP = 50f
    }

    var arm: Arm = Arms.DEFAULT
        private set
    /** App version, logged with every recorded game. */
    var appVersion = "?"
    private var recognizer: Recognizer = arm.make()
    private val info = object : GameInfo {
        override val heading get() = engine.intendedDir
        override val hasTail get() = engine.tail.isNotEmpty()
        override fun room(dir: Int) = engine.room(dir)
    }

    /** Game being recorded (0 = none). */
    var playNo = 0
        private set
    private var playStartT = 0L
    private var playStrokes = 0
    private var playCmds = 0

    // the current stroke
    private var inStroke = false
    private var t0 = 0L
    private var strokePlay = 0
    private var live = false
    private var downX = 0f
    private var downY = 0f
    private var lastT = 0L
    private var lastX = 0f
    private var lastY = 0f
    /** Farthest the finger got from where it landed (dp). */
    var maxDist = 0f
        private set
    /** Commands the current stroke produced. */
    var strokeCmds = 0
        private set
    private val pts = StringBuilder()
    private val heads = StringBuilder()
    private val ticks = StringBuilder()
    private var ptT = 0L
    private var ptX = 0
    private var ptY = 0
    private var tk0 = 0
    private var ptTick = 0

    private var tickNo = 0                         // game ticks since the game started
    private val knownSpears = HashSet<GameEngine.Spear>()   // by identity

    // the last tap during a recorded game, waiting for a second one
    private var tapPlay = 0
    private var tapDownT = 0L
    private var tapUpT = 0L
    private var tapX = 0f
    private var tapY = 0f
    private var tapTk = 0
    /** The recorded game that just ended: a double tap on its lose screen
     *  still marks it (0: none). */
    private var endedPlayNo = 0
    /** Marks (double taps) in the current recorded game, lose screen included. */
    var marks = 0
        private set
    /** The stroke that just ended was the second tap of a mark. */
    var strokeMarked = false
        private set
    /** Called when a double tap marks the moment: its time and where the
     *  first tap landed (dp), for the screen's feedback. */
    var onMark: ((t: Long, x: Float, y: Float) -> Unit)? = null

    // ------------------------------------------------------------ games

    /** A game starts with [a]; [n] > 0 records it as test game n. */
    fun startPlay(a: Arm, n: Int, armN: Int, t: Long, wall: String) {
        arm = a
        engine.turnMode = a.mode
        recognizer = a.make()
        playNo = n
        tickNo = 0
        marks = 0
        tapPlay = 0
        endedPlayNo = 0
        knownSpears.clear()
        knownSpears.addAll(engine.spears)
        if (n > 0) {
            playStartT = t
            playStrokes = 0; playCmds = 0
            lab(LabLog.play(n, a, armN, t, wall, appVersion, LabLog.headStart(engine)))
            lab(LabLog.model(n, a, appVersion))
        }
    }

    /** The game was lost; returns true if it was a recorded one. */
    fun endPlay(t: Long): Boolean {
        if (playNo == 0) return false
        lab(LabLog.death(playNo, t, tickNo, engine))
        lab(LabLog.playEnd(playNo, arm, t, engine.score, t - playStartT,
            engine.lostReason, playStrokes, playCmds))
        endedPlayNo = playNo
        playNo = 0
        return true
    }

    fun abortPlay(t: Long, why: String) {
        if (playNo == 0) return
        lab(LabLog.abort(playNo, t, why))
        playNo = 0
        endedPlayNo = 0
    }

    /** One engine tick at time [t], logging what it moved. A recognizer
     *  that decides per tick does so first. */
    fun tick(t: Long) {
        if (engine.phase == GameEngine.Phase.PLAYING) {
            val cmds = recognizer.tick(info)
            if (cmds.isNotEmpty()) apply(cmds, recognizer.tickT)
            recognizer.ticked(info)
        }
        val hx = engine.headX
        val hy = engine.headY
        val sc = engine.score
        tickNo++               // first, so a death inside this tick carries it
        engine.tick()
        if (playNo > 0) {
            for (s in engine.spears) if (s !in knownSpears) lab(LabLog.spear(playNo, t, tickNo, s))
        }
        knownSpears.clear()
        knownSpears.addAll(engine.spears)
        logMoves(t, hx, hy, sc, false)
    }

    private fun logMoves(t: Long, hx: Int, hy: Int, sc: Int, flush: Boolean) {
        if (playNo == 0) return
        if (engine.headX != hx || engine.headY != hy) lab(LabLog.step(playNo, t, tickNo, engine, flush))
        if (engine.score != sc) lab(LabLog.harp(playNo, t, engine.score, engine))
    }

    // ---------------------------------------------------------- strokes

    fun down(t: Long, x: Float, y: Float) {
        inStroke = true
        t0 = t
        strokePlay = playNo
        live = engine.phase == GameEngine.Phase.PLAYING
        downX = x; downY = y
        lastT = t; lastX = x; lastY = y
        maxDist = 0f
        strokeCmds = 0
        pts.setLength(0); heads.setLength(0); ticks.setLength(0)
        ptT = t; ptX = 0; ptY = 0
        tk0 = tickNo; ptTick = tickNo
        point(t, x, y)
        recognizer.down(t, x, y)
    }

    fun move(tIn: Long, x: Float, y: Float) {
        if (!inStroke) return
        val t = maxOf(tIn, lastT)
        track(t, x, y)
        if (engine.phase == GameEngine.Phase.PLAYING) {
            live = true
            if (strokePlay == 0) strokePlay = playNo
        }
        apply(recognizer.move(t, x, y, info), t)
    }

    /** Ends the stroke: [how] is up (finger lifted at x, y), cancel, or
     *  steal (another finger took over). */
    fun end(tIn: Long, how: String, x: Float, y: Float) {
        if (!inStroke) return
        val t = maxOf(tIn, lastT)
        if (how == "up") {
            track(t, x, y)
            apply(recognizer.up(t, x, y, info), t)
        }
        inStroke = false
        if (strokePlay > 0 && live) {
            if (strokePlay == playNo) playStrokes++
            lab(LabLog.stroke(strokePlay, t0, how, pts.toString(), heads.toString(), tk0, ticks.toString()))
        }
        tap(t, how)
    }

    /** A double tap marks the moment, during a recorded game or on its
     *  lose screen (a "mark" line: the player points out something, usually
     *  the move just before, or the last one). During the game the taps
     *  themselves are strokes like any other, logged and read by the
     *  recognizer as usual. */
    private fun tap(t: Long, how: String) {
        strokeMarked = false
        val play = when {
            live && strokePlay > 0 && strokePlay == playNo -> playNo
            !live && strokePlay == 0 && engine.phase == GameEngine.Phase.LOST -> endedPlayNo
            else -> 0
        }
        val isTap = play > 0 && how == "up" && t - t0 in TAP_MIN_MS..TAP_MS && maxDist <= TAP_DP
        if (!isTap) {
            tapPlay = 0
            return
        }
        if (tapPlay == play && t0 - tapUpT in DOUBLE_MIN_MS..DOUBLE_MS &&
            hypot(downX - tapX, downY - tapY) <= DOUBLE_DP
        ) {
            marks++
            strokeMarked = true
            lab(LabLog.mark(play, tapDownT, tapTk, t0, tk0, tapX, tapY, afterDeath = playNo == 0))
            onMark?.invoke(t, tapX, tapY)
            tapPlay = 0
        } else {
            tapPlay = play
            tapDownT = t0; tapUpT = t
            tapX = downX; tapY = downY
            tapTk = tk0
        }
    }

    private fun track(t: Long, x: Float, y: Float) {
        lastT = t; lastX = x; lastY = y
        val d = hypot(x - downX, y - downY)
        if (d > maxDist) maxDist = d
        point(t, x, y)
    }

    /** Compact sample log: dt,x,y (ms, 0.1dp), the heading the recognizer
     *  judged it against, and the game ticks done since the last sample
     *  (a digit, or (n) from 10 on). */
    private fun point(t: Long, x: Float, y: Float) {
        val qx = Math.round(x * 10f)
        val qy = Math.round(y * 10f)
        if (pts.isNotEmpty()) pts.append(';')
        pts.append(t - ptT).append(',').append(qx).append(',').append(qy)
        heads.append(LabLog.dirLetter(engine.intendedDir))
        val d = tickNo - ptTick
        if (d < 10) ticks.append(d) else ticks.append('(').append(d).append(')')
        ptT = t; ptX = qx; ptY = qy; ptTick = tickNo
    }

    private fun apply(cmds: List<Cmd>, t: Long) {
        for ((i, c) in cmds.withIndex()) {
            if (engine.phase != GameEngine.Phase.PLAYING) return
            val hd = engine.headDir
            val ihd = engine.intendedDir
            val hx = engine.headX
            val hy = engine.headY
            val sc = engine.score
            val p = playNo
            val board = if (p > 0) LabLog.board(engine) else ""
            if (p > 0) playCmds++
            val hold = arm.holdUTurn && c.kind == "uturn" && i == 1
            val r = engine.onSwipe(c.dir, hold)   // may end the game (and the play)
            strokeCmds++
            if (p > 0) {
                lab(LabLog.cmd(p, t, tickNo, c, r.tag, hd, ihd, engine.headDir, hx, hy, board))
                if (playNo > 0) logMoves(t, hx, hy, sc, true)
            }
        }
    }
}

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

    // ------------------------------------------------------------ games

    /** A game starts with [a]; [n] > 0 records it as test game n. */
    fun startPlay(a: Arm, n: Int, armN: Int, t: Long, wall: String) {
        arm = a
        engine.turnMode = a.mode
        recognizer = a.make()
        playNo = n
        tickNo = 0
        knownSpears.clear()
        knownSpears.addAll(engine.spears)
        if (n > 0) {
            playStartT = t
            playStrokes = 0; playCmds = 0
            lab(LabLog.play(n, a, armN, t, wall, appVersion))
            lab(LabLog.model(n, a, appVersion))
        }
    }

    /** The game was lost; returns true if it was a recorded one. */
    fun endPlay(t: Long): Boolean {
        if (playNo == 0) return false
        lab(LabLog.death(playNo, t, tickNo, engine))
        lab(LabLog.playEnd(playNo, arm, t, engine.score, t - playStartT,
            engine.lostReason, playStrokes, playCmds))
        playNo = 0
        return true
    }

    fun abortPlay(t: Long, why: String) {
        if (playNo == 0) return
        lab(LabLog.abort(playNo, t, why))
        playNo = 0
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

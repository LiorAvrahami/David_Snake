package com.davidsnake.game

/**
 * One input-test arm: a recognizer plus the engine's turn execution.
 */
class Arm(
    val name: String,
    val mode: GameEngine.TurnMode,
    /** The U-turn's second turn waits for the first one's step. */
    val holdUTurn: Boolean = false,
    val make: () -> Recognizer
) {
    /** Everything needed to rebuild this input, as a JSON object. */
    fun describe(): String = Json().s("arm", name).s("turn_mode", mode.name)
        .raw("hold_uturn", holdUTurn.toString()).raw("recognizer", make().describe()).toString()
}

object Arms {
    val ORIGINAL = Arm("O-ORIGINAL", GameEngine.TurnMode.STEP) { OriginalRecognizer() }
    val PLUS = Arm("O-PLUS", GameEngine.TurnMode.STEP_SAFE) { OriginalRecognizer(plus = true) }
    val HOLD_UTURN = Arm("O-HOLD-UTURN", GameEngine.TurnMode.STEP_SAFE, holdUTurn = true) {
        OriginalRecognizer(plus = true)
    }
    val HOLD_ALL = Arm("O-HOLD-ALL", GameEngine.TurnMode.STEP_WAIT) { OriginalRecognizer(plus = true) }
    val PLUS28 = Arm("O-PLUS-28", GameEngine.TurnMode.STEP_SAFE, holdUTurn = true) {
        OriginalRecognizer(plus = true, threshold = 28f)
    }
    val SMART_STEP = Arm("S-STEP", GameEngine.TurnMode.STEP_SAFE) { SmartRecognizer() }
    val SMART2 = Arm("S2-STEP", GameEngine.TurnMode.STEP_SAFE, holdUTurn = true) {
        SmartRecognizer(v2 = true)
    }
    val SMART_SCHED = Arm("S-SCHED", GameEngine.TurnMode.SCHED) { SmartRecognizer() }
    val SMART2_FAST = Arm("S2-FAST", GameEngine.TurnMode.STEP_SAFE, holdUTurn = true) {
        SmartRecognizer(v2 = true, cooldownMs = 120L)
    }
    /** The trained turn model ([MlModel]; now MNW1 again). */
    val ML = Arm(MlModel.NAME, GameEngine.TurnMode.STEP_SAFE, holdUTurn = true) { LearnedRecognizer() }

    /** Normal play and debug recording. MNW1 (one hidden layer, no back
     *  answer: a U-turn is two side turns) is trained on the window score
     *  itself (tools/windowlearn.py, docs/window_training.md): every course
     *  of action in every window of the training games scored by the game
     *  simulation, the model's expected score climbed directly, no labels.
     *  On held-out games it beats MNL1, the model trained on the live game's
     *  turns, which beat ML-1. MNW2, its retraining under later scoring
     *  rules, played v1.19; v1.20 went back to MNW1 to start the data set
     *  with the player's marks (double taps). Earlier models: tools/models/. */
    val DEFAULT = ML

    /** Debug mode records with these arms in turn, [BLOCK] games each; a
     *  single arm means no A/B test, just data collection. */
    val CYCLE = listOf(DEFAULT)
    const val BLOCK = 3

    /** Arm of test game [i] (0-based). */
    fun armAt(i: Int): Arm = CYCLE[(i / BLOCK) % CYCLE.size]

    /** 1-based count of game [i] within its block. */
    fun armPlay(i: Int): Int = i % BLOCK + 1
}

/** Minimal JSON object writer (no dependencies, so it also runs on a
 *  desktop JVM for offline checks). */
class Json {
    private val sb = StringBuilder("{")
    private var first = true

    private fun key(k: String): StringBuilder {
        if (!first) sb.append(',')
        first = false
        return sb.append('"').append(k).append("\":")
    }

    fun s(k: String, v: String): Json {
        key(k).append('"')
        for (c in v) when (c) {
            '"' -> sb.append("\\\"")
            '\\' -> sb.append("\\\\")
            '\n' -> sb.append("\\n")
            else -> if (c < ' ') sb.append(' ') else sb.append(c)
        }
        sb.append('"')
        return this
    }

    fun n(k: String, v: Long): Json { key(k).append(v); return this }
    fun n(k: String, v: Int): Json { key(k).append(v); return this }
    fun f(k: String, v: Float): Json { key(k).append("%.3f".format(java.util.Locale.US, v)); return this }
    fun raw(k: String, json: String): Json { key(k).append(json); return this }

    override fun toString(): String = "$sb}"
}

/**
 * Line builders for the input-test file: one JSON object per line.
 * Times are Android uptime milliseconds (the clock of touch events);
 * positions on screen are tenths of a dp; board cells are column, row.
 */
object LabLog {
    fun dirLetter(d: Int) = when (d) {
        GameEngine.UP -> "U"
        GameEngine.RIGHT -> "R"
        GameEngine.DOWN -> "D"
        GameEngine.LEFT -> "L"
        else -> "-"
    }

    /** The file's own explanation of its fields, for whoever reads it. */
    val LEGEND = Json()
        .s("time", "t/t0 = Android uptime ms, one clock for touches and game events")
        .s("session", "device and screen; board = the 21x13 play area on screen in dp")
        .s("play", "a game starts: n = game number in the test, arm = input variant, arm_n = its play count; start (only when the game began with a head start): tail = the cells David's tail begins with, head-first, and ticks = how far the attackers' wave timer was run ahead; the score still counts from 0")
        .s("stroke", "one finger from down to up: pts = 'dt,x,y;...' dt ms since the previous sample (first since t0), x/y in 0.1dp; ih = per sample, the intended heading it was judged against; tk0 = game ticks done when the finger landed; tkd = per sample, game ticks done since the previous sample (a digit, or (n) from 10 on); end = up | cancel | steal (another finger took over)")
        .s("mark", "the player double-tapped to point out the moment, usually the move just before: t/tk = when the first tap landed and the game ticks done then; t2/tk2 = the same for the second tap; x,y = where the first tap landed, in dp; after_death = 1 when the taps came on the lose screen, after the game ended (the ticks keep counting there), pointing out the last move. A tap lifts within 300 ms and 12 dp of landing; the second lands within 300 ms of the first one's lift and 80 dp of it. During the game the two taps are also logged as strokes")
        .s("model", "the input this game was played with, complete enough to rebuild it: arm, turn mode, recognizer and all its settings; a trained model adds its weights, features and how it was trained (code commit, training files with sha256, settings); app = app version")
        .s("cmd", "recognizer output: kind (orig | swipe | chain | lift | ml | uturn | reverse), dir, engine result res, heading hd before and hd2 after, intended heading ihd, head cell; tk = game ticks done")
        .s("step", "David moved: tk = game tick (45 ms each, counted from the game's start), head cell, d = direction moved, hd = heading after (a queued turn applies right after a step); flush = moved by a second quick turn (STEP modes)")
        .s("spear", "a spear was thrown: at its cell at the end of tick tk; it moves one cell in d each later tick until it sticks in a wall")
        .s("harp", "a harp was eaten; next = where the new one appeared")
        .s("board", "harp cell; tail cells head-first; flying spears x,y,dir; attackers wall,pos,state (w = winding up, t = throwing, v = done)")
        .toString()

    fun session(
        version: String, device: String, sdk: Int, density: Float,
        screenWdp: Float, screenHdp: Float,
        boardLeftDp: Float, boardTopDp: Float, boardWdp: Float, boardHdp: Float,
        wall: String, t: Long
    ): String = Json()
        .s("k", "session").s("app", "david-snake").s("ver", version)
        .s("device", device).n("sdk", sdk).f("density", density)
        .raw("screen_dp", "[%.1f,%.1f]".format(java.util.Locale.US, screenWdp, screenHdp))
        .raw("board_dp", "[%.1f,%.1f,%.1f,%.1f]".format(java.util.Locale.US, boardLeftDp, boardTopDp, boardWdp, boardHdp))
        .n("tick_ms", GameEngine.TICK_MS).n("step_ticks", 4)
        .raw("cycle", Arms.CYCLE.joinToString(",", "[", "]") { "\"${it.name}\"" })
        .n("block", Arms.BLOCK)
        .s("wall", wall).n("t", t)
        .raw("legend", LEGEND)
        .toString()

    /** [start]: the head start as JSON ([headStart]), or empty for a normal game. */
    fun play(n: Int, arm: Arm, armN: Int, t: Long, wall: String, ver: String, start: String = ""): String {
        val j = Json().s("k", "play").n("n", n).s("arm", arm.name).s("mode", arm.mode.name)
            .n("arm_n", armN).n("t", t).s("wall", wall).s("app", ver)
        if (start.isNotEmpty()) j.raw("start", start)
        return j.toString()
    }

    /** The head start a game began with ([GameEngine.headStart]), or empty. */
    fun headStart(e: GameEngine): String {
        if (e.headStartTicks == 0 && e.tail.isEmpty()) return ""
        return Json().s("tail", e.tail.joinToString(";") { "${it.x},${it.y}" })
            .n("ticks", e.headStartTicks).toString()
    }

    /** The game's input in full ([Arm.describe]). */
    fun model(n: Int, arm: Arm, ver: String): String = Json()
        .s("k", "model").n("play", n).s("app", ver).raw("spec", arm.describe()).toString()

    fun playEnd(
        n: Int, arm: Arm, t: Long, score: Int, durMs: Long, reason: String,
        strokes: Int, cmds: Int
    ): String = Json()
        .s("k", "play_end").n("n", n).s("arm", arm.name).n("t", t).n("score", score)
        .n("dur_ms", durMs).s("reason", reason).n("strokes", strokes).n("cmds", cmds).toString()

    fun stroke(play: Int, t0: Long, end: String, pts: String, ih: String, tk0: Int, tkd: String): String = Json()
        .s("k", "stroke").n("play", play).n("t0", t0).s("end", end)
        .s("pts", pts).s("ih", ih).n("tk0", tk0).s("tkd", tkd).toString()

    /** A double tap: [t]/[tk] the first tap's landing and the game ticks
     *  done then, [t2]/[tk2] the second's, ([x], [y]) where the first landed
     *  (dp); [afterDeath] when it came on the lose screen. */
    fun mark(play: Int, t: Long, tk: Int, t2: Long, tk2: Int, x: Float, y: Float, afterDeath: Boolean = false): String {
        val j = Json().s("k", "mark").n("play", play).n("t", t).n("tk", tk).n("t2", t2).n("tk2", tk2)
            .f("x", x).f("y", y)
        if (afterDeath) j.n("after_death", 1)
        return j.toString()
    }

    /** [board] and the head cell are captured before the engine ran it. */
    fun cmd(
        play: Int, t: Long, tk: Int, c: Cmd, res: String, hdBefore: Int, intended: Int,
        hdAfter: Int, headX: Int, headY: Int, board: String
    ): String = Json()
        .s("k", "cmd").n("play", play).n("t", t).n("tk", tk).s("kind", c.kind).s("dir", dirLetter(c.dir))
        .s("res", res).s("hd", dirLetter(hdBefore)).s("ihd", dirLetter(intended))
        .s("hd2", dirLetter(hdAfter)).raw("head", "[$headX,$headY]")
        .raw("board", board).toString()

    fun abort(play: Int, t: Long, why: String): String = Json()
        .s("k", "play_abort").n("play", play).n("t", t).s("why", why).toString()

    fun step(play: Int, t: Long, tk: Int, e: GameEngine, flush: Boolean): String {
        val j = Json().s("k", "step").n("play", play).n("t", t).n("tk", tk)
            .raw("head", "[${e.headX},${e.headY}]").s("d", dirLetter(e.movedDir))
            .s("hd", dirLetter(e.headDir))
        if (flush) j.n("flush", 1)
        return j.toString()
    }

    fun harp(play: Int, t: Long, score: Int, e: GameEngine): String = Json()
        .s("k", "harp").n("play", play).n("t", t).n("score", score)
        .raw("next", "[${e.harpX},${e.harpY}]").toString()

    /** A spear was thrown: it sits at (x, y) at the end of game tick tk and
     *  moves one cell in d every later tick, until it sticks in a wall. */
    fun spear(play: Int, t: Long, tk: Int, s: GameEngine.Spear): String = Json()
        .s("k", "spear").n("play", play).n("t", t).n("tk", tk)
        .raw("at", "[${s.x},${s.y}]").s("d", dirLetter(s.dir)).toString()

    fun death(play: Int, t: Long, tk: Int, e: GameEngine): String = Json()
        .s("k", "death").n("play", play).n("t", t).n("tk", tk).s("reason", e.lostReason)
        .n("score", e.score).raw("head", "[${e.headX},${e.headY}]")
        .s("hd", dirLetter(e.headDir)).raw("board", board(e)).toString()

    fun board(e: GameEngine): String {
        val tail = e.tail.joinToString(";") { "${it.x},${it.y}" }
        val spears = e.spears.joinToString(";") { "${it.x},${it.y},${dirLetter(it.dir)}" }
        val att = e.attackers.joinToString(";") {
            "${dirLetter(it.wall)},${it.pos},${if (it.vanishing) "v" else if (it.throwing) "t" else "w"}"
        }
        return Json().raw("harp", "[${e.harpX},${e.harpY}]").s("tail", tail)
            .s("spears", spears).s("att", att).toString()
    }
}

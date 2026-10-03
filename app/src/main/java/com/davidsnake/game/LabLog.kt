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
)

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

    /** Normal play, outside a test: the original input (test 1's winner)
     *  with U-turns, whose second step waits for the first (O-PLUS made
     *  both steps at once: a diagonal jump). */
    val DEFAULT = HOLD_UTURN

    /** Test 2, three games each, one option after the other (the player's
     *  choice): the two ways to stop the diagonal jump (hold only the
     *  U-turn's second turn / hold any quick second turn), 28dp turns, and
     *  S2 (test 1's fast reader, fixed). */
    val ORDER = listOf(HOLD_UTURN, HOLD_ALL, PLUS28, SMART2).flatMap { a -> List(3) { a } }
    const val PLAYS_PER_ARM = 3

    /** 1-based count of [ORDER]'s entry [i] among the plays of its arm. */
    fun armPlay(i: Int): Int = ORDER.subList(0, i + 1).count { it === ORDER[i] }
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
        .s("play", "a game starts: n = game number in the test, arm = input variant, arm_n = its play count")
        .s("stroke", "one finger from down to up: pts = 'dt,x,y;...' dt ms since the previous sample (first since t0), x/y in 0.1dp; ih = per sample, the intended heading it was judged against; end = up | cancel | steal (another finger took over)")
        .s("cmd", "recognizer output: kind (orig | swipe | chain | lift | uturn | reverse), dir, engine result res, heading hd before and hd2 after, intended heading ihd, head cell")
        .s("step", "David moved: head cell, d = direction moved, hd = heading after (a queued turn applies right after a step); flush = moved by a second quick turn (STEP modes)")
        .s("flag", "player double-tapped: an input went wrong; type = fp (a turn not wanted) | fn (no turn when wanted) | wrong (turned, but another way) | none; turn_t/turn_dir/turn_from = the turn picked; want = the direction wanted. The game paused from t until the next resume line (menu, then a 3 s countdown)")
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
        .raw("order", Arms.ORDER.joinToString(",", "[", "]") { "\"${it.name}\"" })
        .s("wall", wall).n("t", t)
        .raw("legend", LEGEND)
        .toString()

    fun play(n: Int, arm: Arm, armN: Int, t: Long, wall: String): String = Json()
        .s("k", "play").n("n", n).s("arm", arm.name).s("mode", arm.mode.name)
        .n("arm_n", armN).n("t", t).s("wall", wall).toString()

    fun playEnd(
        n: Int, arm: Arm, t: Long, score: Int, durMs: Long, reason: String,
        strokes: Int, cmds: Int, flags: Int
    ): String = Json()
        .s("k", "play_end").n("n", n).s("arm", arm.name).n("t", t).n("score", score)
        .n("dur_ms", durMs).s("reason", reason).n("strokes", strokes).n("cmds", cmds)
        .n("flags", flags).toString()

    fun stroke(play: Int, t0: Long, end: String, pts: String, ih: String): String = Json()
        .s("k", "stroke").n("play", play).n("t0", t0).s("end", end)
        .s("pts", pts).s("ih", ih).toString()

    /** [board] and the head cell are captured before the engine ran it. */
    fun cmd(
        play: Int, t: Long, c: Cmd, res: String, hdBefore: Int, intended: Int,
        hdAfter: Int, headX: Int, headY: Int, board: String
    ): String = Json()
        .s("k", "cmd").n("play", play).n("t", t).s("kind", c.kind).s("dir", dirLetter(c.dir))
        .s("res", res).s("hd", dirLetter(hdBefore)).s("ihd", dirLetter(intended))
        .s("hd2", dirLetter(hdAfter)).raw("head", "[$headX,$headY]")
        .raw("board", board).toString()

    fun abort(play: Int, t: Long, why: String): String = Json()
        .s("k", "play_abort").n("play", play).n("t", t).s("why", why).toString()

    fun step(play: Int, t: Long, e: GameEngine, flush: Boolean): String {
        val j = Json().s("k", "step").n("play", play).n("t", t)
            .raw("head", "[${e.headX},${e.headY}]").s("d", dirLetter(e.movedDir))
            .s("hd", dirLetter(e.headDir))
        if (flush) j.n("flush", 1)
        return j.toString()
    }

    fun harp(play: Int, t: Long, score: Int): String = Json()
        .s("k", "harp").n("play", play).n("t", t).n("score", score).toString()

    fun death(play: Int, t: Long, e: GameEngine): String = Json()
        .s("k", "death").n("play", play).n("t", t).s("reason", e.lostReason)
        .n("score", e.score).raw("head", "[${e.headX},${e.headY}]")
        .s("hd", dirLetter(e.headDir)).raw("board", board(e)).toString()

    fun flag(
        play: Int, t: Long, phase: GameEngine.Phase, type: String,
        turn: InputSession.Turn?, want: Int
    ): String {
        val j = Json().s("k", "flag").n("play", play).n("t", t).s("phase", phase.name)
            .s("type", type)
        if (turn != null) j.n("turn_t", turn.t).s("turn_dir", dirLetter(turn.dir)).s("turn_from", dirLetter(turn.from))
        if (want >= 0) j.s("want", dirLetter(want))
        return j.toString()
    }

    fun resume(play: Int, t: Long, pausedMs: Long): String = Json()
        .s("k", "resume").n("play", play).n("t", t).n("paused_ms", pausedMs).toString()

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

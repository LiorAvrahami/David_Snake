package com.davidsnake.game

/**
 * One input-test arm: a recognizer plus the engine's turn execution.
 */
class Arm(val name: String, val mode: GameEngine.TurnMode, val make: () -> Recognizer)

object Arms {
    val ORIGINAL = Arm("O-ORIGINAL", GameEngine.TurnMode.STEP) { OriginalRecognizer() }
    val PLUS = Arm("O-PLUS", GameEngine.TurnMode.STEP_SAFE) { OriginalRecognizer(plus = true) }
    val SMART_STEP = Arm("S-STEP", GameEngine.TurnMode.STEP_SAFE) { SmartRecognizer() }
    val SMART_SCHED = Arm("S-SCHED", GameEngine.TurnMode.SCHED) { SmartRecognizer() }

    /** Normal play, outside a test. Chosen by the first test (v1.1.50):
     *  the original input won; its only flagged failures were blocked
     *  backward swipes, now U-turns. */
    val DEFAULT = PLUS

    /** The current test: three recorded games of the default input, to
     *  check it with real play. (The first test ran O-ORIGINAL, S-STEP
     *  and S-SCHED three games each, in a Latin square.) */
    val ORDER = listOf(PLUS, PLUS, PLUS)
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
        .s("flag", "player double-tapped: an input in the ~1.5 s before t went wrong")
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

    fun flag(play: Int, t: Long, phase: GameEngine.Phase): String = Json()
        .s("k", "flag").n("play", play).n("t", t).s("phase", phase.name).toString()

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

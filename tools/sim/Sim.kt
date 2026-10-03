// Headless test-drive of the engine. Run from the repo root with:
//   kotlinc app/src/main/java/com/davidsnake/game/{GameEngine,Recognizers}.kt tools/sim/Sim.kt -include-runtime -d sim.jar && java -jar sim.jar

package com.davidsnake.game

import kotlin.math.abs
import kotlin.random.Random

var checksRun = 0

fun check(cond: Boolean, msg: String) {
    checksRun++
    if (!cond) throw AssertionError(msg)
}

fun invariants(e: GameEngine) {
    if (e.phase != GameEngine.Phase.PLAYING) return
    var harps = 0; var heads = 0; var tails = 0
    for (x in 0 until GameEngine.COLS) for (y in 0 until GameEngine.ROWS) {
        when (e.blocks[x][y]) {
            GameEngine.HARP -> harps++
            GameEngine.HEAD -> heads++
            GameEngine.TAIL -> tails++
        }
    }
    check(heads == 1 && e.blocks[e.headX][e.headY] == GameEngine.HEAD, "head cell broken")
    check(harps == 1 && e.blocks[e.harpX][e.harpY] == GameEngine.HARP, "harp count=$harps")
    check(tails == e.tail.size, "tail cells=$tails list=${e.tail.size}")
    for (seg in e.tail) check(e.blocks[seg.x][seg.y] == GameEngine.TAIL, "segment unmarked")
    check(e.wallSpears.size <= GameEngine.MAX_WALL_SPEARS, "wallSpears=${e.wallSpears.size}")
    for (s in e.spears) check(
        s.x in 0 until GameEngine.COLS && s.y in 0 until GameEngine.ROWS, "spear OOB (${s.x},${s.y})"
    )
    check(e.score == e.tail.size, "score=${e.score} tail=${e.tail.size}")
}

fun ticksUntilLost(diff: GameEngine.Difficulty, seed: Int): Int {
    val e = GameEngine(Random(seed))
    e.difficulty = diff
    e.tapAction() // READY -> PLAYING, head runs straight up into the wall
    var t = 0
    while (e.phase != GameEngine.Phase.LOST) {
        e.tick(); t++
        check(t < 500, "no wall death after 500 ticks")
    }
    return t
}

fun main() {
    // 1) Wall grace window: easy gives 2 extra ticks over hard, medium 1.
    val te = ticksUntilLost(GameEngine.Difficulty.EASY, 7)
    val tm = ticksUntilLost(GameEngine.Difficulty.MEDIUM, 7)
    val th = ticksUntilLost(GameEngine.Difficulty.HARD, 7)
    println("wall-death ticks easy=$te medium=$tm hard=$th")
    check(te - th == 2 && tm - th == 1, "grace ladder wrong")

    // 2) Last-moment escape during the grace window.
    run {
        val e = GameEngine(Random(7))
        e.difficulty = GameEngine.Difficulty.EASY
        e.tapAction()
        repeat(te - 1) { e.tick(); invariants(e) }
        check(e.phase == GameEngine.Phase.PLAYING, "died too early")
        e.onSwipe(GameEngine.RIGHT)
        repeat(10) { e.tick(); invariants(e) }
        check(e.phase == GameEngine.Phase.PLAYING && e.headX > 10, "escape failed")
        println("grace-window escape OK (head now at ${e.headX},${e.headY})")
    }

    // 3) A swipe rotates instantly; movement waits for the metronome.
    run {
        val e = GameEngine(Random(1))
        e.tapAction()
        e.onSwipe(GameEngine.RIGHT)
        check(e.headDir == GameEngine.RIGHT, "rotation not instant")
        repeat(4) { e.tick() }
        check(e.headX == 10 && e.headY == 6, "moved before the metronome (${e.headX},${e.headY})")
        e.tick()  // t5: the scheduled step
        check(e.headX == 11 && e.headY == 6, "scheduled step missing (${e.headX},${e.headY})")
        println("instant rotation + metronome movement OK")
    }

    // 3b) Depth-two queue: the first swipe turns instantly, a further swipe
    //     is queued (last one wins the slot) and applies right after the step.
    run {
        val e = GameEngine(Random(1))
        e.tapAction()
        e.onSwipe(GameEngine.RIGHT)  // instant turn
        e.onSwipe(GameEngine.DOWN)   // queued...
        e.onSwipe(GameEngine.UP)     // ...and overwritten (last wins)
        check(e.headDir == GameEngine.RIGHT, "only the first swipe turns now (dir=${e.headDir})")
        repeat(5) { e.tick() }       // the scheduled step lands
        check(e.headX == 11 && e.headY == 6, "step went wrong (${e.headX},${e.headY})")
        check(e.headDir == GameEngine.UP, "queued turn not applied (dir=${e.headDir})")
        repeat(4) { e.tick() }       // next metronome step follows the queued turn
        check(e.headX == 11 && e.headY == 5, "queued step went wrong (${e.headX},${e.headY})")
        println("depth-two queue OK (instant turn, queued turn after the step)")
    }

    // 3b2) A rotation that would face the wall is disregarded outright.
    run {
        val e = GameEngine(Random(1))
        e.tapAction()
        var t = 0
        while (e.headY != 0 && t < 60) { e.tick(); t++ }
        check(e.phase == GameEngine.Phase.PLAYING, "died reaching the wall")
        e.onSwipe(GameEngine.LEFT)          // steer out of the wall press
        var t2 = 0
        val x0 = e.headX
        while (e.headX == x0 && t2 < 10) { e.tick(); t2++ }
        check(e.headDir == GameEngine.LEFT, "setup failed")
        val r = e.onSwipe(GameEngine.UP)    // faces the top wall: ignored
        check(r.tag == "wall-block" && e.headDir == GameEngine.LEFT,
            "wall turn not blocked (r=${r.tag} dir=${e.headDir})")
        repeat(12) { e.tick() }
        check(e.phase == GameEngine.Phase.PLAYING && e.headY == 0 && e.headX < x0,
            "cruise along the wall broken (${e.headX},${e.headY})")
        println("wall turn block OK")
    }

    // 3b3) HARD RULE: once the head has rotated, it cannot physically
    //      rotate again until David actually moves. No exceptions.
    run {
        val e = GameEngine(Random(13))
        e.tapAction()
        val script = Random(7)
        var ld = e.headDir
        var rotations = 0
        repeat(20000) {
            repeat(script.nextInt(4)) {
                e.onSwipe(script.nextInt(4))
                if (e.headDir != ld) {
                    rotations++
                    ld = e.headDir
                    check(rotations <= 1, "head rotated twice without moving (input)")
                }
            }
            val px = e.headX
            val py = e.headY
            val pd = e.headDir
            e.tick()
            if (e.headX != px || e.headY != py) {
                ld = e.headDir
                rotations = 0
            } else {
                check(e.headDir == pd, "head rotated inside a tick without moving")
            }
            if (e.phase == GameEngine.Phase.LOST) {
                e.tapAction(); e.tapAction()
                ld = e.headDir
                rotations = 0
            }
        }
        println("rotation invariant OK (one rotation per movement, no exceptions)")
    }

    // 3c) A rotation that would face ANY tail cell is disregarded --
    //     including the last bit: the step checks the landing cell before
    //     that bit vacates, so entering it is certain death (this was a
    //     live bug: the last bit used to be exempt). Exercised by a
    //     harp-seeking agent until both situations actually occur.
    run {
        val e = GameEngine(Random(21))
        e.tapAction()
        var blockedSeen = 0
        var lastBitSeen = 0
        var t = 0
        while (t < 300000 && (blockedSeen < 5 || lastBitSeen < 3)) {
            if (e.phase == GameEngine.Phase.LOST) { e.tapAction(); e.tapAction() }
            val px = e.headX
            val py = e.headY
            e.tick(); t++
            val moved = e.headX != px || e.headY != py
            if (!moved || e.phase != GameEngine.Phase.PLAYING) continue
            // fresh window: probe any turn that faces the tail
            if (e.tail.size >= 2) {
                for (d in 0..3) {
                    if (d == e.headDir) continue
                    if (d == (e.headDir + 2) % 4) continue
                    val nx = e.headX + when (d) { GameEngine.RIGHT -> 1; GameEngine.LEFT -> -1; else -> 0 }
                    val ny = e.headY + when (d) { GameEngine.DOWN -> 1; GameEngine.UP -> -1; else -> 0 }
                    val midTail = e.tail.dropLast(1).any { it.x == nx && it.y == ny }
                    val last = e.tail.last()
                    val isLast = last.x == nx && last.y == ny
                    if (midTail || isLast) {
                        val pd = e.headDir
                        val r = e.onSwipe(d)
                        check(r.tag == "tail-block" && e.headDir == pd,
                            (if (isLast) "last-bit" else "mid-tail") +
                                " turn not blocked (r=${r.tag})")
                        if (isLast) lastBitSeen++ else blockedSeen++
                        break
                    }
                }
            }
            // steer toward the harp along the larger delta axis
            val dx = e.harpX - e.headX
            val dy = e.harpY - e.headY
            val want = if (kotlin.math.abs(dx) >= kotlin.math.abs(dy)) {
                if (dx > 0) GameEngine.RIGHT else GameEngine.LEFT
            } else {
                if (dy > 0) GameEngine.DOWN else GameEngine.UP
            }
            if (want != e.headDir) e.onSwipe(want)
        }
        check(blockedSeen >= 5, "mid-tail block never exercised (t=$t)")
        check(lastBitSeen >= 3, "last-bit block never exercised (t=$t)")
        println("tail turn block OK (mid-tail blocked x$blockedSeen, last bit blocked x$lastBitSeen, $t ticks)")
    }

    // 4) Opening spear kills the head that lingers in row 6.
    run {
        val e = GameEngine(Random(3))
        e.tapAction()
        e.onSwipe(GameEngine.RIGHT)
        var t = 0
        while (e.phase != GameEngine.Phase.LOST && t < 200) { e.tick(); invariants(e); t++ }
        check(e.phase == GameEngine.Phase.LOST && e.headX < 20, "spear kill missing (t=$t x=${e.headX})")
        check(e.spears.any { it.x == e.headX && it.y == e.headY }, "killer spear not at corpse")
        println("spear kill OK at t=$t, corpse (${e.headX},${e.headY})")
        // corpse rain: keep ticking, attackers must keep coming; frozen spears pile up
        var spawns = 0
        var prev = e.attackers.size
        repeat(4000) {
            e.tick()
            if (e.attackers.size > prev) spawns++
            prev = e.attackers.size
        }
        val frozen = e.spears.count { it.x == e.headX && it.y == e.headY }
        check(spawns > 5, "no corpse rain (spawns=$spawns)")
        check(frozen >= 1, "no frozen spears in corpse")
        println("corpse rain OK: $spawns wave spawns, $frozen spear(s) frozen in the corpse")
        e.tapAction()
        check(e.phase == GameEngine.Phase.READY && e.score == 0 && e.attackers.size == 1, "reset broken")
    }

    // 5) Navigation: eat the first harp, then reversal must be blocked.
    run {
        val e = GameEngine(Random(11))
        e.tapAction()
        var t = 0
        while (e.headY != 5 && t < 50) { e.tick(); invariants(e); t++ }
        e.onSwipe(GameEngine.LEFT)
        while (e.score == 0 && t < 300) { e.tick(); invariants(e); t++ }
        check(e.score == 1 && e.tail.size == 1, "harp not eaten (t=$t)")
        check(e.harpX != 3 || e.harpY != 5, "harp did not respawn")
        val dx = e.harpX - e.headX; val dy = e.harpY - e.headY
        check(dx * dx + dy * dy > 16, "harp respawned too close")
        e.onSwipe(GameEngine.RIGHT) // reversal with a tail -> ignored
        check(e.headDir == GameEngine.LEFT, "reversal not blocked")
        e.onSwipe(GameEngine.DOWN)
        check(e.headDir == GameEngine.DOWN, "legal turn rejected")
        println("eat + reversal-block OK, harp respawned at (${e.harpX},${e.harpY})")
    }

    // 6) Tap during play is a no-op (pause was removed for mobile).
    run {
        val e = GameEngine(Random(5))
        e.tapAction()
        repeat(7) { e.tick() }
        val hy = e.headY
        e.tapAction()
        check(e.phase == GameEngine.Phase.PLAYING, "tap changed phase mid-game")
        repeat(2) { e.tick() } // counter was mid-cycle at 1 -> step on 2nd tick
        check(e.headY == hy - 1, "game did not continue")
        println("tap no-op OK")
    }

    // 7) Determinism: same seed + same script => identical outcome.
    run {
        fun play(seed: Int): String {
            val e = GameEngine(Random(seed))
            e.difficulty = GameEngine.Difficulty.HARD
            e.tapAction()
            val script = Random(99)
            repeat(3000) { i ->
                if (i % 7 == 0) e.onSwipe(script.nextInt(4))
                e.tick()
                if (e.phase == GameEngine.Phase.LOST && i % 100 == 0) { e.tapAction(); e.tapAction() }
            }
            return "${e.phase}/${e.score}/${e.headX},${e.headY}/${e.attackers.size}/${e.spears.size}"
        }
        val a = play(42); val b = play(42)
        check(a == b, "non-deterministic: $a vs $b")
        println("determinism OK ($a)")
    }

    // 8) Long soak with a greedy agent across all difficulties.
    var games = 0; var bestScore = 0; var spearOverTail = 0; var maxWallSpears = 0
    var maxAttackers = 0; var maxFlying = 0
    for (diff in GameEngine.Difficulty.values()) {
        val e = GameEngine(Random(1000 + diff.idx))
        e.difficulty = diff
        val agent = Random(2000 + diff.idx)
        e.tapAction()
        var lostTicks = 0
        repeat(200_000) {
            if (e.phase == GameEngine.Phase.PLAYING && agent.nextInt(4) == 0) {
                // greedy-ish: move toward the harp
                val dx = e.harpX - e.headX; val dy = e.harpY - e.headY
                val want = if (abs(dx) > abs(dy)) {
                    if (dx > 0) GameEngine.RIGHT else GameEngine.LEFT
                } else {
                    if (dy > 0) GameEngine.DOWN else GameEngine.UP
                }
                e.onSwipe(if (agent.nextInt(5) == 0) agent.nextInt(4) else want)
            }
            e.tick()
            invariants(e)
            if (e.phase == GameEngine.Phase.PLAYING) {
                for (s in e.spears) {
                    if (e.blocks[s.x][s.y] == GameEngine.TAIL) spearOverTail++
                }
                maxWallSpears = maxOf(maxWallSpears, e.wallSpears.size)
                maxAttackers = maxOf(maxAttackers, e.attackers.size)
                maxFlying = maxOf(maxFlying, e.spears.size)
                bestScore = maxOf(bestScore, e.score)
            } else if (e.phase == GameEngine.Phase.LOST) {
                lostTicks++
                if (lostTicks > 120) {
                    lostTicks = 0; games++
                    e.tapAction(); e.tapAction() // restart and go again
                }
            }
        }
    }
    check(games > 10, "too few games played: $games")
    check(bestScore >= 3, "agent never ate: best=$bestScore")
    check(spearOverTail > 0, "spears never passed over a tail")
    check(maxWallSpears == 6, "wall-spear cap never reached: $maxWallSpears")
    println("soak OK: $games deaths survived-and-restarted, best score=$bestScore, " +
            "spear-over-tail events=$spearOverTail, maxWallSpears=$maxWallSpears, " +
            "maxAttackers=$maxAttackers, maxFlyingSpears=$maxFlying")

    // 9) Late-game storm stress: the post-death rain uses the same wave ramp,
    //    so drive it 20k ticks on HARD. Rooms saturate (they never clear),
    //    forcing the trys>=30 fallback placement over and over.
    run {
        val e = GameEngine(Random(77))
        e.difficulty = GameEngine.Difficulty.HARD
        e.tapAction()
        while (e.phase != GameEngine.Phase.LOST) e.tick()
        var maxAlive = 0
        var maxWave = 0
        var prev = e.attackers.size
        var thrown = 0
        var prevSpears = e.spears.size + e.wallSpears.size
        repeat(20_000) {
            e.tick()
            maxAlive = maxOf(maxAlive, e.attackers.size)
            if (e.attackers.size > prev) maxWave = maxOf(maxWave, e.attackers.size - prev)
            prev = e.attackers.size
            val nowSpears = e.spears.size + e.wallSpears.size
            if (nowSpears > prevSpears) thrown += nowSpears - prevSpears
            prevSpears = nowSpears
            for (a in e.attackers) {
                val legal = if (a.wall == GameEngine.UP || a.wall == GameEngine.DOWN)
                    a.pos in 0 until GameEngine.COLS else a.pos in 0 until GameEngine.ROWS
                check(legal, "attacker parked out of range: wall=${a.wall} pos=${a.pos}")
            }
        }
        check(maxWave == 8, "wave size never reached 8 (max=$maxWave)")
        check(maxAlive >= 10, "storm too small (maxAlive=$maxAlive)")
        check(thrown > 2000, "too few spears thrown ($thrown)")
        println("storm stress OK: maxWave=$maxWave, maxAlive=$maxAlive, spearsThrown=$thrown")
    }

    // 10) STEP modes (the first commit's step-on-swipe): a turn arms a step
    //     that lands on the very next tick; a second turn before it flushes
    //     the armed step at once.
    run {
        val e = GameEngine(Random(1))
        e.turnMode = GameEngine.TurnMode.STEP
        e.tapAction()
        check(e.onSwipe(GameEngine.RIGHT).tag == "step" && e.headX == 10, "STEP moved before the tick")
        e.tick()
        check(e.headX == 11 && e.headY == 6, "STEP armed step missing (${e.headX},${e.headY})")
        e.onSwipe(GameEngine.UP)
        val r = e.onSwipe(GameEngine.LEFT)
        check(r.tag == "flush" && e.headX == 11 && e.headY == 5 && e.headDir == GameEngine.LEFT,
            "STEP flush wrong (r=${r.tag} at ${e.headX},${e.headY} dir=${e.headDir})")
        e.tick()
        check(e.headX == 10 && e.headY == 5, "STEP step after flush wrong (${e.headX},${e.headY})")
        check(e.onSwipe(GameEngine.LEFT).tag == "same", "STEP same-direction not ignored")
        println("STEP mode OK (armed step on the next tick, flush on a quick second turn)")
    }

    // 10b) At the top wall: STEP keeps the original's quirk (a quick second
    //      turn flushes the armed step into the wall); STEP_SAFE re-aims.
    for (mode in listOf(GameEngine.TurnMode.STEP, GameEngine.TurnMode.STEP_SAFE)) {
        val e = GameEngine(Random(1))
        e.turnMode = mode
        e.tapAction()
        while (e.headY > 0) e.tick()
        e.onSwipe(GameEngine.RIGHT); e.tick()     // along the wall to (11,0)
        check(e.headX == 11 && e.headY == 0 && e.phase == GameEngine.Phase.PLAYING, "wall setup")
        check(e.onSwipe(GameEngine.UP).tag == "step", "turn to face the wall not armed")
        val r = e.onSwipe(GameEngine.DOWN)
        if (mode == GameEngine.TurnMode.STEP) {
            check(r.tag == "flush-died" && e.phase == GameEngine.Phase.LOST, "STEP wall flush quirk lost (r=${r.tag})")
        } else {
            check(r.tag == "re-aim" && e.phase == GameEngine.Phase.PLAYING, "STEP_SAFE wall flush (r=${r.tag})")
            e.tick()
            check(e.headX == 11 && e.headY == 1, "STEP_SAFE re-aimed step wrong (${e.headX},${e.headY})")
        }
    }
    println("STEP wall flush: quirk kept in STEP, re-aim in STEP_SAFE OK")

    // 10c) Random soak of both STEP modes: invariants hold; STEP_SAFE input
    //      itself never kills and never turns straight into the tail.
    for (mode in listOf(GameEngine.TurnMode.STEP, GameEngine.TurnMode.STEP_SAFE, GameEngine.TurnMode.STEP_WAIT)) {
        val e = GameEngine(Random(5))
        e.turnMode = mode
        e.tapAction()
        val script = Random(11)
        var games = 0
        var turns = 0
        repeat(60000) {
            if (e.phase == GameEngine.Phase.LOST) { e.tapAction(); e.tapAction(); games++ }
            if (script.nextInt(3) == 0) {
                // steer toward the harp, with some random noise
                val dx = e.harpX - e.headX
                val dy = e.harpY - e.headY
                val d = if (script.nextInt(4) == 0) script.nextInt(4)
                    else if (abs(dx) >= abs(dy)) (if (dx > 0) GameEngine.RIGHT else GameEngine.LEFT)
                    else (if (dy > 0) GameEngine.DOWN else GameEngine.UP)
                val r = e.onSwipe(d)
                if (r.tag == "step" || r.tag == "flush" || r.tag == "re-aim" || r.tag == "rotate") turns++
                if (mode != GameEngine.TurnMode.STEP) {
                    check(e.phase == GameEngine.Phase.PLAYING, "STEP_SAFE input killed (r=${r.tag})")
                    if (r.tag == "step" || r.tag == "re-aim" || r.tag == "flush") {
                        check(e.room(e.headDir, 1) > 0 ||
                            e.headX + (if (e.headDir == GameEngine.RIGHT) 1 else if (e.headDir == GameEngine.LEFT) -1 else 0) !in 0 until GameEngine.COLS ||
                            e.headY + (if (e.headDir == GameEngine.DOWN) 1 else if (e.headDir == GameEngine.UP) -1 else 0) !in 0 until GameEngine.ROWS,
                            "STEP_SAFE turned straight into the tail")
                    }
                }
            }
            e.tick()
            invariants(e)
        }
        check(games > 5 && turns > 1000, "soak too small (games=$games turns=$turns)")
        println("$mode soak OK: $games games, $turns turns")
    }

    // 10d) Held turns: a second quick turn waits for the first turn's step
    //      (STEP_WAIT always, STEP_SAFE when asked), so both steps show.
    for (mode in listOf(GameEngine.TurnMode.STEP_WAIT, GameEngine.TurnMode.STEP_SAFE)) {
        val e = GameEngine(Random(1))
        e.turnMode = mode
        e.tapAction()
        check(e.onSwipe(GameEngine.RIGHT).tag == "step", "$mode first turn not a step")
        val r = e.onSwipe(GameEngine.UP, hold = mode == GameEngine.TurnMode.STEP_SAFE)
        check(r.tag == "queued" && e.headX == 10, "$mode second turn not held (r=${r.tag})")
        e.tick()
        check(e.headX == 11 && e.headY == 6 && e.headDir == GameEngine.UP, "$mode held turn wrong after its step")
        repeat(3) { e.tick() }
        check(e.headX == 11 && e.headY == 6, "$mode held turn moved early (${e.headX},${e.headY})")
        e.tick()
        check(e.headX == 11 && e.headY == 5, "$mode held turn's step missing (${e.headX},${e.headY})")
    }
    run {
        // STEP_WAIT: a turn right after a turn's step only turns the head
        val e = GameEngine(Random(1))
        e.turnMode = GameEngine.TurnMode.STEP_WAIT
        e.tapAction()
        e.onSwipe(GameEngine.RIGHT); e.tick()
        check(e.headX == 11, "setup")
        check(e.onSwipe(GameEngine.DOWN).tag == "rotate" && e.headX == 11 && e.headY == 6, "not a head turn")
        repeat(3) { e.tick() }
        check(e.headY == 6, "moved off the beat")
        e.tick()
        check(e.headX == 11 && e.headY == 7, "beat step missing (${e.headX},${e.headY})")
        check(e.onSwipe(GameEngine.LEFT).tag == "step", "after a beat step a turn should step again")
    }
    println("held turns OK (second quick turn waits for the first turn's step)")

    // 11) O-PLUS U-turn: with a tail, a straight backward drag turns to a
    //     side and back (two quick steps), instead of being blocked.
    run {
        val e = GameEngine(Random(3))
        e.turnMode = GameEngine.TurnMode.STEP_SAFE
        e.tapAction()
        var t = 0
        // grow a tail by steering toward the harp, then get into open space
        fun open(x: Int, y: Int) = e.blocks[x][y] != GameEngine.TAIL
        while (t < 200000 && (e.score < 3 || e.headX !in 6..14 || e.headY !in 4..8 ||
                e.headDir != GameEngine.UP || e.room(GameEngine.LEFT) < 2 || e.room(GameEngine.RIGHT) < 2 ||
                !open(e.headX - 1, e.headY + 1) || !open(e.headX + 1, e.headY + 1))) {
            if (e.phase == GameEngine.Phase.LOST) { e.tapAction(); e.tapAction() }
            val dx = e.harpX - e.headX
            val dy = e.harpY - e.headY
            val want = if (e.score >= 3) GameEngine.UP
                else if (abs(dx) >= abs(dy)) (if (dx > 0) GameEngine.RIGHT else GameEngine.LEFT)
                else (if (dy > 0) GameEngine.DOWN else GameEngine.UP)
            if (t % 4 == 0) e.onSwipe(want)
            e.tick(); t++
        }
        check(e.phase == GameEngine.Phase.PLAYING && e.score >= 3, "U-turn setup failed")
        val info = object : GameInfo {
            override val heading get() = e.intendedDir
            override val hasTail get() = e.tail.isNotEmpty()
            override fun room(dir: Int) = e.room(dir)
        }
        val r = OriginalRecognizer(plus = true)
        val x0 = e.headX
        val y0 = e.headY
        r.down(0L, 100f, 100f)
        val cmds = ArrayList<Cmd>()
        for (k in 1..10) cmds += r.move(k * 8L, 100f + k * 0.5f, 100f + k * 6f, info)  // straight down
        check(cmds.size == 2 && cmds.all { it.kind == "uturn" } && cmds[1].dir == GameEngine.DOWN,
            "backward drag did not make a U-turn ($cmds)")
        for (c in cmds) e.onSwipe(c.dir)
        e.tick()
        check(e.phase == GameEngine.Phase.PLAYING && e.headDir == GameEngine.DOWN &&
            abs(e.headX - x0) == 1 && e.headY == y0 + 1, "U-turn went wrong (${e.headX},${e.headY} dir=${e.headDir})")
        // a diagonal half-backward drag (45 degrees) stays a blocked reversal
        val r2 = OriginalRecognizer(plus = true)
        r2.down(0L, 100f, 100f)
        val c2 = ArrayList<Cmd>()
        for (k in 1..10) c2 += r2.move(k * 8L, 100f + k * 5f, 100f - k * 5.2f, info)  // up-right, heading DOWN
        check(c2.none { it.kind == "uturn" }, "diagonal drag made a U-turn ($c2)")
        println("O-PLUS U-turn OK (side step then back, alive; diagonal stays blocked)")

        // a short flick counts on lift; a tap and the original do not
        fun flick(plus: Boolean, dist: Float, ms: Long): List<Cmd> {
            val f = OriginalRecognizer(plus)
            f.down(0L, 200f, 200f)
            for (k in 1..3) f.move(k * ms / 4, 200f + dist * k / 4, 200f, info)
            return f.up(ms, 200f + dist, 200f + 1f, info)
        }
        check(flick(true, 20f, 80).let { it.size == 1 && it[0].kind == "flick" && it[0].dir == GameEngine.RIGHT },
            "flick not read on lift")
        check(flick(true, 6f, 80).isEmpty(), "tap read as a flick")
        check(flick(true, 20f, 400).isEmpty(), "slow short drag read as a flick")
        check(flick(false, 20f, 80).isEmpty(), "original read a flick")
        println("O-PLUS lift flicks OK")
    }

    println("ALL CHECKS PASSED ($checksRun assertions)")
}

// Headless test-drive of the engine. Run from the repo root with:
//   kotlinc app/src/main/java/com/davidsnake/game/GameEngine.kt tools/sim/Sim.kt -include-runtime -d sim.jar && java -jar sim.jar

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

    // 3b4) Cancellation: an effect is revocable until David moves BECAUSE
    //      of it. A step taken while it sat in the queue does not commit it.
    run {
        // a) cancel a queued turn: the step ignores it entirely
        val e = GameEngine(Random(1))
        e.tapAction()
        check(e.onSwipe(GameEngine.RIGHT).tag == "turn", "setup a")
        val qa = e.onSwipe(GameEngine.DOWN)
        check(qa.tag == "queued", "setup a2")
        check(e.cancelSwipe(qa.id) == "cancel-q", "queued cancel failed")
        repeat(5) { e.tick() }
        check(e.headX == 11 && e.headY == 6 && e.headDir == GameEngine.RIGHT,
            "canceled queue still applied (${e.headX},${e.headY} ${e.headDir})")
        repeat(4) { e.tick() }
        check(e.headX == 12 && e.headY == 6, "post-cancel path wrong")

        // b) cancel an uncommitted instant rotation: heading restored and
        //    the window's rotation refunded
        val f = GameEngine(Random(1))
        f.tapAction()
        val rb = f.onSwipe(GameEngine.RIGHT)
        check(rb.tag == "turn", "setup b")
        check(f.cancelSwipe(rb.id) == "cancel-rot", "rotation cancel failed")
        check(f.headDir == GameEngine.UP, "heading not restored")
        check(f.onSwipe(GameEngine.LEFT).tag == "turn", "rotation not refunded")
        repeat(5) { f.tick() }
        check(f.headX == 9 && f.headY == 6, "post-refund step wrong (${f.headX},${f.headY})")

        // c) the user's scenario: queued, a step happens (caused by the OLD
        //    heading), the turn is dequeued into a rotation -- and can still
        //    be canceled, because no movement was caused by it yet
        val g = GameEngine(Random(1))
        g.tapAction()
        check(g.onSwipe(GameEngine.RIGHT).tag == "turn", "setup c")
        val qc = g.onSwipe(GameEngine.DOWN)
        repeat(5) { g.tick() }
        check(g.headX == 11 && g.headY == 6 && g.headDir == GameEngine.DOWN,
            "dequeue setup wrong")
        check(g.cancelSwipe(qc.id) == "cancel-rot", "post-dequeue cancel failed")
        check(g.headDir == GameEngine.RIGHT, "heading not restored after dequeue")
        repeat(4) { g.tick() }
        check(g.headX == 12 && g.headY == 6 && g.phase == GameEngine.Phase.PLAYING,
            "post-cancel movement wrong (${g.headX},${g.headY})")

        // d) committed: once David steps in the effect's direction, cancel
        //    is stale and changes nothing
        val h = GameEngine(Random(1))
        h.tapAction()
        val rd = h.onSwipe(GameEngine.RIGHT)
        repeat(5) { h.tick() }
        check(h.cancelSwipe(rd.id) == "stale", "committed effect canceled")
        check(h.headDir == GameEngine.RIGHT, "committed heading changed")

        // e) overwritten: a newer queued intent invalidates the older id
        val k = GameEngine(Random(1))
        k.tapAction()
        check(k.onSwipe(GameEngine.RIGHT).tag == "turn", "setup e")
        val q1 = k.onSwipe(GameEngine.DOWN)
        val q2 = k.onSwipe(GameEngine.UP)
        check(q1.id != q2.id, "effect ids not unique")
        check(k.cancelSwipe(q1.id) == "stale", "stale queue id canceled")
        repeat(5) { k.tick() }
        check(k.headDir == GameEngine.UP, "surviving queue entry lost")
        println("cancellation OK (queue, rotation, post-dequeue, committed, overwritten)")
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

    println("ALL CHECKS PASSED ($checksRun assertions)")
}

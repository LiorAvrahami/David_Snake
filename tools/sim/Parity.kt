// Replays labeled trajectories through the Kotlin recognizers for
// tools/check_parity.py. Run from the repo root with:
//   python3 tools/check_parity.py dump cases.txt
//   kotlinc app/src/main/java/com/davidsnake/game/{GameEngine,Recognizers}.kt tools/sim/Parity.kt -include-runtime -d parity.jar
//   java -cp parity.jar com.davidsnake.game.ParityKt cases.txt kotlin.txt
//   python3 tools/check_parity.py diff kotlin.txt

package com.davidsnake.game

import java.io.File

/** Replays "gid heading dt,dx,dy;..." lines through the Kotlin recognizers;
 *  prints "gid O|S t:dir:kind ..." exactly like tools/check_parity.py. */
fun main(args: Array<String>) {
    val out = StringBuilder()
    for (line in File(args[0]).readLines()) {
        if (line.isBlank()) continue
        val (gid, h0, pts) = line.split(" ")
        for ((name, rec) in listOf("O" to OriginalRecognizer(), "P" to OriginalRecognizer(uTurns = true), "S" to SmartRecognizer())) {
            var heading = h0.toInt()
            val g = object : GameInfo {
                override val heading get() = heading
                override val hasTail get() = true
                override fun room(dir: Int) = 6
            }
            var t = 0L; var x = 0f; var y = 0f
            val samples = ArrayList<Triple<Long, Float, Float>>()
            samples.add(Triple(0L, 0f, 0f))
            for (p in pts.split(";")) {
                val (dt, dx, dy) = p.split(",")
                t += dt.toLong(); x += dx.toFloat(); y += dy.toFloat()
                samples.add(Triple(t, x, y))
            }
            val fired = ArrayList<String>()
            rec.down(0L, 0f, 0f)
            for (k in 1 until samples.size) {
                val (st, sx, sy) = samples[k]
                val cmds = if (k == samples.size - 1) rec.up(st, sx, sy, g) else rec.move(st, sx, sy, g)
                for (c in cmds) { fired.add("$st:${c.dir}:${c.kind}"); heading = c.dir }
            }
            out.append(gid).append(' ').append(name).append(' ').append(fired.joinToString(" ")).append('\n')
        }
    }
    File(args[1]).writeText(out.toString())
}

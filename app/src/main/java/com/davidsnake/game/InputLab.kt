package com.davidsnake.game

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * Debug-mode game recordings, kept in the app's private storage until the
 * player exports them.
 *
 * Every recorded game is its own file (games/game_NNNNNN.jsonl), appended
 * at each flush (game start and end, app pause), so games survive closing
 * the app and turning debug mode off. [export] joins all stored games into
 * one file in Downloads, reads it back to check it is complete, and only
 * then deletes the stored games.
 */
class InputLab(private val ctx: Context) {

    private val prefs = ctx.getSharedPreferences("input_lab", Context.MODE_PRIVATE)
    private val io = Executors.newSingleThreadExecutor()
    private val pending = StringBuilder()
    private val dir = File(ctx.filesDir, "games").apply { mkdirs() }
    private var current: File? = null          // file of the game being recorded

    /** Games stored and not yet exported. */
    @Volatile var storedGames = countGames()
        private set

    @Volatile var lastError: String? = null
        private set

    private fun gameFiles(): List<File> =
        (dir.listFiles { f -> f.name.startsWith("game_") && f.name.endsWith(".jsonl") } ?: emptyArray())
            .sortedBy { it.name }

    private fun countGames() = gameFiles().size

    /** Number for a new recorded game: unique across app runs and exports. */
    fun nextGameNo(): Int {
        val n = prefs.getInt(K_NEXT, 1)
        prefs.edit().putInt(K_NEXT, n + 1).apply()
        return n
    }

    /** Lines from now on belong to recorded game [n]. */
    fun beginGame(n: Int) {
        flush()                                  // the previous game's last lines
        io.execute {
            current = File(dir, "game_%06d.jsonl".format(Locale.US, n))
        }
    }

    fun line(s: String) {
        synchronized(pending) { pending.append(s).append('\n') }
    }

    fun flush() {
        val text = synchronized(pending) {
            val s = pending.toString(); pending.setLength(0); s
        }
        if (text.isEmpty()) return
        io.execute {
            val f = current ?: return@execute
            try {
                f.appendText(text)
                storedGames = countGames()
                lastError = null
            } catch (e: Exception) {
                lastError = "save failed: ${e.message}"
            }
        }
    }

    /**
     * Joins all stored games, after [header], into one new file in
     * Downloads; reads it back and checks it is exactly what was written
     * and holds every game; only then deletes the stored games. [done]
     * gets a message for the player (on the io thread).
     */
    fun export(header: String, version: String, done: (String) -> Unit) {
        flush()
        io.execute {
            val files = gameFiles()
            if (files.isEmpty()) {
                done("No games stored.")
                return@execute
            }
            val sb = StringBuilder(header).append('\n')
            var games = 0
            for (f in files) {
                val text = f.readText()
                if (text.contains("\"k\":\"play\"")) games++
                sb.append(text)
                if (text.isNotEmpty() && !text.endsWith("\n")) sb.append('\n')
            }
            val content = sb.toString()
            val stamp = SimpleDateFormat("yyyy-MM-dd_HHmmss", Locale.US).format(Date())
            val name = "DavidSnake_Games_v${version}_${stamp}_${games}games.txt"
            try {
                val where = write(name, content)
                val back = readBack(where.first)
                val playLines = Regex("\"k\":\"play\"").findAll(back).count()
                val ok = back == content && content.length > header.length + 1 && playLines == games
                if (!ok) {
                    done("Export check failed (${back.length} of ${content.length} bytes, " +
                        "$playLines of $games games). Nothing deleted.")
                    return@execute
                }
                for (f in files) f.delete()
                current = null
                storedGames = countGames()
                done("Exported $games games (${content.length / 1024} KB) to ${where.second}")
            } catch (e: Exception) {
                done("Export failed: ${e.message}. Nothing deleted.")
            }
        }
    }

    /** Write a new file; returns (uri, where the player finds it). */
    private fun write(name: String, content: String): Pair<Uri, String> {
        val bytes = content.toByteArray(Charsets.UTF_8)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                put(MediaStore.MediaColumns.MIME_TYPE, "text/plain")
                put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            }
            val uri = ctx.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: throw IllegalStateException("Downloads refused the file")
            ctx.contentResolver.openOutputStream(uri, "w")!!.use { it.write(bytes) }
            return Pair(uri, "Downloads/$name")
        }
        // Android 8-9: the app's own folder (no storage permission needed)
        val d = ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: ctx.filesDir
        val f = File(d, name)
        f.writeBytes(bytes)
        return Pair(Uri.fromFile(f), f.absolutePath)
    }

    private fun readBack(uri: Uri): String =
        if (uri.scheme == "file") File(uri.path!!).readText()
        else ctx.contentResolver.openInputStream(uri)!!.use { it.readBytes().toString(Charsets.UTF_8) }

    companion object {
        private const val K_NEXT = "next_game"
    }
}

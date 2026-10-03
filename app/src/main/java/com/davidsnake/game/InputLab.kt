package com.davidsnake.game

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.ParcelFileDescriptor
import android.provider.MediaStore
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * The input A/B test that runs in debug mode: which arm the next game
 * uses, how many plays are done, and the test file in Downloads.
 *
 * Lines are buffered in memory and appended to the file at every flush
 * (game start and end, each flag, app pause), so a crash loses at most
 * the game in progress. Progress survives app restarts; turning debug
 * mode on resumes the test (of the current plan), or starts a new one.
 */
class InputLab(private val ctx: Context) {

    private val prefs = ctx.getSharedPreferences("input_lab", Context.MODE_PRIVATE)
    private val io = Executors.newSingleThreadExecutor()
    private val pending = StringBuilder()

    /** A test of the current plan is going on. */
    val running: Boolean
        get() = prefs.getBoolean(K_ACTIVE, false) &&
            prefs.getString(K_PLAN, "") == PLAN   // not a test from an older plan
    val playsDone: Int get() = prefs.getInt(K_PLAYS, 0)
    val fileName: String get() = prefs.getString(K_NAME, "") ?: ""
    /** Where the user finds the file. */
    val fileLocation: String get() = prefs.getString(K_WHERE, "") ?: ""

    @Volatile var lastError: String? = null
        private set

    /** Arm of the next game to start. */
    fun nextArm(): Arm = if (running) Arms.armAt(playsDone) else Arms.DEFAULT

    /** Begin a fresh test file with [header] as its first line. */
    fun startNew(header: String, version: String) {
        val stamp = SimpleDateFormat("yyyy-MM-dd_HHmm", Locale.US).format(Date())
        val name = "DavidSnake_InputLab_v${version}_$stamp.txt"
        prefs.edit().clear()  // forget any older test
            .putBoolean(K_ACTIVE, true)
            .putString(K_PLAN, PLAN)
            .putString(K_NAME, name)
            .putString(K_HEADER, header)
            .putInt(K_PLAYS, 0)
            .apply()
        synchronized(pending) { pending.setLength(0) }
        io.execute { create(name, header) }
    }

    fun line(s: String) {
        synchronized(pending) { pending.append(s).append('\n') }
    }

    /** Count a completed game. The test never ends by itself. */
    fun completePlay() {
        prefs.edit().putInt(K_PLAYS, playsDone + 1).apply()
    }

    /** Close the current file (everything so far is written to it) and
     *  send later games to a new one; the test goes on. Returns where the
     *  closed file is. */
    fun saveAndRotate(header: String, version: String): String {
        val closed = fileLocation
        flush()
        val stamp = SimpleDateFormat("yyyy-MM-dd_HHmmss", Locale.US).format(Date())
        val name = "DavidSnake_InputLab_v${version}_$stamp.txt"
        prefs.edit().putString(K_NAME, name).putString(K_HEADER, header).apply()
        io.execute { create(name, header) }
        return closed
    }

    fun flush() {
        val text = synchronized(pending) {
            val s = pending.toString(); pending.setLength(0); s
        }
        if (text.isEmpty()) return
        io.execute { append(text) }
    }

    // ------------------------------------------------------------ file IO
    // (all on the single io thread, in order)

    private fun create(name: String, header: String) {
        try {
            val target: String
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                    put(MediaStore.MediaColumns.MIME_TYPE, "text/plain")
                    put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                }
                val uri = ctx.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: throw IllegalStateException("Downloads refused the file")
                target = uri.toString()
                prefs.edit().putString(K_WHERE, "Downloads/$name").apply()
            } else {
                // Android 8-9: app folder (no storage permission needed)
                val dir = ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: ctx.filesDir
                val f = File(dir, name)
                target = Uri.fromFile(f).toString()
                prefs.edit().putString(K_WHERE, f.absolutePath).apply()
            }
            prefs.edit().putString(K_URI, target).commit()
            write(target, header + "\n")
            lastError = null
        } catch (e: Exception) {
            lastError = "save failed: ${e.message}"
        }
    }

    private fun append(text: String) {
        val target = prefs.getString(K_URI, null) ?: return
        try {
            write(target, text)
            lastError = null
        } catch (e: Exception) {
            // file gone (deleted?): start a continuation file with the header
            try {
                val base = fileName.removeSuffix(".txt")
                val name = "${base}_cont.txt"
                prefs.edit().putString(K_NAME, name).apply()
                create(name, prefs.getString(K_HEADER, "") ?: "")
                val again = prefs.getString(K_URI, null) ?: throw e
                write(again, text)
                lastError = null
            } catch (e2: Exception) {
                lastError = "save failed: ${e2.message}"
            }
        }
    }

    /** Append [text] at the end of the file behind [target]. */
    private fun write(target: String, text: String) {
        val uri = Uri.parse(target)
        val bytes = text.toByteArray(Charsets.UTF_8)
        if (uri.scheme == "file") {
            FileOutputStream(File(uri.path!!), true).use { it.write(bytes) }
            return
        }
        val pfd = ctx.contentResolver.openFileDescriptor(uri, "rw")
            ?: throw IllegalStateException("cannot open $uri")
        ParcelFileDescriptor.AutoCloseOutputStream(pfd).use { out ->
            val ch = out.channel
            ch.position(ch.size())
            out.write(bytes)
        }
    }

    companion object {
        private const val K_ACTIVE = "lab_active"
        private const val K_PLAN = "lab_plan"
        private val PLAN = "cycle:" + Arms.CYCLE.joinToString(",") { it.name } + ":" + Arms.BLOCK
        private const val K_URI = "lab_uri"
        private const val K_NAME = "lab_name"
        private const val K_WHERE = "lab_where"
        private const val K_HEADER = "lab_header"
        private const val K_PLAYS = "lab_plays"
    }
}

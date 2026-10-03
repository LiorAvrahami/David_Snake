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
 * Each app run writes one file, holding everything recorded since the app
 * was opened. Lines are buffered in memory and appended to it at every
 * flush (game start and end, each flag, Save, app pause), so a crash loses
 * at most the game in progress. The test's game count survives app
 * restarts; turning debug mode on resumes it (if the plan is unchanged).
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

    /** Start a new test: the game count goes back to 0. */
    fun startNew() {
        prefs.edit().clear()  // forget any older test
            .putBoolean(K_ACTIVE, true)
            .putString(K_PLAN, PLAN)
            .putInt(K_PLAYS, 0)
            .apply()
    }

    /** This app run's file is open: one file per app run, so it holds
     *  everything recorded since the app was opened. */
    private var fileOpen = false

    /** Debug mode turned off or the app left the screen: write everything
     *  out and end the recording; the next one starts a new file and a new
     *  game count. */
    fun endRun() {
        flush()
        fileOpen = false
        prefs.edit().putBoolean(K_ACTIVE, false).apply()
    }

    /** Open this app run's file (once), with [header] as its first line. */
    fun ensureFile(header: String, version: String) {
        if (fileOpen) return
        fileOpen = true
        val stamp = SimpleDateFormat("yyyy-MM-dd_HHmmss", Locale.US).format(Date())
        val name = "DavidSnake_InputLab_v${version}_$stamp.txt"
        prefs.edit()
            .putString(K_NAME, name)
            .putString(K_HEADER, header)
            .putString(K_WHERE, "Downloads/$name")
            .apply()
        io.execute { create(name, header) }
    }

    fun line(s: String) {
        synchronized(pending) { pending.append(s).append('\n') }
    }

    /** Count a completed game. The test never ends by itself. */
    fun completePlay() {
        prefs.edit().putInt(K_PLAYS, playsDone + 1).apply()
    }

    /** Write out everything recorded so far; returns where the file is. */
    fun save(): String {
        flush()
        return fileLocation
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

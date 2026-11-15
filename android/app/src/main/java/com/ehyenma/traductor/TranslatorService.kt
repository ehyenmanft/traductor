package com.ehyenma.traductor

import android.annotation.SuppressLint
import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import android.widget.Toast
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Captura el audio (de otras apps o del micrófono), lo manda al servidor en PCM 16 kHz mono y pinta los
 * subtítulos que vuelven en el overlay. Mismo protocolo que el cliente de Windows (live_protocol.py).
 * En pausa se cierra la conexión: el servidor no usa Deepgram/Groq ni cuenta minutos.
 */
class TranslatorService : Service() {
    companion object {
        @Volatile var running = false
        const val ACTION_STOP = "stop"
        const val EXTRA_CODE = "code"
        const val EXTRA_DATA = "data"
        const val EXTRA_MIC = "mic"
        private const val CHANNEL = "traductor"
        private const val FRAME_BYTES = 3200             // 100 ms de PCM16 a 16 kHz
        private const val TAG = "Traductor"
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var prefs: Prefs
    private var overlay: Overlay? = null
    private var projection: MediaProjection? = null
    private var record: AudioRecord? = null
    @Volatile private var ws: WebSocket? = null
    @Volatile private var ready = false
    @Volatile private var paused = false
    @Volatile private var stopping = false
    @Volatile private var fatal = false
    private var backoff = 1000L
    private val reconnect = Runnable { connect() }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { stopSelf(); return START_NOT_STICKY }
        if (running) return START_NOT_STICKY
        running = true
        prefs = Prefs(this)
        val mic = intent?.getBooleanExtra(EXTRA_MIC, false) ?: false
        startInForeground(mic)
        try {
            record = if (mic) micRecord() else playbackRecord(intent!!)
        } catch (e: Exception) {
            Toast.makeText(this, "No pude capturar el audio: ${e.message}", Toast.LENGTH_LONG).show()
            stopSelf()
            return START_NOT_STICKY
        }
        overlay = Overlay(this, prefs, onOpen = ::abrirApp, onPause = ::togglePause, onStop = ::stopSelf).also { it.show() }
        record?.startRecording()
        thread(name = "audio") { audioLoop() }
        connect()
        return START_NOT_STICKY
    }

    // ---------- captura ----------

    private val format: AudioFormat = AudioFormat.Builder()
        .setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(16000)
        .setChannelMask(AudioFormat.CHANNEL_IN_MONO).build()

    private fun bufferSize() = maxOf(
        AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT),
        FRAME_BYTES * 4)

    @SuppressLint("MissingPermission")                    // la pantalla principal pide RECORD_AUDIO antes
    private fun micRecord() = AudioRecord.Builder()
        .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
        .setAudioFormat(format).setBufferSizeInBytes(bufferSize()).build()

    @SuppressLint("MissingPermission")
    private fun playbackRecord(intent: Intent): AudioRecord {
        val mpm = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        @Suppress("DEPRECATION")
        val data = intent.getParcelableExtra<Intent>(EXTRA_DATA) ?: error("sin permiso de captura")
        val p = mpm.getMediaProjection(intent.getIntExtra(EXTRA_CODE, Activity.RESULT_CANCELED), data)
            ?: error("Android no dio permiso de captura")
        p.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() { main.post { stopSelf() } }       // el usuario cortó la captura desde Android
        }, main)
        projection = p
        // Android no deja capturar llamadas de voz (Discord, WhatsApp…): solo media, juegos y desconocido
        val cfg = AudioPlaybackCaptureConfiguration.Builder(p)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()
        return AudioRecord.Builder().setAudioFormat(format).setBufferSizeInBytes(bufferSize())
            .setAudioPlaybackCaptureConfig(cfg).build()
    }

    private fun audioLoop() {
        val buf = ByteArray(FRAME_BYTES)
        var frames = 0; var sent = 0; var peak = 0
        while (!stopping) {
            val n = record?.read(buf, 0, buf.size) ?: -1
            if (n <= 0 || paused) { if (n <= 0) Thread.sleep(40); continue }   // en pausa se lee y se descarta
            for (i in 0 until n - 1 step 2) peak = maxOf(peak, kotlin.math.abs((buf[i].toInt() and 0xFF) or (buf[i + 1].toInt() shl 8)))
            val w = ws
            if (ready && w != null && w.queueSize() < 64_000) { w.send(buf.toByteString(0, n)); sent++ }   // red lenta: se descarta
            if (++frames % 30 == 0) {                      // cada ~3 s: ¿llega sonido o solo silencio?
                Log.i(TAG, "audio: nivel máx $peak/32767, enviados $sent de 30")
                if (peak < 30 && ready) ui { status("🔇 No llega sonido de las apps") }
                peak = 0; sent = 0
            }
        }
    }

    // ---------- red ----------

    private fun connect() {
        if (stopping || paused || fatal) return
        Log.i(TAG, "conectando")
        ui { status("🔌 Conectando…") }
        val req = Request.Builder().url(prefs.server).build()
        ws = Api.client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "hello").put("version", 1)
                    .put("user", prefs.user).put("password", prefs.password)
                    .put("target", prefs.target).put("tone", "gamer")
                    .put("lang", prefs.source.ifEmpty { null } ?: JSONObject.NULL)
                    .put("dub", false).put("dub_gate", false).toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                // la conexión cerrada al pausar aún puede recibir un aviso: no debe tocar el estado de la nueva
                if (webSocket !== ws) return
                val m = runCatching { JSONObject(text) }.getOrNull() ?: return
                if (m.optString("type") in setOf("ready", "error")) Log.i(TAG, "servidor: $text")
                when (m.optString("type")) {
                    "ready" -> { ready = true; backoff = 1000; ui { status("✅ Conectado") } }
                    "upsert" -> ui { upsert(m.optInt("uid"), m.optString("text")) }
                    "trans" -> ui { translate(m.optInt("uid"), m.optString("text")) }
                    "notice" -> m.optString("message").takeIf { !it.startsWith("dub:") }?.let { s -> ui { status(s) } }
                    "error" -> {
                        if (m.optBoolean("fatal")) fatal = true
                        ui { status("⛔ " + m.optString("message")) }
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) = Unit   // voz del doblaje: aún no

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = dropped(webSocket)

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = dropped(webSocket)
        })
    }

    private fun dropped(webSocket: WebSocket) {
        if (ws !== webSocket) return                       // una conexión vieja (pausa / reconexión)
        Log.i(TAG, "conexión cerrada (pausa=$paused, fatal=$fatal)")
        ready = false
        ws = null
        if (stopping || paused || fatal) return
        ui { status("⚠️ Sin conexión; reconectando…") }
        main.postDelayed(reconnect, backoff)
        backoff = minOf(backoff * 2, 15_000)
    }

    private fun togglePause() {
        paused = !paused
        Log.i(TAG, if (paused) "pausa" else "reanudar")
        overlay?.setPaused(paused)
        main.removeCallbacks(reconnect)                    // ni una reconexión pendiente de antes
        if (paused) {
            // la captura sigue abierta (se descarta lo leído): reiniciarla falla en algunos móviles
            ready = false
            val old = ws
            ws = null
            old?.close(1000, "pausa")
            overlay?.status("⏸ En pausa: sin envío ni gasto de APIs")
        } else {
            fatal = false                                  // reanudar es una orden del usuario: siempre reintenta
            backoff = 1000
            connect()
        }
    }

    private fun abrirApp() {
        startActivity(Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
    }

    private fun ui(block: Overlay.() -> Unit) = main.post { overlay?.block() }

    // ---------- servicio en primer plano ----------

    private fun startInForeground(mic: Boolean) {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Traducción en vivo", NotificationManager.IMPORTANCE_LOW))
        val stop = PendingIntent.getService(this, 1, Intent(this, TranslatorService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE)
        val open = PendingIntent.getActivity(this, 2, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Traductor en vivo")
            .setContentText(if (mic) "Traduciendo el micrófono" else "Traduciendo el audio del móvil")
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Detener", stop).build())
            .setOngoing(true)
            .build()
        val type = if (mic) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
        if (mic && Build.VERSION.SDK_INT < Build.VERSION_CODES.R) startForeground(1, n) else startForeground(1, n, type)
    }

    override fun onDestroy() {
        stopping = true
        running = false
        main.removeCallbacksAndMessages(null)
        ws?.close(1000, "fin")
        runCatching { record?.stop() }
        record?.release()
        projection?.stop()
        overlay?.hide()
        super.onDestroy()
    }
}

package com.ehyenma.traductor

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private val reqPerms = 1
    private val reqCapture = 2
    private val main = Handler(Looper.getMainLooper())
    private var starting = false                          // esperando a que el servicio arranque

    private lateinit var vLogin: View
    private lateinit var vPanel: View
    private lateinit var tEstado: TextView
    private lateinit var tSub: TextView
    private lateinit var tMensaje: TextView
    private lateinit var boton: ImageButton
    private lateinit var brillo: View
    private lateinit var aro: GradientDrawable

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.main)
        prefs = Prefs(this)
        vLogin = findViewById(R.id.login)
        vPanel = findViewById(R.id.panel)
        tEstado = findViewById(R.id.estado)
        tSub = findViewById(R.id.subestado)
        tMensaje = findViewById(R.id.mensaje)
        boton = findViewById(R.id.boton)
        brillo = findViewById(R.id.brillo)
        aro = boton.background.mutate() as GradientDrawable

        val usuario = findViewById<EditText>(R.id.usuario)
        val clave = findViewById<EditText>(R.id.clave).apply { typeface = Typeface.DEFAULT }   // sin la monoespaciada de Android
        val entrar = findViewById<Button>(R.id.entrar)
        val error = findViewById<TextView>(R.id.errorLogin)
        entrar.setOnClickListener {
            val u = usuario.text.toString().trim()
            val p = clave.text.toString()
            if (u.isEmpty() || p.isEmpty()) { error.text = "Escribe tu usuario y tu clave."; return@setOnClickListener }
            error.text = ""
            entrar.isEnabled = false
            entrar.text = "Entrando…"
            background(onError = { error.text = it; entrar.isEnabled = true; entrar.text = "Entrar" }) {
                val r = Api.login(prefs.server, u, p)
                prefs.user = u; prefs.password = p; prefs.admin = r.optBoolean("admin")
                runOnUiThread { clave.setText(""); entrar.isEnabled = true; entrar.text = "Entrar"; mostrar() }
            }
        }
        clave.setOnEditorActionListener { _, action, _ -> (action == EditorInfo.IME_ACTION_GO).also { if (it) entrar.performClick() } }

        boton.setOnClickListener { if (TranslatorService.running) detener() else iniciar() }
        findViewById<View>(R.id.usuariosBtn).setOnClickListener { startActivity(Intent(this, UsersActivity::class.java)) }
        findViewById<View>(R.id.salir).setOnClickListener {
            detener()
            prefs.logout()
            mostrar()
        }
        findViewById<View>(R.id.idiomaBtn).setOnClickListener { elegirIdioma() }
        findViewById<View>(R.id.fuenteBtn).setOnClickListener { elegirFuente() }
    }

    override fun onResume() {
        super.onResume()
        mostrar()
        main.post(refresco)
    }

    override fun onPause() {
        main.removeCallbacks(refresco)
        super.onPause()
    }

    // el panel flotante puede detener la traducción (✕): el estado se refleja aquí
    private val refresco = object : Runnable {
        override fun run() { pintarEstado(); main.postDelayed(this, 1000) }
    }

    private fun mostrar() {
        val dentro = prefs.user.isNotEmpty()
        vLogin.visibility = if (dentro) View.GONE else View.VISIBLE
        vPanel.visibility = if (dentro) View.VISIBLE else View.GONE
        if (!dentro) return
        findViewById<View>(R.id.usuariosBtn).visibility = if (prefs.admin) View.VISIBLE else View.GONE
        findViewById<TextView>(R.id.idioma).text = LANGS.first { it.first == prefs.target }.second + "  ▾"
        findViewById<TextView>(R.id.fuente).text = (if (prefs.mic) "Micrófono" else "Del móvil") + "  ▾"
        findViewById<TextView>(R.id.cuenta).text = "👤 ${prefs.user}" + if (prefs.admin) "  ·  administrador" else ""
        pintarEstado()
    }

    private fun pintarEstado() {
        if (prefs.user.isEmpty()) return
        val running = TranslatorService.running
        if (running) starting = false
        val (color, titulo, sub) = when {
            running -> Triple(FUCSIA, "Traduciendo", "Los subtítulos flotan sobre tus apps · toca para detener")
            starting -> Triple(AMBAR, "Iniciando…", "Concede los permisos que pide Android")
            else -> Triple(VIOLETA, "Listo", "Toca el botón para empezar a traducir")
        }
        tEstado.text = titulo
        tSub.text = sub
        aro.setStroke(dp(3), color)
        boton.setColorFilter(color)
        brillo.background = GradientDrawable().apply {
            gradientType = GradientDrawable.RADIAL_GRADIENT
            gradientRadius = dp(100).toFloat()
            colors = intArrayOf((color and 0x00FFFFFF) or 0x50000000, color and 0x00FFFFFF)
        }
    }

    private fun mensaje(texto: String?) {
        tMensaje.text = texto ?: ""
        tMensaje.visibility = if (texto.isNullOrEmpty()) View.GONE else View.VISIBLE
    }

    private fun elegirIdioma() {
        if (TranslatorService.running) return mensaje("Detén la traducción para cambiar el idioma.")
        val actual = LANGS.indexOfFirst { it.first == prefs.target }
        AlertDialog.Builder(this).setTitle("Traducir al")
            .setSingleChoiceItems(LANGS.map { it.second }.toTypedArray(), actual) { d, i ->
                prefs.target = LANGS[i].first; d.dismiss(); mostrar()
            }.show()
    }

    private fun elegirFuente() {
        if (TranslatorService.running) return mensaje("Detén la traducción para cambiar el audio.")
        val opciones = arrayOf("Del móvil — videos, juegos y apps", "Micrófono — lo que suena cerca o llamadas")
        AlertDialog.Builder(this).setTitle("¿Qué audio traducir?")
            .setSingleChoiceItems(opciones, if (prefs.mic) 1 else 0) { d, i ->
                prefs.mic = i == 1; d.dismiss(); mostrar()
            }.show()
    }

    // ---------- permisos y arranque ----------

    private fun detener() {
        if (TranslatorService.running)
            startService(Intent(this, TranslatorService::class.java).setAction(TranslatorService.ACTION_STOP))
        starting = false
        main.postDelayed({ pintarEstado() }, 300)
    }

    private fun iniciar() {
        mensaje(null)
        if (!Settings.canDrawOverlays(this)) {
            AlertDialog.Builder(this)
                .setTitle("Permiso para el panel flotante")
                .setMessage("Para mostrar los subtítulos encima de otras apps, activa \"Mostrar sobre otras apps\" " +
                    "para Traductor en vivo y vuelve aquí.")
                .setPositiveButton("Abrir ajustes") { _, _ ->
                    startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
                }
                .setNegativeButton("Cancelar", null).show()
            return
        }
        val needed = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) needed += Manifest.permission.POST_NOTIFICATIONS
        val missing = needed.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) return requestPermissions(missing.toTypedArray(), reqPerms)
        starting = true
        pintarEstado()
        if (prefs.mic) {
            startForegroundService(Intent(this, TranslatorService::class.java).putExtra(TranslatorService.EXTRA_MIC, true))
        } else {
            val mpm = getSystemService(MediaProjectionManager::class.java)
            val intent = if (Build.VERSION.SDK_INT >= 34)
                mpm.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())   // pantalla completa
            else mpm.createScreenCaptureIntent()
            @Suppress("DEPRECATION")
            startActivityForResult(intent, reqCapture)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (requestCode != reqPerms) return
        val audio = permissions.indexOf(Manifest.permission.RECORD_AUDIO)
        if (audio >= 0 && grantResults.getOrNull(audio) == PackageManager.PERMISSION_GRANTED) iniciar()
        else mensaje("Sin permiso de audio no puedo traducir.")
    }

    @Deprecated("API de Activity sin AndroidX")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != reqCapture) return
        if (resultCode != RESULT_OK || data == null) {
            starting = false
            pintarEstado()
            return mensaje("Sin permiso de grabación no puedo oír las apps.")
        }
        startForegroundService(Intent(this, TranslatorService::class.java)
            .putExtra(TranslatorService.EXTRA_CODE, resultCode)
            .putExtra(TranslatorService.EXTRA_DATA, data))
    }
}

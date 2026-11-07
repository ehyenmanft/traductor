package com.ehyenma.traductor

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

// ---------- piezas de interfaz compartidas (sin XML) ----------

fun Context.column(): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL
    val pad = (20 * resources.displayMetrics.density).toInt()
    setPadding(pad, pad, pad, pad)
}

fun Context.label(text: String, size: Float = 15f) = TextView(this).apply {
    this.text = text
    textSize = size
    setPadding(0, (8 * resources.displayMetrics.density).toInt(), 0, (4 * resources.displayMetrics.density).toInt())
}

fun Context.field(hint: String, password: Boolean = false, value: String = "") = EditText(this).apply {
    this.hint = hint
    setText(value)
    setSingleLine()
    inputType = if (password) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
    else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
}

fun Context.button(text: String, onClick: () -> Unit) = Button(this).apply {
    this.text = text
    isAllCaps = false
    setOnClickListener { onClick() }
}

fun Activity.toast(msg: String?) = runOnUiThread { Toast.makeText(this, msg ?: "Error", Toast.LENGTH_LONG).show() }

/** Ejecuta [work] fuera del hilo de la interfaz y muestra el error, si lo hay. */
fun Activity.background(work: () -> Unit) = thread {
    try { work() } catch (e: Exception) { toast(e.message) }
}

class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private val reqPerms = 1
    private val reqCapture = 2

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
    }

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun render() {
        val content = if (prefs.user.isEmpty()) loginView() else homeView()
        setContentView(ScrollView(this).apply { addView(content) })
    }

    private fun loginView(): View = column().apply {
        addView(label("🎙 Traductor en vivo", 24f))
        addView(label("Entra con el usuario y la clave que te dio el administrador.", 14f))
        val server = field("Servidor (wss://…)", value = prefs.server)
        val user = field("Usuario")
        val pass = field("Clave", password = true)
        addView(label("Servidor", 13f)); addView(server)
        addView(label("Usuario", 13f)); addView(user)
        addView(label("Clave", 13f)); addView(pass)
        val enter = button("Entrar") {}
        enter.setOnClickListener {
            val s = cleanServer(server.text.toString())
            val u = user.text.toString().trim()
            val p = pass.text.toString()
            if (!s.startsWith("wss://")) return@setOnClickListener toast("La dirección debe empezar por wss://")
            if (u.isEmpty() || p.isEmpty()) return@setOnClickListener toast("Escribe usuario y clave.")
            enter.isEnabled = false
            background {
                try {
                    val r = Api.login(s, u, p)
                    prefs.server = s; prefs.user = u; prefs.password = p; prefs.admin = r.optBoolean("admin")
                    runOnUiThread { render() }
                } finally {
                    runOnUiThread { enter.isEnabled = true }
                }
            }
        }
        addView(enter)
    }

    private fun homeView(): View = column().apply {
        addView(label("🎙 Traductor en vivo", 24f))
        addView(label("Conectado como ${prefs.user}" + if (prefs.admin) " (administrador)" else "", 14f))

        addView(label("Traducir al:", 13f))
        addView(Spinner(this@MainActivity).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, LANGS.map { it.second })
            setSelection(LANGS.indexOfFirst { it.first == prefs.target }.coerceAtLeast(0))
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) { prefs.target = LANGS[pos].first }
                override fun onNothingSelected(p: AdapterView<*>?) = Unit
            }
        })

        addView(label("¿Qué audio traducir?", 13f))
        addView(RadioGroup(this@MainActivity).apply {
            val apps = RadioButton(this@MainActivity).apply { id = View.generateViewId(); text = "Audio del móvil (videos, juegos)" }
            val mic = RadioButton(this@MainActivity).apply { id = View.generateViewId(); text = "Micrófono (lo que suena cerca)" }
            addView(apps); addView(mic)
            check(if (prefs.mic) mic.id else apps.id)
            setOnCheckedChangeListener { _, checked -> prefs.mic = checked == mic.id }
        })

        val running = TranslatorService.running
        addView(button(if (running) "⏹ Detener traducción" else "▶ Iniciar traducción") {
            if (TranslatorService.running) {
                startService(Intent(this@MainActivity, TranslatorService::class.java).setAction(TranslatorService.ACTION_STOP))
                window.decorView.postDelayed({ render() }, 400)
            } else start()
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        if (running) addView(label("Para cambiar el idioma o el audio, detén y vuelve a iniciar.", 12f))

        if (prefs.admin) addView(button("👥 Gestionar usuarios") {
            startActivity(Intent(this@MainActivity, UsersActivity::class.java))
        })
        addView(button("Cerrar sesión") {
            startService(Intent(this@MainActivity, TranslatorService::class.java).setAction(TranslatorService.ACTION_STOP))
            prefs.logout(); render()
        })
        addView(label(
            "• Al iniciar, Android pide permiso para grabar: elige \"Pantalla completa\" (solo se usa el audio).\n" +
            "• Android no deja capturar llamadas de voz (Discord, WhatsApp): para eso usa \"Micrófono\".\n" +
            "• El botón ⏸ del panel corta la conexión: en pausa no se gasta nada.", 12f))
    }

    // ---------- permisos y arranque ----------

    private fun start() {
        if (!Settings.canDrawOverlays(this)) {
            AlertDialog.Builder(this)
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
        if (prefs.mic) {
            startForegroundService(Intent(this, TranslatorService::class.java).putExtra(TranslatorService.EXTRA_MIC, true))
            window.decorView.postDelayed({ render() }, 400)
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
        if (audio >= 0 && grantResults.getOrNull(audio) == PackageManager.PERMISSION_GRANTED) start()
        else toast("Sin permiso de audio no puedo traducir.")
    }

    @Deprecated("API de Activity sin AndroidX")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != reqCapture) return
        if (resultCode != RESULT_OK || data == null) return toast("Sin permiso de captura no puedo oír las apps.")
        startForegroundService(Intent(this, TranslatorService::class.java)
            .putExtra(TranslatorService.EXTRA_CODE, resultCode)
            .putExtra(TranslatorService.EXTRA_DATA, data))
        window.decorView.postDelayed({ render() }, 400)
    }
}

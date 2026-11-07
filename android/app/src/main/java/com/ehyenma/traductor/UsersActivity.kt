package com.ehyenma.traductor

import android.app.Activity
import android.app.AlertDialog
import android.content.res.ColorStateList
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject

/** Panel del superusuario, como el de MiVPN: crear, cambiar clave y cupo, habilitar/deshabilitar y revocar. */
class UsersActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var lista: LinearLayout
    private lateinit var estado: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        estado = texto("Cargando…", 13f, TENUE).apply { gravity = Gravity.CENTER; setPadding(0, dp(8), 0, 0) }
        lista = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val cabecera = LinearLayout(this).apply {
            gravity = Gravity.CENTER_VERTICAL
            addView(texto("‹ Volver", 14f, ENLACE).apply {
                setPadding(0, dp(10), dp(12), dp(10)); setOnClickListener { finish() }
            })
            addView(texto("Usuarios", 16f, TEXTO, bold = true).apply { gravity = Gravity.CENTER },
                LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            addView(texto("Actualizar", 14f, ENLACE).apply {
                setPadding(dp(12), dp(10), 0, dp(10)); setOnClickListener { llamar("list") }
            })
        }
        val crear = Button(this).apply {
            text = "+  Crear usuario"
            isAllCaps = false
            textSize = 15f
            setTextColor(0xFFFFFFFF.toInt())
            setTypeface(null, android.graphics.Typeface.BOLD)
            setBackgroundResource(R.drawable.boton)
            stateListAnimator = null
            setOnClickListener { dialogoCrear() }
        }
        setContentView(ScrollView(this).apply {
            fitsSystemWindows = true
            addView(columna().apply {
                setPadding(dp(20), dp(12), dp(20), dp(20))
                addView(cabecera)
                addView(estado)
                addView(crear, LinearLayout.LayoutParams(MATCH_PARENT, dp(48)).apply { topMargin = dp(12) })
                addView(lista)
            })
        })
        llamar("list")
    }

    private fun llamar(action: String, extra: JSONObject = JSONObject(), despues: () -> Unit = {}) {
        estado.text = "Un momento…"
        estado.setTextColor(TENUE)
        background(onError = { estado.text = it; estado.setTextColor(ROJO) }) {
            val users = Api.admin(prefs, action, extra).getJSONArray("users")
            runOnUiThread { despues(); pintar(users) }
        }
    }

    private fun pintar(users: JSONArray) {
        lista.removeAllViews()
        val enLinea = (0 until users.length()).count { users.getJSONObject(it).optBoolean("online") }
        estado.text = "${users.length()} usuarios · $enLinea en línea ahora"
        estado.setTextColor(TENUE)
        for (i in 0 until users.length()) {
            val u = users.getJSONObject(i)
            val n = u.getString("name")
            val yo = n == prefs.user
            val activo = u.optBoolean("enabled", true)
            val cupo = u.optDouble("daily_minutes", 0.0)

            val tarjeta = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setBackgroundResource(R.drawable.tarjeta)
                setPadding(dp(16), dp(12), dp(16), dp(14))
            }
            lista.addView(tarjeta, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(10) })

            val fila = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
            tarjeta.addView(fila)
            fila.addView(texto(n + (if (u.optBoolean("admin")) "  · admin" else "") + (if (yo) "  (tú)" else ""), 16f, TEXTO, bold = true),
                LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            if (!yo) fila.addView(Switch(this).apply {
                isChecked = activo
                contentDescription = if (activo) "Deshabilitar $n" else "Habilitar $n"
                val tinte = ColorStateList(arrayOf(intArrayOf(android.R.attr.state_checked), intArrayOf()), intArrayOf(VIOLETA, TENUE))
                thumbTintList = tinte
                trackTintList = tinte
                setOnCheckedChangeListener { _, marcado -> llamar(if (marcado) "enable" else "disable", JSONObject().put("name", n)) }
            })

            val (estadoU, color) = when {
                !activo -> "Deshabilitado" to ROJO
                u.optBoolean("online") -> "Traduciendo ahora" to FUCSIA
                else -> "Sin conexión" to TENUE
            }
            val uso = "Hoy ${u.optDouble("used_today").toInt()} min" + if (cupo > 0) " de ${cupo.toInt()}" else " · sin cupo"
            tarjeta.addView(texto("$estadoU  ·  $uso" + if (u.optBoolean("has_password")) "" else "  ·  sin clave", 13f, color)
                .apply { setPadding(0, dp(4), 0, 0) })

            val acciones = LinearLayout(this).apply { setPadding(0, dp(6), 0, 0) }
            tarjeta.addView(acciones)
            acciones.addView(accion(if (u.optBoolean("has_password")) "Cambiar clave" else "Poner clave") { dialogoClave(n, yo) })
            acciones.addView(accion("Cupo") { dialogoCupo(n, cupo) },
                LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { leftMargin = dp(24) })
            if (!yo) acciones.addView(accion("Revocar", ROJO) { confirmarRevocar(n) },
                LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { leftMargin = dp(24) })
        }
    }

    private fun dialogo(titulo: String, mensaje: String?, vararg vistas: android.view.View, ok: String, alAceptar: () -> Unit) {
        AlertDialog.Builder(this).setTitle(titulo).apply { if (mensaje != null) setMessage(mensaje) }
            .setView(columna().apply { vistas.forEach { addView(it) } })
            .setPositiveButton(ok) { _, _ -> alAceptar() }
            .setNegativeButton("Cancelar", null).show()
    }

    private fun dialogoCrear() {
        val nombre = campo("Usuario (sin espacios ni tildes)")
        val clave = campo("Clave (mínimo 8 caracteres)", clave = true)
        val minutos = campo("Minutos por día (vacío = sin cupo)").apply { inputType = InputType.TYPE_CLASS_NUMBER }
        val admin = CheckBox(this).apply { text = "Administrador (puede gestionar usuarios)"; setTextColor(TEXTO) }
        dialogo("Crear usuario", null, nombre, clave, minutos, admin, ok = "Crear") {
            llamar("add", JSONObject().put("name", nombre.text.toString().trim()).put("new_password", clave.text.toString())
                .put("minutes", minutos.text.toString().toDoubleOrNull() ?: 0.0).put("admin", admin.isChecked))
        }
    }

    private fun dialogoClave(n: String, yo: Boolean) {
        val clave = campo("Clave nueva (mínimo 8 caracteres)", clave = true)
        dialogo("Clave de $n", if (yo) "Es tu propia clave: la app la recordará." else "Si está conectado, se le cerrará la sesión.",
            clave, ok = "Guardar") {
            val nueva = clave.text.toString()
            llamar("passwd", JSONObject().put("name", n).put("new_password", nueva)) { if (yo) prefs.password = nueva }
        }
    }

    private fun dialogoCupo(n: String, actual: Double) {
        val minutos = campo("Minutos por día (0 = sin cupo)", valor = if (actual > 0) actual.toInt().toString() else "")
            .apply { inputType = InputType.TYPE_CLASS_NUMBER }
        dialogo("Cupo diario de $n", "Cuando lo agota, no puede traducir hasta el día siguiente.", minutos, ok = "Guardar") {
            llamar("minutes", JSONObject().put("name", n).put("minutes", minutos.text.toString().toDoubleOrNull() ?: 0.0))
        }
    }

    private fun confirmarRevocar(n: String) {
        AlertDialog.Builder(this).setTitle("¿Revocar a $n?")
            .setMessage("Se borra su usuario y se le cierra la sesión. No se puede deshacer.")
            .setPositiveButton("Revocar") { _, _ -> llamar("delete", JSONObject().put("name", n)) }
            .setNegativeButton("Cancelar", null).show()
    }
}

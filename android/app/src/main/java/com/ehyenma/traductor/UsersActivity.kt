package com.ehyenma.traductor

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.os.Bundle
import android.text.InputType
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ScrollView
import org.json.JSONArray
import org.json.JSONObject

/** Panel del superusuario: crear, cambiar clave y cupo, habilitar/deshabilitar y borrar usuarios. */
class UsersActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var list: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        list = column()
        setContentView(ScrollView(this).apply {
            addView(column().apply {
                addView(label("👥 Usuarios", 22f))
                addView(LinearLayout(this@UsersActivity).apply {
                    addView(button("+ Nuevo usuario") { addDialog() })
                    addView(button("↻ Actualizar") { call("list") })
                })
                addView(list)
            })
        })
        call("list")
    }

    private fun call(action: String, extra: JSONObject = JSONObject()) = background {
        val users = Api.admin(prefs, action, extra).getJSONArray("users")
        runOnUiThread { render(users) }
    }

    private fun render(users: JSONArray) {
        list.removeAllViews()
        for (i in 0 until users.length()) {
            val u = users.getJSONObject(i)
            val name = u.getString("name")
            val enabled = u.optBoolean("enabled", true)
            val quota = u.optDouble("daily_minutes", 0.0)
            val info = listOfNotNull(
                if (u.optBoolean("admin")) "admin" else null,
                if (u.optBoolean("online")) "🟢 en línea" else null,
                if (!enabled) "⛔ deshabilitado" else null,
                "hoy ${u.optDouble("used_today").toInt()}" + if (quota > 0) "/${quota.toInt()} min" else " min (sin cupo)",
                if (!u.optBoolean("has_password")) "sin clave (solo token antiguo)" else null,
            ).joinToString(" · ")
            list.addView(column().apply {
                setPadding(0, (12 * resources.displayMetrics.density).toInt(), 0, 0)
                addView(label(name, 18f).apply { if (!enabled) setTextColor(Color.GRAY) })
                addView(label(info, 12f))
                addView(LinearLayout(this@UsersActivity).apply {
                    if (name != prefs.user) addView(button(if (enabled) "Deshabilitar" else "Habilitar") {
                        call(if (enabled) "disable" else "enable", JSONObject().put("name", name))
                    })
                    addView(button("Clave") { passwordDialog(name) })
                    addView(button("Cupo") { minutesDialog(name, quota) })
                    if (name != prefs.user) addView(button("Borrar") { deleteDialog(name) })
                })
            })
        }
    }

    private fun addDialog() {
        val name = field("Usuario (sin espacios)")
        val pass = field("Clave (mínimo 8)", password = true)
        val minutes = field("Minutos por día (vacío = sin cupo)").apply { inputType = InputType.TYPE_CLASS_NUMBER }
        val admin = CheckBox(this).apply { text = "Administrador (puede gestionar usuarios)" }
        AlertDialog.Builder(this).setTitle("Nuevo usuario")
            .setView(column().apply { addView(name); addView(pass); addView(minutes); addView(admin) })
            .setPositiveButton("Crear") { _, _ ->
                call("add", JSONObject().put("name", name.text.toString().trim())
                    .put("new_password", pass.text.toString())
                    .put("minutes", minutes.text.toString().toDoubleOrNull() ?: 0.0)
                    .put("admin", admin.isChecked))
            }
            .setNegativeButton("Cancelar", null).show()
    }

    private fun passwordDialog(name: String) {
        val pass = field("Clave nueva (mínimo 8)", password = true)
        AlertDialog.Builder(this).setTitle("Cambiar la clave de $name")
            .setMessage(if (name == prefs.user) "Es tu propia clave: después tendrás que volver a entrar." else
                "Si está conectado, se le cerrará la sesión.")
            .setView(column().apply { addView(pass) })
            .setPositiveButton("Cambiar") { _, _ ->
                val newPw = pass.text.toString()
                background {
                    val users = Api.admin(prefs, "passwd", JSONObject().put("name", name).put("new_password", newPw))
                        .getJSONArray("users")
                    if (name == prefs.user) prefs.password = newPw      // seguir entrando con la clave nueva
                    runOnUiThread { render(users) }
                }
            }
            .setNegativeButton("Cancelar", null).show()
    }

    private fun minutesDialog(name: String, current: Double) {
        val minutes = field("Minutos por día (0 = sin cupo)", value = if (current > 0) current.toInt().toString() else "")
            .apply { inputType = InputType.TYPE_CLASS_NUMBER }
        AlertDialog.Builder(this).setTitle("Cupo diario de $name")
            .setView(column().apply { addView(minutes) })
            .setPositiveButton("Guardar") { _, _ ->
                call("minutes", JSONObject().put("name", name).put("minutes", minutes.text.toString().toDoubleOrNull() ?: 0.0))
            }
            .setNegativeButton("Cancelar", null).show()
    }

    private fun deleteDialog(name: String) {
        AlertDialog.Builder(this).setTitle("¿Borrar a $name?")
            .setMessage("Se borra su usuario para siempre y se le cierra la sesión. No se puede deshacer.")
            .setPositiveButton("Borrar") { _, _ -> call("delete", JSONObject().put("name", name)) }
            .setNegativeButton("Cancelar", null).show()
    }
}

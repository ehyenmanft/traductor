package com.ehyenma.traductor

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.text.InputType
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

const val VIOLETA = 0xFFA78BFA.toInt()
const val FUCSIA = 0xFFF472B6.toInt()
const val ENLACE = 0xFFC4B5FD.toInt()
const val TEXTO = 0xFFEDE9FE.toInt()
const val TENUE = 0xFF9A93B8.toInt()
const val ROJO = 0xFFFF5C7A.toInt()
const val AMBAR = 0xFFF5B942.toInt()

fun Context.dp(v: Int) = (v * resources.displayMetrics.density).toInt()

fun Context.texto(s: String, sp: Float, color: Int = TEXTO, bold: Boolean = false) = TextView(this).apply {
    text = s
    textSize = sp
    setTextColor(color)
    if (bold) setTypeface(null, Typeface.BOLD)
}

/** Acción de texto como en MiVPN ("Cambiar clave", "Revocar"). */
fun Context.accion(s: String, color: Int = ENLACE, onClick: () -> Unit) = texto(s, 14f, color, bold = true).apply {
    setPadding(0, dp(8), 0, dp(4))
    setOnClickListener { onClick() }
}

fun Context.columna(padding: Int = 20) = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL
    setPadding(dp(padding), dp(8), dp(padding), 0)
}

fun Context.campo(pista: String, clave: Boolean = false, valor: String = "") = EditText(this).apply {
    hint = pista
    setText(valor)
    setSingleLine()
    setTextColor(TEXTO)
    setHintTextColor(Color.argb(140, 154, 147, 184))
    inputType = if (clave) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
    else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
    typeface = Typeface.DEFAULT                           // Android pone monoespaciada en las claves
}

fun Activity.toast(msg: String?) = runOnUiThread { Toast.makeText(this, msg ?: "Error", Toast.LENGTH_LONG).show() }

/** Ejecuta [work] fuera del hilo de la interfaz y avisa del error, si lo hay. */
fun Activity.background(onError: (String) -> Unit = { toast(it) }, work: () -> Unit) = thread {
    try { work() } catch (e: Exception) { runOnUiThread { onError(e.message ?: "Error") } }
}

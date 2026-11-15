package com.ehyenma.traductor

import android.content.Context

const val DEFAULT_SERVER = "wss://3-147-129-93.sslip.io"

val LANGS = listOf(
    "es" to "Español", "en" to "English", "pt" to "Português", "fr" to "Français",
    "de" to "Deutsch", "it" to "Italiano", "ja" to "日本語", "ko" to "한국어",
    "zh-cn" to "中文", "ru" to "Русский",
)

/** Idioma que se habla. "" = detectar (Deepgram multilingüe). Fijarlo afina la transcripción.
 *  Solo códigos que Deepgram nova-3 acepta tal cual; chino y coreano van por la detección. */
val SOURCES = listOf(
    "" to "Detectar automáticamente", "en" to "English", "es" to "Español", "pt" to "Português",
    "fr" to "Français", "de" to "Deutsch", "it" to "Italiano", "ru" to "Русский", "ja" to "日本語",
)

/** Sesión y ajustes. shortcut: la clave queda en las preferencias privadas de la app (solo esta app
 *  las lee); pasar a EncryptedSharedPreferences si se publica en Play Store. */
class Prefs(ctx: Context) {
    private val sp = ctx.getSharedPreferences("traductor", Context.MODE_PRIVATE)

    var server: String
        get() = sp.getString("server", DEFAULT_SERVER) ?: DEFAULT_SERVER
        set(v) = sp.edit().putString("server", v).apply()
    var user: String
        get() = sp.getString("user", "") ?: ""
        set(v) = sp.edit().putString("user", v).apply()
    var password: String
        get() = sp.getString("password", "") ?: ""
        set(v) = sp.edit().putString("password", v).apply()
    var admin: Boolean
        get() = sp.getBoolean("admin", false)
        set(v) = sp.edit().putBoolean("admin", v).apply()
    var target: String
        get() = sp.getString("target", "es") ?: "es"
        set(v) = sp.edit().putString("target", v).apply()
    var source: String
        get() = sp.getString("source", "") ?: ""
        set(v) = sp.edit().putString("source", v).apply()
    // estilo del panel flotante
    var opacity: Int                                       // 0-255 del fondo (el texto siempre opaco)
        get() = sp.getInt("opacity", 215)
        set(v) = sp.edit().putInt("opacity", v).apply()
    var fontSize: Int                                      // sp de la traducción
        get() = sp.getInt("fontSize", 17)
        set(v) = sp.edit().putInt("fontSize", v).apply()
    var colorIndex: Int
        get() = sp.getInt("color", 0)
        set(v) = sp.edit().putInt("color", v).apply()
    var showOriginal: Boolean
        get() = sp.getBoolean("showOriginal", true)
        set(v) = sp.edit().putBoolean("showOriginal", v).apply()
    var mic: Boolean
        get() = sp.getBoolean("mic", false)
        set(v) = sp.edit().putBoolean("mic", v).apply()

    fun logout() = sp.edit().remove("user").remove("password").remove("admin").apply()
}

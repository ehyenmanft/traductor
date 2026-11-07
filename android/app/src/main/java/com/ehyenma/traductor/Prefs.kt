package com.ehyenma.traductor

import android.content.Context

const val DEFAULT_SERVER = "wss://3-147-129-93.sslip.io"

val LANGS = listOf(
    "es" to "Español", "en" to "English", "pt" to "Português", "fr" to "Français",
    "de" to "Deutsch", "it" to "Italiano", "ja" to "日本語", "ko" to "한국어",
    "zh-cn" to "中文", "ru" to "Русский",
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
    var mic: Boolean
        get() = sp.getBoolean("mic", false)
        set(v) = sp.edit().putBoolean("mic", v).apply()

    fun logout() = sp.edit().remove("user").remove("password").remove("admin").apply()
}

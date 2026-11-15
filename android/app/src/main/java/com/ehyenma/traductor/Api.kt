package com.ehyenma.traductor

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class ApiError(message: String) : Exception(message)

/** Peticiones de una sola respuesta al servidor (login y panel de usuarios). Bloquea: llamar desde un hilo. */
object Api {
    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    fun request(server: String, msg: JSONObject, timeoutSeconds: Long = 20): JSONObject {
        val latch = CountDownLatch(1)
        var result: JSONObject? = null
        var error: String? = null
        client.newWebSocket(Request.Builder().url(server).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(msg.put("version", 1).toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                result = runCatching { JSONObject(text) }.getOrNull()
                latch.countDown()
                webSocket.close(1000, null)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                error = t.message ?: t.javaClass.simpleName
                latch.countDown()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = latch.countDown()
        })
        if (!latch.await(timeoutSeconds, TimeUnit.SECONDS)) throw ApiError("El servidor no responde.")
        val r = result ?: throw ApiError("No pude conectar con el servidor (${error ?: "sin respuesta"}).")
        if (r.optString("type") == "error") throw ApiError(r.optString("message", "Error del servidor."))
        return r
    }

    fun login(server: String, user: String, password: String): JSONObject =
        request(server, JSONObject().put("type", "login").put("user", user).put("password", password))

    fun admin(p: Prefs, action: String, extra: JSONObject = JSONObject()): JSONObject {
        val msg = extra.put("type", "admin").put("user", p.user).put("password", p.password).put("action", action)
        val r = request(p.server, msg)
        if (!r.optBoolean("ok")) throw ApiError(r.optString("error", "No se pudo."))
        return r
    }
}

package com.ehyenma.traductor

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView

/** Panel flotante sobre otras apps: original pequeño, traducción grande, botones ⏸/▶ y ✕. Hilo principal. */
class Overlay(private val ctx: Context, onPause: () -> Unit, onClose: () -> Unit) {
    private val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val dp = ctx.resources.displayMetrics.density
    private val entries = LinkedHashMap<Int, Array<String>>()      // uid → [original, traducción]

    private val status = text(11f, Color.parseColor("#9BE8FF")).apply { text = "Conectando…" }
    private val original = text(12f, Color.parseColor("#B8C0CC")).apply { maxLines = 2 }
    private val translation = text(17f, Color.WHITE).apply { maxLines = 4 }
    private val pauseBtn = roundButton("⏸", onPause)
    private val root = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        val pad = (12 * dp).toInt()
        setPadding(pad, (8 * dp).toInt(), pad, pad)
        background = GradientDrawable().apply { setColor(Color.argb(205, 16, 20, 26)); cornerRadius = 18 * dp }
        addView(LinearLayout(ctx).apply {
            gravity = Gravity.CENTER_VERTICAL
            addView(status, LinearLayout.LayoutParams(0, -2, 1f))
            addView(pauseBtn)
            addView(roundButton("✕", onClose).apply {
                (layoutParams as? LinearLayout.LayoutParams)?.leftMargin = (8 * dp).toInt()
            })
        })
        addView(original)
        addView(translation)
    }
    private val params = WindowManager.LayoutParams(
        (ctx.resources.displayMetrics.widthPixels * 0.94).toInt(),
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,      // el juego conserva el teclado y los toques
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        x = (ctx.resources.displayMetrics.widthPixels * 0.03).toInt()
        y = (80 * dp).toInt()
    }

    private fun text(sp: Float, color: Int) = TextView(ctx).apply {
        textSize = sp
        setTextColor(color)
        setShadowLayer(3f, 0f, 1f, Color.BLACK)
    }

    private fun roundButton(label: String, onClick: () -> Unit) = TextView(ctx).apply {
        text = label
        textSize = 16f
        gravity = Gravity.CENTER
        setTextColor(Color.WHITE)
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(Color.argb(60, 255, 255, 255)) }
        val size = (40 * dp).toInt()                    // táctil cómodo (≥ 40 dp)
        layoutParams = LinearLayout.LayoutParams(size, size)
        setOnClickListener { onClick() }
    }

    @SuppressLint("ClickableViewAccessibility")
    fun show() {
        var startX = 0; var startY = 0; var touchX = 0f; var touchY = 0f
        root.setOnTouchListener { _, e ->                // arrastrar el panel
            when (e.action) {
                MotionEvent.ACTION_DOWN -> { startX = params.x; startY = params.y; touchX = e.rawX; touchY = e.rawY }
                MotionEvent.ACTION_MOVE -> {
                    params.x = startX + (e.rawX - touchX).toInt()
                    params.y = startY + (e.rawY - touchY).toInt()
                    wm.updateViewLayout(root, params)
                }
            }
            true
        }
        wm.addView(root, params)
    }

    fun hide() {
        if (root.isAttachedToWindow) wm.removeView(root)
    }

    fun status(s: String) { status.text = s }

    fun setPaused(paused: Boolean) {
        pauseBtn.text = if (paused) "▶" else "⏸"
        (pauseBtn.background as GradientDrawable).setColor(
            if (paused) Color.argb(150, 255, 140, 0) else Color.argb(60, 255, 255, 255))
    }

    fun upsert(uid: Int, text: String) { entry(uid)[0] = text; render() }

    fun translate(uid: Int, text: String) { entry(uid)[1] = text; render() }

    private fun entry(uid: Int): Array<String> {
        val e = entries.getOrPut(uid) { arrayOf("", "") }
        while (entries.size > 3) entries.remove(entries.keys.first())
        return e
    }

    private fun render() {
        val last = entries.entries.lastOrNull() ?: return
        original.text = last.value[0]
        val shown = entries.values.map { it[1] }.filter { it.isNotBlank() }.takeLast(2)
        val sb = SpannableStringBuilder()
        shown.forEachIndexed { i, t ->
            if (sb.isNotEmpty()) sb.append("\n")
            val start = sb.length
            sb.append(t)
            if (i == shown.lastIndex) {
                sb.setSpan(StyleSpan(Typeface.BOLD), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            } else {
                sb.setSpan(ForegroundColorSpan(Color.argb(170, 255, 255, 255)), start, sb.length,
                    Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
        }
        translation.text = sb
        translation.visibility = if (sb.isEmpty()) View.GONE else View.VISIBLE
    }
}

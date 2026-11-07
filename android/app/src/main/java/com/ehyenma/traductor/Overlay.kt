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
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

/** Colores del subtítulo que se eligen desde el panel. */
private val SUB_COLORS = listOf("Blanco" to Color.WHITE, "Amarillo" to 0xFFFFE066.toInt(),
    "Cian" to 0xFF7FE7FF.toInt(), "Verde" to 0xFF8CFFB4.toInt(), "Rosa" to 0xFFFFA8D6.toInt())

/** Panel flotante sobre otras apps: original pequeño, traducción grande; botones redondos para el estilo, volver
 *  a la app, pausar/reanudar y detener (iconos vectoriales: los emojis cambian de tamaño y color). Hilo principal. */
class Overlay(private val ctx: Context, private val prefs: Prefs,
              onOpen: () -> Unit, onPause: () -> Unit, onStop: () -> Unit) {
    private val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val dp = ctx.resources.displayMetrics.density
    private val entries = LinkedHashMap<Int, Array<String>>()      // uid → [original, traducción]

    private val status = text(11f, ENLACE).apply { text = "Conectando…"; maxLines = 1 }
    private val original = text(12f, Color.parseColor("#B8B0D8")).apply { maxLines = 2 }
    private val translation = text(17f, Color.WHITE).apply { maxLines = 4 }
    private val pauseBtn = roundButton(R.drawable.ic_pausa, onPause)
    private val bg = GradientDrawable().apply {
        setStroke((1 * dp).toInt(), Color.argb(90, 167, 139, 250))
        cornerRadius = 18 * dp
    }

    // ---- fila de estilo (oculta hasta pulsar el botón de estilo) ----
    private val opacityLabel = chip("") {}
    private val colorChip = chip("") { prefs.colorIndex = (prefs.colorIndex + 1) % SUB_COLORS.size; applyStyle() }
    private val originalChip = chip("") { prefs.showOriginal = !prefs.showOriginal; applyStyle() }
    private val styleRow = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        visibility = View.GONE
        setPadding(0, (6 * dp).toInt(), 0, (2 * dp).toInt())
        addView(row(chip("−") { changeOpacity(-25) }, opacityLabel, chip("+") { changeOpacity(25) }))
        addView(row(chip("A−") { changeSize(-2) }, chip("A+") { changeSize(2) }, colorChip, originalChip))
    }

    private val root = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        val pad = (12 * dp).toInt()
        setPadding(pad, (8 * dp).toInt(), pad, pad)
        background = bg
        addView(LinearLayout(ctx).apply {
            gravity = Gravity.CENTER_VERTICAL
            addView(status, LinearLayout.LayoutParams(0, -2, 1f))
            addView(roundButton(R.drawable.ic_estilo) { toggleStyle() }.apply { contentDescription = "Estilo del subtítulo" })
            addView(roundButton(R.drawable.ic_abrir, onOpen).apply { contentDescription = "Volver a la app"; spaced() })
            addView(pauseBtn.apply { contentDescription = "Pausar o reanudar"; spaced() })
            addView(roundButton(R.drawable.ic_stop, onStop).apply { contentDescription = "Detener la traducción"; spaced() })
        })
        addView(styleRow)
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

    private fun roundButton(icon: Int, onClick: () -> Unit) = ImageView(ctx).apply {
        setImageResource(icon)
        val pad = (9 * dp).toInt()
        setPadding(pad, pad, pad, pad)
        background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(Color.argb(40, 255, 255, 255))
            setStroke((1.5f * dp).toInt(), VIOLETA)
        }
        val size = (38 * dp).toInt()                    // táctil cómodo con 4 botones en la fila
        layoutParams = LinearLayout.LayoutParams(size, size)
        setOnClickListener { onClick() }
    }

    private fun chip(label: String, onClick: () -> Unit) = TextView(ctx).apply {
        text = label
        textSize = 13f
        gravity = Gravity.CENTER
        setTextColor(TEXTO)
        minWidth = (40 * dp).toInt()
        minHeight = (36 * dp).toInt()
        val h = (10 * dp).toInt()
        setPadding(h, 0, h, 0)
        background = GradientDrawable().apply { setColor(Color.argb(40, 255, 255, 255)); cornerRadius = 18 * dp }
        setOnClickListener { onClick() }
    }

    private fun row(vararg views: View) = LinearLayout(ctx).apply {
        gravity = Gravity.CENTER_VERTICAL
        setPadding(0, (4 * dp).toInt(), 0, 0)
        views.forEachIndexed { i, v ->
            addView(v, LinearLayout.LayoutParams(-2, -2).apply { if (i > 0) leftMargin = (6 * dp).toInt() })
        }
    }

    private fun View.spaced() { (layoutParams as LinearLayout.LayoutParams).leftMargin = (6 * dp).toInt() }

    // ---------- estilo ----------

    private fun applyStyle() {
        val (colorName, color) = SUB_COLORS[prefs.colorIndex % SUB_COLORS.size]
        bg.setColor(Color.argb(prefs.opacity, 14, 11, 31))       // noche índigo de la app
        translation.textSize = prefs.fontSize.toFloat()
        translation.setTextColor(color)
        original.textSize = maxOf(11f, prefs.fontSize * 0.7f)
        original.visibility = if (prefs.showOriginal && original.text.isNotEmpty()) View.VISIBLE else View.GONE
        opacityLabel.text = "Fondo ${prefs.opacity * 100 / 255}%"
        colorChip.text = "● $colorName"
        colorChip.setTextColor(color)
        originalChip.text = if (prefs.showOriginal) "Original: sí" else "Original: no"
        render()
    }

    private fun changeOpacity(delta: Int) { prefs.opacity = (prefs.opacity + delta).coerceIn(0, 255); applyStyle() }

    private fun changeSize(delta: Int) { prefs.fontSize = (prefs.fontSize + delta).coerceIn(12, 32); applyStyle() }

    /** Con los ajustes abiertos el panel toma el foco para recibir las teclas de volumen; al cerrarlos lo
     *  devuelve, para que el juego o el video vuelvan a tener el volumen y el teclado. */
    private fun toggleStyle() {
        val open = styleRow.visibility != View.VISIBLE
        styleRow.visibility = if (open) View.VISIBLE else View.GONE
        params.flags = if (open) 0 else WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
        wm.updateViewLayout(root, params)
        if (open) {
            root.isFocusableInTouchMode = true
            root.requestFocus()
            status("🔊 Volumen + / − cambia el fondo")
        }
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
        root.setOnKeyListener { _, code, e ->           // solo llegan con los ajustes de estilo abiertos
            val ours = code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN ||
                code == KeyEvent.KEYCODE_BACK
            if (ours && e.action == KeyEvent.ACTION_DOWN) when (code) {
                KeyEvent.KEYCODE_VOLUME_UP -> changeOpacity(25)
                KeyEvent.KEYCODE_VOLUME_DOWN -> changeOpacity(-25)
                else -> toggleStyle()
            }
            ours
        }
        applyStyle()
        wm.addView(root, params)
    }

    fun hide() {
        if (root.isAttachedToWindow) wm.removeView(root)
    }

    fun status(s: String) { status.text = s }

    fun setPaused(paused: Boolean) {
        pauseBtn.setImageResource(if (paused) R.drawable.ic_play else R.drawable.ic_pausa)
        (pauseBtn.background as GradientDrawable).apply {
            setColor(if (paused) Color.argb(110, 245, 185, 66) else Color.argb(40, 255, 255, 255))
            setStroke((1.5f * dp).toInt(), if (paused) AMBAR else VIOLETA)
        }
    }

    fun upsert(uid: Int, text: String) { entry(uid)[0] = text; render() }

    fun translate(uid: Int, text: String) { entry(uid)[1] = text; render() }

    private fun entry(uid: Int): Array<String> {
        val e = entries.getOrPut(uid) { arrayOf("", "") }
        while (entries.size > 3) entries.remove(entries.keys.first())
        return e
    }

    /** Frases largas: lo nuevo está al final, así que se muestra la cola ("…" delante) y no el principio. */
    private fun tail(s: String, max: Int): String {
        if (s.length <= max) return s
        val cut = s.length - max
        val space = s.indexOf(' ', cut).takeIf { it in cut until cut + 20 } ?: cut
        return "…" + s.substring(space).trimStart()
    }

    private fun render() {
        val last = entries.entries.lastOrNull() ?: return
        val perLine = maxOf(20, (params.width / (translation.textSize * 0.55f)).toInt())   // caracteres por línea aprox.
        original.text = tail(last.value[0], perLine * 2)
        original.visibility = if (prefs.showOriginal && original.text.isNotEmpty()) View.VISIBLE else View.GONE
        val shown = entries.values.map { it[1] }.filter { it.isNotBlank() }.takeLast(2)
        val sb = SpannableStringBuilder()
        shown.forEachIndexed { i, t ->
            val newest = i == shown.lastIndex
            if (sb.isNotEmpty()) sb.append("\n")
            val start = sb.length
            sb.append(tail(t, perLine * if (newest) 3 else 1))   // la frase anterior, en una línea
            if (newest) {
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

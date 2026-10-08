# Mejoras del traductor de voz en vivo

**Traducción con contexto (requiere `groq_api_key`)** — `live_translator.py`
- Las frases finales se traducen con **streaming** (aparecen palabra a palabra) y con las últimas `context_lines` frases como contexto, para mantener nombres y tono. Cadena de respaldo: `llama-3.3-70b-versatile` → `llama-3.1-8b-instant` → Google Translate. Si Groq falla 4 veces seguidas, usa Google durante 30 s.
- **Tono** (menú de la bandeja → 🗣 Tono de traducción, o `"tone"` en `config.json`): `gamer` (por defecto), `natural`, `formal`, `casual`, `technical`, `funny`.
- **Glosario** (`"glossary": {"origen": "destino"}`; si origen y destino son iguales, el término no se traduce). Menú de la bandeja → 📖 Recargar glosario. Los términos del glosario también se envían a Deepgram como `keyterms` para reconocerlos mejor.
- Los parciales solo se traducen cuando el texto se estabiliza (`"translate_partials": false` para desactivarlo). La traducción final corrige a la del parcial en su sitio.
- Corregido: una frase final podía perder su traducción si llegaba un parcial de la siguiente antes de traducirla.

**Audio y reconocimiento**
- Remuestreo a 16 kHz con filtro (antes se plegaban frecuencias altas sobre la voz) y fragmentos de 100 ms.
- Cierre de frase por **segundos** de silencio (0,5 s) en los motores local y Groq.
- Deepgram: `UtteranceEnd` (no deja frases abiertas con ruido de fondo), `KeepAlive` (no reconecta tras silencios; el loopback de Windows no emite audio en silencio), `"endpointing_ms"` y `"keyterms"` configurables.

Pruebas: `python -m unittest discover tests` (no requieren Windows ni claves).

**Overlay personalizable (🎨)** — `overlay_style.py`, `subtitle_view.py`, `style_dialog.py`
- Botón 🎨 del overlay (o bandeja → 🎨 Personalizar estilo…): estilo listo (Gamer, Clásico, Cine, Minimal, Neón, Terminal), fuente, tamaño, colores (traducción, original, etiqueta, contorno), **contorno** y sombra reales, alineación, líneas visibles, tamaño del original, opacidad, posición 3×3 y plantillas propias. Todo se aplica en vivo y se guarda en `config.json` (`"style"`).
- El modo HUD (F6) usa un visor propio con alto automático y frases completas; antes el contorno (`text-shadow`) no se dibujaba nunca porque Qt no lo soporta y la segunda frase salía recortada.

**Doblaje de voz en vivo (F11)** — `live_dubber.py`
- Lee en voz alta cada traducción final con voces neuronales (`edge-tts`, necesita internet). Activa/desactiva con **F11**, con el botón **🔊/🔇** del overlay (verde = activo) o desde la bandeja (🔊 Doblaje de voz), donde también eliges voz (mujer/hombre) y dispositivo de salida.
- **Anti-realimentación:** si la voz sale por el mismo dispositivo que se captura, la captura se silencia mientras habla (se pierde ese tramo del audio original). Para no perderlo, elige en la bandeja otra salida para la voz (por ejemplo unos audífonos distintos del dispositivo capturado).
- Si se atrasa, descarta las frases más antiguas (`"dub_max_backlog"`, 2 por defecto) y acelera la voz hasta un 40 %. Tras 3 fallos seguidos (¿sin internet?) se desactiva solo y avisa.
- Ajustes en `config.json`: `"dub"`, `"dub_gender"`, `"dub_device"`, `"dub_volume"`, `"dub_max_backlog"`.

> Para actualizar el `.exe` ejecuta de nuevo `build.bat` en Windows (añade `edge-tts` al paquete). La captura WASAPI, las voces reales y el `.exe` no se pueden probar fuera de Windows: lo verificado automáticamente son la lógica, la interfaz (modo offscreen) y los audios de prueba.


## Opciones nuevas de `config.json`

Todas son opcionales (la app usa valores por defecto). Hay un ejemplo completo en
[`config.live.example.json`](../config.live.example.json).

| Clave | Para qué |
|---|---|
| `tone` | Tono de la traducción: `gamer` (por defecto), `natural`, `formal`, `casual`, `technical`, `funny` |
| `glossary` | `{"origen": "destino"}`; si son iguales, el término no se traduce |
| `keyterms` | Palabras que Deepgram debe reconocer mejor (se suman las del glosario) |
| `context_lines` | Frases anteriores usadas como contexto al traducir (4) |
| `translate_partials` | Traducir también los parciales (true) |
| `endpointing_ms` | Silencio que cierra una frase en Deepgram (300) |
| `dub`, `dub_gender`, `dub_device`, `dub_volume`, `dub_max_backlog` | Doblaje de voz |
| `style`, `style_templates` | Los guarda el panel 🎨; no hace falta editarlos a mano |

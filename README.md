# 🎙️ Traductor de Voz en Vivo — Gaming Overlay HUD

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-0078d6.svg)](https://www.microsoft.com/windows/)
[![UI](https://img.shields.io/badge/GUI-PyQt6-brightgreen.svg)](https://riverbankcomputing.com/software/pyqt/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Traductor de voz en tiempo real con **overlay translúcido estilo HUD / Cine** optimizado para **videojuegos y aplicaciones de PC** en Windows. Captura la salida de audio de tu juego o Discord (WASAPI loopback), transcribe en vivo con **Deepgram nova-3**, **Groq** o **faster-whisper local**, traduce al instante y muestra los subtítulos en pantalla sin interferir con tu partida.

---

## ✨ Características Principales

- **🎮 Modo Subtítulos Gaming HUD (`F6`):**
  - Muestra de 1 a 2 líneas activas compactas.
  - Texto renderizado con sombreado de alto contraste (`text-shadow`), 100% legible sobre explosiones y fondos claros u oscuros.
  - No obstruye minimapas, barras de vida o inventarios.
- **🛡️ Modo Click-Through (`F8`):** Los clics del ratón atraviesan el panel para que puedas jugar y disparar con total normalidad.
- **🌐 Selector de Idiomas en Vivo:** Cambia el idioma destino al vuelo sin reiniciar la aplicación ni pausar el flujo de audio.
- **⚡ Latencia Ultra-Baja:** Parciales en ~200 ms mediante WebSockets con **Deepgram nova-3** y streaming bidireccional.
- **🔌 Multi-Motor Inteligente:**
  - `Deepgram`: nova-3 multilingüe por streaming (Recomendado).
  - `Groq`: whisper-large-v3-turbo por API remota.
  - `Local`: faster-whisper (CPU / NVIDIA CUDA) para funcionar 100% en tu equipo.
- **🛡️ Icono en la Bandeja del Sistema (System Tray):** Menú rápido en la barra de tareas de Windows para controlar visibilidad, idiomas, modo gaming y salir limpiamente.
- **💾 Historial y Guardado:** Guarda las transcripciones de tus sesiones de juego en archivos de texto con hora, idioma original y traducción.
- **🌙 Auto-Atenuado:** Se atenúa solo tras 6 segundos de inactividad y se reactiva automáticamente al detectar voz.

---

## ⌨️ Controles y Atajos Globales

Los atajos funcionan globalmente incluso cuando el videojuego tiene el foco:

| Atajo | Acción |
|---|---|
| **`F6`** | Alternar **Modo Subtítulos Gaming HUD** vs Modo Historial |
| **`F7`** | Ciclar nivel de opacidad (35% → 65% → 100% → 150% → 210) |
| **`F8`** | Alternar **Click-Through** (los clics atraviesan el panel) |
| **`F9`** | Mostrar / Ocultar el overlay |
| **`F10`**| Alternar **Modo Compacto** (solo traducción / original + traducción) |
| **`Ctrl + Rueda`** | Aumentar o reducir tamaño de fuente |
| **`Arrastrar`** | Mover el panel por la pantalla (cuando click-through está desactivado) |

---

## 🚀 Requisitos e Instalación

### Requisitos del Sistema
- **Windows 10 o Windows 11** (la captura de audio utiliza WASAPI Loopback).
- **Python 3.10+** (si se ejecuta desde código fuente).
- Conexión a internet (para Deepgram / Groq / Google Translate).

---

### Opción 1: Ejecución Rápida (Desde Código Fuente)

1. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/TU_USUARIO/TU_REPOSITORIO.git
   cd TU_REPOSITORIO
   ```

2. **Crear archivo de configuración:**
   Copia `config.example.json` a `config.json` e introduce tu API key de Deepgram:
   ```json
   {
     "deepgram_api_key": "TU_API_KEY_AQUI",
     "target_lang": "es",
     "mode": "subtitle",
     "opacity": 90,
     "font_size": 13,
     "width": 560
   }
   ```
   *(Consigue $200 de crédito gratuito registrándote en [deepgram.com](https://deepgram.com))*.

3. **Iniciar con el script automatizado:**
   Simplemente ejecuta:
   ```cmd
   run.bat
   ```
   *(El script creará el entorno virtual `venv` e instalará las dependencias automáticamente en el primer inicio)*.

---

### Opción 2: Compilar el Ejecutable `.exe` Standalone

Si deseas generar el archivo `.exe` para distribuirlo sin requerir Python:

```cmd
build.bat
```
El ejecutable listo para usar se generará en `dist/TraductorEnVivo/TraductorEnVivo.exe`.

---

## 🏗️ Arquitectura del Proyecto

```
voice-overlay/
├── audio_capture.py          # WASAPI loopback (captura de audio de salida de PC a 16 kHz)
├── transcriber_deepgram.py   # Motor streaming por WebSockets con Deepgram nova-3
├── transcriber_groq.py       # Motor remoto con Groq Whisper Large v3
├── transcriber.py            # Motor local con faster-whisper + VAD dinámico
├── translator.py             # Motor de traducción con caché en tiempo real
├── overlay.py                # Interfaz gráfica translúcida PyQt6 (Gaming HUD + Historial)
├── main.py                   # Coordinador de hilos, System Tray y atajos globales
├── config.example.json       # Plantilla de configuración
├── run.bat                   # Lanzador automático
├── build.bat                 # Script de compilación PyInstaller
└── requirements.txt          # Dependencias de Python
```

---

## ⚠️ Notas para Videojuegos

- Para que el overlay se superponga sobre tus videojuegos, asegúrate de configurar el juego en modo **Ventana sin bordes (Borderless Windowed)** o **Pantalla completa en ventana**, al igual que overlays como GeForce Experience o Discord.

---

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Consulta el archivo [LICENSE](LICENSE) para más detalles.


---

## 🆕 Mejoras de traducción y audio en vivo

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
- Lee en voz alta cada traducción final con voces neuronales (`edge-tts`, necesita internet). Activa/desactiva con **F11** o desde la bandeja (🔊 Doblaje de voz), donde también eliges voz (mujer/hombre) y dispositivo de salida.
- **Anti-realimentación:** si la voz sale por el mismo dispositivo que se captura, la captura se silencia mientras habla (se pierde ese tramo del audio original). Para no perderlo, elige en la bandeja otra salida para la voz (por ejemplo unos audífonos distintos del dispositivo capturado).
- Si se atrasa, descarta las frases más antiguas (`"dub_max_backlog"`, 2 por defecto) y acelera la voz hasta un 40 %. Tras 3 fallos seguidos (¿sin internet?) se desactiva solo y avisa.
- Ajustes en `config.json`: `"dub"`, `"dub_gender"`, `"dub_device"`, `"dub_volume"`, `"dub_max_backlog"`.

> Para actualizar el `.exe` ejecuta de nuevo `build.bat` en Windows (añade `edge-tts` al paquete). La captura WASAPI, las voces reales y el `.exe` no se pueden probar fuera de Windows: lo verificado automáticamente son la lógica, la interfaz (modo offscreen) y los audios de prueba.

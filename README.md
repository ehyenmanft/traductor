# 🎙️ Traductor de Voz en Vivo — Gaming Overlay HUD

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-0078d6.svg)](https://www.microsoft.com/windows/)
[![UI](https://img.shields.io/badge/GUI-PyQt6-brightgreen.svg)](https://riverbankcomputing.com/software/pyqt/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Traductor de voz en tiempo real con **overlay translúcido estilo HUD / Cine** optimizado para **videojuegos y aplicaciones de PC** en Windows. Captura la salida de audio de tu juego o Discord (WASAPI loopback), transcribe en vivo con **Deepgram nova-3**, **Groq** o **faster-whisper local**, traduce al instante y muestra los subtítulos en pantalla sin interferir con tu partida.

📘 **Novedades de la app de escritorio** (traducción con contexto, overlay personalizable, doblaje de voz): ver [docs/MEJORAS_EN_VIVO.md](docs/MEJORAS_EN_VIVO.md).

☁️ **Variante cliente ligero + AWS** (la PC solo captura audio y muestra el overlay; todo lo demás y tus claves en tu servidor): ver [docs/CLIENTE_AWS.md](docs/CLIENTE_AWS.md).

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

## 🤖 Bot de Telegram: traducir videos con subtítulos incrustados

`telegram_bot.py` reutiliza el motor del traductor (Deepgram nova-3 para transcribir y detectar idioma, Groq/Google para traducir) y devuelve **tu video original, intacto, con la traducción superpuesta** (el audio se copia sin recodificar).

**Flujo:** envías un video → el bot muestra un menú (estilo, posición, tamaño, idioma destino, texto original debajo) → pulsas **✅ Confirmar** → recibes el video traducido y un `.srt`.

```bash
sudo apt install ffmpeg            # o: winget install ffmpeg  (debe estar en el PATH)
pip install -r requirements-bot.txt
export TELEGRAM_BOT_TOKEN=...      # de @BotFather
export DEEPGRAM_API_KEY=...
export GROQ_API_KEY=...            # opcional, mejora la traducción
export ALLOWED_USERS=123456789     # tu id (el bot lo muestra con /start); evita que otros gasten tu API
python telegram_bot.py
```

Las claves también pueden ir en `config.json` (ver `config.example.json`).

**Límites:** la Bot API pública solo deja a los bots descargar videos de hasta **20 MB** y enviar hasta **50 MB** (el bot comprime el resultado para entrar). Para videos grandes, ejecuta un [servidor local de Bot API](https://github.com/tdlib/telegram-bot-api) y define `TELEGRAM_API_URL=http://localhost:8081`.

Pruebas: `python -m unittest discover tests`

### ☁️ Despliegue 24/7 en AWS (Ubuntu / Lightsail)

```bash
git clone -b <rama> https://github.com/ehyenmanft/traductor.git && cd traductor
bash deploy/install.sh      # instala ffmpeg, dependencias, pide tus claves y crea el servicio systemd
```

El bot queda como servicio (`traductor-bot`): arranca con el servidor y se reinicia solo si falla. Logs: `journalctl -u traductor-bot -f`. Para actualizar: `bash deploy/update.sh`.

### 🎛️ Personalización estilo CapCut / Captions

| Categoría | Opciones |
|---|---|
| **Estilos listos** | Clásico, Cine amarillo, Caja oscura, Gamer neón, Cómic, Elegante, Retro, Minimal, 🔥 Hormozi, ⚡ Beast, 🎤 Karaoke, ⌨️ Tecleo, 💡 Neón |
| **Aspecto** | 5 fuentes, 10 colores, contorno (5 grosores y color), caja suave/sólida, sombra, negrita, cursiva, MAYÚSCULAS, espaciado, tamaño XS–XL |
| **Posición** | cuadrícula 5×5 (incluye puntos intermedios) |
| **Karaoke** | palabra activa en color / con pop / relleno progresivo; 1, 2, 3 o 5 palabras por pantalla |
| **Animación** | fundido, pop, rebote, máquina de escribir |
| **Efectos** | neón, palabras clave en color, barra de progreso, censura de groserías |
| **Traducción** | 10 idiomas o solo transcribir, tono (natural/formal/casual/gamer/técnico/humor), glosario (`/glosario hola=hello`, `/conservar Nombre`), texto original debajo |
| **Doblaje** | voz IA (mujer/hombre) con el audio original mantenido, bajo o silenciado |
| **Flujo** | vista previa en vivo, ⭐ plantillas propias, ✏️ editar el texto (.srt) y 🎨 repetir con otro estilo sin volver a transcribir |

El tono, el contexto y el glosario completo los aplica Groq (`groq_api_key`); sin Groq se usa Google/MyMemory con protección de los términos del glosario. El doblaje usa `edge-tts` (internet) y, si falla, el video sale igual sin doblaje.

No incluido: sincronía de labios, avatares IA, emojis a color animados ni música/transiciones.

### 📏 Videos grandes (más de 20 MB) y barra de progreso

Telegram solo deja que un bot descargue archivos de hasta **20 MB**, y partir el video no ayuda (el límite es por archivo que el bot descarga). Hay dos soluciones, que se pueden combinar:

1. **Pegar un enlace** (Drive, Dropbox, YouTube, Vimeo o archivo directo `.mp4`): el bot lo descarga él mismo, hasta 2 GB y 90 min. Se rechazan direcciones privadas por seguridad.
2. **Servidor local de la Bot API** (`bash deploy/local_api.sh`, requiere `api_id`/`api_hash` de my.telegram.org): el bot recibe y envía videos de hasta ~2 GB directamente. Configura `telegram_api_url` en `config.json`.

Mientras se procesa, el mensaje muestra una **barra con porcentaje y tiempo transcurrido** (`▰▰▰▰▱▱▱▱ 52%`), calculada con el avance real de ffmpeg, la traducción y el doblaje. Sin servidor local, el resultado se comprime para entrar en los 50 MB que permite subir Telegram.

### 🧹 Limpieza de temporales

- El bot borra la carpeta de trabajo de cada video al cancelar, al fallar y a las 2 h de terminar (`keep_minutes` en `config.json`; mientras tanto permite ✏️ editar y 🎨 repetir). Revisa cada 10 min.
- Con el servidor local de Telegram, el video recibido se **mueve** (no se copia) a la carpeta del bot, y los `.srt` se borran tras leerse.
- Red de seguridad: `deploy/cleanup.sh` corre por cron cada 30 min (`deploy/install_cleanup.sh`, lo instala `update.sh`) y borra descargas del servidor local de más de 60 min y carpetas `/tmp/trad_*` de más de 4 h, sin tocar el estado interno de Telegram.
- Antes de aceptar un video comprueba que haya espacio en disco, y los logs de Docker rotan (3 × 10 MB).

### 💾 Almacenamiento desde Telegram

`/almacenamiento` (también `/espacio`) muestra el disco del servidor (usado/libre con barra), los temporales del bot (de videos abiertos y huérfanos) y las descargas del servidor local de Telegram. El botón **🧹 Borrar temporales** pide confirmación y elimina lo que no está en uso: nunca toca videos que se están procesando ni descargando, ni el estado interno de Telegram.

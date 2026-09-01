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

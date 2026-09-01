# Traductor de voz en vivo — Overlay translúcido

Captura el audio que suena en tu PC (voz del juego, Discord, etc.), lo transcribe con Whisper, detecta el idioma, lo traduce y muestra original + traducción en un panel translúcido siempre visible.

## Requisitos

- Windows 10/11 (la captura usa WASAPI loopback)
- Python 3.10+
- GPU NVIDIA opcional pero recomendada (Whisper corre mucho más rápido)

## Instalación

```bash
pip install -r requirements.txt
```

Si tienes GPU NVIDIA y quieres usarla, instala además CUDA/cuDNN según la guía de faster-whisper, o simplemente deja `--device auto` (cae a CPU si no hay CUDA).

## Uso

```bash
python main.py                     # traduce todo al español
python main.py --target en         # traduce al inglés
python main.py --model medium      # más precisión, más latencia
python main.py --opacity 60        # panel más transparente
```

## Controles y Atajos Globales
 
| Tecla | Acción |
|---|---|
| **F6** | Alternar **Modo Subtítulos Gaming HUD** (subtítulos flotantes de alto contraste) vs Historial |
| **F7** | Ciclar nivel de opacidad (35%, 65%, 100%, 150%, 210) |
| **F8** | Alternar **Click-through** (los clics atraviesan el panel para no interferir con el juego) |
| **F9** | Mostrar / Ocultar el panel |
| **F10**| Alternar **Modo Compacto** (muestra solo la traducción o ambos) |
| **Ctrl + Rueda** | Aumentar o reducir tamaño de fuente en tiempo real |
| **Arrastrar** | Mover el panel (cuando click-through está desactivado) |

## Funcionalidades para Videojuegos

- **Modo Gaming Subtitle (HUD):** Diseñado especialmente para juegos. Muestra las 1-2 frases activas con texto contorneado de alto contraste (legible sobre explosiones y fondos oscuros o claros).
- **Selector de Idioma en Vivo:** Cambia el idioma destino al vuelo sin reiniciar (botón `🌐` en la barra o en la bandeja del sistema).
- **Icono en la Bandeja del Sistema (System Tray):** Controla el estado, idiomas y visibilidad desde la barra de tareas de Windows junto al reloj.
- **Auto-Atenuado:** El overlay reduce su brillo automáticamente tras 6 segundos de inactividad de voz y despierta al instante cuando alguien habla.

## Motor Deepgram (recomendado): subtítulos en vivo con nova-3

Ya viene configurado si tu `config.json` incluye `"deepgram_api_key"`.
También puedes usar la variable de entorno `DEEPGRAM_API_KEY`.
Streaming por websocket: parciales en ~200 ms mientras hablan y final
al detectar la pausa (endpointing del servidor). $200 de crédito gratis
al registrarte en deepgram.com.

Con `--engine auto` (el default) la prioridad es: deepgram > groq > local,
según qué keys tengas configuradas. Forzar: `run.bat --engine deepgram`.

## Motor Groq (alternativo): precisión de large-v3 con CPU casi libre

1. Crea una API key gratis en https://console.groq.com/keys
2. Configúrala de una de estas dos formas:
   - Variable de entorno: `setx GROQ_API_KEY "tu_key"` (cierra y reabre la consola)
   - O añade `"groq_api_key": "tu_key"` dentro de `config.json`
3. Ejecuta `run.bat` normalmente. Si detecta la key, usa Groq automáticamente;
   sin key (o si Groq falla al iniciar), cae al motor local sin que hagas nada.

Forzar motor: `run.bat --engine groq` o `run.bat --engine local`.

El plan gratuito de Groq (2,000 solicitudes/día) alcanza para varias horas
diarias de sesión. El motor incluye un limitador que respeta esos topes
alargando segmentos automáticamente si hablas sin parar.

## Notas importantes

- **Juegos**: el overlay solo se ve si el juego está en modo *borderless windowed* (pantalla completa sin bordes), no en fullscreen exclusivo. Igual que el overlay de GeForce.
- **Latencia**: con modelo `small` en GPU, ~1-2 s tras cada pausa en el habla. El sistema corta segmentos por silencio o cada 7 s máximo.
- La traducción usa Google Translate vía `deep-translator` (requiere internet). Para algo 100% offline puedes sustituir `translator.py` por `argostranslate`.
- Los atajos F8/F9 funcionan cuando el panel tiene foco. Para atajos globales (con el juego en foco), añade la librería `keyboard` y registra hooks globales en `main.py`.

## Arquitectura

```
audio_capture.py   WASAPI loopback → chunks float32 16 kHz
transcriber.py     buffer + VAD por energía → faster-whisper → texto + idioma
translator.py      deep-translator (Google) con caché
overlay.py         PyQt6: ventana translúcida, siempre encima, click-through
main.py            hilos + cola + señal Qt hacia la UI
```

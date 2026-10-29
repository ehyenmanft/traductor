---
name: traductor-video
description: Subtitular y traducir videos con el motor del traductor (Deepgram + Groq) y estilos de subtítulos como KidCut, Hormozi, Beast, TikTok, Podcast, Neón… Úsala cuando te manden un video (archivo o ruta) para subtitularlo, traducirlo, doblarlo, cambiar el estilo/fuente/posición de los subtítulos, o cuando pidan «el estilo kidcut».
---

# Traductor de video (subtítulos con estilo)

Todo corre en este servidor con la herramienta `cli_video.py` del proyecto `~/traductor`. Las claves (Deepgram, Groq)
ya están en `~/traductor/config.json`: **nunca las muestres, imprimas ni pidas por el chat**.

## Cómo se usa

```bash
cd ~/traductor && .venv-bot/bin/python cli_video.py subtitle RUTA_DEL_VIDEO --style kidcut --target es --pos top
```

- `--style`: nombre del estilo, sin emoji (`kidcut`, `hormozi`, `beast`, `tiktok`, `podcast`…). Lista completa y fuentes/colores:
  `.venv-bot/bin/python cli_video.py styles`
- `--target`: idioma destino (`es`, `en`, `pt`, `fr`, `de`, `it`, `ja`, `ko`, `zh-cn`, `ru`). El idioma original se detecta solo.
- `--pos`: posición (`top`, `upper`, `middle`, `lower`, `bottom`, opcional `-left/-right`). `--size xs|s|m|l|xl`.
- `--tone natural|formal|casual|gamer|technical|funny` y `--dub female|male` (voz IA que dobla).
- Al terminar imprime líneas `RESULTADO: ruta` (video subtitulado y `.srt`). **Entrega esos archivos al usuario** con la
  herramienta de envío de archivos del canal. Si pesa más de ~50 MB por Telegram público, dilo y ofrece un enlace o recomprimirlo.

## Estilo KidCut (plantilla de los videos KidCut)
Línea pequeña en Montserrat Medium y debajo la palabra clave en Montserrat ExtraBold cursiva MAYÚSCULAS que aparece al decirse:
la primera clave con halo azul, las negativas (no, riesgo, quiebra…) con halo rojo, el resto blanco. Mejor con `--pos top`.
Si el usuario dice «aplica kidcut», usa `--style kidcut --pos top` salvo que pida otra posición.

## Reglas
- Un video a la vez (el servidor es pequeño). Videos de hasta ~90 min; si falla por espacio, avisa.
- Si el video ya está en el idioma destino la herramienta lo dice: propón otro idioma o `--target` distinto.
- No borres nada fuera de `~/traductor/salidas/` (se limpia sola a los 2 días).
- Si el usuario quiere el mismo resultado desde Telegram con el bot de traducción, también existe `/estilo kidcut` allí.

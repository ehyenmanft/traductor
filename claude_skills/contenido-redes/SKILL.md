---
name: contenido-redes
description: Crear contenido para redes a partir de un video - reels 9:16, quitar silencios, clips de 55 s, miniaturas, carruseles de imágenes con las ideas clave, y títulos/descripción/hashtags. Úsala cuando pidan reels, shorts, TikTok, carruseles de Instagram, cortes, miniaturas o textos para publicar.
---

# Contenido para redes desde un video

Herramienta: `~/traductor/cli_video.py` (usa el venv `~/traductor/.venv-bot`). Las claves ya están en el servidor: **nunca las muestres**.
Cada orden imprime `RESULTADO: ruta` por cada archivo creado (en `~/traductor/salidas/`): entrégalos al usuario con la herramienta de
envío de archivos del canal.

```bash
cd ~/traductor
.venv-bot/bin/python cli_video.py reel VIDEO [--mode blur|crop]        # vertical 9:16 (blur = fondo difuminado; crop = recorte central)
.venv-bot/bin/python cli_video.py cuts VIDEO                           # quita silencios (jump cuts)
.venv-bot/bin/python cli_video.py clips VIDEO --seconds 55             # clips de hasta 55 s
.venv-bot/bin/python cli_video.py thumb VIDEO --title "TÍTULO CORTO"   # el fotograma más nítido, con título
.venv-bot/bin/python cli_video.py carousel VIDEO --theme oscuro --target es --slides 6   # imágenes 1080×1350
.venv-bot/bin/python cli_video.py texts VIDEO --target es              # títulos, gancho de 3 s, descripción y hashtags
```

Temas de carrusel: `oscuro`, `claro`, `neon`, `calido`. Para firmar las diapositivas: `--handle @usuario`.

## Flujo recomendado para un reel completo
1. `subtitle` con el estilo pedido (ver la skill `traductor-video`, p. ej. `--style kidcut --pos top`).
2. `reel` sobre el video subtitulado (`--mode blur`, así se conservan los subtítulos). Si lo quieren a pantalla completa
   y con subtítulos rehechos para vertical, usa el bot de Telegram del traductor (botón «Reel (recorte)»).
3. `texts` para el título y los hashtags, y `thumb` para la miniatura.

## Reglas
- `carousel` y `texts` transcriben el video (Deepgram) y usan Groq: pueden tardar; avisa al usuario antes de empezar si el video es largo.
- Si `texts` o `carousel` dicen que falta una clave, díselo al dueño; no la pidas por el chat.
- No inventes datos: los textos salen de lo que se dice en el video.

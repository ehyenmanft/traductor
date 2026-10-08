#!/usr/bin/env bash
# Limpieza de temporales (se ejecuta sola cada 30 min vía cron; ver install_cleanup.sh).
#  • Archivos multimedia que el servidor local de Telegram descargó hace > 60 min
#    (NO toca su base de datos ni su estado).
#  • Carpetas de trabajo del bot (/tmp/trad_*) sin actividad hace > 4 h.
# Variables para pruebas: TELEGRAM_DATA, TMP_DIR, MEDIA_MIN, WORK_MIN.
DATA="${TELEGRAM_DATA:-/var/lib/telegram-bot-api}"
TMP="${TMP_DIR:-/tmp}"
MEDIA_MIN="${MEDIA_MIN:-60}"
WORK_MIN="${WORK_MIN:-240}"

if [ -d "$DATA" ]; then
  find "$DATA" -type f \( -path '*/videos/*' -o -path '*/documents/*' -o -path '*/animations/*' \
       -o -path '*/video_notes/*' -o -path '*/photos/*' -o -path '*/audios/*' -o -path '*/voice/*' \
       -o -path '*/temp/*' \) -mmin "+$MEDIA_MIN" -delete 2>/dev/null
fi
find "$TMP" -maxdepth 1 -type d -name 'trad_*' -mmin "+$WORK_MIN" -exec rm -rf {} + 2>/dev/null
exit 0

#!/usr/bin/env bash
# Instala y deja corriendo 24/7 el bot de Telegram en Ubuntu (AWS Lightsail/EC2).
# Uso (desde la carpeta del repo, con tu usuario normal, no root):
#     bash deploy/install.sh
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Ejecuta este script con tu usuario normal (ubuntu), no como root." >&2
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(id -un)"
SERVICE=traductor-bot
cd "$APP_DIR"

echo "==> Instalando paquetes del sistema (ffmpeg, python, fuentes)..."
sudo apt-get update -y
sudo apt-get install -y ffmpeg python3 python3-venv python3-pip git \
    fonts-liberation fonts-dejavu-core fonts-noto-cjk fontconfig

bash deploy/fonts.sh
echo "==> Creando entorno virtual e instalando dependencias..."
python3 -m venv .venv-bot
.venv-bot/bin/pip install -q --upgrade pip
.venv-bot/bin/pip install -q -r requirements-bot.txt

if [ ! -f config.json ]; then
  echo "==> Configuración inicial (se guarda en config.json, solo legible por ti)"
  read -rp  "Token del bot (@BotFather): " TG_TOKEN
  read -rsp "API key de Deepgram: " DG_KEY; echo
  read -rp  "Tu id de Telegram (ver con /start; Enter para dejarlo abierto): " TG_USER
  read -rp  "API key de Groq (opcional, Enter para omitir): " GQ_KEY
  TG_TOKEN="$TG_TOKEN" DG_KEY="$DG_KEY" TG_USER="$TG_USER" GQ_KEY="$GQ_KEY" \
  python3 - <<'PY'
import json, os
json.dump({
    "deepgram_api_key": os.environ["DG_KEY"],
    "groq_api_key": os.environ["GQ_KEY"],
    "telegram_bot_token": os.environ["TG_TOKEN"],
    "telegram_allowed_users": os.environ["TG_USER"],
}, open("config.json", "w"), indent=2)
PY
  chmod 600 config.json
fi

echo "==> Creando servicio systemd ($SERVICE)..."
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<UNIT
[Unit]
Description=Bot de Telegram - Traductor de videos
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv-bot/bin/python $APP_DIR/telegram_bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Nice=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE
sleep 3
sudo systemctl --no-pager status $SERVICE | head -n 12 || true

cat <<MSG

Listo. El bot corre 24/7 y arranca solo al reiniciar el servidor.
  Ver logs en vivo : journalctl -u $SERVICE -f
  Reiniciar        : sudo systemctl restart $SERVICE
  Detener          : sudo systemctl stop $SERVICE
  Actualizar       : bash deploy/update.sh
MSG

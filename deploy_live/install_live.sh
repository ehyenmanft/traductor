#!/usr/bin/env bash
# Instala el SERVIDOR de traducción en vivo en AWS, AISLADO del bot de Telegram:
#   • carpeta propia (la de este repositorio, p. ej. ~/traductor-live)
#   • entorno virtual propio (.venv-live) y dependencias propias
#   • servicio propio (traductor-live) con tope de memoria/CPU para no afectar al bot
# Solo LEE el config.json del bot para tomar las claves; no lo modifica.
#
# Uso (con tu usuario normal, no root):   bash deploy_live/install_live.sh
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then echo "Ejecútalo con tu usuario normal (ubuntu), no como root." >&2; exit 1; fi
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(id -un)"
SERVICE=traductor-live
PORT=8765
cd "$APP_DIR"

echo "==> Instalando Python y dependencias (entorno propio .venv-live)..."
sudo apt-get update -y >/dev/null
sudo apt-get install -y python3 python3-venv python3-pip curl >/dev/null
python3 -m venv .venv-live
.venv-live/bin/pip install -q --upgrade pip
.venv-live/bin/pip install -q -r requirements-live-server.txt

# ---- claves: se leen del config.json del bot (no se duplican ni se copian)
KEYS="${KEYS_FILE:-$HOME/traductor/config.json}"
if [ ! -f "$KEYS" ]; then
  read -rp "Ruta del config.json que tiene deepgram_api_key (y groq_api_key): " KEYS
fi
[ -f "$KEYS" ] || { echo "No existe $KEYS" >&2; exit 1; }
python3 - "$KEYS" <<'PY'
import json, sys
k = json.load(open(sys.argv[1], encoding="utf-8")).get("deepgram_api_key", "")
if not k or k.upper().startswith("TU_"):
    sys.exit("Ese config.json no tiene una deepgram_api_key válida.")
PY

# ---- configuración del servidor + token (solo si no existe: reinstalar no cambia el token)
if [ ! -f live_config.json ]; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  KEYS="$KEYS" TOKEN="$TOKEN" PORT="$PORT" python3 - <<'PY'
import json, os
json.dump({"host": "127.0.0.1", "port": int(os.environ["PORT"]), "token": os.environ["TOKEN"],
           "keys_file": os.environ["KEYS"], "max_session_minutes": 0},
          open("live_config.json", "w"), indent=2)
PY
  chmod 600 live_config.json
  echo "==> Token nuevo generado en live_config.json"
fi

echo "==> Creando el servicio systemd ($SERVICE)..."
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<UNIT
[Unit]
Description=Traductor en vivo - servidor (WebSocket)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv-live/bin/python $APP_DIR/live_server.py --live-config $APP_DIR/live_config.json
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
# para no quitarle recursos al bot de Telegram que comparte la instancia
Nice=10
MemoryMax=600M
CPUQuota=80%
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE
sleep 4

echo "==> Comprobando el servidor..."
if .venv-live/bin/python deploy_live/selfcheck.py live_config.json; then :; else
  echo "El servidor no respondió. Logs:  journalctl -u $SERVICE -n 30 --no-pager" >&2
  exit 1
fi

IP="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
TOKEN="$(python3 -c "import json; print(json.load(open('live_config.json'))['token'])")"
cat <<MSG

======================================================================
 Servidor listo (solo escucha en 127.0.0.1: no hay puertos nuevos abiertos).
 Datos para el cliente de la PC (config.json junto al .exe):

 {
   "server": "ws://127.0.0.1:$PORT",
   "token": "$TOKEN",
   "ssh": { "host": "${IP:-TU_IP_DE_AWS}", "user": "$APP_USER", "key": "RUTA\\\\A\\\\TU\\\\CLAVE.pem" }
 }

 ⚠️  El token es como una contraseña: no lo compartas ni lo pegues en chats.
 Ver logs:     journalctl -u $SERVICE -f
 Reiniciar:    sudo systemctl restart $SERVICE
 Actualizar:   bash deploy_live/update_live.sh
 Desinstalar:  bash deploy_live/uninstall_live.sh
======================================================================
MSG

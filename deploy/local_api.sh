#!/usr/bin/env bash
# Instala el servidor local de la Bot API de Telegram (en Docker) para que el bot
# pueda recibir videos de hasta 2 GB (en vez de 20 MB) y enviar hasta ~2 GB.
#
# Antes necesitas api_id y api_hash: entra a https://my.telegram.org → "API
# development tools" → crea una app (el nombre da igual).
#
# Uso:  bash deploy/local_api.sh
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then echo "Ejecútalo con tu usuario normal (ubuntu), no root." >&2; exit 1; fi
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
[ -f config.json ] || { echo "Falta config.json (corre primero deploy/install.sh)"; exit 1; }
TOKEN="$(python3 -c "import json;print(json.load(open('config.json'))['telegram_bot_token'])")"

# Se pueden pasar por variables:  API_ID=123 API_HASH=abc YES=1 bash deploy/local_api.sh
API_ID="${API_ID:-}"; API_HASH="${API_HASH:-}"
[ -n "$API_ID" ]   || read -rp "api_id: " API_ID
[ -n "$API_HASH" ] || read -rp "api_hash: " API_HASH
API_HASH="$(echo "$API_HASH" | tr -d '[:space:]')"; API_ID="$(echo "$API_ID" | tr -d '[:space:]')"
[ -n "$API_ID" ] && [ -n "$API_HASH" ] || { echo "Faltan api_id/api_hash" >&2; exit 1; }
DATA=/var/lib/telegram-bot-api

echo "==> Instalando Docker..."
sudo apt-get update -y >/dev/null
sudo apt-get install -y docker.io curl >/dev/null
sudo systemctl enable --now docker
sudo mkdir -p "$DATA"
sudo chown "$(id -u):$(id -g)" "$DATA"

echo "==> Deteniendo el bot y cerrando su sesión en la nube de Telegram..."
echo "    (Telegram exige hacerlo una vez; para volver a la nube hay que esperar ~10 min)"
OK="${YES:+s}"
[ -n "$OK" ] || read -rp "¿Continuar? [s/N] " OK
[ "${OK,,}" = "s" ] || { echo "Cancelado."; exit 0; }
sudo systemctl stop traductor-bot || true
# si ya estaba cerrada (por un intento anterior) Telegram responde error: no importa
curl -sS "https://api.telegram.org/bot${TOKEN}/logOut" || true; echo

echo "==> Arrancando el servidor local..."
IMG=aiogram/telegram-bot-api:latest
sudo docker pull -q "$IMG" >/dev/null
sudo docker rm -f telegram-bot-api >/dev/null 2>&1 || true
# Se ejecuta el binario directamente (saltando el entrypoint de la imagen, que
# intenta hacer chown y falla sin root) con TU usuario: así los archivos que
# descarga quedan a tu nombre y el bot puede leerlos, moverlos y borrarlos.
BIN="$(sudo docker run --rm --entrypoint sh "$IMG" -c 'command -v telegram-bot-api' || true)"
BIN="${BIN:-/usr/local/bin/telegram-bot-api}"
sudo docker run -d --name telegram-bot-api --restart always \
  --user "$(id -u):$(id -g)" --entrypoint "$BIN" -p 127.0.0.1:8081:8081 \
  -v "$DATA:$DATA" "$IMG" \
  --local --api-id="$API_ID" --api-hash="$API_HASH" \
  --dir="$DATA" --temp-dir=/tmp --http-port=8081 >/dev/null
sleep 6

if curl -fsS "http://127.0.0.1:8081/bot${TOKEN}/getMe" | grep -q '"ok":true'; then
  echo "==> Servidor local OK. Configurando el bot..."
  python3 - <<'PY'
import json
d = json.load(open("config.json")); d["telegram_api_url"] = "http://127.0.0.1:8081"
json.dump(d, open("config.json", "w"), indent=2)
PY
  chmod 600 config.json
  sudo systemctl start traductor-bot
  sleep 3
  journalctl -u traductor-bot -n 5 --no-pager
  echo
  echo "Listo: ya puedes enviar videos de hasta 2 GB directamente al bot."
else
  echo "[ERROR] El servidor local no respondió. Últimos logs:" >&2
  sudo docker logs --tail 20 telegram-bot-api >&2 || true
  echo "El bot quedó detenido. Para volver a la nube (espera ~10 min tras el logOut):" >&2
  echo "  sudo systemctl start traductor-bot" >&2
  exit 1
fi

cat <<'MSG'

Para volver al modo nube (límite de 20 MB):
  1) quita "telegram_api_url" de config.json
  2) sudo docker rm -f telegram-bot-api
  3) sudo systemctl restart traductor-bot   (si falla, espera ~10 min tras el logOut)
MSG

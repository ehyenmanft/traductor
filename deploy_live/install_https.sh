#!/usr/bin/env bash
# Publica el servidor de traducción en vivo por HTTPS (wss://) para que otras personas se conecten
# SIN clave SSH: Caddy recibe en el 443 con certificado de Let's Encrypt y reenvía a 127.0.0.1:8765.
# El servidor sigue sin escuchar en internet; solo Caddy lo hace. No toca el bot.
#
# Antes: 1) bash deploy_live/install_live.sh   (servidor instalado)
#        2) Lightsail → la instancia → Redes: IP estática asociada y puertos 80 y 443 (TCP) abiertos
# Uso:   bash deploy_live/install_https.sh [dominio]     (sin dominio usa IP.sslip.io, gratis)
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then echo "Ejecútalo con tu usuario normal (ubuntu), no como root." >&2; exit 1; fi
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f live_config.json ] || { echo "Primero instala el servidor: bash deploy_live/install_live.sh" >&2; exit 1; }
PORT="$(python3 -c "import json; print(json.load(open('live_config.json')).get('port', 8765))")"

IP="$(curl -fsS --max-time 5 https://checkip.amazonaws.com | tr -d '[:space:]')"
DOMAIN="${1:-${IP//./-}.sslip.io}"                  # 3.147.64.254 → 3-147-64-254.sslip.io
echo "==> Dirección pública: $DOMAIN"

for p in 80 443; do                                   # ¿otro programa (nginx, apache…) ocupa el puerto?
  owner="$(sudo ss -ltnpH "sport = :$p" | grep -o 'users:(("[^"]*' | cut -d'"' -f2 | head -1 || true)"
  if [ -n "$owner" ] && [ "$owner" != "caddy" ]; then
    echo "El puerto $p lo usa '$owner'. Libéralo o pon este servidor detrás de él." >&2; exit 1
  fi
done

if ! command -v caddy >/dev/null; then
  echo "==> Instalando Caddy (repositorio oficial)..."
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y >/dev/null
  sudo apt-get install -y caddy >/dev/null
fi

echo "==> Configurando Caddy..."
sudo tee /etc/caddy/traductor-live.caddy >/dev/null <<CADDY
$DOMAIN {
	reverse_proxy 127.0.0.1:$PORT
}
CADDY
# Caddyfile de fábrica (página de prueba en :80) → se reemplaza; si ya tenía sitios tuyos, solo se añade el import
if grep -q "/usr/share/caddy" /etc/caddy/Caddyfile 2>/dev/null || [ ! -s /etc/caddy/Caddyfile ]; then
  echo "import /etc/caddy/traductor-live.caddy" | sudo tee /etc/caddy/Caddyfile >/dev/null
elif ! grep -q "traductor-live.caddy" /etc/caddy/Caddyfile; then
  echo "import /etc/caddy/traductor-live.caddy" | sudo tee -a /etc/caddy/Caddyfile >/dev/null
fi
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
sudo systemctl enable caddy >/dev/null 2>&1
sudo systemctl reload-or-restart caddy

python3 - "wss://$DOMAIN" <<'PY'
import json, os, sys
cfg = json.load(open("live_config.json", encoding="utf-8"))
cfg["public_url"] = sys.argv[1]
json.dump(cfg, open("live_config.json", "w", encoding="utf-8"), indent=2)
os.chmod("live_config.json", 0o600)
PY

echo "==> Comprobando HTTPS (el certificado puede tardar ~30 s la primera vez)..."
ok=""
for _ in $(seq 1 12); do
  if .venv-live/bin/python - "wss://$DOMAIN" 2>/dev/null <<'PY'   # mientras sale el certificado falla: sin ruido
import json, sys, websocket
cfg = json.load(open("live_config.json"))
ws = websocket.create_connection(sys.argv[1], timeout=8)
ws.send(json.dumps({"type": "hello", "version": 1, "token": cfg["token"]}))
assert json.loads(ws.recv())["type"] == "ready"
ws.close()
PY
  then ok=1; break; fi
  sleep 5
done
if [ -z "$ok" ]; then
  echo "No responde por https. ¿Abriste los puertos 80 y 443 en Lightsail? Logs: journalctl -u caddy -n 30 --no-pager" >&2
  exit 1
fi

cat <<MSG

======================================================================
 Listo: el servidor responde en  wss://$DOMAIN
 Crea un usuario por persona (cada uno con su token):

   .venv-live/bin/python deploy_live/users.py add amigo --minutes 120
   .venv-live/bin/python deploy_live/users.py list
   .venv-live/bin/python deploy_live/users.py remove amigo

 Si cambia la IP de la instancia, vuelve a ejecutar este script (usa IP estática).
======================================================================
MSG

#!/usr/bin/env bash
# Quita el servicio del servidor de traducción en vivo. No toca el bot de Telegram.
set -euo pipefail
sudo systemctl disable --now traductor-live 2>/dev/null || true
sudo rm -f /etc/systemd/system/traductor-live.service
sudo systemctl daemon-reload
echo "Servicio eliminado. (La carpeta y live_config.json siguen ahí; bórralos a mano si quieres.)"

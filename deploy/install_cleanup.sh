#!/usr/bin/env bash
# Instala la limpieza automática (cron, cada 30 min). Es idempotente.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
chmod +x "$APP_DIR/deploy/cleanup.sh"
echo "*/30 * * * * root $APP_DIR/deploy/cleanup.sh >/dev/null 2>&1" | sudo tee /etc/cron.d/traductor-cleanup >/dev/null
sudo chmod 644 /etc/cron.d/traductor-cleanup
echo "Limpieza automática instalada (cada 30 min)."

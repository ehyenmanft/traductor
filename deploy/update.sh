#!/usr/bin/env bash
# Baja la última versión del código y reinicia el bot.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git pull --ff-only
.venv-bot/bin/pip install -q -r requirements-bot.txt
bash deploy/fonts.sh
bash deploy/install_cleanup.sh
sudo systemctl restart traductor-bot
sleep 2
sudo systemctl --no-pager status traductor-bot | head -n 8

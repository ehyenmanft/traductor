#!/usr/bin/env bash
# Actualiza el servidor de traducción en vivo (no toca el bot ni el token).
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git pull --ff-only
.venv-live/bin/pip install -q -r requirements-live-server.txt
sudo systemctl restart traductor-live
sleep 3
.venv-live/bin/python deploy_live/selfcheck.py live_config.json

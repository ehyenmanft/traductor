#!/usr/bin/env bash
# Instala las skills de video/contenido en Claude Code (~/.claude/skills) para que TODOS los bots de Claude
# de este usuario (tmux «general», «amazona»…) las tengan. Idempotente: vuelve a ejecutarlo tras cada update.
#   bash deploy/install_skills.sh            instala
#   bash deploy/install_skills.sh --restart  instala y reinicia las sesiones de Claude para que las carguen
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
dest="$HOME/.claude/skills"
mkdir -p "$dest"
for d in claude_skills/*/; do
  name="$(basename "$d")"
  rm -rf "$dest/$name"
  cp -r "$d" "$dest/$name"
  echo "✔ skill instalada: $name"
done

if [[ -x .venv-bot/bin/python ]]; then
  .venv-bot/bin/python -c "import PIL" 2>/dev/null || { echo "instalando Pillow…"; .venv-bot/bin/pip install -q -r requirements-bot.txt; }
  .venv-bot/bin/python cli_video.py styles >/dev/null && echo "✔ cli_video.py funciona"
else
  echo "[aviso] no encuentro .venv-bot: las skills necesitan el entorno del bot de traducción (bash deploy/install.sh)"
fi

if [[ "${1:-}" == "--restart" ]]; then
  if pgrep -f "claude --channels" >/dev/null; then
    echo "Reiniciando las sesiones de Claude (se relanzan solas en ~10 s; se corta la conversación en curso)…"
    pkill -f "claude --channels" || true
  else
    echo "No hay sesiones de Claude con canales en ejecución."
  fi
else
  echo "Para que las sesiones ya abiertas las carguen: bash deploy/install_skills.sh --restart"
fi

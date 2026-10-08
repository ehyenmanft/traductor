#!/usr/bin/env bash
# Instala las fuentes que usan los estilos de subtítulos (idempotente).
set -uo pipefail
sudo apt-get install -y fonts-liberation fonts-dejavu-core fonts-noto-cjk fonts-roboto fontconfig >/dev/null
if ! fc-list | grep -qi "anton"; then
  sudo apt-get install -y fonts-anton >/dev/null 2>&1 || {
    mkdir -p "$HOME/.fonts"
    curl -fsSL -o "$HOME/.fonts/Anton-Regular.ttf" \
      https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf \
      || echo "[aviso] no se pudo instalar Anton; el estilo Impacto usará otra fuente"
  }
fi
fc-cache -f >/dev/null 2>&1
fc-list | grep -qi anton && echo "fuentes OK (Anton incluida)" || echo "fuentes OK (sin Anton)"

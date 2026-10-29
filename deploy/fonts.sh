#!/usr/bin/env bash
# Instala las fuentes que usan los estilos de subtítulos (idempotente).
# Las de Google Fonts salen de deploy/fonts.list (una por línea: ruta|familia).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
sudo apt-get install -y fonts-liberation fonts-dejavu-core fonts-noto-cjk fonts-roboto fontconfig >/dev/null
mkdir -p "$HOME/.fonts"
ok=0; missing=()
while IFS='|' read -r path family; do
  [[ -z "${path// }" || "$path" == \#* ]] && continue
  file="$HOME/.fonts/$(basename "$path")"
  if [[ ! -s "$file" ]]; then
    curl -fsSL --retry 2 -o "$file.tmp" "https://raw.githubusercontent.com/google/fonts/main/$path" \
      && mv "$file.tmp" "$file" || { rm -f "$file.tmp"; }
  fi
  if [[ -s "$file" ]]; then ok=$((ok+1)); else missing+=("$family"); fi
done < fonts.list
fc-cache -f >/dev/null 2>&1
echo "fuentes OK: $ok instaladas"
if ((${#missing[@]})); then
  echo "[aviso] no se pudieron bajar: ${missing[*]} (esos estilos usarán otra fuente parecida)"
fi

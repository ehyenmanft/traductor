#!/usr/bin/env bash
# Lista los bots/servicios de este servidor SIN mostrar claves ni variables de entorno:
# servicios de systemd (estado, usuario, carpeta, comando), procesos de pm2/docker si existen y
# carpetas con aspecto de bot (git, lenguaje y si ya traen las skills de video del traductor).
# Solo lee; no cambia nada.
set -uo pipefail

line() { printf '%*s\n' 70 '' | tr ' ' '-'; }
has_skills() {   # ¿esa carpeta ya tiene las skills de video del traductor?
  local d="$1" out=""
  [[ -f "$d/subtitle_style.py" ]] && out+=" estilos"
  [[ -f "$d/content_tools.py" ]] && out+=" contenido(reel/carrusel)"
  [[ -f "$d/deploy/fonts.list" ]] && out+=" fuentes"
  grep -q '"kidcut"' "$d/subtitle_style.py" 2>/dev/null && out+=" KidCut"
  echo "${out:- ninguna}"
}
describe_dir() {
  local d="$1"
  [[ -d "$d" ]] || return
  echo "    carpeta   : $d"
  if [[ -d "$d/.git" ]]; then
    echo "    git       : $(git -C "$d" remote get-url origin 2>/dev/null | sed -E 's#(https?://)[^@/]*@#\1#') · rama $(git -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null) · $(git -C "$d" log -1 --format='%h %cs' 2>/dev/null)"
  fi
  local lang=""
  [[ -f "$d/requirements.txt" || -f "$d/requirements-bot.txt" || -n "$(ls "$d"/*.py 2>/dev/null | head -1)" ]] && lang+="python "
  [[ -f "$d/package.json" ]] && lang+="node "
  echo "    lenguaje  : ${lang:-?}"
  echo "    skills    :$(has_skills "$d")"
}

echo "SERVIDOR: $(hostname) · $(whoami) · $(date '+%F %T')"
line
echo "1) SERVICIOS DE SYSTEMD (los que instalaste tú, no los del sistema)"
line
if command -v systemctl >/dev/null 2>&1; then
  mapfile -t units < <(ls /etc/systemd/system/*.service 2>/dev/null | xargs -r -n1 basename | grep -Ev '^(cloud|snap|getty|serial|ssh|multi-user|sys|dbus|polkit)')
  if ((${#units[@]} == 0)); then echo "  (ninguno)"; fi
  for u in "${units[@]}"; do
    active=$(systemctl is-active "$u" 2>/dev/null); enabled=$(systemctl is-enabled "$u" 2>/dev/null)
    wd=$(systemctl show "$u" -p WorkingDirectory --value 2>/dev/null)
    usr=$(systemctl show "$u" -p User --value 2>/dev/null)
    cmd=$(systemctl show "$u" -p ExecStart --value 2>/dev/null | sed -E 's/.*argv\[\]=([^;]*) ;.*/\1/' | cut -c1-110)
    printf '  • %-28s %s / %s  (usuario: %s)\n' "$u" "$active" "$enabled" "${usr:-root}"
    echo "    comando   : ${cmd:-?}"
    [[ -n "$wd" && "$wd" != "[not set]" ]] && describe_dir "$wd"
    echo
  done
else
  echo "  (este equipo no usa systemd)"
fi

echo "2) PM2 / DOCKER (si existen)"
line
if command -v pm2 >/dev/null 2>&1; then pm2 jlist 2>/dev/null | python3 -c "
import sys, json
try:
    for p in json.load(sys.stdin):
        print('  • pm2 %-20s %s  %s' % (p['name'], p['pm2_env'].get('status'), p['pm2_env'].get('pm_cwd', '')))
except Exception:
    print('  (pm2 sin procesos)')"; else echo "  pm2: no instalado"; fi
if command -v docker >/dev/null 2>&1; then docker ps --format '  • docker {{.Names}}  {{.Image}}  {{.Status}}' 2>/dev/null || echo "  docker: sin permiso (prueba con sudo)"; else echo "  docker: no instalado"; fi
echo

echo "3) CARPETAS CON ASPECTO DE BOT en $HOME (hasta 3 niveles)"
line
found=0
while IFS= read -r d; do
  found=1; echo "  • $(basename "$d")"; describe_dir "$d"; echo
done < <(find "$HOME" -maxdepth 3 -type d \( -name node_modules -o -name '.venv*' -o -name '.cache' -o -name '.git' \) -prune -o \
  -type f \( -name 'telegram_bot.py' -o -name 'bot.py' -o -name 'main.py' -o -name 'index.js' -o -name 'bot.js' \) -print 2>/dev/null \
  | xargs -r -n1 dirname | sort -u)
((found)) || echo "  (ninguna)"
echo
echo "Fuentes instaladas en este usuario (~/.fonts): $(ls "$HOME/.fonts" 2>/dev/null | wc -l) archivos"
echo "Copia esta salida (no incluye claves) y pégamela para integrar las skills en cada bot."

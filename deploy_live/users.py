"""Usuarios del servidor de traducción en vivo (cada uno con su token). El servidor relee la lista en
cada conexión: no hace falta reiniciar.

  python deploy_live/users.py list
  python deploy_live/users.py add amigo [--minutes 120]     (120 min/día; 0 = sin cupo)
  python deploy_live/users.py remove amigo                  (su token deja de valer al instante)
"""
import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "live_config.json")


def load() -> dict:
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)


def save(cfg: dict):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PATH)


def used_today(name: str) -> float:
    try:
        with open(os.path.join(HERE, "usage.json"), encoding="utf-8") as f:
            usage = json.load(f)
        return usage.get(datetime.now(timezone.utc).strftime("%Y-%m-%d"), {}).get(name, 0) / 60
    except (OSError, ValueError):
        return 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Usuarios del traductor en vivo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--minutes", type=float, default=0, help="cupo diario en minutos (0 = sin cupo)")
    r = sub.add_parser("remove")
    r.add_argument("name")
    args = ap.parse_args(argv)

    cfg = load()
    users = cfg.setdefault("users", {})
    if args.cmd == "list":
        print(f"principal   (tu token de live_config.json)   hoy: {used_today('principal'):.0f} min")
        for name, u in users.items():
            quota = f"{u.get('daily_minutes', 0):g} min/día" if u.get("daily_minutes") else "sin cupo"
            print(f"{name:<11} {quota:<32} hoy: {used_today(name):.0f} min")
        return 0
    if args.name == "principal" or not args.name.replace("-", "").replace("_", "").isalnum():
        print("Nombre inválido: usa letras, números, - o _ (y no 'principal').", file=sys.stderr)
        return 1
    if args.cmd == "remove":
        if users.pop(args.name, None) is None:
            print(f"No existe el usuario {args.name}.", file=sys.stderr)
            return 1
        save(cfg)
        print(f"Usuario {args.name} eliminado: su token ya no sirve.")
        return 0

    token = secrets.token_urlsafe(24)
    users[args.name] = {"token": token, "daily_minutes": args.minutes}
    save(cfg)
    server = cfg.get("public_url") or "wss://TU_SERVIDOR"
    print(f"""Usuario {args.name} creado ({f'{args.minutes:g} min/día' if args.minutes else 'sin cupo'}).
Pásale estos dos datos (el instalador se los pide):

  Servidor: {server}
  Token:    {token}

El token es personal: si se filtra, bórralo con  python deploy_live/users.py remove {args.name}""")
    return 0


if __name__ == "__main__":
    sys.exit(main())

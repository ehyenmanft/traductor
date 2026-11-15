"""Usuarios del servidor de traducción en vivo (usuario + clave). El servidor relee la lista en cada
conexión: no hace falta reiniciar. Lo normal es gestionarlos desde la APK con tu superusuario;
esto es la vía de terminal (y la única para crear el primer superusuario).

  python deploy_live/users.py superuser ehyenma            (crea o convierte en admin; pide la clave)
  python deploy_live/users.py list
  python deploy_live/users.py add amigo [--minutes 120] [--admin]
  python deploy_live/users.py passwd amigo
  python deploy_live/users.py disable amigo  |  enable amigo
  python deploy_live/users.py remove amigo
"""
import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import live_users as U  # noqa: E402

PATH = os.path.join(HERE, "live_config.json")


def used_today(name: str) -> float:
    try:
        with open(os.path.join(HERE, "usage.json"), encoding="utf-8") as f:
            usage = json.load(f)
        return usage.get(datetime.now(timezone.utc).strftime("%Y-%m-%d"), {}).get(name, 0) / 60
    except (OSError, ValueError):
        return 0.0


def ask_password(ask) -> str:
    pw = ask("Clave nueva (no se ve al escribir): ")
    if pw != ask("Repite la clave: "):
        raise ValueError("Las claves no coinciden.")
    return U.check_password(pw)


def main(argv=None, ask=getpass.getpass) -> int:
    ap = argparse.ArgumentParser(description="Usuarios del traductor en vivo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for cmd in ("superuser", "passwd", "enable", "disable", "remove"):
        sub.add_parser(cmd).add_argument("name")
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--minutes", type=float, default=0, help="cupo diario en minutos (0 = sin cupo)")
    a.add_argument("--admin", action="store_true", help="puede gestionar usuarios desde la APK")
    args = ap.parse_args(argv)

    cfg = U.load(PATH)
    users = cfg.setdefault("users", {})
    try:
        if args.cmd == "list":
            for name, u in sorted(users.items()):
                flags = ("admin " if u.get("admin") else "") + ("" if u.get("enabled", True) else "DESHABILITADO ")
                login = "usuario+clave" if u.get("password") else "solo token (sin clave)"
                quota = f"{u.get('daily_minutes', 0):g} min/día" if u.get("daily_minutes") else "sin cupo"
                print(f"{name:<14} {login:<24} {quota:<14} {flags:<20} hoy: {used_today(name):.0f} min")
            return 0
        name = U.check_name(args.name)
        if args.cmd == "superuser":
            pw = ask_password(ask)
            if name in users:
                U.set_password(cfg, name, pw)
                users[name].update(admin=True, enabled=True)
            else:
                U.add_user(cfg, name, pw, admin=True)
            msg = f"{name} es superusuario: entra en la APK con este usuario y su clave."
        elif args.cmd == "add":
            U.add_user(cfg, name, ask_password(ask), args.minutes, args.admin)
            msg = f"Usuario {name} creado. Pásale su usuario y su clave (por separado)."
        elif args.cmd == "passwd":
            U.set_password(cfg, name, ask_password(ask))
            msg = f"Clave de {name} cambiada."
        elif args.cmd in ("enable", "disable"):
            U.set_enabled(cfg, name, args.cmd == "enable")
            msg = f"{name} {'habilitado' if args.cmd == 'enable' else 'deshabilitado: ya no puede entrar'}."
        else:
            U.delete_user(cfg, name)
            msg = f"Usuario {name} eliminado."
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    U.save(PATH, cfg)
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

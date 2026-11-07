"""
Cuentas del servidor en vivo (usuario + clave), compartidas por el servidor (panel de la APK) y
deploy_live/users.py (terminal). Viven en "users" de live_config.json:

  "users": {"ana": {"password": "pbkdf2$…", "enabled": true, "admin": false, "daily_minutes": 120}}

Las claves se guardan con PBKDF2-SHA256 (no se pueden recuperar, solo comprobar). Los tokens de
versiones anteriores ("token") siguen valiendo mientras exista el usuario.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets

ITERATIONS = 200_000
MIN_PASSWORD = 8
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{2,32}$")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(calc.hex(), digest)
    except (ValueError, AttributeError):
        return False


def check_name(name: str) -> str:
    name = str(name or "").strip()
    if name == "principal" or not NAME_RE.match(name):
        raise ValueError("Nombre inválido: 2 a 32 letras, números, - o _ (sin espacios ni tildes).")
    return name


def check_password(password: str) -> str:
    password = str(password or "")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"La clave debe tener al menos {MIN_PASSWORD} caracteres.")
    return password


# ---------- archivo ----------

def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: str, cfg: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


# ---------- cambios (sobre el dict de live_config.json) ----------

def _user(cfg: dict, name: str) -> dict:
    u = cfg.setdefault("users", {}).get(name)
    if not isinstance(u, dict):
        raise ValueError(f"No existe el usuario {name}.")
    return u


def _admins(cfg: dict) -> list[str]:
    return [n for n, u in cfg.get("users", {}).items()
            if isinstance(u, dict) and u.get("admin") and u.get("enabled", True) and u.get("password")]


def add_user(cfg: dict, name: str, password: str, minutes: float = 0, admin: bool = False):
    name = check_name(name)
    if name in cfg.setdefault("users", {}):
        raise ValueError(f"El usuario {name} ya existe.")
    cfg["users"][name] = {"password": hash_password(check_password(password)), "enabled": True,
                          "admin": bool(admin), "daily_minutes": max(0.0, float(minutes or 0))}


def set_password(cfg: dict, name: str, password: str):
    _user(cfg, name)["password"] = hash_password(check_password(password))


def set_enabled(cfg: dict, name: str, enabled: bool, actor: str = ""):
    u = _user(cfg, name)
    if not enabled and name == actor:
        raise ValueError("No puedes deshabilitarte a ti mismo.")
    if not enabled and u.get("admin") and _admins(cfg) == [name]:
        raise ValueError("Es el único administrador: no se puede deshabilitar.")
    u["enabled"] = bool(enabled)


def set_minutes(cfg: dict, name: str, minutes: float):
    _user(cfg, name)["daily_minutes"] = max(0.0, float(minutes or 0))


def set_admin(cfg: dict, name: str, admin: bool, actor: str = ""):
    u = _user(cfg, name)
    if not admin and (name == actor or _admins(cfg) == [name]):
        raise ValueError("No puedes quitarte el rol de administrador (te quedarías sin acceso al panel).")
    u["admin"] = bool(admin)


def delete_user(cfg: dict, name: str, actor: str = ""):
    u = _user(cfg, name)
    if name == actor:
        raise ValueError("No puedes borrarte a ti mismo.")
    if u.get("admin") and _admins(cfg) == [name]:
        raise ValueError("Es el único administrador: no se puede borrar.")
    del cfg["users"][name]


def public_list(cfg: dict, used_minutes, online) -> list[dict]:
    """Lo que ve el panel: nunca claves ni tokens."""
    out = []
    for name, u in sorted(cfg.get("users", {}).items()):
        if not isinstance(u, dict):
            continue
        out.append({"name": name, "enabled": bool(u.get("enabled", True)), "admin": bool(u.get("admin")),
                    "daily_minutes": float(u.get("daily_minutes") or 0), "has_password": bool(u.get("password")),
                    "used_today": round(used_minutes(name), 1), "online": name in online})
    return out

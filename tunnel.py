"""
Túnel SSH hacia el servidor de AWS, gestionado por el propio cliente.

Windows 10/11 ya incluyen el cliente OpenSSH (ssh.exe): el túnel reenvía un
puerto local al puerto del servidor, así el audio y el token viajan cifrados y
NO hay que abrir ningún puerto nuevo en AWS (solo el 22 que ya usas).

  ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 -i clave.pem ubuntu@IP

Si el túnel se cae, se vuelve a levantar solo.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_key(spec: str, base_dir: str) -> str:
    """Localiza la clave SSH. `spec` es lo que diga "key" en config.json:

      "auto" (o ausente)  → la única clave .pem que haya junto al programa
      "mi_clave.pem"      → ese archivo, buscado junto al programa
      "C:/ruta/clave.pem" → esa ruta (admite ~ y %VARIABLES%); si no existe pero hay un archivo con
                            el MISMO NOMBRE junto al programa, se usa ese (típico: ruta mal escrita)
    """
    spec = (spec or "").strip()
    if not spec or spec.lower() == "auto":
        pems = sorted(glob.glob(os.path.join(base_dir, "*.pem")))
        if len(pems) == 1:
            return pems[0]
        if not pems:
            raise FileNotFoundError(
                f"No hay ninguna clave .pem junto al programa ({base_dir}).\n"
                "Copia ahí tu clave, o pon su ruta en \"key\" de config.json.")
        raise FileNotFoundError(
            "Hay varias claves .pem junto al programa: "
            + ", ".join(os.path.basename(p) for p in pems)
            + ".\nIndica cuál usar en \"key\" de config.json.")
    path = os.path.expandvars(os.path.expanduser(spec))
    name = re.split(r"[\\/]", path)[-1]                      # nombre de archivo con / o \
    candidates = [path] if os.path.isabs(path) else [os.path.join(base_dir, path), os.path.abspath(path)]
    candidates.append(os.path.join(base_dir, name))           # mismo nombre junto al programa
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise FileNotFoundError(f"No encuentro la clave SSH: {spec}\n"
                            f"La busqué también en la carpeta del programa ({base_dir}).")


def _protect(path: str):
    """Deja el archivo legible SOLO para el usuario actual (OpenSSH lo exige)."""
    if sys.platform == "win32":
        user = os.environ.get("USERNAME", "")
        subprocess.run(["icacls", path, "/inheritance:r"], capture_output=True)
        if user:
            subprocess.run(["icacls", path, "/grant:r", f"{user}:R"], capture_output=True)
    else:
        os.chmod(path, 0o600)


def _unprotect_and_remove(path: str) -> bool:
    """Borra una copia anterior aunque se haya dejado de solo lectura (el 2.º arranque fallaba aquí)."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        pass
    try:                                                    # devolverle el control al usuario y reintentar
        if sys.platform == "win32":
            user = os.environ.get("USERNAME", "")
            if user:
                subprocess.run(["icacls", path, "/grant:r", f"{user}:F"], capture_output=True)
        else:
            os.chmod(path, 0o600)
        os.remove(path)
        return True
    except OSError:
        return False


def prepare_key(path: str, dest_dir: str) -> str:
    """OpenSSH de Windows rechaza claves legibles por otros usuarios ("UNPROTECTED PRIVATE KEY
    FILE"). Se usa una COPIA con permisos solo para ti; el archivo original no se toca.

    Es idempotente: si la copia ya existe y es idéntica se reutiliza tal cual; si cambió la clave se
    reemplaza; y si no se puede escribir encima se usa un nombre distinto (nunca debe impedir arrancar)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No encuentro la clave SSH: {path}")
    os.makedirs(dest_dir, exist_ok=True)
    with open(path, "rb") as f:
        data = f.read()
    dest = os.path.join(dest_dir, "clave_aws.pem")
    try:
        with open(dest, "rb") as f:
            if f.read() == data:
                return dest                                 # ya está lista (y protegida) de un arranque anterior
    except OSError:
        pass
    if not _unprotect_and_remove(dest):
        dest = os.path.join(dest_dir, f"clave_aws_{os.getpid()}.pem")   # no se pudo reemplazar: nombre nuevo
    with open(dest, "wb") as f:
        f.write(data)
    _protect(dest)
    for old in glob.glob(os.path.join(dest_dir, "clave_aws_*.pem")):    # limpieza de copias de arranques anteriores
        if old != dest:
            _unprotect_and_remove(old)
    return dest


class SshTunnel:
    def __init__(self, host: str, user: str = "ubuntu", key: str = "", local_port: int = 8765,
                 remote_port: int = 8765, ssh_exe: str | None = None, port: int = 22,
                 on_status=None, retry_seconds: float = 3.0):
        self.host, self.user, self.key = host, user, key
        self.local_port, self.remote_port, self.port = int(local_port), int(remote_port), int(port)
        self.ssh_exe = ssh_exe or shutil.which("ssh") or "ssh"
        self.on_status = on_status or (lambda s: None)
        self.retry_seconds = retry_seconds
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def command(self) -> list[str]:
        cmd = [self.ssh_exe, "-N",
               "-L", f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}",
               "-p", str(self.port),
               "-o", "StrictHostKeyChecking=accept-new",   # primera vez: guarda la huella; luego la verifica
               "-o", "ExitOnForwardFailure=yes",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
               "-o", "BatchMode=yes",                      # nunca pide contraseña (se colgaría sin ventana)
               "-o", "ConnectTimeout=10"]
        if self.key:
            cmd += ["-i", self.key, "-o", "IdentitiesOnly=yes"]
        return cmd + [f"{self.user}@{self.host}"]

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="ssh-tunnel")
        self._thread.start()

    def wait_ready(self, timeout: float = 20.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self._stop.is_set():
            if port_open(self.local_port):
                return True
            time.sleep(0.25)
        return False

    def stop(self):
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    def _run(self):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)       # sin ventana negra en Windows
        while not self._stop.is_set():
            self.on_status("starting")
            try:
                self._proc = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                              creationflags=flags)
            except FileNotFoundError:
                self.on_status("no-ssh")                         # Windows sin OpenSSH instalado
                return
            err = b""
            while self._proc.poll() is None and not self._stop.is_set():
                time.sleep(0.5)
            if self._proc.stderr:
                try:
                    err = self._proc.stderr.read(2000) or b""
                except Exception:  # noqa: BLE001
                    pass
            if self._stop.is_set():
                return
            msg = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["(sin detalle)"]
            self.on_status("failed: " + msg[0][:150])
            self._stop.wait(self.retry_seconds)

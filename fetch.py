"""
Descarga de videos desde un enlace (para saltarse el límite de 20 MB de
Telegram): archivos directos (.mp4, Dropbox, etc.) por HTTP y el resto
(YouTube, Google Drive, Vimeo, X…) con yt-dlp.

Por seguridad se rechazan direcciones privadas/locales (p. ej. la metadata de
AWS en 169.254.169.254) salvo allow_private=True (solo para pruebas).
"""
from __future__ import annotations

import glob
import ipaddress
import os
import re
import socket
from urllib.parse import parse_qs, unquote, urlparse

import requests

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".flv", ".mpeg", ".mpg", ".3gp")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_url(text: str) -> str | None:
    m = _URL.search(text or "")
    return m.group(0).rstrip(".,;)") if m else None


def normalize(url: str) -> str:
    """Dropbox ?dl=0 → ?dl=1; Drive /file/d/ID/view → descarga directa."""
    u = urlparse(url)
    if "dropbox.com" in u.netloc:
        q = parse_qs(u.query)
        q["dl"] = ["1"]
        return u._replace(query="&".join(f"{k}={v[0]}" for k, v in q.items())).geturl()
    m = re.search(r"drive\.google\.com/file/d/([\w-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def check_public(url: str):
    host = urlparse(url).hostname
    if not host:
        raise RuntimeError("Enlace inválido.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise RuntimeError(f"No pude resolver {host}.") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise RuntimeError("Ese enlace apunta a una dirección no permitida.")


def _direct(url: str, workdir: str, on_frac, max_bytes: int) -> str | None:
    """Descarga HTTP directa. Devuelve la ruta, o None si no parece un video."""
    with requests.get(url, stream=True, timeout=30, headers=UA, allow_redirects=True) as r:
        if r.status_code != 200:
            return None
        ctype = (r.headers.get("content-type") or "").lower()
        disp = unquote(r.headers.get("content-disposition", ""))
        name = urlparse(r.url).path.lower() + " " + disp.lower()
        is_video = ctype.startswith("video/") or any(e in name for e in VIDEO_EXTS)
        if not is_video:
            return None
        total = int(r.headers.get("content-length") or 0)
        if total > max_bytes:
            raise RuntimeError(f"El archivo pesa {total / 1e6:.0f} MB; el máximo es {max_bytes / 1e6:.0f} MB.")
        ext = next((e for e in VIDEO_EXTS if e in name), ".mp4")
        path = os.path.join(workdir, "entrada" + ext)
        got = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                got += len(chunk)
                if got > max_bytes:
                    raise RuntimeError("El archivo supera el tamaño máximo permitido.")
                f.write(chunk)
                if on_frac and total:
                    on_frac(got / total)
        return path


def _ytdlp(url: str, workdir: str, on_frac, max_bytes: int) -> str:
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError("Falta yt-dlp en el servidor (pip install yt-dlp).") from e

    def hook(d):
        if d.get("status") == "downloading" and on_frac:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                on_frac(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {"outtmpl": os.path.join(workdir, "entrada.%(ext)s"), "quiet": True,
            "no_warnings": True, "noplaylist": True, "max_filesize": max_bytes,
            "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "merge_output_format": "mp4", "progress_hooks": [hook]}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"No pude descargar ese enlace: {str(e)[:200]}") from e
    files = [f for f in glob.glob(os.path.join(workdir, "entrada.*"))
             if not f.endswith((".part", ".ytdl"))]
    if not files:
        raise RuntimeError("La descarga no produjo ningún archivo (¿supera el tamaño máximo?).")
    return max(files, key=os.path.getsize)


def download_url(url: str, workdir: str, on_frac=None, max_mb: int = 2000,
                 allow_private: bool = False) -> str:
    url = normalize(url)
    if not allow_private:
        check_public(url)
    max_bytes = max_mb * 1024 * 1024
    try:
        path = _direct(url, workdir, on_frac, max_bytes)
    except requests.RequestException:
        path = None
    return path or _ytdlp(url, workdir, on_frac, max_bytes)

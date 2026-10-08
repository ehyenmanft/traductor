"""
Protocolo entre el cliente de la PC y el servidor de AWS (WebSocket).

Cliente → servidor
  texto JSON  {"type":"hello", "version":1, "token":"…", "target":"es", "tone":"gamer",
               "lang":null, "dub":false, "dub_gender":"female", "dub_gate":true}
  texto JSON  {"type":"set", …campos a cambiar…}          (ver SET_FIELDS)
  texto JSON  {"type":"reload_glossary"}
  binario     audio PCM int16 little-endian, mono, 16 kHz (fragmentos de ~100 ms)

Servidor → cliente
  texto JSON  {"type":"ready", "engine":"deepgram", "version":1}
  texto JSON  {"type":"upsert", "uid":3, "text":"…", "lang":"en", "final":false}
  texto JSON  {"type":"trans",  "uid":3, "text":"…"}
  texto JSON  {"type":"gate", "muted":true}               (silenciar/activar la captura)
  texto JSON  {"type":"dub_stop"}                         (cortar la voz que suena)
  texto JSON  {"type":"notice", "message":"…"}
  texto JSON  {"type":"error", "message":"…", "code":4003, "fatal":true}   (fatal: no reintentar)
  binario     voz del doblaje: cabecera DUB_HEADER + PCM int16 mono

Sin dependencias (ni numpy ni Qt): lo usan servidor y cliente.
"""
from __future__ import annotations

import json
import struct

PROTOCOL_VERSION = 1
MAX_AUDIO_FRAME = 64 * 1024          # bytes por fragmento de audio (≈ 2 s): tope de cordura
MAX_TEXT_FRAME = 64 * 1024
DUB_MAGIC = b"DB"
DUB_HEADER = struct.Struct(">2sII")  # magia, uid, frecuencia
CLOSE_AUTH = 4003                    # token inválido
CLOSE_BUSY = 4009                    # otro cliente tomó la sesión (el cliente NO reintenta)
CLOSE_LIMIT = 4010                   # límite de tiempo de sesión (el cliente puede reconectar)
CLOSE_PROTOCOL = 4002                # mensaje inválido / versión incompatible

LANGS = {"es", "en", "pt", "fr", "de", "it", "ja", "ko", "zh-cn", "ru"}
TONES = {"gamer", "natural", "formal", "casual", "technical", "funny"}
GENDERS = {"female", "male"}


class ProtocolError(ValueError):
    pass


def encode(msg: dict) -> str:
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":"))


def decode(text: str | bytes) -> dict:
    if len(text) > MAX_TEXT_FRAME:
        raise ProtocolError("mensaje demasiado grande")
    try:
        msg = json.loads(text)
    except (ValueError, TypeError) as e:
        raise ProtocolError("JSON inválido") from e
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("falta 'type'")
    return msg


def pack_dub(uid: int, sr: int, pcm: bytes) -> bytes:
    return DUB_HEADER.pack(DUB_MAGIC, uid & 0xFFFFFFFF, sr) + pcm


def unpack_dub(data: bytes) -> tuple[int, int, bytes]:
    if len(data) < DUB_HEADER.size:
        raise ProtocolError("audio de doblaje truncado")
    magic, uid, sr = DUB_HEADER.unpack_from(data)
    if magic != DUB_MAGIC or not (8000 <= sr <= 96000):
        raise ProtocolError("cabecera de doblaje inválida")
    pcm = data[DUB_HEADER.size:]
    return uid, sr, pcm[: len(pcm) // 2 * 2]       # siempre un número entero de muestras int16


def _norm_lang(code) -> str | None:
    if not isinstance(code, str):
        return None
    c = code.lower().strip()
    c = "zh-cn" if c.startswith("zh") else c.split("-")[0]
    return c if c in LANGS else None


def clean_settings(msg: dict) -> dict:
    """Valida los ajustes que manda el cliente (hello/set). Lo inválido se ignora."""
    out: dict = {}
    if "target" in msg and _norm_lang(msg["target"]):
        out["target"] = _norm_lang(msg["target"])
    if msg.get("tone") in TONES:
        out["tone"] = msg["tone"]
    if "lang" in msg:
        out["lang"] = _norm_lang(msg["lang"])                # None = autodetectar
    for key in ("dub", "dub_gate"):
        if isinstance(msg.get(key), bool):
            out[key] = msg[key]
    if msg.get("dub_gender") in GENDERS:
        out["dub_gender"] = msg["dub_gender"]
    vol = msg.get("dub_volume")
    if isinstance(vol, (int, float)) and not isinstance(vol, bool):
        out["dub_volume"] = max(0.0, min(2.0, float(vol)))
    return out

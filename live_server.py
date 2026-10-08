"""
Servidor de traducción en vivo (corre en AWS). La PC solo manda audio y recibe
subtítulos (y voz de doblaje); las claves de Deepgram/Groq y todo el trabajo
pesado quedan aquí.

    audio PCM (WebSocket) → Deepgram nova-3 → traducción (Groq/Google, tono,
    glosario, contexto) → subtítulos + (opcional) voz edge-tts → de vuelta a la PC

Seguridad: escucha SOLO en 127.0.0.1 por defecto (se accede con un túnel SSH,
sin abrir puertos). Exige un token. Una sesión a la vez: un cliente nuevo
reemplaza al anterior (reconexión tras un corte de red).

Uso:  python live_server.py --live-config live_config.json
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import queue
import threading
from dataclasses import dataclass, field

import numpy as np
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import live_protocol as P
from live_dubber import EdgeSynth, LiveDubber
from live_translator import LiveTranslationWorker, LiveTranslator, norm_lang

log = logging.getLogger("live-server")
HELLO_TIMEOUT = 10.0
AUDIO_QUEUE_MAX = 600          # ≈ 60 s de audio pendiente: si Deepgram se atasca, se descarta lo viejo
OUT_QUEUE_MAX = 400


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = ""
    keys_file: str = ""                 # config.json con deepgram_api_key / groq_api_key / glossary…
    max_session_minutes: int = 0        # 0 = sin límite

    def keys(self) -> dict:
        """Claves y ajustes; se releen en cada sesión (cambiar una clave no exige reiniciar)."""
        try:
            with open(self.keys_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def load_config(path: str | None) -> ServerConfig:
    cfg = ServerConfig()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for k in ("host", "port", "token", "keys_file", "max_session_minutes"):
            if k in data:
                setattr(cfg, k, type(getattr(cfg, k))(data[k]))
    cfg.token = os.environ.get("LIVE_TOKEN", cfg.token)
    return cfg


# ------------------------------------------------------------------ fábricas (se sustituyen en las pruebas)

@dataclass
class Factories:
    transcriber: object = None      # (audio_queue, keys, lang) -> objeto con text_queue/start/stop
    translator: object = None       # (target, keys, tone) -> LiveTranslator
    synth: object = None            # () -> objeto con synthesize(text, voice, rate_pct)

    def __post_init__(self):
        self.transcriber = self.transcriber or _deepgram
        self.translator = self.translator or _translator
        self.synth = self.synth or EdgeSynth


def _deepgram(audio_q, keys, lang):
    from transcriber_deepgram import DeepgramTranscriber
    terms = list(keys.get("keyterms") or []) + list((keys.get("glossary") or {}))
    return DeepgramTranscriber(
        audio_q, api_key=keys["deepgram_api_key"], language=lang,
        endpointing_ms=int(keys.get("endpointing_ms", 300)),
        keyterms=list(dict.fromkeys(terms)))


def _translator(target, keys, tone):
    return LiveTranslator(target=target, groq_key=str(keys.get("groq_api_key", "")), tone=tone,
                          glossary=keys.get("glossary") or {},
                          context_size=int(keys.get("context_lines", 4)))


# ------------------------------------------------------------------ reproductor "remoto"

class SendPlayer:
    """El 'altavoz' del doblador en el servidor: manda el audio a la PC y espera lo que
    dura, para que la cola del doblador mantenga su ritmo y su lógica de atraso."""

    def __init__(self, session: "Session"):
        self.session = session
        self._n = 0

    def list_outputs(self):
        return []

    def play(self, pcm, sr, device, volume, cancel):
        self._n += 1
        self.session.emit(P.pack_dub(self._n, sr, np.asarray(pcm, dtype="<i2").tobytes()))
        if cancel.wait(len(pcm) / float(sr)):                  # True = cortaron la voz
            self.session.emit({"type": "dub_stop"})


# ------------------------------------------------------------------ sesión

class Session:
    """Una conexión de la PC: transcriptor + traductor + doblador + hilo de pipeline."""

    def __init__(self, loop, cfg: ServerConfig, keys: dict, settings: dict, factories: Factories):
        self.loop, self.cfg, self.keys, self.f = loop, cfg, keys, factories
        self.settings = {"target": "es", "tone": "gamer", "lang": None, "dub": False,
                         "dub_gender": "female", "dub_gate": True, **settings}
        self.out: asyncio.Queue = asyncio.Queue(maxsize=OUT_QUEUE_MAX)
        self.audio_q: queue.Queue = queue.Queue(maxsize=AUDIO_QUEUE_MAX)
        self.stop = threading.Event()
        self.closed = False

        st = self.settings
        self.transcriber = factories.transcriber(self.audio_q, keys, st["lang"])
        self.translator = factories.translator(st["target"], keys, st["tone"])
        self.worker = LiveTranslationWorker(
            self.translator, lambda uid, text: self.emit({"type": "trans", "uid": uid, "text": text}),
            self.stop, translate_partials=bool(keys.get("translate_partials", True)),
            on_final=self._on_final)
        self.dubber = LiveDubber(
            target=st["target"], synth=factories.synth(), player=SendPlayer(self),
            gender=st["dub_gender"], enabled=st["dub"], max_backlog=int(keys.get("dub_max_backlog", 2)),
            on_gate=lambda muted: self.emit({"type": "gate", "muted": bool(muted)}),
            on_notice=lambda m: self.emit({"type": "notice", "message": m}),
            on_state=lambda on: self.emit({"type": "notice", "message": "dub:" + ("on" if on else "off")}))
        self.dubber.gate_override = bool(st["dub_gate"])
        self.transcriber.start()
        threading.Thread(target=self._pipeline, daemon=True, name="pipeline").start()

    # ---------- salida (hilos → asyncio) ----------

    def emit(self, item):
        if self.closed:
            return
        try:
            self.loop.call_soon_threadsafe(self._put, item)
        except RuntimeError:                      # el bucle ya se cerró
            pass

    def _put(self, item):
        try:
            self.out.put_nowait(item)
        except asyncio.QueueFull:
            log.warning("cola de salida llena: descartado un mensaje")

    # ---------- pipeline ----------

    def _on_final(self, uid, text, src, lang):
        if text.strip() != src.strip():
            self.dubber.submit(uid, text)

    def _pipeline(self):
        while not self.stop.is_set():
            try:
                seg = self.transcriber.text_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self.emit({"type": "upsert", "uid": seg.utterance_id, "text": seg.text,
                       "lang": seg.language, "final": seg.is_final})
            if norm_lang(seg.language) == self.translator.target:
                self.emit({"type": "trans", "uid": seg.utterance_id, "text": seg.text})
            else:
                self.worker.submit(seg.utterance_id, seg.text, seg.language, seg.is_final)

    # ---------- entrada ----------

    def feed_audio(self, data: bytes):
        if len(data) > P.MAX_AUDIO_FRAME or len(data) < 2:
            return
        data = data[: len(data) // 2 * 2]
        try:
            self.audio_q.put_nowait(data)
        except queue.Full:
            try:                                  # mejor perder lo viejo que ir atrasado
                self.audio_q.get_nowait()
                self.audio_q.put_nowait(data)
            except (queue.Empty, queue.Full):
                pass

    def apply(self, msg: dict):
        s = P.clean_settings(msg)
        if "target" in s:
            self.translator.set_target(s["target"])
            self.dubber.set_target(s["target"])
        if "tone" in s:
            self.translator.set_tone(s["tone"])
        if "dub_gender" in s:
            self.dubber.set_gender(s["dub_gender"])
        if "dub_gate" in s:
            self.dubber.gate_override = s["dub_gate"]
        if "dub" in s:
            self.dubber.set_enabled(s["dub"])
        if "lang" in s and s["lang"] != self.settings.get("lang"):
            self.emit({"type": "notice", "message": "El idioma de origen se aplica al reconectar."})
        self.settings.update(s)

    def reload_glossary(self):
        self.keys = self.cfg.keys()
        self.translator.set_glossary(self.keys.get("glossary") or {})

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        for obj in (self.dubber, self.transcriber):
            try:
                obj.stop()
            except Exception:  # noqa: BLE001
                pass
        self.out.put_nowait(None) if not self.out.full() else None


# ------------------------------------------------------------------ servidor

class LiveServer:
    def __init__(self, cfg: ServerConfig, factories: Factories | None = None):
        if not cfg.token:
            raise SystemExit("Falta el token (live_config.json o LIVE_TOKEN): sin él no arranco.")
        self.cfg, self.f = cfg, factories or Factories()
        self.current: Session | None = None
        self.current_ws = None
        self.sessions_started = 0

    async def handler(self, ws):
        peer = getattr(ws, "remote_address", None)
        # ---- 1. saludo + autenticación
        try:
            raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT)
            if isinstance(raw, bytes):
                raise P.ProtocolError("se esperaba un mensaje de texto")
            hello = P.decode(raw)
            if hello["type"] != "hello":
                raise P.ProtocolError("el primer mensaje debe ser 'hello'")
            if hello.get("version") != P.PROTOCOL_VERSION:
                await self._reject(ws, P.CLOSE_PROTOCOL, "Versión de protocolo incompatible: actualiza el cliente o el servidor.")
                return
        except (asyncio.TimeoutError, P.ProtocolError, ConnectionClosed) as e:
            log.info("conexión rechazada (%s) desde %s", type(e).__name__, peer)
            await self._reject(ws, P.CLOSE_PROTOCOL, "Saludo inválido.")
            return
        if not hmac.compare_digest(str(hello.get("token", "")).encode(), self.cfg.token.encode()):
            await asyncio.sleep(0.5)                                 # frena la fuerza bruta
            log.warning("token inválido desde %s", peer)
            await self._reject(ws, P.CLOSE_AUTH, "Token inválido.")
            return

        # ---- 2. claves del servidor
        keys = self.cfg.keys()
        if not keys.get("deepgram_api_key"):
            await self._reject(ws, P.CLOSE_PROTOCOL, "El servidor no tiene deepgram_api_key configurada.")
            return

        # ---- 3. una sesión a la vez: el cliente nuevo reemplaza al anterior
        if self.current is not None:
            log.info("sesión anterior reemplazada por una nueva conexión")
            old, old_ws = self.current, self.current_ws
            old.close()
            self.current = self.current_ws = None
            try:                              # sin esperar a una conexión vieja que puede estar muerta
                await asyncio.wait_for(self._reject(old_ws, P.CLOSE_BUSY, "Otra conexión tomó la sesión."), 1.0)
            except Exception:  # noqa: BLE001 — incluye TimeoutError
                pass
        loop = asyncio.get_running_loop()
        try:
            session = Session(loop, self.cfg, keys, P.clean_settings(hello), self.f)
        except Exception as e:  # noqa: BLE001
            log.exception("no pude crear la sesión")
            await self._reject(ws, P.CLOSE_PROTOCOL, f"Error del servidor: {type(e).__name__}")
            return
        self.current, self.current_ws = session, ws
        self.sessions_started += 1
        log.info("sesión iniciada desde %s (objetivo=%s, doblaje=%s)", peer,
                 session.settings["target"], session.settings["dub"])
        try:
            await ws.send(P.encode({"type": "ready", "engine": "deepgram", "version": P.PROTOCOL_VERSION}))
        except ConnectionClosed:
            session.close()
            if self.current is session:
                self.current = self.current_ws = None
            return

        sender = asyncio.create_task(self._sender(ws, session))
        limit = None
        if self.cfg.max_session_minutes > 0:
            limit = asyncio.create_task(self._time_limit(ws, session))
        try:
            async for msg in ws:
                if session.closed:
                    break
                if isinstance(msg, bytes):
                    session.feed_audio(msg)
                    continue
                try:
                    data = P.decode(msg)
                except P.ProtocolError:
                    continue
                kind = data["type"]
                if kind == "set":
                    session.apply(data)
                elif kind == "reload_glossary":
                    session.reload_glossary()
        except ConnectionClosed:
            pass
        finally:
            session.close()
            if limit:
                limit.cancel()
            sender.cancel()                      # no esperar a una cola que puede estar llena
            await asyncio.gather(sender, return_exceptions=True)
            if self.current is session:
                self.current = self.current_ws = None
            log.info("sesión terminada")

    @staticmethod
    async def _reject(ws, code: int, message: str, fatal: bool = True):
        try:
            await ws.send(P.encode({"type": "error", "message": message, "code": code, "fatal": fatal}))
            await ws.close(code, message[:120])
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    async def _sender(ws, session: Session):
        while True:
            item = await session.out.get()
            if item is None:
                return
            try:
                await ws.send(item if isinstance(item, bytes) else P.encode(item))
            except ConnectionClosed:
                return

    async def _time_limit(self, ws, session: Session):
        await asyncio.sleep(self.cfg.max_session_minutes * 60)
        await self._reject(ws, P.CLOSE_LIMIT, "Límite de sesión alcanzado; reconecta para seguir.", fatal=False)

    async def serve(self, ready: "asyncio.Future | None" = None):
        async with serve(self.handler, self.cfg.host, self.cfg.port, max_size=P.MAX_AUDIO_FRAME + 64,
                         ping_interval=20, ping_timeout=20, close_timeout=3) as server:
            if ready is not None and not ready.done():
                ready.set_result(server.sockets[0].getsockname()[1])
            log.info("escuchando en ws://%s:%s", self.cfg.host, self.cfg.port)
            await server.serve_forever()


def main():
    ap = argparse.ArgumentParser(description="Servidor de traducción en vivo")
    ap.add_argument("--live-config", default="live_config.json")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--keys-file")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load_config(args.live_config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.keys_file:
        cfg.keys_file = args.keys_file
    if cfg.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("Escuchando en %s: el tráfico NO va cifrado salvo que pongas TLS delante. "
                    "Recomendado: 127.0.0.1 + túnel SSH.", cfg.host)
    asyncio.run(LiveServer(cfg).serve())


if __name__ == "__main__":
    main()

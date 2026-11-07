"""
Servidor de traducción en vivo (corre en AWS). La PC solo manda audio y recibe
subtítulos (y voz de doblaje); las claves de Deepgram/Groq y todo el trabajo
pesado quedan aquí.

    audio PCM (WebSocket) → Deepgram nova-3 → traducción (Groq/Google, tono,
    glosario, contexto) → subtítulos + (opcional) voz edge-tts → de vuelta a la PC

Seguridad: escucha SOLO en 127.0.0.1 por defecto (delante va Caddy con HTTPS, o un
túnel SSH). Cada usuario tiene su token ("users" en live_config.json, se relee en cada
conexión: añadir o quitar usuarios no exige reiniciar). Varios usuarios a la vez; el
mismo usuario conectándose otra vez reemplaza su sesión anterior (reconexión tras un
corte de red). Cupo opcional de minutos por día y usuario.

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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import live_protocol as P
import live_users as U
from live_dubber import EdgeSynth, LiveDubber
from live_translator import LiveTranslationWorker, LiveTranslator, norm_lang

log = logging.getLogger("live-server")
HELLO_TIMEOUT = 10.0
AUDIO_QUEUE_MAX = 600          # ≈ 60 s de audio pendiente: si Deepgram se atasca, se descarta lo viejo
OUT_QUEUE_MAX = 400
MAX_FAILS, FAIL_WINDOW = 5, 600     # 5 intentos fallidos en 10 min → ese usuario / esa IP esperan 10 min


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = ""                     # token del dueño (usuario "principal")
    keys_file: str = ""                 # config.json con deepgram_api_key / nvidia_api_keys / groq_api_key / glossary…
    max_session_minutes: int = 0        # 0 = sin límite
    max_sessions: int = 4               # usuarios conectados a la vez (cada uno es un stream de Deepgram)
    users: dict = field(default_factory=dict)   # {"nombre": {"token": "…", "daily_minutes": 120}}
    usage_file: str = ""                # minutos usados por día y usuario (sobrevive a reinicios)
    path: str = ""                      # live_config.json, para releer "users"

    def keys(self) -> dict:
        """Claves y ajustes; se releen en cada sesión (cambiar una clave no exige reiniciar)."""
        try:
            with open(self.keys_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def store(self) -> dict:
        """live_config.json completo (relee el archivo: altas, bajas y claves se ven al momento)."""
        if self.path:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data.get("users", {}), dict):
                    return data
            except (OSError, ValueError, AttributeError):
                pass
        return {"users": self.users}

    def save_store(self, data: dict):
        if self.path:
            U.save(self.path, data)
        else:
            self.users = data.get("users", {})

    def accounts(self) -> dict:
        """Usuarios HABILITADOS: {nombre: {"token"?, "password"?, "admin", "daily_minutes"}}."""
        out = {}
        if self.token:
            out["principal"] = {"token": self.token, "daily_minutes": 0, "admin": False}
        for name, u in (self.store().get("users") or {}).items():
            if not isinstance(u, dict) or not u.get("enabled", True):
                continue
            acc = {"daily_minutes": float(u.get("daily_minutes") or 0), "admin": bool(u.get("admin"))}
            if isinstance(u.get("token"), str) and len(u["token"]) >= 16:
                acc["token"] = u["token"]
            if isinstance(u.get("password"), str):
                acc["password"] = u["password"]
            if "token" in acc or "password" in acc:
                out[str(name)] = acc
        return out

    def find_user(self, token: str) -> tuple[str, dict] | None:
        found = None
        for name, u in self.accounts().items():               # sin cortar antes: mismo tiempo para todos
            if "token" in u and hmac.compare_digest(token.encode(), u["token"].encode()):
                found = (name, u)
        return found

    def find_login(self, name: str, password: str):
        """(nombre, cuenta) si usuario y clave son correctos; "disabled" si lo son pero está deshabilitado."""
        u = (self.store().get("users") or {}).get(name)
        if not isinstance(u, dict) or not U.verify_password(password, str(u.get("password", ""))):
            return None
        if not u.get("enabled", True):
            return "disabled"
        return name, self.accounts()[name]


def load_config(path: str | None) -> ServerConfig:
    cfg = ServerConfig()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for k in ("host", "port", "token", "keys_file", "max_session_minutes", "max_sessions", "usage_file"):
            if k in data:
                setattr(cfg, k, type(getattr(cfg, k))(data[k]))
        cfg.users = data.get("users") or {}
        cfg.path = path
        if not cfg.usage_file:
            cfg.usage_file = os.path.join(os.path.dirname(os.path.abspath(path)), "usage.json")
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
    from transcriber_nvidia import build_fallback
    terms = list(keys.get("keyterms") or []) + list((keys.get("glossary") or {}))
    return DeepgramTranscriber(
        audio_q, api_key=keys["deepgram_api_key"], language=lang,
        endpointing_ms=int(keys.get("endpointing_ms", 300)),
        keyterms=list(dict.fromkeys(terms)),
        fallback=build_fallback(audio_q, keys.get("nvidia_api_keys"), lang))


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
        self.translator.on_problem = self._translation_problem
        self._last_problem = 0.0
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

    def _translation_problem(self, message: str):
        """La frase salió sin traducir: se avisa en el overlay (como mucho cada 20 s) para que no falle en silencio."""
        now = time.monotonic()
        if now - self._last_problem >= 20:
            self._last_problem = now
            log.warning("traducción fallida: %s", message)
            self.emit({"type": "notice", "message": message})

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
        if not cfg.accounts():
            raise SystemExit("Falta el token (live_config.json o LIVE_TOKEN): sin él no arranco.")
        self.cfg, self.f = cfg, factories or Factories()
        self.sessions: dict[str, tuple[Session, object]] = {}     # usuario → (sesión, websocket)
        self.sessions_started = 0
        self.usage = self._load_usage()
        self._fails: dict[str, list[float]] = {}                 # "u:nombre" / "ip:x.x.x.x" → horas de fallo

    @property
    def current(self) -> Session | None:
        """La sesión más reciente (compatibilidad con el modo de un solo usuario)."""
        return next(reversed(self.sessions.values()))[0] if self.sessions else None

    # ---------- cupo diario ----------

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_usage(self) -> dict:
        try:
            with open(self.cfg.usage_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def used_seconds(self, user: str) -> float:
        return float(self.usage.get(self._today(), {}).get(user, 0))

    def _add_usage(self, user: str, seconds: float):
        day = self._today()
        self.usage = {day: self.usage.get(day, {})}            # solo se guarda el día en curso
        self.usage[day][user] = round(self.used_seconds(user) + seconds, 1)
        if self.cfg.usage_file:
            try:
                tmp = self.cfg.usage_file + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.usage, f)
                os.replace(tmp, self.cfg.usage_file)
            except OSError as e:
                log.warning("no pude guardar el uso: %s", e)

    async def handler(self, ws):
        peer = getattr(ws, "remote_address", None)
        # ---- 1. saludo + autenticación
        try:
            raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT)
            if isinstance(raw, bytes):
                raise P.ProtocolError("se esperaba un mensaje de texto")
            hello = P.decode(raw)
            if hello["type"] not in ("hello", "login", "admin"):
                raise P.ProtocolError("el primer mensaje debe ser 'hello', 'login' o 'admin'")
            if hello.get("version") != P.PROTOCOL_VERSION:
                await self._reject(ws, P.CLOSE_PROTOCOL, "Versión de protocolo incompatible: actualiza el cliente o el servidor.")
                return
        except (asyncio.TimeoutError, P.ProtocolError, ConnectionClosed) as e:
            log.info("conexión rechazada (%s) desde %s", type(e).__name__, peer)
            await self._reject(ws, P.CLOSE_PROTOCOL, "Saludo inválido.")
            return
        account = await self._authenticate(ws, hello)
        if account is None:
            return
        user, acc = account
        if hello["type"] == "login":
            await self._reply(ws, {"type": "login_result", "ok": True, "user": user, "admin": acc["admin"]})
            return
        if hello["type"] == "admin":
            await self._admin(ws, user, acc, hello)
            return
        quota = acc["daily_minutes"] * 60
        if quota > 0 and self.used_seconds(user) >= quota:
            await self._reject(ws, P.CLOSE_AUTH, "Cupo diario de minutos agotado; vuelve mañana.")
            return

        # ---- 2. claves del servidor
        keys = self.cfg.keys()
        if not keys.get("deepgram_api_key"):
            await self._reject(ws, P.CLOSE_PROTOCOL, "El servidor no tiene deepgram_api_key configurada.")
            return

        # ---- 3. una sesión por usuario: su conexión nueva reemplaza a la anterior
        if user in self.sessions:
            log.info("[%s] sesión anterior reemplazada por una nueva conexión", user)
            old, old_ws = self.sessions.pop(user)
            old.close()
            try:                              # sin esperar a una conexión vieja que puede estar muerta
                await asyncio.wait_for(self._reject(old_ws, P.CLOSE_BUSY, "Otra conexión tomó la sesión."), 1.0)
            except Exception:  # noqa: BLE001 — incluye TimeoutError
                pass
        elif len(self.sessions) >= self.cfg.max_sessions:
            log.warning("[%s] rechazado: servidor lleno (%d sesiones)", user, len(self.sessions))
            await self._reject(ws, P.CLOSE_BUSY, "El servidor está lleno; inténtalo en un rato.")
            return
        loop = asyncio.get_running_loop()
        try:
            session = Session(loop, self.cfg, keys, P.clean_settings(hello), self.f)
        except Exception as e:  # noqa: BLE001
            log.exception("no pude crear la sesión")
            await self._reject(ws, P.CLOSE_PROTOCOL, f"Error del servidor: {type(e).__name__}")
            return
        self.sessions[user] = (session, ws)
        self.sessions_started += 1
        started = time.monotonic()
        log.info("[%s] sesión iniciada (objetivo=%s, doblaje=%s, activas=%d)", user,
                 session.settings["target"], session.settings["dub"], len(self.sessions))
        try:
            await ws.send(P.encode({"type": "ready", "engine": "deepgram", "version": P.PROTOCOL_VERSION}))
        except ConnectionClosed:
            session.close()
            self._forget(user, session)
            return

        sender = asyncio.create_task(self._sender(ws, session))
        limits = [self.cfg.max_session_minutes * 60] if self.cfg.max_session_minutes > 0 else []
        if quota > 0:
            limits.append(quota - self.used_seconds(user))
        limit = asyncio.create_task(self._time_limit(ws, min(limits))) if limits else None
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
            self._forget(user, session)
            self._add_usage(user, time.monotonic() - started)
            log.info("[%s] sesión terminada (hoy: %.0f min)", user, self.used_seconds(user) / 60)

    def _forget(self, user: str, session: Session):
        if self.sessions.get(user, (None,))[0] is session:
            del self.sessions[user]

    # ---------- autenticación ----------

    @staticmethod
    def _client_ip(ws) -> str:
        peer = getattr(ws, "remote_address", None) or ("?",)
        ip = str(peer[0])
        if ip in ("127.0.0.1", "::1"):                         # detrás de Caddy: la IP real viene en la cabecera
            try:
                fwd = ws.request.headers.get("X-Forwarded-For", "")
                ip = fwd.split(",")[0].strip() or ip
            except AttributeError:
                pass
        return ip

    def _locked(self, *keys: str) -> bool:
        now = time.monotonic()
        for k in keys:
            self._fails[k] = [t for t in self._fails.get(k, []) if now - t < FAIL_WINDOW]
            if len(self._fails[k]) >= MAX_FAILS:
                return True
        return False

    async def _authenticate(self, ws, msg: dict):
        """Usuario + clave (o token antiguo). Rechaza y devuelve None si no vale."""
        ip_key = "ip:" + self._client_ip(ws)
        name = str(msg.get("user", "")).strip()
        keys = (ip_key, "u:" + name) if name else (ip_key,)
        if self._locked(*keys):
            await self._reject(ws, P.CLOSE_AUTH, "Demasiados intentos fallidos: espera 10 minutos.")
            return None
        if name:
            res = await asyncio.to_thread(self.cfg.find_login, name, str(msg.get("password", "")))
        else:
            res = self.cfg.find_user(str(msg.get("token", "")))
        if res == "disabled":
            await self._reject(ws, P.CLOSE_AUTH, "Tu usuario está deshabilitado. Habla con el administrador.")
            return None
        if res is None:
            for k in keys:
                self._fails.setdefault(k, []).append(time.monotonic())
            await asyncio.sleep(0.5)                                 # frena la fuerza bruta
            log.warning("acceso inválido (%s) desde %s", name or "token", ip_key[3:])
            await self._reject(ws, P.CLOSE_AUTH, "Usuario o clave incorrectos." if name else "Token inválido.")
            return None
        self._fails.pop("u:" + res[0], None)
        return res

    # ---------- panel de usuarios (solo admin) ----------

    async def _admin(self, ws, actor: str, acc: dict, msg: dict):
        if not acc.get("admin") or not msg.get("user"):
            await self._reject(ws, P.CLOSE_AUTH, "Solo el administrador puede gestionar usuarios.")
            return
        action, name = str(msg.get("action", "list")), str(msg.get("name", "")).strip()
        data = self.cfg.store()
        try:
            if action == "add":
                U.add_user(data, name, str(msg.get("new_password", "")), msg.get("minutes", 0), bool(msg.get("admin")))
            elif action == "passwd":
                U.set_password(data, name, str(msg.get("new_password", "")))
            elif action in ("enable", "disable"):
                U.set_enabled(data, name, action == "enable", actor)
            elif action == "minutes":
                U.set_minutes(data, name, msg.get("minutes", 0))
            elif action == "set_admin":
                U.set_admin(data, name, bool(msg.get("admin")), actor)
            elif action == "delete":
                U.delete_user(data, name, actor)
            elif action != "list":
                raise ValueError("Acción desconocida.")
            if action != "list":
                self.cfg.save_store(data)
                log.info("[%s] admin: %s %s", actor, action, name)
        except (ValueError, TypeError) as e:
            await self._reply(ws, {"type": "admin_result", "ok": False, "error": str(e)})
            return
        if action in ("disable", "delete") or (action == "passwd" and name != actor):
            await self._kick(name, "Tu acceso cambió: vuelve a iniciar sesión." if action == "passwd"
                             else "Tu acceso fue retirado por el administrador.")
        users = U.public_list(data, lambda n: self.used_seconds(n) / 60, set(self.sessions))
        await self._reply(ws, {"type": "admin_result", "ok": True, "users": users})

    async def _kick(self, user: str, message: str):
        if user in self.sessions:
            session, ws = self.sessions.pop(user)
            session.close()
            try:
                await asyncio.wait_for(self._reject(ws, P.CLOSE_AUTH, message), 1.0)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    async def _reply(ws, msg: dict):
        try:
            await ws.send(P.encode(msg))
            await ws.close()
        except Exception:  # noqa: BLE001
            pass

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

    async def _time_limit(self, ws, seconds: float):
        await asyncio.sleep(max(0.0, seconds))
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

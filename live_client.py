"""
Cliente ligero para la PC: captura el audio del sistema, lo manda al servidor de
AWS y muestra lo que vuelve. No transcribe ni traduce (sin modelos, sin claves).

  RemoteSession    conexión WebSocket con reconexión automática
  RemoteTranslator / RemoteDubber   sustitutos de LiveTranslator / LiveDubber con la
                   misma interfaz que usa el menú de la bandeja (app_ui)
  ClientDubPlayer  reproduce la voz del doblaje que llega del servidor
Solo depende de numpy y websocket-client (nada pesado).
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np
import websocket  # websocket-client

import live_protocol as P


class RemoteSession:
    def __init__(self, url: str, token: str, audio_queue: "queue.Queue", settings: dict | None = None,
                 on_upsert=None, on_trans=None, on_notice=None, on_status=None, on_dub=None,
                 on_gate=None, on_dub_stop=None, on_fatal=None, max_backoff: float = 15.0):
        self.url, self.token, self.audio_queue = url, token, audio_queue
        self.settings = dict(settings or {})
        self.cb = dict(upsert=on_upsert, trans=on_trans, notice=on_notice, status=on_status,
                       dub=on_dub, gate=on_gate, dub_stop=on_dub_stop, fatal=on_fatal)
        self.max_backoff = max_backoff
        self._stop = threading.Event()
        self._ws = None
        self._thread: threading.Thread | None = None
        self.connected = False
        self.fatal = False

    # ---------- API ----------

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="remote-session")
        self._thread.start()

    def stop(self):
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close(timeout=1)
            except Exception:  # noqa: BLE001
                pass

    def send_set(self, **kw):
        """Cambia ajustes en el servidor; se recuerdan para el saludo si hay que reconectar."""
        self.settings.update(kw)
        self._send_json({"type": "set", **kw})

    def reload_glossary(self):
        self._send_json({"type": "reload_glossary"})

    # ---------- internos ----------

    def _call(self, name: str, *args):
        fn = self.cb.get(name)
        if fn:
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001 — la interfaz nunca debe tumbar la red
                print(f"[cliente] error en callback {name}: {type(e).__name__}")

    def _send_json(self, msg: dict):
        ws = self._ws
        if ws is not None and self.connected:
            try:
                ws.send(P.encode(msg))
            except Exception:  # noqa: BLE001
                pass

    def _drain_audio(self):
        try:
            while True:
                self.audio_queue.get_nowait()
        except queue.Empty:
            pass

    def _sleep(self, seconds: float):
        """Espera descartando el audio que se acumule (no se manda nada sin conexión)."""
        end = time.monotonic() + seconds
        while not self._stop.is_set() and time.monotonic() < end:
            self._drain_audio()
            time.sleep(0.2)

    def _connect(self):
        ws = websocket.create_connection(self.url, timeout=10, enable_multithread=True)
        ws.send(P.encode({"type": "hello", "version": P.PROTOCOL_VERSION, "token": self.token,
                          **self.settings}))
        while True:                                   # esperar 'ready' (o un error)
            raw = ws.recv()
            if isinstance(raw, bytes):
                continue
            msg = P.decode(raw)
            if msg["type"] == "ready":
                return ws
            if msg["type"] == "error":
                ws.close()
                raise _ServerError(msg.get("message", "error"), bool(msg.get("fatal")))

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            self._call("status", "connecting")
            try:
                ws = self._connect()
            except _ServerError as e:
                if e.fatal:
                    self.fatal = True
                    self._call("fatal", e.message)
                    return
                self._call("notice", e.message)
                self._sleep(backoff)
                continue
            except Exception:  # noqa: BLE001 — red caída, servidor apagado, túnel sin levantar…
                self._call("status", "reconnecting")
                self._sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff)
                continue
            self._ws, self.connected, backoff = ws, True, 1.0
            self._call("status", "connected")
            alive = threading.Event()
            alive.set()
            rt = threading.Thread(target=self._recv_loop, args=(ws, alive), daemon=True)
            rt.start()
            try:
                self._drain_audio()                   # nada de audio viejo tras reconectar
                while not self._stop.is_set() and alive.is_set():
                    try:
                        chunk = self.audio_queue.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    pcm = (np.clip(chunk, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                    ws.send_binary(pcm)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.connected = False
                try:
                    ws.close(timeout=1)              # sin esperar al servidor si ya cerró él
                except Exception:  # noqa: BLE001
                    pass
            if self.fatal or self._stop.is_set():
                return
            self._call("status", "reconnecting")
            self._sleep(backoff)

    def _recv_loop(self, ws, alive: threading.Event):
        try:
            while not self._stop.is_set():
                raw = ws.recv()
                if raw is None or raw == "":
                    break
                if isinstance(raw, bytes):
                    try:
                        uid, sr, pcm = P.unpack_dub(raw)
                    except P.ProtocolError:
                        continue
                    self._call("dub", uid, sr, pcm)
                    continue
                try:
                    msg = P.decode(raw)
                except P.ProtocolError:
                    continue
                kind = msg["type"]
                if kind == "upsert":
                    self._call("upsert", int(msg["uid"]), str(msg["text"]), str(msg.get("lang", "auto")),
                               bool(msg.get("final")))
                elif kind == "trans":
                    self._call("trans", int(msg["uid"]), str(msg["text"]))
                elif kind == "gate":
                    self._call("gate", bool(msg.get("muted")))
                elif kind == "dub_stop":
                    self._call("dub_stop")
                elif kind == "notice":
                    self._call("notice", str(msg.get("message", "")))
                elif kind == "error":
                    if msg.get("fatal"):
                        self.fatal = True
                        self._call("fatal", str(msg.get("message", "")))
                        break
                    self._call("notice", str(msg.get("message", "")))
        except Exception:  # noqa: BLE001 — conexión cerrada
            pass
        finally:
            alive.clear()


class _ServerError(Exception):
    def __init__(self, message: str, fatal: bool):
        super().__init__(message)
        self.message, self.fatal = message, fatal


# ------------------------------------------------------------------ proxies para la bandeja

class RemoteTranslator:
    """Misma interfaz que LiveTranslator para el menú; los cambios viajan al servidor."""

    def __init__(self, session: RemoteSession, target: str = "es", tone: str = "gamer"):
        self.session, self.target, self.tone = session, P._norm_lang(target) or "es", tone

    def set_target(self, lang: str):
        lang = P._norm_lang(lang)
        if lang and lang != self.target:
            self.target = lang
            self.session.send_set(target=lang)

    def set_tone(self, tone: str):
        if tone in P.TONES and tone != self.tone:
            self.tone = tone
            self.session.send_set(tone=tone)

    def set_glossary(self, glossary=None):
        self.session.reload_glossary()           # el glosario vive en el servidor


class RemoteDubber:
    """Misma interfaz que LiveDubber para el menú/F11; el doblaje lo hace el servidor."""

    def __init__(self, session: RemoteSession, player, enabled: bool = False, gender: str = "female",
                 device: str = "", on_state=None, capture_device=None):
        self.session, self.player = session, player
        self.enabled, self.gender, self.device = bool(enabled), gender, device or ""
        self.on_state = on_state or (lambda on: None)
        self.capture_device = capture_device or (lambda: "")

    @property
    def needs_gate(self) -> bool:
        """¿La voz saldría por el dispositivo capturado? Entonces se silencia la captura mientras habla."""
        dev = self.device.strip().lower()
        if not dev or dev == "default":
            return True
        cap = (self.capture_device() or "").lower()
        return not cap or dev in cap or cap in dev

    def _push(self):
        self.session.send_set(dub=self.enabled, dub_gender=self.gender, dub_gate=self.needs_gate)

    def set_enabled(self, on: bool, notify_server: bool = True):
        changed = self.enabled != bool(on)
        self.enabled = bool(on)
        if changed:
            self.on_state(self.enabled)
        if notify_server:
            self._push()

    def toggle(self) -> bool:
        self.set_enabled(not self.enabled)
        return self.enabled

    def set_gender(self, gender: str):
        if gender in P.GENDERS:
            self.gender = gender
            self._push()

    def set_device(self, device: str):
        self.device = device or ""
        self._push()

    def set_target(self, lang: str):            # el servidor lo cambia con el traductor
        pass

    def stop(self):
        pass


class ClientDubPlayer:
    """Reproduce la voz que llega del servidor, de una en una, y silencia la captura
    mientras suena si hace falta (anti-realimentación)."""

    def __init__(self, player, set_muted, dubber: RemoteDubber, volume: float = 1.0):
        self.player, self.set_muted, self.dubber, self.volume = player, set_muted, dubber, volume
        self._q: "queue.Queue[tuple[int, bytes]]" = queue.Queue(maxsize=8)
        self._cancel = threading.Event()
        self._server_gate = False
        self._playing = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="dub-player").start()

    def _apply_gate(self):
        with self._lock:
            self.set_muted(self._server_gate or (self._playing and self.dubber.needs_gate))

    def server_gate(self, muted: bool):
        self._server_gate = bool(muted)
        self._apply_gate()

    def enqueue(self, sr: int, pcm: bytes):
        if not self.dubber.enabled:
            return
        try:
            self._q.put_nowait((sr, pcm))
        except queue.Full:
            try:                                  # descarta la más vieja: la voz no debe ir atrasada
                self._q.get_nowait()
                self._q.put_nowait((sr, pcm))
            except (queue.Empty, queue.Full):
                pass

    def stop_current(self):
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._cancel.set()

    def stop(self):
        self._stop.set()
        self.stop_current()

    def _loop(self):
        while not self._stop.is_set():
            try:
                sr, pcm = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if not self.dubber.enabled or not pcm:
                continue
            self._cancel.clear()
            self._playing = True
            self._apply_gate()
            try:
                self.player.play(np.frombuffer(pcm, dtype="<i2"), sr, self.dubber.device,
                                 self.volume, self._cancel)
            except Exception as e:  # noqa: BLE001
                print(f"[cliente] error al reproducir la voz: {type(e).__name__}")
            finally:
                self._playing = False
                self._apply_gate()

"""
Cliente ligero del traductor en vivo para la PC. NO transcribe ni traduce: manda el audio
del sistema al servidor de AWS y muestra los subtítulos (y reproduce el doblaje) que vuelven.
Sin modelos ni claves en la PC; se empaqueta en un .exe, sin necesidad de instalar Python.

Configuración (config.json junto al .exe):
  {"server": "ws://127.0.0.1:8765", "token": "…",
   "ssh": {"host": "IP-DE-AWS", "user": "ubuntu", "key": "C:\\ruta\\clave.pem"}}
Con la sección "ssh" el cliente abre solo el túnel cifrado hacia AWS.
"""
import argparse
import json
import os
import sys
from types import SimpleNamespace

from apppath import app_dir

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from app_ui import Bridge, setup_global_hotkeys, setup_system_tray
from audio_capture import SystemAudioCapture
from live_client import ClientDubPlayer, RemoteDubber, RemoteSession, RemoteTranslator
from live_dubber import PyAudioPlayer
from overlay import TranslationOverlay
from tunnel import SshTunnel, find_key, prepare_key

STATUS_TEXT = {"connecting": "🔌 Conectando con AWS…", "connected": "✅ Conectado con AWS",
               "reconnecting": "⚠️ Sin conexión; reconectando…"}


def load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fatal_box(app: QApplication, title: str, text: str):
    QMessageBox.critical(None, title, text)


class ClientError(Exception):
    """Error de configuración que se muestra al usuario en un cuadro de diálogo."""


def build(cfg: dict, app: QApplication, no_tunnel: bool = False, capture_factory=SystemAudioCapture,
          player=None, config_path: str = ""):
    """Crea todas las piezas del cliente y las conecta (sin entrar al bucle de Qt ni arrancar red)."""
    token = str(cfg.get("token", "")).strip()
    url = str(cfg.get("server", "ws://127.0.0.1:8765")).strip()
    if not token:
        raise ClientError(f"Falta el token en {config_path or 'config.json'}.\n\n"
                          f"Agrega:\n{{\"token\": \"…\", \"server\": \"{url}\"}}")
    icon_path = os.path.join(app_dir(), "traductor.ico")

    overlay = TranslationOverlay()
    if os.path.exists(icon_path):
        overlay.setWindowIcon(QIcon(icon_path))
    overlay.show()

    bridge = Bridge()
    bridge.upsert.connect(overlay.upsert_entry)
    bridge.set_trans.connect(overlay.set_translation)
    bridge.hotkey.connect(overlay.handle_hotkey)
    bridge.notice.connect(overlay._flash)

    capture = capture_factory()
    player = player or PyAudioPlayer()

    session = RemoteSession(
        url, token, capture.audio_queue,
        settings={"target": overlay.target_lang, "tone": str(cfg.get("tone", "gamer")),
                  "dub": bool(cfg.get("dub", False)), "dub_gender": str(cfg.get("dub_gender", "female")),
                  "dub_gate": True},
        on_upsert=bridge.upsert.emit, on_trans=bridge.set_trans.emit,
        on_status=lambda s: bridge.notice.emit(STATUS_TEXT.get(s, s)),
        on_fatal=lambda m: bridge.notice.emit("⛔ " + m),
        on_notice=lambda m: bridge.dub_state.emit(m == "dub:on") if m.startswith("dub:") else bridge.notice.emit(m))

    translator = RemoteTranslator(session, overlay.target_lang, str(cfg.get("tone", "gamer")))
    dubber = RemoteDubber(session, player, enabled=bool(cfg.get("dub", False)),
                          gender=str(cfg.get("dub_gender", "female")), device=str(cfg.get("dub_device", "") or ""),
                          on_state=bridge.dub_state.emit,
                          capture_device=lambda: capture.current_device_name)
    session.settings["dub_gate"] = dubber.needs_gate
    dub_player = ClientDubPlayer(player, capture.set_muted, dubber, float(cfg.get("dub_volume", 1.0)))
    session.cb.update(dub=lambda uid, sr, pcm: dub_player.enqueue(sr, pcm), gate=dub_player.server_gate,
                      dub_stop=dub_player.stop_current)
    overlay.set_dub_state(dubber.enabled)

    tray = setup_system_tray(app, overlay, translator, dubber, reload_glossary=session.reload_glossary)

    def _on_dub_state(on: bool):
        # el servidor puede apagar el doblaje solo (fallos); se refleja sin reenviarlo
        if dubber.enabled != on:
            dubber.set_enabled(on, notify_server=False)
        overlay.set_dub_state(on)
        tray.sync_dub()
        overlay.save_setting("dub", on)
        if not on:
            dub_player.stop_current()

    bridge.dub_state.connect(_on_dub_state)

    def _toggle_dub():
        on = dubber.toggle()
        overlay._flash("🔊 Doblaje de voz: ACTIVADO" if on else "🔇 Doblaje de voz: apagado")

    overlay.dub_toggle_requested.connect(_toggle_dub)

    tunnel = None
    ssh = cfg.get("ssh") if isinstance(cfg.get("ssh"), dict) else None
    if ssh and not no_tunnel:
        try:
            spec = ssh.get("key", "auto")           # "" = sin clave (agente SSH); "auto" = la .pem junto al programa
            key = prepare_key(find_key(str(spec), app_dir()),
                              os.path.join(os.environ.get("LOCALAPPDATA", app_dir()), "TraductorCliente")) \
                if str(spec).strip() != "" else ""
        except FileNotFoundError as e:
            raise ClientError(str(e)) from e

        def _tunnel_status(s: str):
            if s == "no-ssh":
                bridge.notice.emit("🔑 Windows no tiene OpenSSH (Configuración → Aplicaciones → Características opcionales)")
            elif s != "starting":
                bridge.notice.emit("🔑 Túnel SSH: " + s)

        tunnel = SshTunnel(str(ssh["host"]), str(ssh.get("user", "ubuntu")), key,
                           local_port=int(ssh.get("local_port", 8765)),
                           remote_port=int(ssh.get("remote_port", 8765)), port=int(ssh.get("port", 22)),
                           on_status=_tunnel_status)

    def shutdown():
        session.stop()
        dub_player.stop()
        capture.stop()
        if tunnel:
            tunnel.stop()

    return SimpleNamespace(overlay=overlay, bridge=bridge, capture=capture, session=session,
                           translator=translator, dubber=dubber, dub_player=dub_player, tray=tray,
                           tunnel=tunnel, shutdown=shutdown)


def selftest(out_path: str) -> int:
    """Autoprueba para el .exe empaquetado (se usa en la compilación automática): comprueba que
    Qt, el overlay, el visor de subtítulos y las librerías de red cargan. Sin audio ni red."""
    import tempfile
    lines, ok = [], True
    try:
        import numpy, websocket, PyQt6  # noqa: F401
        import live_protocol, tunnel  # noqa: F401
        import overlay as ov
        ov.CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "c.json")        # no tocar la config real
        app = QApplication.instance() or QApplication(sys.argv)
        w = ov.TranslationOverlay()
        w.mode = "subtitle"
        w.upsert_entry(1, "hello world", "en", True)
        w.set_translation(1, "hola mundo")
        w._render()
        pix = w.grab()
        lines.append(f"overlay ok {pix.width()}x{pix.height()}")
        from live_client import RemoteSession  # noqa: F401
        from style_dialog import StyleDialog
        StyleDialog(w).sync_from_style()
        lines.append("panel de estilo ok")
        lines.append("SELFTEST OK")
    except Exception as e:  # noqa: BLE001
        ok = False
        lines.append(f"SELFTEST FALLO: {type(e).__name__}: {e}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", metavar="ARCHIVO", help="comprueba el programa y escribe el resultado en ARCHIVO")
    ap.add_argument("--config", default=os.path.join(app_dir(), "config.json"))
    ap.add_argument("--no-tunnel", action="store_true", help="no abrir el túnel SSH (ya hay un servidor accesible)")
    args = ap.parse_args()
    if args.selftest:
        return selftest(args.selftest)
    cfg = load_config(args.config)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    icon_path = os.path.join(app_dir(), "traductor.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    try:
        c = build(cfg, app, no_tunnel=args.no_tunnel, config_path=args.config)
    except ClientError as e:
        fatal_box(app, "Traductor en vivo (cliente)", str(e))
        return 1

    if setup_global_hotkeys(c.bridge):
        c.overlay.disable_local_shortcuts()
    if c.tunnel:
        c.tunnel.start()
        c.tunnel.wait_ready(20)
    c.capture.start()
    c.session.start()
    code = app.exec()
    c.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())

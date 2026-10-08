"""
Traductor de voz en vivo con overlay translúcido (modo subtítulos y gaming HUD).

Flujo:  audio del sistema (WASAPI loopback)
        → faster-whisper / deepgram / groq (parciales en vivo + final, detecta idioma)
        → Google Translate (worker con descarte de parciales viejos)
        → overlay PyQt6 (original + traducción, actualizados en su sitio)

Uso:    python main.py [--target es] [--model tiny] [--lang en] [--engine auto]
"""
import argparse
import os
import queue
import sys
import threading
import time

from apppath import app_dir

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from app_ui import (Bridge, setup_global_hotkeys,  # noqa: F401 — re-exportados
                    setup_system_tray)
from audio_capture import SystemAudioCapture
from overlay import TranslationOverlay
from transcriber import StreamingTranscriber
from live_dubber import LiveDubber
from live_translator import (LiveTranslationWorker, LiveTranslator,
                             norm_lang)


def _cfg_value(cfg_field: str, default=None):
    """Valor de cualquier tipo desde config.json (listas, números…)."""
    try:
        import json
        with open(os.path.join(app_dir(), "config.json"), encoding="utf-8") as f:
            return json.load(f).get(cfg_field, default)
    except Exception:
        return default


def _cfg_key(env_var: str, cfg_field: str) -> str:
    """API key desde variable de entorno o config.json."""
    key = os.environ.get(env_var, "").strip()
    if key:
        return key
    try:
        import json
        cfg_path = os.path.join(app_dir(), "config.json")
        with open(cfg_path, encoding="utf-8") as f:
            return str(json.load(f).get(cfg_field, "")).strip()
    except Exception:
        return ""


def build_transcriber(args, audio_queue):
    """Prioridad en auto: deepgram > groq > local, según keys presentes."""
    dg_key = _cfg_key("DEEPGRAM_API_KEY", "deepgram_api_key")
    gq_key = _cfg_key("GROQ_API_KEY", "groq_api_key")
    engine = args.engine
    if engine == "auto":
        engine = "deepgram" if dg_key else ("groq" if gq_key else "local")
    if engine == "deepgram":
        try:
            from transcriber_deepgram import DeepgramTranscriber
            terms = list(_cfg_value("keyterms", []) or []) + list((_cfg_value("glossary", {}) or {}))
            return DeepgramTranscriber(
                audio_queue, api_key=dg_key, language=args.lang,
                endpointing_ms=int(_cfg_value("endpointing_ms", 300)),
                keyterms=list(dict.fromkeys(terms)))
        except Exception as e:
            print(f"[engine] Deepgram no disponible ({e}); probando siguiente.")
            engine = "groq" if gq_key else "local"
    if engine == "groq":
        try:
            from transcriber_groq import GroqTranscriber
            return GroqTranscriber(audio_queue, api_key=gq_key,
                                   language=args.lang)
        except Exception as e:
            print(f"[engine] Groq no disponible ({e}); usando motor local.")
    return StreamingTranscriber(
        audio_queue, model_size=args.model, device=args.device,
        language=args.lang)


def pipeline(transcriber: StreamingTranscriber, translator: LiveTranslator,
             worker: LiveTranslationWorker, bridge: Bridge, stop: threading.Event):
    while not stop.is_set():
        try:
            seg = transcriber.text_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        bridge.upsert.emit(seg.utterance_id, seg.text, seg.language, seg.is_final)
        if norm_lang(seg.language) == translator.target:
            bridge.set_trans.emit(seg.utterance_id, seg.text)
        else:
            worker.submit(seg.utterance_id, seg.text, seg.language, seg.is_final)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="es", help="Idioma destino (es, en, pt...)")
    ap.add_argument("--model", default="tiny",
                    help="Modelo Whisper: tiny/base/small/medium/large-v3")
    ap.add_argument("--device", default="cpu", help="cpu, cuda o auto")
    ap.add_argument("--lang", default=None,
                    help="Forzar idioma origen (ej. en). Omitir = autodetectar")
    ap.add_argument("--engine", default="auto", choices=["auto", "deepgram", "groq", "local"],
                    help="auto: deepgram > groq > local según keys configuradas")
    ap.add_argument("--opacity", type=int, default=None,
                    help="Alfa del fondo del panel (0-255)")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    icon_path = os.path.join(app_dir(), "traductor.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    overlay = TranslationOverlay(opacity=args.opacity, target_lang=args.target)
    if os.path.exists(icon_path):
        overlay.setWindowIcon(QIcon(icon_path))
    overlay.show()


    gq_key = _cfg_key("GROQ_API_KEY", "groq_api_key")
    translator = LiveTranslator(
        target=overlay.target_lang, groq_key=gq_key,
        tone=str(_cfg_value("tone", "gamer")),
        glossary=_cfg_value("glossary", {}) or {},
        context_size=int(_cfg_value("context_lines", 4)))


    bridge = Bridge()
    bridge.upsert.connect(overlay.upsert_entry)
    bridge.set_trans.connect(overlay.set_translation)
    bridge.hotkey.connect(overlay.handle_hotkey)
    bridge.notice.connect(overlay._flash)

    capture = SystemAudioCapture()
    dubber = LiveDubber(
        target=translator.target, gender=str(_cfg_value("dub_gender", "female")),
        device=str(_cfg_value("dub_device", "") or ""),
        volume=float(_cfg_value("dub_volume", 1.0)),
        max_backlog=int(_cfg_value("dub_max_backlog", 2)),
        enabled=bool(_cfg_value("dub", False)),
        on_gate=capture.set_muted, capture_device=lambda: capture.current_device_name,
        on_notice=bridge.notice.emit, on_state=bridge.dub_state.emit)
    overlay.language_changed.connect(dubber.set_target)
    overlay.set_dub_state(dubber.enabled)

    tray = setup_system_tray(
        app, overlay, translator, dubber,
        reload_glossary=lambda: translator.set_glossary(_cfg_value("glossary", {}) or {}))

    def _on_dub_state(on: bool):          # botón del overlay y casilla de la bandeja siempre al día
        overlay.set_dub_state(on)
        tray.sync_dub()
        overlay.save_setting("dub", on)

    bridge.dub_state.connect(_on_dub_state)

    def _toggle_dub():
        on = dubber.toggle()
        overlay._flash("🔊 Doblaje de voz: ACTIVADO" if on else "🔇 Doblaje de voz: apagado")

    overlay.dub_toggle_requested.connect(_toggle_dub)

    if setup_global_hotkeys(bridge):
        overlay.disable_local_shortcuts()
        print("[hotkeys] Globales activos: F6 gaming, F7 opacidad, F8 clics, "
              "F9 ocultar, F10 compacto, F11 doblaje")
    else:
        print("[hotkeys] Solo locales (instala 'keyboard' para globales)")

    transcriber = build_transcriber(args, capture.audio_queue)

    stop = threading.Event()
    worker = LiveTranslationWorker(
        translator, bridge.set_trans.emit, stop,
        translate_partials=bool(_cfg_value("translate_partials", True)),
        on_final=lambda uid, text, src, lang: (
            dubber.submit(uid, text) if text.strip() != src.strip() else None))
    capture.start()
    transcriber.start()
    threading.Thread(
        target=pipeline, args=(transcriber, translator, worker, bridge, stop),
        daemon=True,
    ).start()

    code = app.exec()
    dubber.stop()
    stop.set()
    transcriber.stop()
    capture.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()

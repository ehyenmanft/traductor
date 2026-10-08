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

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from audio_capture import SystemAudioCapture
from overlay import AVAILABLE_LANGUAGES, TranslationOverlay
from transcriber import StreamingTranscriber
from live_translator import (TONES, LiveTranslationWorker, LiveTranslator,
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


class Bridge(QObject):
    """Puente thread → hilo de UI de Qt."""
    upsert = pyqtSignal(int, str, str, bool)  # uid, original, idioma, final
    set_trans = pyqtSignal(int, str)          # uid, traducción
    hotkey = pyqtSignal(str)                  # "f6".."f10" desde hook global


def setup_global_hotkeys(bridge: Bridge) -> bool:
    """Hotkeys que funcionan aunque el juego tenga el foco.
    Requiere la librería `keyboard`; si falta, se usan los atajos locales."""
    try:
        import keyboard
    except ImportError:
        return False
    try:
        for key in ("f6", "f7", "f8", "f9", "f10"):
            keyboard.add_hotkey(key, lambda k=key: bridge.hotkey.emit(k))
        return True
    except Exception:
        return False


def setup_system_tray(app: QApplication, overlay: TranslationOverlay, translator: LiveTranslator) -> QSystemTrayIcon:
    """Crea el icono en la bandeja del sistema (System Tray) con menú rápido."""
    tray = QSystemTrayIcon(app)
    icon_path = os.path.join(app_dir(), "traductor.ico")
    if os.path.exists(icon_path):
        tray.setIcon(QIcon(icon_path))
    else:
        tray.setIcon(app.style().standardIcon(app.style().StandardPixmap.SP_ComputerIcon))

    tray.setToolTip("🎙 Traductor de Voz en Vivo")

    menu = QMenu()
    menu.setStyleSheet(
        "QMenu{background-color:#161922;color:#e0e6ed;border:1px solid #333a4d;border-radius:6px;padding:4px;}"
        "QMenu::item{padding:6px 20px;border-radius:4px;font-family:'Segoe UI';font-size:11px;}"
        "QMenu::item:selected{background-color:#2a334a;color:#ffffff;}"
        "QMenu::separator{height:1px;background-color:#333a4d;margin:4px 8px;}"
    )

    title_act = QAction("🎙 Traductor de Voz en Vivo", menu)
    title_act.setEnabled(False)
    menu.addAction(title_act)
    menu.addSeparator()

    act_vis = QAction("👁 Mostrar / Ocultar Panel (F9)", menu)
    act_vis.triggered.connect(overlay.toggle_visible)
    menu.addAction(act_vis)

    act_click = QAction("🛡 Modo Click-Through (F8)", menu)
    act_click.triggered.connect(overlay.toggle_click_through)
    menu.addAction(act_click)

    act_gaming = QAction("🎮 Modo Subtítulos Gaming HUD (F6)", menu)
    act_gaming.triggered.connect(overlay.toggle_gaming_mode)
    menu.addAction(act_gaming)

    act_compact = QAction("🕶 Modo Compacto (F10)", menu)
    act_compact.triggered.connect(overlay.toggle_compact)
    menu.addAction(act_compact)

    menu.addSeparator()

    # Submenú de selección de idioma
    lang_menu = menu.addMenu("🌐 Idioma Destino")
    lang_menu.setStyleSheet(menu.styleSheet())
    lang_group = QActionGroup(lang_menu)
    lang_group.setExclusive(True)

    def _sync_lang_actions():
        for act in lang_group.actions():
            act.setChecked(act.data() == translator.target)

    for code, label in AVAILABLE_LANGUAGES:
        act_lang = QAction(label, lang_menu, checkable=True)
        act_lang.setData(code)
        if code == translator.target:
            act_lang.setChecked(True)

        def _on_lang_trigger(checked, c=code):
            overlay.set_target_language(c)

        act_lang.triggered.connect(_on_lang_trigger)
        lang_group.addAction(act_lang)
        lang_menu.addAction(act_lang)

    menu.addSeparator()

    # Tono de la traducción (se guarda en config.json)
    tone_menu = menu.addMenu("🗣 Tono de traducción")
    tone_menu.setStyleSheet(menu.styleSheet())
    tone_group = QActionGroup(tone_menu)
    tone_group.setExclusive(True)
    for key, (label, _) in TONES.items():
        act_tone = QAction(label, tone_menu, checkable=True)
        act_tone.setChecked(key == translator.tone)

        def _on_tone(checked, k=key):
            translator.set_tone(k)
            overlay.save_setting("tone", k)
            overlay._flash(f"🗣 Tono: {TONES[k][0]}")

        act_tone.triggered.connect(_on_tone)
        tone_group.addAction(act_tone)
        tone_menu.addAction(act_tone)

    act_gloss = QAction("📖 Recargar glosario (config.json)", menu)

    def _reload_glossary():
        translator.set_glossary(_cfg_value("glossary", {}) or {})
        overlay._flash("📖 Glosario recargado")

    act_gloss.triggered.connect(_reload_glossary)
    menu.addAction(act_gloss)

    menu.addSeparator()

    def _open_folder():
        folder = os.path.join(app_dir(), "transcripciones")
        os.makedirs(folder, exist_ok=True)
        try:
            os.startfile(folder)
        except Exception as e:
            print(f"[tray] No se pudo abrir la carpeta: {e}")

    act_folder = QAction("📁 Abrir transcripciones guardadas", menu)
    act_folder.triggered.connect(_open_folder)
    menu.addAction(act_folder)

    menu.addSeparator()

    act_exit = QAction("✕ Salir", menu)
    act_exit.triggered.connect(overlay.close_app)
    menu.addAction(act_exit)

    tray.setContextMenu(menu)

    def _on_tray_activated(reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            overlay.toggle_visible()
            if overlay.isVisible():
                overlay.raise_()
                overlay.activateWindow()

    tray.activated.connect(_on_tray_activated)

    # Actualizar estado de checked al cambiar idioma desde el overlay
    def _on_overlay_lang_changed(lang_code):
        translator.set_target(lang_code)
        _sync_lang_actions()

    overlay.language_changed.connect(_on_overlay_lang_changed)

    tray.show()
    return tray


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

    tray = setup_system_tray(app, overlay, translator)

    if setup_global_hotkeys(bridge):
        overlay.disable_local_shortcuts()
        print("[hotkeys] Globales activos: F6 gaming, F7 opacidad, F8 clics, "
              "F9 ocultar, F10 compacto")
    else:
        print("[hotkeys] Solo locales (instala 'keyboard' para globales)")

    capture = SystemAudioCapture()
    transcriber = build_transcriber(args, capture.audio_queue)

    stop = threading.Event()
    worker = LiveTranslationWorker(
        translator, bridge.set_trans.emit, stop,
        translate_partials=bool(_cfg_value("translate_partials", True)))
    capture.start()
    transcriber.start()
    threading.Thread(
        target=pipeline, args=(transcriber, translator, worker, bridge, stop),
        daemon=True,
    ).start()

    code = app.exec()
    stop.set()
    transcriber.stop()
    capture.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()

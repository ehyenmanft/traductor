"""
Piezas de interfaz compartidas por la app de escritorio (main.py) y el cliente
ligero que usa el servidor de AWS (client_main.py): señales entre hilos y Qt,
atajos globales y menú de la bandeja. NO importa transcriptores ni modelos.
"""
import os

from apppath import app_dir

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from overlay import AVAILABLE_LANGUAGES, TranslationOverlay
from live_translator import TONES


class Bridge(QObject):
    """Puente thread → hilo de UI de Qt."""
    upsert = pyqtSignal(int, str, str, bool)  # uid, original, idioma, final
    set_trans = pyqtSignal(int, str)          # uid, traducción
    hotkey = pyqtSignal(str)                  # "f6".."f11" desde hook global
    notice = pyqtSignal(str)                  # avisos breves (doblaje, etc.)
    dub_state = pyqtSignal(bool)              # el doblaje se encendió/apagó (también solo, por fallos)


def setup_global_hotkeys(bridge: Bridge) -> bool:
    """Hotkeys que funcionan aunque el juego tenga el foco.
    Requiere la librería `keyboard`; si falta, se usan los atajos locales."""
    try:
        import keyboard
    except ImportError:
        return False
    try:
        for key in ("f6", "f7", "f8", "f9", "f10", "f11"):
            keyboard.add_hotkey(key, lambda k=key: bridge.hotkey.emit(k))
        return True
    except Exception:
        return False


def setup_system_tray(app: QApplication, overlay: TranslationOverlay, translator,
                      dubber, reload_glossary=None) -> QSystemTrayIcon:
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

    act_style = QAction("🎨 Personalizar estilo…", menu)
    act_style.triggered.connect(overlay.open_style_dialog)
    menu.addAction(act_style)

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

    # ---- Doblaje de voz ----
    act_dub = QAction("🔊 Doblaje de voz (F11)", menu, checkable=True)
    act_dub.setChecked(dubber.enabled)
    act_dub.triggered.connect(lambda checked: overlay.dub_toggle_requested.emit())
    menu.addAction(act_dub)

    voice_menu = menu.addMenu("   🎙 Voz del doblaje")
    voice_menu.setStyleSheet(menu.styleSheet())
    voice_group = QActionGroup(voice_menu)
    for key, label in (("female", "Voz de mujer"), ("male", "Voz de hombre")):
        a = QAction(label, voice_menu, checkable=True)
        a.setChecked(dubber.gender == key)

        def _on_voice(checked, k=key):
            dubber.set_gender(k)
            overlay.save_setting("dub_gender", k)

        a.triggered.connect(_on_voice)
        voice_group.addAction(a)
        voice_menu.addAction(a)

    dev_menu = menu.addMenu("   🎧 Salida de la voz")
    dev_menu.setStyleSheet(menu.styleSheet())
    dev_group = QActionGroup(dev_menu)

    def _fill_devices():
        dev_menu.clear()
        options = [("", "Predeterminada (silencia la captura mientras habla)")]
        options += [(n, n) for n in dubber.player.list_outputs()]
        for value, label in options:
            a = QAction(label if len(label) < 60 else label[:57] + "…", dev_menu, checkable=True)
            a.setChecked(dubber.device == value)

            def _on_dev(checked, v=value):
                dubber.set_device(v)
                overlay.save_setting("dub_device", v)
                overlay._flash("🎧 Salida de voz: " + (v or "predeterminada"))

            a.triggered.connect(_on_dev)
            dev_group.addAction(a)
            dev_menu.addAction(a)

    dev_menu.aboutToShow.connect(_fill_devices)

    act_gloss = QAction("📖 Recargar glosario (config.json)", menu)

    def _reload_glossary():
        if reload_glossary:
            reload_glossary()
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

    tray.sync_dub = lambda: act_dub.setChecked(dubber.enabled)
    tray.show()
    return tray

"""
Overlay translúcido con PyQt6 — subtítulos en vivo y modo gaming.
- F6: Modo Subtítulos Gaming HUD (1-2 líneas estilo cine/juego)
- F7: Ciclar opacidad · F8: Click-through · F9: Mostrar/Ocultar
- F10: Modo compacto (solo traducción) · Ctrl+rueda: Tamaño de fuente
- Selector de idioma destino en vivo con persistencia en config.json
- Se atenúa solo tras 6 s sin actividad; despierta con texto nuevo.
"""
import html as html_mod
import json
import os
from collections import deque
from datetime import datetime

from apppath import app_dir

from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut, QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QWidget,
    QHBoxLayout, QPushButton, QTextEdit, QMenu,
)

SCREEN_ENTRIES = 200      # frases navegables con scroll en modo normal
PER_ENTRY_CHARS = 300     # tope de caracteres mostrados por frase
TEXT_AREA_HEIGHT = 280    # alto fijo del visor en modo historial
WIDTH_PRESETS = [440, 560, 720, 900]
CONFIG_PATH = os.path.join(app_dir(), "config.json")
OPACITY_STEPS = [35, 65, 100, 150, 210]
FADE_AFTER_MS = 6000
FADED_OPACITY = 0.35

AVAILABLE_LANGUAGES = [
    ("es", "🇪🇸 Español"),
    ("en", "🇬🇧 English"),
    ("pt", "🇧🇷 Português"),
    ("fr", "🇫🇷 Français"),
    ("de", "🇩🇪 Deutsch"),
    ("it", "🇮🇹 Italiano"),
    ("ja", "🇯🇵 日本語"),
    ("ko", "🇰🇷 한국어"),
    ("zh-CN", "🇨🇳 中文"),
    ("ru", "🇷🇺 Русский"),
]


class TranslationOverlay(QWidget):
    language_changed = pyqtSignal(str)

    def __init__(self, width: int | None = None, opacity: int | None = None, target_lang: str = "es"):
        super().__init__()
        cfg = self._load_config()
        self.bg_alpha = opacity if opacity is not None else cfg.get("opacity", 90)
        self.font_size = cfg.get("font_size", 13)
        self.compact = cfg.get("compact", False)
        self.mode = cfg.get("mode", "history")  # "history" o "subtitle"
        self.target_lang = cfg.get("target_lang", target_lang)

        self.panel_width = width if width is not None else cfg.get("width", 560)
        if self.panel_width not in WIDTH_PRESETS:
            self.panel_width = 560
        self.collapsed = False
        self.recording = True
        self.history: list[dict] = []
        self.entries: deque[dict] = deque(maxlen=SCREEN_ENTRIES)
        self.click_through = False
        self._drag_pos: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(self.panel_width)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 8, 12, 8)
        self.layout.setSpacing(4)

        # ---------- Cintillo superior ----------
        self.bar = QHBoxLayout()
        self.bar.setContentsMargins(0, 0, 0, 2)
        self.bar.setSpacing(4)

        self.title = self._label("🎙 Traductor en vivo", "#9be8ff",
                                 max(9, self.font_size - 3), italic=True)
        self.title.setToolTip(
            "F6: Gaming Subtitle · F7: Opacidad · F8: Click-through\n"
            "F9: Ocultar · F10: Compacto · Ctrl+rueda: Tamaño fuente\n"
            "Arrastrar: Mover ventana")
        self.bar.addWidget(self.title)
        self.bar.addStretch()

        def _btn(text, tip, slot):
            b = QPushButton(text)
            b.setFixedHeight(22)
            b.setMinimumWidth(24)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{color:#c8d0dc;background:rgba(255,255,255,18);"
                "border:none;border-radius:4px;font-size:11px;padding:2px 4px;}"
                "QPushButton:hover{background:rgba(255,255,255,60);"
                "color:#ffffff;}")
            b.clicked.connect(slot)
            self.bar.addWidget(b)
            return b

        # Botón selector de idioma rápido
        self.btn_lang = _btn(f"🌐 {self.target_lang.upper()}", "Cambiar idioma destino", self.open_language_menu)
        self.btn_lang.setStyleSheet(
            "QPushButton{color:#9be8ff;background:rgba(0,180,255,25);"
            "border:1px solid rgba(0,180,255,50);border-radius:4px;font-size:11px;font-weight:bold;padding:1px 6px;}"
            "QPushButton:hover{background:rgba(0,180,255,65);color:#ffffff;}")

        # Botón modo Gaming HUD Subtitle
        self.btn_gaming = _btn("🎮" if self.mode == "subtitle" else "💬",
                               "Alternar modo Gaming Subtítulo HUD (F6)",
                               self.toggle_gaming_mode)
        self._style_gaming_btn()

        self.btn_eye = _btn("👁", "Mostrar/ocultar originales e idioma fuente (F10)",
                            self.toggle_compact)
        self.btn_rec = _btn("⏺", "Registro de transcripción: ACTIVO",
                            self.toggle_recording)
        self._style_rec()
        self.btn_save = _btn("💾", "Guardar transcripción de la sesión",
                             self.save_transcript)
        self.btn_min = _btn("–", "Minimizar (colapsar al cintillo)",
                            self.toggle_collapse)
        self.btn_scale = _btn("⤢", "Escalar ancho del panel",
                              self.cycle_width)
        self.btn_close = _btn("✕", "Cerrar la aplicación",
                              self.close_app)
        self.btn_close.setStyleSheet(
            "QPushButton{color:#c8d0dc;background:rgba(255,255,255,18);"
            "border:none;border-radius:4px;font-size:11px;padding:2px 4px;}"
            "QPushButton:hover{background:rgba(220,60,60,200);"
            "color:#ffffff;}")
        self.layout.addLayout(self.bar)

        # Visor de texto
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setFrameStyle(0)
        self.text.setFixedHeight(TEXT_AREA_HEIGHT if self.mode == "history" else 105)
        self.text.viewport().setAutoFillBackground(False)
        self.text.setStyleSheet(
            "QTextEdit{background:transparent;border:none;}"
            "QScrollBar:vertical{background:rgba(255,255,255,15);width:7px;"
            "border-radius:3px;margin:0;}"
            "QScrollBar::handle:vertical{background:rgba(255,255,255,90);"
            "border-radius:3px;min-height:24px;}"
            "QScrollBar::handle:vertical:hover{background:rgba(255,255,255,150);}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        self.text.installEventFilter(self)
        self.text.viewport().installEventFilter(self)
        self.layout.addWidget(self.text)

        # Atajos locales
        self._shortcuts = [
            QShortcut(QKeySequence("F6"), self, activated=self.toggle_gaming_mode),
            QShortcut(QKeySequence("F7"), self, activated=self.cycle_opacity),
            QShortcut(QKeySequence("F8"), self, activated=self.toggle_click_through),
            QShortcut(QKeySequence("F9"), self, activated=self.toggle_visible),
            QShortcut(QKeySequence("F10"), self, activated=self.toggle_compact),
        ]

        # Auto-atenuado por inactividad
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(
            lambda: self.setWindowOpacity(FADED_OPACITY))
        self._wake()

        pos = cfg.get("pos")
        if pos and len(pos) == 2:
            self.move(pos[0], pos[1])
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            if self.mode == "subtitle":
                self.move((screen.width() - self.panel_width) // 2, screen.height() - 170)
            else:
                self.move(screen.width() - self.panel_width - 30, 60)

    # ---------- Config ----------

    def _load_config(self) -> dict:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_config(self):
        try:
            cfg = self._load_config()
            cfg.update({
                "opacity": self.bg_alpha,
                "font_size": self.font_size,
                "compact": self.compact,
                "mode": self.mode,
                "target_lang": self.target_lang,
                "width": self.panel_width,
                "pos": [self.x(), self.y()],
            })
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
        except Exception:
            pass

    def closeEvent(self, e):
        self._save_config()
        super().closeEvent(e)

    # ---------- UI Helpers ----------

    def _label(self, text, color, size, italic=False) -> QLabel:
        lbl = QLabel(text)
        f = QFont("Segoe UI", size)
        f.setItalic(italic)
        lbl.setFont(f)
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        shadow = QGraphicsDropShadowEffect(blurRadius=6, xOffset=1, yOffset=1)
        shadow.setColor(QColor(0, 0, 0, 230))
        lbl.setGraphicsEffect(shadow)
        return lbl

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QBrush, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.mode == "subtitle":
            p.setBrush(QBrush(QColor(6, 8, 14, min(self.bg_alpha, 160))))
            p.setPen(QPen(QColor(255, 255, 255, 25), 1))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        else:
            p.setBrush(QBrush(QColor(10, 12, 20, self.bg_alpha)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(self.rect(), 12, 12)

    def _wake(self):
        self.setWindowOpacity(1.0)
        self._fade_timer.start(FADE_AFTER_MS)

    def _apply_fonts(self):
        self.title.setFont(QFont("Segoe UI", max(9, self.font_size - 3),
                                 italic=True))
        self._refresh()

    def _style_gaming_btn(self):
        if self.mode == "subtitle":
            self.btn_gaming.setText("🎮")
            self.btn_gaming.setToolTip("Modo actual: Subtítulos Gaming HUD (F6 para volver a Historial)")
            self.btn_gaming.setStyleSheet(
                "QPushButton{color:#ffe066;background:rgba(255,200,0,40);"
                "border:1px solid rgba(255,200,0,100);border-radius:4px;font-size:11px;padding:2px 4px;}"
                "QPushButton:hover{background:rgba(255,200,0,80);color:#ffffff;}")
        else:
            self.btn_gaming.setText("💬")
            self.btn_gaming.setToolTip("Modo actual: Historial Completo (F6 para modo Subtítulos Gaming)")
            self.btn_gaming.setStyleSheet(
                "QPushButton{color:#c8d0dc;background:rgba(255,255,255,18);"
                "border:none;border-radius:4px;font-size:11px;padding:2px 4px;}"
                "QPushButton:hover{background:rgba(255,255,255,60);color:#ffffff;}")

    # ---------- Idioma Destino ----------

    def open_language_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background-color:#161922;color:#e0e6ed;border:1px solid #333a4d;border-radius:6px;padding:4px;}"
            "QMenu::item{padding:6px 20px;border-radius:4px;font-family:'Segoe UI';font-size:11px;}"
            "QMenu::item:selected{background-color:#2a334a;color:#ffffff;}")
        for code, label in AVAILABLE_LANGUAGES:
            check = " ✓" if code == self.target_lang else ""
            action = QAction(f"{label}{check}", self)
            action.triggered.connect(lambda checked, c=code: self.set_target_language(c))
            menu.addAction(action)
        menu.exec(self.btn_lang.mapToGlobal(QPoint(0, self.btn_lang.height() + 2)))

    def set_target_language(self, lang_code: str):
        self.target_lang = lang_code
        self.btn_lang.setText(f"🌐 {lang_code.upper()}")
        self._flash(f"🌐 Idioma: {lang_code.upper()}")
        self.language_changed.emit(lang_code)
        self._save_config()

    # ---------- Acciones / Hotkeys ----------

    def toggle_gaming_mode(self):
        self.mode = "subtitle" if self.mode == "history" else "history"
        if self.mode == "subtitle":
            self.text.setFixedHeight(105)
            self._flash("🎮 Modo Gaming Subtítulos HUD")
        else:
            self.text.setFixedHeight(TEXT_AREA_HEIGHT)
            self._flash("💬 Modo Historial Completo")
        self._style_gaming_btn()
        self.update()
        self._refresh()
        self._save_config()

    def cycle_opacity(self):
        i = min(range(len(OPACITY_STEPS)),
                key=lambda k: abs(OPACITY_STEPS[k] - self.bg_alpha))
        self.bg_alpha = OPACITY_STEPS[(i + 1) % len(OPACITY_STEPS)]
        self._flash(f"Opacidad: {int(self.bg_alpha / 255 * 100)}%")
        self.update()
        self._save_config()

    def toggle_click_through(self):
        self.click_through = not self.click_through
        self.setWindowFlag(
            Qt.WindowType.WindowTransparentForInput, self.click_through)
        self.show()
        if self.click_through:
            self._flash("🛡 Click-through ACTIVO (F8: desactivar)")
        else:
            self._flash("🖱 Click-through DESACTIVADO")

    def toggle_visible(self):
        self.setVisible(not self.isVisible())

    def toggle_compact(self):
        self.compact = not self.compact
        self._flash("Modo Compacto: " + ("ON" if self.compact else "OFF"))
        self._refresh()
        self._save_config()

    def _style_rec(self):
        color = "#ff6b6b" if self.recording else "#8a8f98"
        self.btn_rec.setText("⏺" if self.recording else "⭘")
        self.btn_rec.setToolTip(
            "Registro de transcripción: "
            + ("ACTIVO" if self.recording else "PAUSADO"))
        self.btn_rec.setStyleSheet(
            f"QPushButton{{color:{color};background:rgba(255,255,255,18);"
            "border:none;border-radius:4px;font-size:11px;padding:2px 4px;}"
            "QPushButton:hover{background:rgba(255,255,255,60);}")

    def toggle_recording(self):
        self.recording = not self.recording
        self._style_rec()
        self._flash("⏺ Registro activo" if self.recording else "⏸ Registro pausado")

    def save_transcript(self):
        if not self.history:
            self._flash("Nada que guardar aún")
            return
        folder = os.path.join(app_dir(), "transcripciones")
        try:
            os.makedirs(folder, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(folder, f"transcripcion_{stamp}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("Transcripción — "
                        + datetime.now().strftime("%Y-%m-%d %H:%M") + "\n")
                f.write("=" * 60 + "\n\n")
                for e in self.history:
                    f.write(f"[{e['time']}] [{e['lang'].upper()}] "
                            f"{e['orig']}\n")
                    f.write(" " * 11 + f"[→] {e['trans']}\n\n")
            self._flash("💾 Guardado: " + os.path.basename(path))
        except Exception as ex:
            self._flash("Error al guardar: " + type(ex).__name__)

    def _flash(self, msg: str, ms: int = 2500):
        self.title.setText(msg)
        QTimer.singleShot(ms, lambda: self.title.setText(
            "🎙 Traductor en vivo"))

    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        self.btn_min.setText("▢" if self.collapsed else "–")
        self.btn_min.setToolTip("Restaurar" if self.collapsed
                                else "Minimizar (colapsar al cintillo)")
        self._refresh()

    def cycle_width(self):
        try:
            i = WIDTH_PRESETS.index(self.panel_width)
            self.panel_width = WIDTH_PRESETS[(i + 1) % len(WIDTH_PRESETS)]
        except ValueError:
            self.panel_width = WIDTH_PRESETS[1]
        self.setFixedWidth(self.panel_width)
        self.adjustSize()
        self._save_config()

    def close_app(self):
        self._save_config()
        QApplication.quit()

    def handle_hotkey(self, name: str):
        actions = {
            "f6": self.toggle_gaming_mode,
            "f7": self.cycle_opacity,
            "f8": self.toggle_click_through,
            "f9": self.toggle_visible,
            "f10": self.toggle_compact,
        }
        action = actions.get(name.lower())
        if action:
            action()

    def disable_local_shortcuts(self):
        for sc in self._shortcuts:
            sc.setEnabled(False)

    # ---------- Interacción ----------

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        if (ev.type() == QEvent.Type.Wheel
                and ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            delta = 1 if ev.angleDelta().y() > 0 else -1
            self.font_size = max(9, min(24, self.font_size + delta))
            self._apply_fonts()
            self._save_config()
            return True
        return super().eventFilter(obj, ev)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = 1 if e.angleDelta().y() > 0 else -1
            self.font_size = max(9, min(24, self.font_size + delta))
            self._apply_fonts()
            self._save_config()
        else:
            super().wheelEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        if self._drag_pos:
            self._save_config()
        self._drag_pos = None

    # ---------- API pública (señales) ----------

    def upsert_entry(self, uid: int, original: str, lang: str, final: bool):
        if self.entries and self.entries[-1]["id"] == uid:
            e = self.entries[-1]
            e["orig"], e["lang"], e["final"] = original, lang, final
        else:
            e = {"id": uid, "orig": original, "trans": "…",
                 "lang": lang, "final": final}
            self.entries.append(e)
        if final and self.recording and not e.get("logged"):
            e["logged"] = True
            e["time"] = datetime.now().strftime("%H:%M:%S")
            self.history.append(e)
            if len(self.history) > 5000:
                del self.history[:1000]
        self._wake()
        self._refresh()

    def set_translation(self, uid: int, translated: str):
        for e in reversed(self.entries):
            if e["id"] == uid:
                e["trans"] = translated
                break
        self._wake()
        self._refresh()

    @staticmethod
    def _clip(text: str) -> str:
        if len(text) > PER_ENTRY_CHARS:
            return text[:PER_ENTRY_CHARS - 1] + "…"
        return text

    def _refresh(self):
        if self.collapsed:
            self.text.hide()
            self.adjustSize()
            return
        self.text.show()

        entries_to_show = list(self.entries)
        if self.mode == "subtitle":
            # En modo gaming subtitle solo mostramos las últimas 1 o 2 frases
            entries_to_show = entries_to_show[-2:] if len(entries_to_show) >= 2 else entries_to_show

        parts = []
        is_sub = (self.mode == "subtitle")

        for e in entries_to_show:
            cursor = "" if e["final"] else " ▌"
            orig = html_mod.escape(self._clip(e["orig"]))
            trans = html_mod.escape(self._clip(e["trans"]))

            # Sombra y contorno de alto contraste para visibilidad sobre videojuegos
            glow_style = (
                "text-shadow: 1px 1px 0 #000, -1px -1px 0 #000, "
                "1px -1px 0 #000, -1px 1px 0 #000, 0 2px 5px rgba(0,0,0,0.95);"
            )

            if not self.compact:
                orig_color = "#e2e8f0" if is_sub else "#c8d0dc"
                orig_size = f"font-size:{self.font_size - 1}pt;" if is_sub else ""
                parts.append(
                    f'<div style="color:{orig_color};{glow_style}{orig_size}line-height:1.2;">'
                    f'<span style="color:#7ea8f8;font-weight:600">[{e["lang"].upper()}]</span> {orig}{cursor}</div>')
                cursor = ""

            display_trans = trans
            if self.compact and (trans == "…" or not trans):
                display_trans = orig

            trans_color = "#ffe066" if is_sub else "#7dffb2"
            trans_weight = "bold" if is_sub else "600"
            margin = "margin-bottom:4px;" if is_sub else "margin-bottom:7px;"
            font_bump = f"font-size:{self.font_size + 1}pt;" if is_sub else ""

            parts.append(
                f'<div style="color:{trans_color};font-weight:{trans_weight};'
                f'{glow_style}{font_bump}{margin}line-height:1.25;">&rarr; {display_trans}{cursor}</div>')


        sb = self.text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        old = sb.value()
        self.text.setHtml(
            f'<body style="font-family:\'Segoe UI\', sans-serif;'
            f'font-size:{self.font_size}pt">' + "".join(parts) + "</body>")
        sb.setValue(sb.maximum() if at_bottom else min(old, sb.maximum()))
        self.adjustSize()

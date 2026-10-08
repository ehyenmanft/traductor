"""
Panel de ajustes de estilo del overlay (🎨). Todo se aplica al instante sobre
el propio overlay (vista previa en vivo) y se guarda en config.json.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog,
                             QFontComboBox, QGridLayout, QHBoxLayout, QInputDialog,
                             QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout)

import overlay_style as osty

CSS = """
QDialog{background:#161922;color:#e0e6ed;font-family:'Segoe UI';font-size:11px;}
QLabel{color:#c8d0dc;}
QCheckBox{color:#e0e6ed;spacing:6px;}
QPushButton{background:#2a334a;color:#e0e6ed;border:1px solid #3a4560;border-radius:5px;padding:4px 8px;}
QPushButton:hover{background:#35405c;}
QComboBox,QSpinBox,QFontComboBox{background:#1f2433;color:#e0e6ed;border:1px solid #333a4d;
 border-radius:4px;padding:2px 4px;}
QSlider::groove:horizontal{height:4px;background:#333a4d;border-radius:2px;}
QSlider::handle:horizontal{background:#9be8ff;width:12px;margin:-5px 0;border-radius:6px;}
"""
COLOR_BUTTONS = [("trans_color", "Traducción"), ("orig_color", "Original"),
                 ("tag_color", "Etiqueta [EN]"), ("outline_color", "Contorno")]
ALIGN_LABELS = [("left", "Izquierda"), ("center", "Centro"), ("right", "Derecha")]
ANCHORS = [["top-left", "top-center", "top-right"],
           ["middle-left", "middle-center", "middle-right"],
           ["bottom-left", "bottom-center", "bottom-right"]]
ARROWS = [["↖", "⬆", "↗"], ["⬅", "⏺", "➡"], ["↙", "⬇", "↘"]]


class StyleDialog(QDialog):
    def __init__(self, overlay):
        super().__init__(None)
        self.ov = overlay
        self._syncing = False
        self.setWindowTitle("🎨 Estilo del overlay")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(CSS)
        self.setMinimumWidth(360)
        root = QVBoxLayout(self)

        # --- preset + plantillas ---
        row = QHBoxLayout()
        row.addWidget(QLabel("Estilo listo"))
        self.cb_preset = QComboBox()
        for key, p in osty.PRESETS.items():
            self.cb_preset.addItem(p["label"], key)
        self.cb_preset.addItem("✏️ Personalizado", "custom")
        self.cb_preset.activated.connect(self._on_preset)
        row.addWidget(self.cb_preset, 1)
        root.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("⭐ Mis plantillas"))
        self.cb_tpl = QComboBox()
        row.addWidget(self.cb_tpl, 1)
        for text, slot in (("Aplicar", self._tpl_apply), ("Guardar actual…", self._tpl_save),
                           ("🗑", self._tpl_delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        root.addLayout(row)

        # --- fuente y tamaño ---
        row = QHBoxLayout()
        row.addWidget(QLabel("Fuente"))
        self.cb_font = QFontComboBox()
        self.cb_font.currentFontChanged.connect(
            lambda f: self._set("font", f.family()))
        row.addWidget(self.cb_font, 1)
        row.addWidget(QLabel("Tamaño"))
        self.sp_size = QSpinBox()
        self.sp_size.setRange(9, 24)
        self.sp_size.valueChanged.connect(self._on_size)
        row.addWidget(self.sp_size)
        root.addLayout(row)

        # --- colores ---
        grid = QGridLayout()
        self.color_btns: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate(COLOR_BUTTONS):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self._pick_color(k))
            self.color_btns[key] = b
            grid.addWidget(b, i // 2, i % 2)
        root.addLayout(grid)

        # --- contorno, líneas, tamaño del original, opacidad ---
        grid = QGridLayout()
        self.sp_outline = QSpinBox(); self.sp_outline.setRange(*osty.LIMITS["outline"])
        self.sp_outline.valueChanged.connect(lambda v: self._set("outline", v))
        self.sp_lines = QSpinBox(); self.sp_lines.setRange(*osty.LIMITS["max_lines"])
        self.sp_lines.valueChanged.connect(lambda v: self._set("max_lines", v))
        self.sl_orig = QSlider(Qt.Orientation.Horizontal); self.sl_orig.setRange(50, 120)
        self.sl_orig.valueChanged.connect(lambda v: self._set("orig_scale", v / 100))
        self.sl_alpha = QSlider(Qt.Orientation.Horizontal); self.sl_alpha.setRange(0, 255)
        self.sl_alpha.valueChanged.connect(self._on_alpha)
        self.cb_align = QComboBox()
        for key, label in ALIGN_LABELS:
            self.cb_align.addItem(label, key)
        self.cb_align.activated.connect(lambda i: self._set("align", self.cb_align.itemData(i)))
        for r, (label, w) in enumerate((("Contorno (px)", self.sp_outline),
                                        ("Líneas visibles (HUD)", self.sp_lines),
                                        ("Alineación", self.cb_align),
                                        ("Tamaño del original", self.sl_orig),
                                        ("Opacidad del panel", self.sl_alpha))):
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(w, r, 1)
        root.addLayout(grid)

        # --- casillas ---
        self.checks: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        for i, (key, label) in enumerate((("shadow", "Sombra"), ("bold", "Negrita (traducción)"),
                                          ("italic", "Cursiva"), ("show_tag", "Etiqueta de idioma"),
                                          ("anim", "Fundido de entrada"), ("compact", "Solo traducción"))):
            c = QCheckBox(label)
            c.toggled.connect(lambda v, k=key: self._on_check(k, v))
            self.checks[key] = c
            grid.addWidget(c, i // 2, i % 2)
        root.addLayout(grid)

        # --- posición ---
        root.addWidget(QLabel("Posición en pantalla"))
        grid = QGridLayout()
        grid.setSpacing(3)
        for r, names in enumerate(ANCHORS):
            for c, name in enumerate(names):
                b = QPushButton(ARROWS[r][c])
                b.setFixedWidth(46)
                b.clicked.connect(lambda _=False, n=name: self.ov.anchor_to(n))
                grid.addWidget(b, r, c)
        wrap = QHBoxLayout(); wrap.addLayout(grid); wrap.addStretch()
        root.addLayout(wrap)

        row = QHBoxLayout()
        b = QPushButton("↺ Restablecer estilo")
        b.clicked.connect(self._reset)
        row.addWidget(b)
        row.addStretch()
        b = QPushButton("Cerrar")
        b.clicked.connect(self.close)
        row.addWidget(b)
        root.addLayout(row)

    # ---------- sincronía ----------

    def sync_from_style(self):
        st = self.ov.ostyle
        self._syncing = True
        try:
            i = self.cb_preset.findData(st.preset)
            self.cb_preset.setCurrentIndex(i if i >= 0 else self.cb_preset.findData("custom"))
            self.cb_font.setCurrentFont(QFont(st.font))
            self.sp_size.setValue(self.ov.font_size)
            for key, b in self.color_btns.items():
                c = getattr(st, key)
                dark = QColor(c).lightness() < 128
                b.setStyleSheet(f"background:{c};color:{'#ffffff' if dark else '#000000'};")
            self.sp_outline.setValue(st.outline)
            self.sp_lines.setValue(st.max_lines)
            self.sl_orig.setValue(int(st.orig_scale * 100))
            self.sl_alpha.setValue(int(self.ov.bg_alpha))
            self.cb_align.setCurrentIndex(self.cb_align.findData(st.align))
            for key, c in self.checks.items():
                c.setChecked(self.ov.compact if key == "compact" else bool(getattr(st, key)))
            self._fill_templates()
        finally:
            self._syncing = False

    def _fill_templates(self):
        self.cb_tpl.clear()
        for name in (self.ov._load_config().get("style_templates") or {}):
            self.cb_tpl.addItem(name, name)

    # ---------- acciones ----------

    def _set(self, key, value):
        if not self._syncing:
            self.ov.set_style_field(key, value)
            self.sync_from_style()

    def _on_preset(self, i):
        key = self.cb_preset.itemData(i)
        if key != "custom":
            self.ov.apply_style_preset(key)
            self.sync_from_style()

    def _on_size(self, v):
        if not self._syncing:
            self.ov.font_size = v
            self.ov.style_changed()

    def _on_alpha(self, v):
        if not self._syncing:
            self.ov.bg_alpha = v
            self.ov.update()
            self.ov._save_config()

    def _on_check(self, key, v):
        if self._syncing:
            return
        if key == "compact":
            self.ov.compact = v
            self.ov.style_changed()
        else:
            self._set(key, v)

    def _pick_color(self, key):
        c = QColorDialog.getColor(QColor(getattr(self.ov.ostyle, key)), self, "Elegir color")
        if c.isValid():
            self._set(key, c.name())

    def _reset(self):
        self.ov.apply_style_preset(self.ov.ostyle.preset if self.ov.ostyle.preset in osty.PRESETS else "gamer")
        self.sync_from_style()

    # ---------- plantillas propias ----------

    def _tpl_save(self):
        name, ok = QInputDialog.getText(self, "Guardar plantilla", "Nombre:")
        name = (name or "").strip()[:40]
        if ok and name:
            cfg = self.ov._load_config()
            tpls = dict(cfg.get("style_templates") or {})
            if len(tpls) >= 12 and name not in tpls:
                return
            tpls[name] = osty.to_dict(self.ov.ostyle)
            self.ov.save_setting("style_templates", tpls)
            self._fill_templates()
            self.cb_tpl.setCurrentIndex(self.cb_tpl.findData(name))

    def _tpl_apply(self):
        name = self.cb_tpl.currentData()
        tpl = (self.ov._load_config().get("style_templates") or {}).get(name)
        if tpl:
            self.ov.ostyle = osty.from_dict({**tpl, "preset": "custom"})
            self.ov.style_changed()
            self.sync_from_style()

    def _tpl_delete(self):
        name = self.cb_tpl.currentData()
        tpls = dict(self.ov._load_config().get("style_templates") or {})
        if name in tpls:
            del tpls[name]
            self.ov.save_setting("style_templates", tpls)
            self._fill_templates()

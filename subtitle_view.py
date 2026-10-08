"""
Visor de subtítulos para el modo HUD: dibuja el texto con QPainterPath, lo que
permite CONTORNO y SOMBRA reales (el motor de texto enriquecido de Qt ignora
text-shadow), alineación, número máximo de líneas, alto automático y fundido
de entrada de la frase nueva.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QPointF, QSize, Qt, QTimer
from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QPainter, QPainterPath,
                         QPen)
from PyQt6.QtWidgets import QWidget

from overlay_style import OverlayStyle

FADE_SECONDS = 0.18
PAD = 6


class SubtitleView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.style_ = OverlayStyle()
        self.font_size = 13
        self.compact = False
        self._entries: list[dict] = []
        self._lines: list[dict] = []
        self._born: dict[int, float] = {}
        self._height = 40
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._tick)

    # ---------- datos ----------

    def set_data(self, entries: list[dict], style: OverlayStyle, font_size: int,
                 compact: bool):
        self.style_, self.font_size, self.compact = style, font_size, compact
        self._entries = entries[-max(style.max_lines, 1):]
        now = time.monotonic()
        for e in self._entries:
            self._born.setdefault(e["id"], now)
        if len(self._born) > 200:
            self._born = {k: v for k, v in self._born.items()
                          if k in {e["id"] for e in self._entries}}
        self._relayout()
        if style.anim and any(now - self._born[e["id"]] < FADE_SECONDS for e in self._entries):
            self._anim.start()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._relayout(resize_only=True)

    def sizeHint(self) -> QSize:
        return QSize(max(self.width(), 200), self._height)

    # ---------- fuentes ----------

    def _fonts(self):
        st = self.style_
        ft = QFont(st.font)
        ft.setPointSizeF(self.font_size + 1)
        ft.setBold(st.bold)
        ft.setItalic(st.italic)
        fo = QFont(st.font)
        fo.setPointSizeF(max(7.0, self.font_size * st.orig_scale))
        return ft, fo

    # ---------- maquetación ----------

    def _wrap(self, segs: list[tuple[str, QFont, str]], width: float):
        """Divide una frase (lista de (texto, fuente, color)) en líneas que caben."""
        lines, cur, cur_w = [], [], 0.0
        for text, font, color in segs:
            fm = QFontMetricsF(font)
            space = fm.horizontalAdvance(" ")
            for word in text.split(" "):
                if not word:
                    continue
                w = fm.horizontalAdvance(word)
                add = w if not cur else space + w
                if cur and cur_w + add > width:
                    lines.append(cur)
                    cur, cur_w, add = [], 0.0, w
                cur.append(((" " if cur else "") + word, font, color))
                cur_w += add
        if cur:
            lines.append(cur)
        return lines

    def _relayout(self, resize_only: bool = False):
        st = self.style_
        ft, fo = self._fonts()
        avail = max(60.0, self.width() - 2 * PAD - 2 * st.outline)
        blocks = []                       # una frase = un bloque indivisible: (uid, líneas)
        for e in self._entries:
            cursor = "" if e["final"] else " ▌"
            lines = []
            if not self.compact:
                segs = []
                if st.show_tag:
                    segs.append((f"[{e['lang'].upper()}]", fo, st.tag_color))
                segs.append((e["orig"], fo, st.orig_color))
                lines += self._wrap(segs, avail)
            trans = e["trans"] if e["trans"] and e["trans"] != "…" else (e["orig"] if self.compact else "…")
            lines += self._wrap([("→ " + trans + cursor, ft, st.trans_color)], avail)
            blocks.append((e["id"], lines))
        # bloques COMPLETOS desde el final mientras quepan en max_lines; si el más
        # reciente solo ya excede el máximo, se muestran sus últimas líneas
        limit, chosen, total = max(st.max_lines, 1), [], 0
        for uid, lines in reversed(blocks):
            if not chosen:
                lines = lines[-limit:]
            elif total + len(lines) > limit:
                break
            chosen.append((uid, lines))
            total += len(lines)
        self._lines = [{"uid": uid, "segs": ln}
                       for uid, lines in reversed(chosen) for ln in lines]
        h = 2 * PAD + 2 * st.outline
        for ln in self._lines:
            h += self._line_height(ln)
        h = max(h, 28)
        self._height = int(h)
        if self.height() != self._height:
            self.setFixedHeight(self._height)
        self.updateGeometry()
        self.update()

    def _line_height(self, line: dict) -> float:
        fm_h = max(QFontMetricsF(f).height() for _, f, _ in line["segs"])
        return fm_h * self.style_.line_spacing

    # ---------- pintado ----------

    def _tick(self):
        now = time.monotonic()
        if not any(now - self._born.get(ln["uid"], 0) < FADE_SECONDS for ln in self._lines):
            self._anim.stop()
        self.update()

    def paintEvent(self, ev):
        st = self.style_
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        now = time.monotonic()
        y = PAD + st.outline
        for ln in self._lines:
            lh = self._line_height(ln)
            fm = QFontMetricsF(ln["segs"][0][1])
            width = sum(QFontMetricsF(f).horizontalAdvance(t) for t, f, _ in ln["segs"])
            if st.align == "center":
                x = (self.width() - width) / 2
            elif st.align == "right":
                x = self.width() - PAD - st.outline - width
            else:
                x = PAD + st.outline
            base = y + (lh - fm.height()) / 2 + fm.ascent()
            alpha = 1.0
            if st.anim:
                alpha = min(1.0, (now - self._born.get(ln["uid"], 0)) / FADE_SECONDS)
            self._draw_line(p, ln["segs"], x, base, alpha)
            y += lh
        p.end()

    def _draw_line(self, p: QPainter, segs, x: float, base: float, alpha: float):
        st = self.style_
        for text, font, color in segs:
            path = QPainterPath()
            path.addText(QPointF(x, base), font, text)
            if st.shadow:
                sh = QPainterPath(path)
                sh.translate(1.5, 2.0)
                p.fillPath(sh, QColor(0, 0, 0, int(170 * alpha)))
            if st.outline > 0:
                oc = QColor(st.outline_color)
                oc.setAlphaF(alpha)
                pen = QPen(oc, st.outline * 2)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.strokePath(path, pen)
            fc = QColor(color)
            fc.setAlphaF(alpha)
            p.fillPath(path, fc)
            x += QFontMetricsF(font).horizontalAdvance(text)

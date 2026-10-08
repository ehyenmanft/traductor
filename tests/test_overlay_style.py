import json, os, sys, tempfile, unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overlay_style as osty

try:
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QColorDialog, QInputDialog
    import overlay as ov
    import style_dialog as sd
    from subtitle_view import SubtitleView
    HAVE_QT = True
except Exception:       # pragma: no cover
    HAVE_QT = False


class StyleModel(unittest.TestCase):
    def test_presets_and_validation(self):
        st = osty.apply_preset("cine")
        self.assertEqual((st.align, st.max_lines, st.show_tag), ("center", 2, False))
        bad = osty.from_dict({"trans_color": "rojo", "outline": 99, "align": "diagonal",
                              "max_lines": "x", "font": "  ", "foo": 1, "bold": 0, "orig_scale": 9})
        self.assertEqual((bad.trans_color, bad.outline, bad.align, bad.max_lines, bad.bold, bad.orig_scale),
                         ("#ffe066", 8, "left", 4, False, 1.2))
        self.assertEqual(osty.from_dict("basura").preset, "gamer")
        self.assertEqual(osty.set_field(st, "outline", 5).preset, "custom")
        self.assertEqual(osty.from_dict(osty.to_dict(st)), st)           # ida y vuelta


def pixels(img: QImage, pred):
    return sum(1 for y in range(img.height()) for x in range(img.width()) if pred(img.pixelColor(x, y)))


@unittest.skipUnless(HAVE_QT, "sin PyQt6")
class Visual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.cfg = os.path.join(tempfile.mkdtemp(), "c.json")
        p = mock.patch.object(ov, "CONFIG_PATH", self.cfg)
        p.start(); self.addCleanup(p.stop)

    def make(self, **cfg):
        if cfg:
            json.dump(cfg, open(self.cfg, "w"))
        w = ov.TranslationOverlay(); w.mode = "subtitle"; w.show(); self.addCleanup(w.close)
        return w

    @staticmethod
    def entries(n, final=True):
        return [{"id": i, "orig": f"Original sentence number {i}", "trans": f"Traducción de la frase {i}",
                 "lang": "en", "final": final} for i in range(n)]

    def view(self, st, n=3, width=400, compact=False):
        v = SubtitleView(); v.resize(width, 50)
        v.set_data(self.entries(n), st, 13, compact)
        return v

    def test_outline_is_really_drawn(self):
        st = osty.set_field(osty.apply_preset("gamer"), "anim", False)
        v = self.view(st)
        v.show(); QTest.qWait(30)
        img = v.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        dark = lambda c: c.alpha() > 200 and c.red() < 40 and c.green() < 40 and c.blue() < 40
        yellow = lambda c: c.alpha() > 200 and c.red() > 220 and c.green() > 190 and c.blue() < 130
        self.assertGreater(pixels(img, dark), 300); self.assertGreater(pixels(img, yellow), 200)
        v2 = self.view(osty.set_field(osty.set_field(st, "outline", 0), "shadow", False))
        v2.show(); QTest.qWait(30)
        img2 = v2.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        self.assertEqual(pixels(img2, dark), 0)                             # sin contorno ni sombra: nada oscuro

    def test_shows_whole_blocks_within_max_lines(self):
        st = osty.set_field(osty.apply_preset("gamer"), "max_lines", 3)
        v = self.view(st, n=4, width=900)
        self.assertEqual({ln["uid"] for ln in v._lines}, {3})               # solo la última frase, completa
        self.assertEqual(len(v._lines), 2)                                  # original + traducción
        st2 = osty.set_field(st, "max_lines", 4)
        self.assertEqual({ln["uid"] for ln in self.view(st2, n=4, width=900)._lines}, {2, 3})
        small = self.view(osty.set_field(st, "max_lines", 1), n=2, width=900)
        self.assertEqual(len(small._lines), 1)                              # cabe una línea
        c = self.view(osty.apply_preset("gamer"), n=1, width=900, compact=True)
        self.assertEqual(len(c._lines), 1)                                  # compacto: solo traducción

    def test_height_follows_content_and_wraps(self):
        st = osty.apply_preset("classic")
        wide = self.view(st, n=1, width=900); narrow = self.view(st, n=1, width=220)
        self.assertGreater(narrow.height(), wide.height())

    def test_persistence_and_old_config(self):
        w = self.make()                                      # sin "style": preset según el modo
        self.assertEqual(w.ostyle.preset, "classic")         # el modo por defecto es historial
        w.set_style_field("outline", 5); w.apply_style_preset("neon"); w.set_style_field("trans_color", "#123456")
        w.close()
        saved = json.load(open(self.cfg, encoding="utf-8"))["style"]
        self.assertEqual((saved["preset"], saved["trans_color"]), ("custom", "#123456"))
        w2 = ov.TranslationOverlay(); self.addCleanup(w2.close)
        self.assertEqual(w2.ostyle.trans_color, "#123456")
        broken = self.make(style={"outline": "mucho", "align": 3, "trans_color": None})     # no debe romper
        self.assertEqual(broken.ostyle.outline, osty.apply_preset("classic").outline)   # vuelve al preset del modo

    def test_history_mode_uses_style(self):
        w = self.make(); w.mode = "history"
        w.apply_style_preset("terminal")
        for e in self.entries(2):
            w.upsert_entry(e["id"], e["orig"], "en", True); w.set_translation(e["id"], e["trans"])
        w._render()
        html = w.text.toHtml()
        self.assertIn("#39ff6a", html.lower()); self.assertIn("consolas", html.lower())

    def test_dialog_controls_apply_and_save(self):
        w = self.make(); d = sd.StyleDialog(w); d.sync_from_style(); self.addCleanup(d.close)
        d._on_preset(d.cb_preset.findData("cine"))
        self.assertEqual((w.ostyle.align, w.ostyle.preset), ("center", "cine"))
        d.sp_outline.setValue(6); self.assertEqual((w.ostyle.outline, w.ostyle.preset), (6, "custom"))
        d.sp_lines.setValue(5); d.cb_align.setCurrentIndex(d.cb_align.findData("right")); d.cb_align.activated.emit(d.cb_align.currentIndex())
        self.assertEqual((w.ostyle.max_lines, w.ostyle.align), (5, "right"))
        with mock.patch.object(QColorDialog, "getColor", return_value=QColor("#ff0000")):
            d._pick_color("trans_color")
        self.assertEqual(w.ostyle.trans_color, "#ff0000")
        d.checks["shadow"].setChecked(False); d.checks["compact"].setChecked(True); d.sp_size.setValue(18); d.sl_alpha.setValue(200)
        self.assertEqual((w.ostyle.shadow, w.compact, w.font_size, w.bg_alpha), (False, True, 18, 200))
        cfg = json.load(open(self.cfg, encoding="utf-8"))
        self.assertEqual((cfg["font_size"], cfg["opacity"], cfg["style"]["outline"]), (18, 200, 6))

    def test_templates_save_apply_delete(self):
        w = self.make(); d = sd.StyleDialog(w); d.sync_from_style(); self.addCleanup(d.close)
        d.sp_outline.setValue(7); d._set("trans_color", "#00ff00")
        with mock.patch.object(QInputDialog, "getText", return_value=("Mi estilo", True)):
            d._tpl_save()
        self.assertIn("Mi estilo", json.load(open(self.cfg, encoding="utf-8"))["style_templates"])
        w.apply_style_preset("minimal"); d.sync_from_style()
        d._tpl_apply()
        self.assertEqual((w.ostyle.outline, w.ostyle.trans_color), (7, "#00ff00"))
        d._tpl_delete()
        self.assertEqual(json.load(open(self.cfg, encoding="utf-8"))["style_templates"], {})

    def test_anchor_moves_window(self):
        w = self.make()
        w.anchor_to("top-left"); tl = (w.x(), w.y())
        w.anchor_to("bottom-right"); br = (w.x(), w.y())
        self.assertLess(tl[0], br[0]); self.assertLess(tl[1], br[1])

if __name__ == "__main__":
    unittest.main()

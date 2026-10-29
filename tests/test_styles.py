import hashlib, os, re, shutil, subprocess, sys, tempfile, time, unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import subtitle_style as ss
import video_translator as vt
from subtitle_ass import build_ass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAVE_FF = bool(shutil.which("ffmpeg"))
SYSTEM = {"Liberation Sans", "Liberation Serif", "DejaVu Sans Mono", "Roboto"}      # vienen con apt (fonts.sh)


def listed():
    out = {}
    with open(os.path.join(ROOT, "deploy", "fonts.list"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                path, fam = line.rstrip("\n").split("|")
                out[fam] = path
    return out


def installed(family):
    r = subprocess.run(["fc-list", family], capture_output=True, text=True)
    return bool(r.stdout.strip())


class Catalog(unittest.TestCase):
    def test_every_font_is_installed_by_fonts_sh(self):
        lst = listed()
        for key, (label, family) in ss.FONTS.items():
            self.assertTrue(family in lst or family in SYSTEM, f"{key}: «{family}» no está en deploy/fonts.list")
        for fam, path in lst.items():                                    # rutas bien formadas y sin duplicados
            self.assertRegex(path, r"^(ofl|apache|ufl)/[a-z0-9]+/[A-Za-z0-9\-\[\]]+\.ttf$")
        self.assertEqual(len(set(lst.values())), len(lst))
        with open(os.path.join(ROOT, "deploy", "fonts.sh"), encoding="utf-8") as fh:
            script = fh.read()
        self.assertIn("fonts.list", script)
        self.assertEqual(subprocess.run(["bash", "-n", os.path.join(ROOT, "deploy", "fonts.sh")]).returncode, 0)

    def test_presets_only_use_known_values_and_apply_cleanly(self):
        for name, p in ss.PRESETS.items():
            st = ss.apply_preset(ss.SubtitleStyle(target="es"), name)
            self.assertIn(st.font, ss.FONTS, name); self.assertIn(st.color, ss.COLORS, name)
            self.assertIn(st.outline_color, ss.COLORS, name); self.assertIn(st.hl_color, ss.COLORS, name)
            self.assertIn(st.outline, ss.OUTLINES, name); self.assertIn(st.bg, ss.BGS, name)
            self.assertIn(st.anim, ss.ANIMS, name); self.assertIn(st.highlight, ss.HIGHLIGHTS, name)
            self.assertIn(st.words, ss.WORDS, name); self.assertIn(st.progress, ss.PROGRESS, name)
            segs = [ss.Segment(0, 3, "hello there my friend", "en", "hola amigo mío, ¿cómo estás?",
                               words=[("hello", 0, .5), ("there", .5, 1), ("my", 1, 1.5), ("friend", 1.5, 2)])]
            ass = build_ass(segs, st, 1080, 1920, 3.0)
            self.assertIn("[Events]", ass); self.assertIn(ss.FONTS[st.font][1], ass)

    def test_single_weight_fonts_never_ask_for_synthetic_bold(self):
        for key in ss.SINGLE_WEIGHT:
            st = ss.SubtitleStyle(font=key, bold=True)
            line = [l for l in build_ass([ss.Segment(0, 2, "a", "en", "b")], st, 640, 360, 2).splitlines() if l.startswith("Style:")][0]
            self.assertEqual(line.split(",")[7], "0", key)                 # campo Bold del estilo ASS
        sans = [l for l in build_ass([ss.Segment(0, 2, "a", "en", "b")], ss.SubtitleStyle(bold=True), 640, 360, 2).splitlines()
                if l.startswith("Style:")][0]
        self.assertEqual(sans.split(",")[7], "-1")                         # las demás sí

    def test_colors_have_valid_ass_values(self):
        for k, (label, val) in ss.COLORS.items():
            self.assertRegex(val, r"^&H00[0-9A-F]{6}$", k)


@unittest.skipUnless(HAVE_FF, "necesita ffmpeg")
class Render(unittest.TestCase):
    def test_each_installed_font_renders_differently(self):
        d = tempfile.mkdtemp()
        frame = os.path.join(d, "f.jpg")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=640x360:d=1", "-frames:v", "1", frame],
                       check=True, capture_output=True)
        hashes, skipped = {}, []
        for key, (label, family) in ss.FONTS.items():
            if not installed(family):
                skipped.append(family); continue
            st = replace(ss.SubtitleStyle(font=key, target="es", bold=False), size="l")
            out = vt.render_preview(frame, st, os.path.join(d, f"{key}.jpg"), d)
            with open(out, "rb") as fh:
                hashes[key] = hashlib.md5(fh.read()).hexdigest()
        if len(hashes) < 3:
            self.skipTest("fuentes no instaladas en este equipo: " + ", ".join(skipped))
        dup = [k for k, h in hashes.items() if list(hashes.values()).count(h) > 1]
        self.assertEqual(dup, [], "estas fuentes se ven idénticas (libass no las encontró): %s" % dup)
        shutil.rmtree(d, ignore_errors=True)


try:
    import telegram  # noqa: F401
    HAVE_TG = True
except ImportError:
    HAVE_TG = False


@unittest.skipUnless(HAVE_TG, "sin telegram")
class Menu(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:a"); os.environ.setdefault("DEEPGRAM_API_KEY", "x")
        global tb
        import telegram_bot as tb

    def all_buttons(self, page, st=None):
        kb = tb.keyboard(123456, st or ss.SubtitleStyle(), page)
        return [b for row in kb.inline_keyboard for b in row]

    def test_pages_fit_telegram_limits_and_cover_everything(self):
        n_fonts = -(-len(ss.FONTS) // ss.FONTS_PER_PAGE); n_styles = -(-(len(ss.PRESETS) - ss.PRESETS_PER_PAGE) // ss.PRESETS_PER_PAGE)
        pages = ["main"] + [("font" if i == 1 else f"font{i}") for i in range(1, n_fonts + 1)] \
              + [("styles" if i == 1 else f"styles{i}") for i in range(1, n_styles + 1)] + ["color", "outline", "anim", "fx"]
        seen_fonts, seen_presets = set(), set()
        for pg in pages:
            btns = self.all_buttons(pg)
            self.assertLessEqual(len(btns), 60, pg)
            for b in btns:
                self.assertLessEqual(len(b.callback_data.encode()), 64, (pg, b.callback_data))
                m = re.match(r"o\|\d+\|(font|preset)\|(\w+)\|", b.callback_data)
                if m:
                    (seen_fonts if m.group(1) == "font" else seen_presets).add(m.group(2))
        self.assertEqual(seen_fonts, set(ss.FONTS)); self.assertEqual(seen_presets, set(ss.PRESETS))

    def test_pager_wraps_around(self):
        last = f"font{-(-len(ss.FONTS) // ss.FONTS_PER_PAGE)}"
        first_next = [b.callback_data for b in self.all_buttons("font") if b.text == "➡️"][0]
        self.assertTrue(first_next.endswith("|font2"))
        back = [b.callback_data for b in self.all_buttons("font") if b.text == "⬅️"][0]
        self.assertTrue(back.endswith("|" + last))                          # desde la primera, ⬅️ va a la última
        forward = [b.callback_data for b in self.all_buttons(last) if b.text == "➡️"][0]
        self.assertTrue(forward.endswith("|font"))

    def test_picking_a_font_or_style_on_a_later_page_stays_on_that_page(self):
        st = tb.set_option(ss.SubtitleStyle(), "font", "bebas"); self.assertEqual(st.font, "bebas")
        st = tb.set_option(st, "preset", "fire"); self.assertEqual((st.preset, st.font), ("fire", "passion"))
        st = tb.set_option(st, "color", "gold"); self.assertEqual(st.color, "gold")
        n = -(-(len(ss.PRESETS) - ss.PRESETS_PER_PAGE) // ss.PRESETS_PER_PAGE)
        for i in range(1, n + 1):
            page = "styles" if i == 1 else f"styles{i}"
            hit = [b for b in self.all_buttons(page) if "preset|fire|" in b.callback_data]
            if hit:
                self.assertTrue(hit[0].callback_data.endswith("|" + page)); break
        else:
            self.fail("«fire» no aparece en ninguna página de estilos")


if __name__ == "__main__":
    unittest.main()

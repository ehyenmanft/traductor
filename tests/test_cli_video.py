import contextlib, io, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cli_video as cli
import translate_engine as te
import video_translator as vt
from test_video_translator import FAKE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def results(text):
    return [l.split(": ", 1)[1] for l in text.splitlines() if l.startswith("RESULTADO: ")]


@unittest.skipUnless(shutil.which("ffmpeg"), "necesita ffmpeg")
class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.video = os.path.join(cls.d, "in.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=4", "-f", "lavfi", "-i", "sine=d=4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", cls.video],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def setUp(self):
        self.out_root = tempfile.mkdtemp()
        self.patches = [mock.patch.object(cli, "OUT_ROOT", self.out_root),
                        mock.patch.dict(os.environ, {"DEEPGRAM_API_KEY": "dg", "GROQ_API_KEY": ""}),
                        mock.patch.object(vt, "transcribe_file", lambda audio, key: vt.segments_from_deepgram(FAKE)),
                        mock.patch.object(te, "_google", lambda t, s, g: "TRAD " + t)]
        for p in self.patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(shutil.rmtree, self.out_root, True)

    def test_styles_lists_kidcut_fonts_and_positions(self):
        code, out, _ = run("styles")
        self.assertEqual(code, 0)
        for word in ("kidcut", "hormozi", "montxb", "top", "gold"):
            self.assertIn(word, out)

    def test_subtitle_with_kidcut_produces_video_and_srt(self):
        code, out, err = run("subtitle", self.video, "--style", "✨ KidCut", "--target", "fr", "--pos", "top")
        self.assertEqual(code, 0, err)
        paths = results(out)
        self.assertEqual(len(paths), 2); self.assertTrue(all(os.path.exists(p) for p in paths))
        self.assertTrue(paths[0].endswith(".mp4") and paths[1].endswith(".srt"))
        self.assertAlmostEqual(vt.probe(paths[0])["duration"], 4.0, delta=0.4)
        with open(paths[1], encoding="utf-8") as fh:
            self.assertIn("TRAD", fh.read())
        self.assertIn("KidCut", out)
        self.assertNotIn("dg", err.replace("dg_", ""))                     # no filtra la clave

    def test_bad_style_position_and_missing_keys_are_clear_errors(self):
        code, _, err = run("subtitle", self.video, "--style", "noexiste")
        self.assertEqual(code, 1); self.assertIn("No conozco el estilo", err)
        code, _, err = run("subtitle", self.video, "--pos", "diagonal")
        self.assertEqual(code, 1); self.assertIn("Posición inválida", err)
        code, _, err = run("subtitle", os.path.join(self.d, "nada.mp4"))
        self.assertEqual(code, 1); self.assertIn("No encuentro el video", err)
        with mock.patch.dict(os.environ, {"DEEPGRAM_API_KEY": ""}), mock.patch.object(cli, "cfg", lambda *a, **k: ""):
            code, _, err = run("subtitle", self.video)
        self.assertEqual(code, 1); self.assertIn("Falta la clave de Deepgram", err)

    def test_reel_cuts_clips_thumb(self):
        code, out, err = run("reel", self.video); self.assertEqual(code, 0, err)
        self.assertEqual((vt.probe(results(out)[0])["width"], vt.probe(results(out)[0])["height"]), (1080, 1920))
        code, out, err = run("reel", self.video, "--mode", "crop"); self.assertEqual(code, 0, err)
        code, out, err = run("cuts", self.video); self.assertEqual(code, 0, err); self.assertIn("silencios", out)
        code, out, err = run("clips", self.video, "--seconds", "2"); self.assertEqual(code, 0, err)
        self.assertEqual(len(results(out)), 2)
        code, out, err = run("thumb", self.video, "--title", "Hola"); self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(results(out)[0]))

    def test_carousel_without_groq_falls_back_and_texts_needs_groq(self):
        code, out, err = run("carousel", self.video, "--theme", "neon", "--slides", "4", "--target", "fr")
        self.assertEqual(code, 0, err); self.assertIn("groq_api_key", out)
        self.assertGreaterEqual(len(results(out)), 2); self.assertTrue(all(p.endswith(".png") for p in results(out)))
        with mock.patch.object(cli, "cfg", lambda env, key, default="": "dg" if "deepgram" in key else ""):
            code, _, err = run("texts", self.video)
        self.assertEqual(code, 1); self.assertIn("groq_api_key", err)

    def test_old_outputs_are_cleaned(self):
        old = os.path.join(self.out_root, "viejo"); os.makedirs(old)
        past = os.path.getmtime(old) - 5 * 86400; os.utime(old, (past, past))
        run("thumb", self.video)
        self.assertFalse(os.path.exists(old))


class Skills(unittest.TestCase):
    def test_skill_files_are_valid_and_installer_copies_them(self):
        base = os.path.join(ROOT, "claude_skills")
        names = sorted(os.listdir(base)); self.assertEqual(names, ["contenido-redes", "traductor-video"])
        for n in names:
            with open(os.path.join(base, n, "SKILL.md"), encoding="utf-8") as fh:
                text = fh.read()
            head = text.split("---")[1]
            self.assertIn(f"name: {n}", head); self.assertIn("description:", head)
            self.assertNotIn("gsk_", text); self.assertNotIn("api_hash", text)
        home = tempfile.mkdtemp()
        r = subprocess.run(["bash", os.path.join(ROOT, "deploy", "install_skills.sh")], capture_output=True, text=True,
                           env={**os.environ, "HOME": home}, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in names:
            self.assertTrue(os.path.exists(os.path.join(home, ".claude", "skills", n, "SKILL.md")))
        r2 = subprocess.run(["bash", os.path.join(ROOT, "deploy", "install_skills.sh")], capture_output=True, text=True,
                            env={**os.environ, "HOME": home}, timeout=60)
        self.assertEqual(r2.returncode, 0)                                  # idempotente
        shutil.rmtree(home, ignore_errors=True)

    def test_every_command_documented_in_the_skills_exists_in_the_cli(self):
        import re
        p = cli.parser()
        sub = [a for a in p._actions if a.dest == "cmd"][0].choices
        for n in os.listdir(os.path.join(ROOT, "claude_skills")):
            with open(os.path.join(ROOT, "claude_skills", n, "SKILL.md"), encoding="utf-8") as fh:
                text = fh.read()
            for cmd in re.findall(r"cli_video\.py (\w+)", text):
                self.assertIn(cmd, sub, f"{n}: «{cmd}» no existe")


if __name__ == "__main__":
    unittest.main()

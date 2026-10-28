import json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import content_tools as ct
import groq_models as gm
from video_translator import probe

HAVE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_video(path, w=640, h=360, speech=(2.0, 2.0), gap=2.0):
    """Video de prueba: voz (tono) – silencio – voz, con imagen en movimiento."""
    a, b = speech
    graph = (f"sine=frequency=440:duration={a}[s1];anullsrc=r=44100:cl=mono,atrim=0:{gap}[sil];"
             f"sine=frequency=330:duration={b}[s2];[s1][sil][s2]concat=n=3:v=0:a=1[a]")
    total = a + gap + b
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=25:duration={total}",
                    "-filter_complex", graph, "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", path], check=True, capture_output=True)
    return path


class Resp:
    def __init__(self, code=200, content="", headers=None):
        self.status_code, self.headers, self.text = code, headers or {}, content
        self.content = content.encode("utf-8") if isinstance(content, str) else content

    def json(self):
        return json.loads(self.content)


def chat_resp(text):
    return Resp(200, json.dumps({"choices": [{"message": {"content": text}}]}, ensure_ascii=False))


@unittest.skipUnless(HAVE, "necesita ffmpeg")
class Video(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.src = make_video(os.path.join(cls.d, "src.mp4"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_reel_blur_is_1080x1920_and_keeps_audio_and_duration(self):
        out = ct.reel_blur(self.src, os.path.join(self.d, "r.mp4"))
        i = probe(out)
        self.assertEqual((i["width"], i["height"]), (1080, 1920)); self.assertTrue(i["has_audio"])
        self.assertAlmostEqual(i["duration"], 6.0, delta=0.4)

    def test_reel_crop_is_1080x1920_for_wide_and_for_vertical_sources(self):
        out = ct.reel_crop(self.src, os.path.join(self.d, "c.mp4"), focus=0.2)
        self.assertEqual((probe(out)["width"], probe(out)["height"]), (1080, 1920))
        vert = make_video(os.path.join(self.d, "v.mp4"), 360, 640)
        out2 = ct.reel_crop(vert, os.path.join(self.d, "c2.mp4"))
        self.assertEqual((probe(out2)["width"], probe(out2)["height"]), (1080, 1920))

    def test_jump_cuts_remove_the_silence(self):
        res = ct.jump_cuts(self.src, os.path.join(self.d, "j.mp4"))
        self.assertIsNotNone(res.path)
        self.assertAlmostEqual(res.removed, 2.0, delta=0.9)                # ~2 s de silencio (menos el respiro)
        i = probe(res.path)
        self.assertAlmostEqual(i["duration"], 6.0 - res.removed, delta=0.5); self.assertTrue(i["has_audio"])
        self.assertLess(i["duration"], 5.0)

    def test_jump_cuts_without_silences_reports_instead_of_reencoding(self):
        solid = os.path.join(self.d, "solid.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3", "-f", "lavfi",
                        "-i", "sine=frequency=500:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", solid], check=True, capture_output=True)
        res = ct.jump_cuts(solid, os.path.join(self.d, "j2.mp4"))
        self.assertIsNone(res.path); self.assertIn("silencios", res.note)

    def test_jump_cuts_needs_audio(self):
        mute = os.path.join(self.d, "mute.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", mute], check=True, capture_output=True)
        with self.assertRaises(RuntimeError):
            ct.jump_cuts(mute, os.path.join(self.d, "j3.mp4"))

    def test_split_clips_cover_the_whole_video_without_exceeding_the_limit(self):
        out = os.path.join(self.d, "clips"); os.makedirs(out)
        paths = ct.split_clips(self.src, out, max_seconds=2.5)
        self.assertEqual(len(paths), 3)
        durs = [probe(p)["duration"] for p in paths]
        self.assertTrue(all(d <= 2.5 + 0.3 for d in durs), durs)
        self.assertAlmostEqual(sum(durs), 6.0, delta=0.6)

    def test_thumbnail_with_title(self):
        out = ct.thumbnail(self.src, os.path.join(self.d, "t.jpg"), "Así se hace ¡Ñandú!", samples=4)
        from PIL import Image
        with Image.open(out) as im:
            self.assertEqual(im.size, (640, 360))

    def test_speech_intervals_math(self):
        self.assertEqual(ct.speech_intervals(10, [(2, 4), (6, 7)], pad=0.0, min_len=0.1),
                         [(0.0, 2), (4, 6), (7, 10)])
        self.assertEqual(ct.speech_intervals(10, [(0, 1)], pad=0.0), [(1, 10)])
        self.assertEqual(ct.speech_intervals(10, [(2.0, 2.3)], pad=0.2, min_len=0.1), [(0.0, 10)])   # el respiro los une


class Carousel(unittest.TestCase):
    SLIDES = [{"title": "Los trucos que nadie te cuenta", "body": "Hilo 🧵"},
              {"title": "1. Revisa el informe", "body": "Hay formas de reportar tramposos dentro del juego, y es lo que hace la mayoría."},
              {"title": "2. Ya hay baneos", "body": "Se lanzó una gran ola de baneos contra cuentas que violaban los términos de servicio, incluso por boosting " * 2},
              {"title": "¿Te gustó?", "body": "Sígueme para más"}]

    def test_render_all_themes_with_accents_and_long_text(self):
        from PIL import Image
        for theme in ct.THEMES:
            d = tempfile.mkdtemp()
            paths = ct.render_carousel(self.SLIDES, d, theme, "@ehyenma")
            self.assertEqual(len(paths), 4)
            for p in paths:
                with Image.open(p) as im:
                    self.assertEqual(im.size, (1080, 1350))
            shutil.rmtree(d)

    def test_slides_from_ai_and_fallbacks(self):
        data = {"slides": [{"title": "Gancho", "body": ""}, {"title": "Idea 1", "body": "x"}, {"title": "Cierra", "body": "y"}]}
        with mock.patch.object(gm.requests, "get", side_effect=RuntimeError("sin red")), \
             mock.patch.object(gm.requests, "post", return_value=chat_resp(json.dumps(data))):
            slides, note = ct.carousel_slides("Hola. Esto es un video. Dice cosas.", "k", "es", 3)
        self.assertEqual([s["title"] for s in slides], ["Gancho", "Idea 1", "Cierra"]); self.assertEqual(note, "")
        slides, note = ct.carousel_slides("Primera frase importante. Segunda idea. Tercera idea. Cuarta. Quinta.", None, "es", 4)
        self.assertGreaterEqual(len(slides), 2); self.assertIn("groq_api_key", note)
        with mock.patch.object(gm.requests, "get", side_effect=RuntimeError("x")), \
             mock.patch.object(gm.requests, "post", return_value=Resp(500, "boom")):
            slides, note = ct.carousel_slides("Una frase. Otra frase.", "k", "es", 3)
        self.assertTrue(slides and "reparto simple" in note)                  # la IA falla: no se pierde el trabajo

    def test_caption_pack(self):
        data = {"titles": ["A", "B", "C"], "description": "Desc", "hashtags": ["gaming", "# trucos", "d2"], "hook_first_3s": "¡Mira!"}
        with mock.patch.object(gm.requests, "get", side_effect=RuntimeError("x")), \
             mock.patch.object(gm.requests, "post", return_value=chat_resp(json.dumps(data, ensure_ascii=False))):
            txt = ct.caption_pack("texto", "k", "es")
        self.assertIn("1. A", txt); self.assertIn("#gaming", txt); self.assertIn("#trucos", txt); self.assertIn("¡Mira!", txt)
        with self.assertRaises(RuntimeError):
            ct.caption_pack("texto", None)


class GroqModels(unittest.TestCase):
    def setUp(self):
        gm._cache.update(at=0.0, models=[]); gm._dead.clear()

    def test_picks_only_chat_models_from_the_account(self):
        ids = ["whisper-large-v3", "openai/gpt-oss-safeguard-20b", "canopylabs/orpheus-v1-english", "openai/gpt-oss-20b",
               "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "meta-llama/llama-prompt-guard-2-22m", "allam-2-7b"]
        got = gm.pick(ids)
        self.assertEqual(set(got), {"openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"})
        self.assertEqual(got[0], "openai/gpt-oss-120b")

    def test_chat_skips_retired_models_and_sets_reasoning_and_utf8(self):
        seen = []
        def post(url, **k):
            seen.append(k["json"]); m = k["json"]["model"]
            return chat_resp("¿Cómo está el artículo?") if m == "qwen/qwen3.8-27b" else Resp(404, "model not found")
        listing = Resp(200, json.dumps({"data": [{"id": "openai/gpt-oss-120b"}, {"id": "qwen/qwen3.8-27b"}]}))
        with mock.patch.object(gm.requests, "get", return_value=listing), mock.patch.object(gm.requests, "post", post):
            out = gm.chat("k", [{"role": "user", "content": "hi"}], json_mode=True)
            out2 = gm.chat("k", [{"role": "user", "content": "hi"}])
        self.assertEqual(out, "¿Cómo está el artículo?"); self.assertEqual(out2, out)
        self.assertEqual(seen[0]["reasoning_effort"], "low"); self.assertEqual(seen[0]["response_format"], {"type": "json_object"})
        self.assertIn("openai/gpt-oss-120b", gm._dead)
        self.assertEqual([s["model"] for s in seen].count("openai/gpt-oss-120b"), 1)   # el retirado no se reintenta

    def test_rate_limit_waits_and_repeats(self):
        seq = iter([Resp(429, "slow", {"retry-after": "0.5"}), chat_resp("ok")])
        with mock.patch.object(gm.requests, "get", return_value=Resp(200, json.dumps({"data": [{"id": "openai/gpt-oss-120b"}]}))), \
             mock.patch.object(gm.requests, "post", lambda *a, **k: next(seq)):
            self.assertEqual(gm.chat("k", [{"role": "user", "content": "hi"}]), "ok")

    def test_all_fail_raises_with_cause(self):
        with mock.patch.object(gm.requests, "get", side_effect=RuntimeError("x")), \
             mock.patch.object(gm.requests, "post", return_value=Resp(401, "bad key")):
            with self.assertRaises(RuntimeError) as c:
                gm.chat("k", [{"role": "user", "content": "hi"}])
        self.assertIn("401", str(c.exception))


if __name__ == "__main__":
    unittest.main()

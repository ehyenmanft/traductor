import os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import video_translator as vt

FAKE = {"results": {"channels": [{"alternatives": [{"words": [
    {"word": "hello", "punctuated_word": "Hello", "start": 0.5, "end": 0.9, "language": "en"},
    {"word": "everyone", "punctuated_word": "everyone.", "start": 0.9, "end": 1.6, "language": "en"},
    {"word": "hola", "punctuated_word": "Hola", "start": 2.0, "end": 2.4, "language": "es"},
    {"word": "amigos", "punctuated_word": "amigos", "start": 2.4, "end": 3.0, "language": "es"},
]}]}]}}


class T(unittest.TestCase):
    def test_segments(self):
        segs = vt.segments_from_deepgram(FAKE)
        self.assertEqual([s.lang for s in segs], ["en", "es"])
        self.assertEqual(segs[0].text, "Hello everyone.")

    def test_ass_options(self):
        segs = vt.segments_from_deepgram(FAKE)
        for s in segs: s.translation = "Hola a todos" if s.lang == "en" else s.text
        st = vt.SubtitleStyle(bilingual=True, align_h="right", align_v="top", upper=True)
        ass = vt.build_ass(segs, st, 1280, 720, 10)
        self.assertIn("HOLA A TODOS\\N", ass)
        self.assertIn(",9,", ass)                 # alineación arriba-derecha
        lo = vt.build_ass(segs, vt.SubtitleStyle(align_v="lower", align_h="leftmid"), 1000, 800)
        self.assertIn(",2,50,400,200,1", lo)      # centrado, márgenes 5 %/40 %, 25 % vertical
        self.assertIn("PlayResY: 720", ass)
        box = vt.build_ass(segs, vt.apply_preset(vt.SubtitleStyle(), "box"), 640, 360)
        self.assertIn(",3,", box)                 # BorderStyle caja
        self.assertIn("00:00:00,500", vt.build_srt(segs))

    def test_translation_chain_and_failures(self):
        segs = vt.segments_from_deepgram(FAKE)
        with mock.patch.object(vt, "_google", side_effect=RuntimeError("bloqueado")), \
             mock.patch.object(vt, "_mymemory", return_value="Hola a todos"):
            errs = vt.translate_segments(segs, "es")
        self.assertEqual(segs[0].translation, "Hola a todos")   # respaldo MyMemory
        self.assertEqual(segs[1].translation, "Hola amigos")    # ya en español
        self.assertTrue(any("Google" in e for e in errs))
        with mock.patch.object(vt, "_google", side_effect=RuntimeError("x")), \
             mock.patch.object(vt, "_mymemory", side_effect=RuntimeError("y")), \
             mock.patch.object(vt.time, "sleep"):
            vt.translate_segments(segs[:1], "fr")
        self.assertEqual(segs[0].translation, "")                # fallo visible

    def test_same_language_detected(self):
        tr = vt.Transcription([vt.Segment(0, 1, "hola", "es")], dict(width=2, height=2, duration=1))
        with self.assertRaises(vt.SameLanguageError):
            vt.render_video("x", tr, vt.SubtitleStyle(target="es"), ".")

    @unittest.skipUnless(shutil.which("ffmpeg"), "sin ffmpeg")
    def test_burn_keeps_audio_and_preview(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "in.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=4",
                        "-f", "lavfi", "-i", "sine=d=4", "-c:v", "libx264",
                        "-c:a", "aac", "-shortest", src], check=True, capture_output=True)
        segs = vt.segments_from_deepgram(FAKE)
        for s in segs: s.translation = "Traducción de prueba ñandú"
        out = vt.burn_subtitles(src, vt.build_ass(segs, vt.SubtitleStyle(), 640, 360, 4),
                                os.path.join(d, "out.mp4"), d, 4)
        a, b = vt.probe(src), vt.probe(out)
        self.assertTrue(b["has_audio"])
        self.assertEqual((a["width"], a["height"]), (b["width"], b["height"]))
        self.assertAlmostEqual(a["duration"], b["duration"], delta=0.3)
        fr = vt.extract_frame(src, os.path.join(d, "f.jpg"), a["duration"])
        for name in vt.PRESETS:
            p = vt.render_preview(fr, vt.apply_preset(vt.SubtitleStyle(), name),
                                  os.path.join(d, f"p_{name}.jpg"), d)
            self.assertGreater(os.path.getsize(p), 1000)

if __name__ == "__main__":
    unittest.main()

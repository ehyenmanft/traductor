import os, shutil, subprocess, sys, tempfile, unittest
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

    def test_ass_and_srt(self):
        segs = vt.segments_from_deepgram(FAKE)
        for s in segs: s.translation = "Hola a todos" if s.lang == "en" else s.text
        ass = vt.build_ass(segs, vt.SubtitleStyle(bilingual=True), 1280, 720, 10)
        self.assertIn("Hola a todos\\N", ass)
        self.assertIn("PlayResY: 720", ass)
        self.assertIn("00:00:00,500", vt.build_srt(segs))

    @unittest.skipUnless(shutil.which("ffmpeg"), "sin ffmpeg")
    def test_burn_keeps_audio(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "in.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=4",
                        "-f", "lavfi", "-i", "sine=d=4", "-c:v", "libx264",
                        "-c:a", "aac", "-shortest", src], check=True, capture_output=True)
        segs = vt.segments_from_deepgram(FAKE)
        for s in segs: s.translation = "Traducción de prueba ñandú"
        ass = vt.build_ass(segs, vt.SubtitleStyle(preset="box"), 640, 360, 4)
        out = vt.burn_subtitles(src, ass, os.path.join(d, "out.mp4"), d, 4)
        a, b = vt.probe(src), vt.probe(out)
        self.assertTrue(b["has_audio"])
        self.assertEqual((a["width"], a["height"]), (b["width"], b["height"]))
        self.assertAlmostEqual(a["duration"], b["duration"], delta=0.3)

if __name__ == "__main__":
    unittest.main()

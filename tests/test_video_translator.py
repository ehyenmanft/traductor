import os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import video_translator as vt
import translate_engine as te
import subtitle_ass as sa
import dubbing

FAKE = {"results": {"channels": [{"alternatives": [{"words": [
    {"word": "hello", "punctuated_word": "Hello", "start": 0.5, "end": 0.9, "language": "en"},
    {"word": "everyone", "punctuated_word": "everyone.", "start": 0.9, "end": 1.6, "language": "en"},
    {"word": "hola", "punctuated_word": "Hola", "start": 2.0, "end": 2.4, "language": "es"},
    {"word": "amigos", "punctuated_word": "amigos", "start": 2.4, "end": 3.0, "language": "es"},
]}]}]}}


def segs():
    return vt.segments_from_deepgram(FAKE)


class Subs(unittest.TestCase):
    def test_segments(self):
        s = segs()
        self.assertEqual([x.lang for x in s], ["en", "es"])
        self.assertEqual(s[0].text, "Hello everyone.")
        self.assertEqual(s[0].words[0][0], "Hello")

    def test_ass_position_box_upper_bilingual(self):
        s = segs()
        for x in s: x.translation = "Hola a todos" if x.lang == "en" else x.text
        ass = vt.build_ass(s, vt.SubtitleStyle(bilingual=True, align_h="right", align_v="top", upper=True), 1280, 720, 10)
        self.assertIn("HOLA A TODOS\\N", ass)
        self.assertIn(",9,", ass)
        lo = vt.build_ass(s, vt.SubtitleStyle(align_v="lower", align_h="leftmid"), 1000, 800)
        self.assertIn(",2,50,400,200,1", lo)
        self.assertIn(",3,", vt.build_ass(s, vt.apply_preset(vt.SubtitleStyle(), "box"), 640, 360))

    def test_karaoke_modes(self):
        s = [vt.Segment(0, 3, "hello big world today", "en", "hola gran mundo hoy")]
        col = vt.build_ass(s, vt.SubtitleStyle(highlight="color", words=2, hl_color="green"), 640, 360, 3)
        self.assertEqual(col.count("Dialogue: 0"), 4)              # 2 pantallas x 2 palabras
        self.assertIn("\\1c&H00FF40&", col)
        fill = vt.build_ass(s, vt.SubtitleStyle(highlight="fill"), 640, 360, 3)
        self.assertEqual(fill.count("Dialogue: 0"), 1)
        self.assertIn("\\kf", fill)
        typ = vt.build_ass(s, vt.SubtitleStyle(anim="typewriter"), 640, 360, 3)
        self.assertIn("\\alpha&HFF&", typ)
        bar = vt.build_ass(s, vt.SubtitleStyle(progress="top"), 640, 360, 3)
        self.assertIn("\\t(0,3000,\\clip(0,0,640,360))", bar)

    def test_real_word_timing_when_untranslated(self):
        s = segs()[:1]
        toks = sa.seg_tokens(s[0], s[0].text)
        self.assertAlmostEqual(toks[1][1], 0.9)

    def test_keywords_censor_srt(self):
        self.assertIn(1, sa.find_keywords(["muy", "4K", "bien"]))
        self.assertEqual(sa.censor_text("esto es una mierda"), "esto es una m*****")
        texts = sa.parse_srt_texts("1\n00:00:00,500 --> 00:00:01,600\nHola\ncolega\n\n2\n00:00:02,000 --> 00:00:03,000\nAdiós\n")
        self.assertEqual(texts, ["Hola colega", "Adiós"])
        tr = vt.Transcription(segs(), dict(width=2, height=2, duration=4))
        vt.apply_edited_srt(tr, "1\n00:00:00,5 --> 00:00:01,6\nEditado uno\n\n2\n00:00:02,0 --> 00:00:03,0\nEditado dos\n")
        self.assertEqual(tr.segments[1].translation, "Editado dos")
        with self.assertRaises(ValueError):
            vt.apply_edited_srt(tr, "1\n00:00:00,5 --> 00:00:01,6\nSolo uno\n")

    def test_presets_apply_look_only(self):
        st = vt.apply_preset(vt.SubtitleStyle(target="fr", tone="gamer", align_v="top"), "hormozi")
        self.assertEqual((st.target, st.tone, st.align_v), ("fr", "gamer", "top"))
        self.assertEqual((st.highlight, st.words), ("color", 3))
        back = vt.apply_preset(st, "classic")
        self.assertEqual((back.highlight, back.words, back.anim), ("none", 0, "none"))


class Translation(unittest.TestCase):
    def test_chain_glossary_and_failures(self):
        s = segs()
        with mock.patch.object(te, "_google", side_effect=RuntimeError("bloqueado")), \
             mock.patch.object(te, "_mymemory", return_value="Hola a todos"), mock.patch.object(te.time, "sleep"):
            errs = te.translate_segments(s, "es")
        self.assertEqual(s[0].translation, "Hola a todos")
        self.assertEqual(s[1].translation, "Hola amigos")          # ya en español
        self.assertTrue(any("Google" in e for e in errs))
        with mock.patch.object(te, "_google", side_effect=RuntimeError("x")), \
             mock.patch.object(te, "_mymemory", side_effect=RuntimeError("y")), mock.patch.object(te.time, "sleep"):
            te.translate_segments(s[:1], "fr")
        self.assertEqual(s[0].translation, "")                      # fallo visible

    def test_glossary_protect_restore(self):
        text, slots = te._protect("Join the Black Mesa crew", {"black mesa": "Black Mesa"})
        self.assertNotIn("Mesa", text)
        self.assertEqual(te._restore(text.replace("Join the", "Únete a la"), slots), "Únete a la Black Mesa crew")

    def test_groq_batch_used_with_tone_and_glossary(self):
        s = segs()[:1]
        calls = {}
        def fake(key, lines, target, tone, glossary):
            calls.update(tone=tone, glossary=glossary, n=len(lines)); return {0: "Hola a todos!"}
        with mock.patch.object(te, "_groq_batch", fake):
            te.translate_segments(s, "es", "gsk_x", "gamer", {"GG": "GG"}, censor=False)
        self.assertEqual((calls["tone"], calls["n"]), ("gamer", 1))
        self.assertEqual(s[0].translation, "Hola a todos!")
        self.assertIn('"GG"', te._system_prompt("es", "gamer", {"GG": "GG"}))

    def test_orig_mode_and_same_language(self):
        s = segs()
        te.translate_segments(s, "orig")
        self.assertEqual(s[0].translation, "Hello everyone.")
        tr = vt.Transcription([vt.Segment(0, 1, "hola", "es")], dict(width=2, height=2, duration=1))
        with self.assertRaises(vt.SameLanguageError):
            vt.render_video("x", tr, vt.SubtitleStyle(target="es"), ".")


@unittest.skipUnless(shutil.which("ffmpeg"), "sin ffmpeg")
class Media(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.src = os.path.join(self.d, "in.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=4",
                        "-f", "lavfi", "-i", "sine=d=4", "-c:v", "libx264",
                        "-c:a", "aac", "-shortest", self.src], check=True, capture_output=True)

    def test_burn_keeps_audio_and_previews(self):
        s = segs()
        for x in s: x.translation = "Traducción de prueba ñandú"
        out = vt.burn_subtitles(self.src, vt.build_ass(s, vt.SubtitleStyle(), 640, 360, 4),
                                os.path.join(self.d, "out.mp4"), self.d, 4)
        a, b = vt.probe(self.src), vt.probe(out)
        self.assertTrue(b["has_audio"])
        self.assertEqual((a["width"], a["height"]), (b["width"], b["height"]))
        self.assertAlmostEqual(a["duration"], b["duration"], delta=0.3)
        fr = vt.extract_frame(self.src, os.path.join(self.d, "f.jpg"), a["duration"])
        for name in vt.PRESETS:
            p = vt.render_preview(fr, vt.apply_preset(vt.SubtitleStyle(), name),
                                  os.path.join(self.d, f"p_{name}.jpg"), self.d)
            self.assertGreater(os.path.getsize(p), 1000)

    def test_progress_real_and_monotonic(self):
        seen = []
        vt._run_progress(["ffmpeg", "-y", "-i", self.src, "-c:v", "libx264", os.path.join(self.d, "p.mp4")],
                         duration=4, on_frac=seen.append)
        self.assertEqual(seen[-1], 1.0)
        self.assertTrue(all(0 <= x <= 1 for x in seen))
        with self.assertRaises(RuntimeError):
            vt._run_progress(["ffmpeg", "-y", "-i", "/no/existe.mp4", os.path.join(self.d, "z.mp4")], duration=4)

    def test_render_video_reports_progress(self):
        pts = []
        tr = vt.Transcription(segs(), vt.probe(self.src))
        with mock.patch.object(te, "_google", side_effect=lambda t, s, g: "TRAD " + t):
            vt.render_video(self.src, tr, vt.SubtitleStyle(target="fr"), self.d,
                            progress=lambda p, t="": pts.append((p, t)))
        pcts = [p for p, _ in pts]
        self.assertEqual(pcts, sorted(pcts))                       # nunca retrocede
        self.assertTrue(40 <= min(pcts) <= 41 and max(pcts) >= 97)
        self.assertTrue(any("Traduciendo" in t for _, t in pts) and any("Incrustando" in t for _, t in pts))
        self.assertGreater(len(pcts), 3)

    def test_fit_under_size_limit(self):
        big = os.path.join(self.d, "big.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=4", "-f", "lavfi", "-i", "sine=d=4",
                        "-c:v", "libx264", "-b:v", "3M", "-c:a", "aac", big], check=True, capture_output=True)
        limit = 0.15
        self.assertGreater(os.path.getsize(big), limit * 1024 * 1024)
        out = vt.burn_subtitles(big, vt.build_ass(segs(), vt.SubtitleStyle(), 640, 360, 4),
                                os.path.join(self.d, "fit.mp4"), self.d, 4, max_mb=limit)
        self.assertLessEqual(os.path.getsize(out), limit * 1024 * 1024)

    def test_dubbing_mix(self):
        s = segs()
        for x in s: x.translation = "texto doblado"
        def fake_synth(text, voice, path):     # tono de 1 s en lugar de la voz real
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", "-q:a", "5", path],
                           check=True, capture_output=True)
        track = dubbing.make_dub_track(s, "es", "female", 4, self.d, synth=fake_synth)
        out = vt.burn_subtitles(self.src, vt.build_ass(s, vt.SubtitleStyle(), 640, 360, 4),
                                os.path.join(self.d, "dub.mp4"), self.d, 4, dub_track=track, orig_gain=0.3)
        info = vt.probe(out)
        self.assertTrue(info["has_audio"])
        self.assertAlmostEqual(info["duration"], 4, delta=0.4)
        muted = vt.burn_subtitles(self.src, vt.build_ass(s, vt.SubtitleStyle(), 640, 360, 4), os.path.join(self.d, "m.mp4"), self.d, 4, dub_track=track, orig_gain=0)
        self.assertTrue(vt.probe(muted)["has_audio"])

if __name__ == "__main__":
    unittest.main()

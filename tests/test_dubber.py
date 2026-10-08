import os, queue, subprocess, sys, tempfile, threading, time, unittest

# Este archivo prueba la app de escritorio: se omite en entornos que no la tienen (p. ej. el servidor del bot)
for _mod in ("numpy",):
    try:
        __import__(_mod)
    except ImportError:
        raise unittest.SkipTest("sin %s (app de escritorio)" % _mod)
from unittest import mock
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
sys.modules.setdefault("pyaudiowpatch", MagicMock())
import audio_capture as ac
import live_dubber as ld
import live_translator as lt


def tone_pcm(seconds=0.1, sr=24000):
    t = np.arange(int(seconds * sr)) / sr
    return (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16), sr


class FakeSynth:
    def __init__(self, delay=0.0, fail=False):
        self.calls, self.delay, self.fail = [], delay, fail

    def synthesize(self, text, voice, rate):
        self.calls.append((text, voice, rate))
        if self.fail:
            raise RuntimeError("sin internet")
        time.sleep(self.delay)
        return tone_pcm()


class FakePlayer:
    def __init__(self, delay=0.05, log=None):
        self.played, self.delay, self.log = [], delay, log if log is not None else []
        self.cancels = []

    def list_outputs(self):
        return ["Altavoces", "Auriculares"]

    def play(self, pcm, sr, device, volume, cancel):
        self.log.append("play")
        self.played.append((len(pcm), sr, device, volume))
        cancel.wait(self.delay)
        self.cancels.append(cancel.is_set())
        self.log.append("end")


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


_APP = None


class Audio(unittest.TestCase):
    def test_decode_real_mp3(self):
        try:
            import av  # noqa: F401
        except ImportError:
            self.skipTest('sin PyAV')
        d = tempfile.mkdtemp(); path = os.path.join(d, "t.mp3")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", "-ar", "44100", "-q:a", "5", path],
                       check=True, capture_output=True)
        with open(path, "rb") as f:
            pcm, sr = ld.decode_audio(f.read())
        self.assertEqual(sr, 24000); self.assertEqual(pcm.dtype, np.int16)
        self.assertAlmostEqual(len(pcm) / sr, 1.0, delta=0.1)
        self.assertGreater(int(np.abs(pcm).max()), 3000)                   # no es silencio

    def test_prepare_pcm(self):
        pcm, sr = tone_pcm(0.5, 24000)
        out = np.frombuffer(ld.prepare_pcm(pcm, sr, 48000, 2, 1.0), dtype=np.int16)
        self.assertAlmostEqual(len(out) / 2 / 48000, 0.5, delta=0.01)       # 48 kHz estéreo, misma duración
        self.assertTrue(np.array_equal(out[0::2], out[1::2]))               # canales iguales
        half = np.frombuffer(ld.prepare_pcm(pcm, sr, 24000, 1, 0.5), dtype=np.int16)
        full = np.frombuffer(ld.prepare_pcm(pcm, sr, 24000, 1, 1.0), dtype=np.int16)
        self.assertAlmostEqual(float(np.abs(half).max()) / float(np.abs(full).max()), 0.5, delta=0.02)
        loud = np.frombuffer(ld.prepare_pcm(pcm, sr, 24000, 1, 2.0), dtype=np.int16)
        self.assertLessEqual(int(np.abs(loud).max()), 32767)                # sin desbordar

    def test_capture_mute_gate(self):
        c = ac.SystemAudioCapture(); r = ac.Resampler(16000)
        data = (np.ones(1600, dtype=np.int16) * 1000).tobytes()
        c.set_muted(True); c._process(data, 1, r)
        self.assertTrue(c.audio_queue.empty())
        c.set_muted(False); c._process(data, 1, r)
        self.assertFalse(c.audio_queue.empty())


class Dubber(unittest.TestCase):
    def make(self, synth=None, player=None, **kw):
        d = ld.LiveDubber(synth=synth or FakeSynth(), player=player or FakePlayer(), **kw)
        self.addCleanup(d.stop)
        return d

    def test_gate_wraps_playback_when_same_device(self):
        log = []
        d = self.make(player=FakePlayer(log=log), enabled=True, on_gate=lambda m: log.append(f"gate={m}"))
        with mock.patch.object(ld, "TAIL_SECONDS", 0.02):
            d.submit(1, "hola")
            self.assertTrue(wait_for(lambda: log[-1:] == ["gate=False"]))
        self.assertEqual(log, ["gate=True", "play", "end", "gate=False"])

    def test_no_gate_when_other_device(self):
        log = []
        d = self.make(player=FakePlayer(log=log), enabled=True, device="Auriculares",
                      capture_device=lambda: "Altavoces (Realtek)", on_gate=lambda m: log.append("gate"))
        self.assertFalse(d.needs_gate)
        d.submit(1, "hola"); self.assertTrue(wait_for(lambda: "end" in log))
        self.assertNotIn("gate", log)
        d.set_device("altavoces"); self.assertTrue(d.needs_gate)                    # mismo dispositivo que la captura
        d.set_device("Auriculares"); d.capture_device = lambda: ""; self.assertTrue(d.needs_gate)   # desconocido: por seguridad

    def test_voice_selection_and_unsupported_language(self):
        s = FakeSynth()
        d = self.make(synth=s, target="fr", gender="male", enabled=True)
        d.submit(1, "bonjour"); self.assertTrue(wait_for(lambda: s.calls))
        self.assertEqual(s.calls[0][1], "fr-FR-HenriNeural")
        d.set_target("ar"); d.submit(2, "x"); time.sleep(0.2); self.assertEqual(len(s.calls), 1)

    def test_disabled_does_nothing_and_toggle(self):
        s = FakeSynth(); d = self.make(synth=s, enabled=False)
        d.submit(1, "hola"); time.sleep(0.2); self.assertEqual(s.calls, [])
        self.assertTrue(d.toggle()); d.submit(2, "hola"); self.assertTrue(wait_for(lambda: s.calls))
        self.assertFalse(d.toggle())

    def test_backlog_drops_oldest_and_speeds_up(self):
        s = FakeSynth(delay=0.15); p = FakePlayer(delay=0.02)
        d = self.make(synth=s, player=p, enabled=True, max_backlog=2)
        for i in range(6):
            d.submit(i, f"frase {i}")
        self.assertTrue(wait_for(lambda: len(s.calls) >= 2, 4))
        time.sleep(0.4)
        texts = [c[0] for c in s.calls]
        self.assertEqual(texts, ["frase 4", "frase 5"])       # solo las 2 más recientes; lo viejo se descartó
        self.assertEqual(d.dropped, 4)
        self.assertGreater(s.calls[0][2], 0)                  # con otra frase esperando, habla más rápido
        self.assertEqual(s.calls[1][2], 0)                    # y sin atraso, a velocidad normal

    def test_clear_cancels_current_playback_and_target_change_clears_queue(self):
        p = FakePlayer(delay=2.0); d = self.make(player=p, enabled=True)
        d.submit(1, "larga"); self.assertTrue(wait_for(lambda: p.played))
        t0 = time.time(); d.set_target("en")
        self.assertTrue(wait_for(lambda: p.cancels, 1.5)); self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(p.cancels, [True])                   # se cortó, no esperó 2 s

    def test_on_state_reports_every_change_once(self):
        states = []
        d = self.make(enabled=False, on_state=states.append)
        d.toggle(); d.toggle(); d.set_enabled(False)          # el último no cambia nada
        self.assertEqual(states, [True, False])
        d2 = self.make(synth=FakeSynth(fail=True), enabled=True, on_state=states.append, max_failures=2)
        states.clear()
        for i in range(2):
            d2.submit(i, f"x{i}"); time.sleep(0.15)
        self.assertTrue(wait_for(lambda: states == [False]))   # el apagado automático también avisa

    def test_repeated_failures_disable_with_notice(self):
        notes = []
        d = self.make(synth=FakeSynth(fail=True), enabled=True, on_notice=notes.append, max_failures=3)
        for i in range(3):
            d.submit(i, f"x{i}"); time.sleep(0.15)
        self.assertTrue(wait_for(lambda: not d.enabled))
        self.assertTrue(notes and "Doblaje desactivado" in notes[0])


class Wiring(unittest.TestCase):
    def test_worker_on_final_and_errors_are_isolated(self):
        got, stop = [], threading.Event()
        class T:
            def translate_final(self, text, lang, on_delta=None): return "T:" + text
            def translate_partial(self, *a): return ""
        def cb(uid, text, src, lang):
            got.append((uid, text, src, lang)); raise RuntimeError("boom")
        w = lt.LiveTranslationWorker(T(), lambda *a: None, stop, on_final=cb)
        w.submit(1, "uno", "en", True); w.submit(2, "dos", "en", True)
        self.assertTrue(wait_for(lambda: len(got) == 2)); stop.set()
        self.assertEqual(got[0], (1, "T:uno", "uno", "en"))   # la 2.ª se procesó pese a la excepción de la 1.ª

    def test_overlay_dub_button(self):
        try:
            from PyQt6.QtWidgets import QApplication
            import overlay as ov
        except Exception as e:      # pragma: no cover
            self.skipTest(f"sin Qt: {e}")
        global _APP
        _APP = QApplication.instance() or QApplication(sys.argv)
        with mock.patch.object(ov, "CONFIG_PATH", os.path.join(tempfile.mkdtemp(), "c.json")):
            w = ov.TranslationOverlay(); self.addCleanup(w.close)
            self.assertEqual(w.btn_dub.text(), "🔇"); self.assertIn("apagado", w.btn_dub.toolTip())
            hits = []; w.dub_toggle_requested.connect(lambda: hits.append(1))
            w.btn_dub.click(); self.assertEqual(hits, [1])             # el clic pide alternar
            w.set_dub_state(True)
            self.assertEqual(w.btn_dub.text(), "🔊"); self.assertIn("ACTIVO", w.btn_dub.toolTip())
            self.assertIn("rgba(0,220,120", w.btn_dub.styleSheet())    # se ve en color
            w.set_dub_state(False); self.assertEqual(w.btn_dub.text(), "🔇")

    def test_tray_menu_and_f11(self):
        try:
            from PyQt6.QtWidgets import QApplication
            import overlay as ov
            import main
        except Exception as e:      # pragma: no cover
            self.skipTest(f"sin Qt: {e}")
        global _APP
        _APP = QApplication.instance() or QApplication(sys.argv)     # referencia viva hasta el final
        app = _APP
        with mock.patch.object(ov, "CONFIG_PATH", os.path.join(tempfile.mkdtemp(), "c.json")):
            w = ov.TranslationOverlay(); self.addCleanup(w.close)
            tr = lt.LiveTranslator(target="es", groq_key="")
            d = ld.LiveDubber(synth=FakeSynth(), player=FakePlayer(), enabled=False); self.addCleanup(d.stop)
            tray = main.setup_system_tray(app, w, tr, d)
            menu = tray.contextMenu()
            titles = [a.text() for a in menu.actions()]
            self.assertTrue(any("Doblaje de voz" in t for t in titles))
            act = next(a for a in menu.actions() if "Doblaje de voz" in a.text())
            self.assertTrue(act.isCheckable())
            hits = []; w.dub_toggle_requested.connect(lambda: hits.append(1))
            act.trigger(); w.handle_hotkey("f11")
            self.assertEqual(len(hits), 2)                      # menú y F11 piden lo mismo
            dev_menu = next(a.menu() for a in menu.actions() if a.menu() and "Salida" in a.text())
            dev_menu.aboutToShow.emit()
            self.assertEqual([a.text() for a in dev_menu.actions()][1:], ["Altavoces", "Auriculares"])
            tray.hide()

if __name__ == "__main__":
    unittest.main()

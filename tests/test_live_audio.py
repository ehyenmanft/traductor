"""Pruebas del audio en vivo: remuestreo, cierre de frases, Deepgram y overlay."""
import json, os, queue, sys, tempfile, threading, time, unittest

# Este archivo prueba la app de escritorio: se omite en entornos que no la tienen (p. ej. el servidor del bot)
for _mod in ("numpy", "faster_whisper"):
    try:
        __import__(_mod)
    except ImportError:
        raise unittest.SkipTest("sin %s (app de escritorio)" % _mod)
from unittest import mock
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

# audio_capture importa pyaudiowpatch (solo Windows): se sustituye para probar el remuestreo
sys.modules.setdefault("pyaudiowpatch", MagicMock())
import audio_capture as ac
import transcriber as tr
import transcriber_groq as tg
import transcriber_deepgram as td


def tone(freq, seconds, rate, amp=0.5):
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def band_power(x, rate, lo, hi):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / rate)
    return spec[(f >= lo) & (f <= hi)].sum()


class Resample(unittest.TestCase):
    def test_voice_band_kept_and_alias_removed(self):
        # 1 kHz (voz) se conserva; 10 kHz a 48 kHz se plegaría a 6 kHz si no hay filtro
        x = tone(1000, 1.0, 48000) + tone(10000, 1.0, 48000)
        good = ac.Resampler(48000).process(x)
        naive = np.interp(np.linspace(0, len(x), len(x) // 3, endpoint=False), np.arange(len(x)), x).astype(np.float32)
        self.assertAlmostEqual(len(good) / 16000, 1.0, delta=0.01)
        voice = band_power(good, 16000, 900, 1100)
        alias_good = band_power(good, 16000, 5800, 6200)
        alias_naive = band_power(naive, 16000, 5800, 6200)
        self.assertLess(alias_good, alias_naive * 0.001)      # alias ≥ 30 dB menor
        self.assertGreater(voice, alias_good * 1000)

    def test_chunking_is_seamless(self):
        x = tone(440, 1.0, 44100) + tone(2300, 1.0, 44100, 0.2)
        whole = ac.Resampler(44100).process(x)
        r = ac.Resampler(44100)
        parts = np.concatenate([r.process(x[i:i + 4410]) for i in range(0, len(x), 4410)])   # 100 ms
        n = min(len(whole), len(parts))
        self.assertLess(abs(len(whole) - len(parts)), 3)
        self.assertLess(np.max(np.abs(whole[200:n - 200] - parts[200:n - 200])), 0.02)

    def test_passthrough_and_lengths(self):
        x = tone(300, 0.1, 16000)
        self.assertTrue(np.array_equal(ac.Resampler(16000).process(x), x))
        r = ac.Resampler(48000)
        total = sum(len(r.process(tone(300, 0.1, 48000))) for _ in range(30))
        self.assertAlmostEqual(total, 3 * 16000, delta=4)


def feed(q, speech_s, silence_s, chunk=0.1, amp=0.2, rate=16000):
    n = int(chunk * rate)
    for _ in range(int(speech_s / chunk)):
        q.put((np.random.uniform(-amp, amp, n)).astype(np.float32))
    for _ in range(int(silence_s / chunk)):
        q.put(np.zeros(n, dtype=np.float32))


class Utterances(unittest.TestCase):
    """Con fragmentos de 100 ms el silencio que cierra la frase sigue siendo ~0.5 s."""

    def _run(self, t, q, speech, silence):
        threading.Thread(target=t._loop, daemon=True).start()
        feed(q, speech, silence)
        got = []
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                got.append(t.text_queue.get(timeout=0.2))
                if got[-1].is_final:
                    break
            except queue.Empty:
                pass
        t.stop()
        return got

    def test_local_engine_closes_after_half_second_of_silence(self):
        q = queue.Queue()
        with mock.patch.object(tr, "WhisperModel", MagicMock()):
            t = tr.StreamingTranscriber(q)
        t._transcribe = lambda a: ("hola mundo", "es", 0.9, 0.01)
        got = self._run(t, q, 1.5, 0.8)
        self.assertTrue(got and got[-1].is_final and got[-1].text == "hola mundo")

    def test_local_engine_does_not_cut_on_short_pause(self):
        q = queue.Queue()
        with mock.patch.object(tr, "WhisperModel", MagicMock()):
            t = tr.StreamingTranscriber(q)
        calls = []
        t._transcribe = lambda a: (calls.append(len(a) / 16000) or "texto", "es", 0.9, 0.01)
        threading.Thread(target=t._loop, daemon=True).start()
        feed(q, 1.2, 0.3)            # pausa de 0.3 s: NO cierra
        feed(q, 1.0, 0.7)            # y después sí
        deadline = time.time() + 3
        finals = []
        while time.time() < deadline and not finals:
            try:
                s = t.text_queue.get(timeout=0.2)
                if s.is_final:
                    finals.append(s)
            except queue.Empty:
                pass
        t.stop()
        self.assertEqual(len(finals), 1)       # una sola frase que incluye ambos tramos
        self.assertGreater(calls[-1], 2.4)

    def test_groq_engine_flushes_by_seconds(self):
        q = queue.Queue()
        t = tg.GroqTranscriber(q, api_key="x")
        t._request = lambda a: ("hello there", "en")
        got = self._run(t, q, 1.0, 0.8)
        self.assertTrue(got and got[-1].text == "hello there" and got[-1].language == "en")


class FakeWS:
    def __init__(self):
        self.sent, self.closed = [], False

    def settimeout(self, t): pass
    def send(self, data): self.sent.append(("text", data))
    def send_binary(self, data): self.sent.append(("bin", len(data)))
    def close(self): self.closed = True

    def recv(self):
        time.sleep(0.05)
        raise TimeoutError("timed out")


class Deepgram(unittest.TestCase):
    def _transcriber(self, **kw):
        q = queue.Queue()
        return td.DeepgramTranscriber(q, api_key="k", **kw), q

    def test_nonstop_speech_is_cut_into_finals_and_pending_text_is_flushed(self):
        t, _ = self._transcriber(); q = t.text_queue
        sentence = "This is a fairly long sentence about the game and what we are doing today. "
        for i in range(5):                      # 5 resultados finales sin speech_final (habla sin pausas)
            t.handle_message({"type": "Results", "is_final": True, "speech_final": False,
                              "channel": {"alternatives": [{"transcript": sentence.strip(), "words": []}]}})
        finals = []
        while not q.empty():
            seg = q.get()
            if seg.is_final: finals.append(seg.text)
        self.assertGreaterEqual(len(finals), 1)                          # no espera a una pausa que no llega
        self.assertTrue(all(len(f) < 460 for f in finals))
        t.handle_message({"type": "Results", "is_final": False, "channel": {"alternatives": [{"transcript": "and one last thing", "words": []}]}})
        t._flush_final(True)                                             # corte de conexión: lo visto no se pierde
        tail = []
        while not q.empty():
            seg = q.get()
            if seg.is_final: tail.append(seg.text)
        self.assertIn("and one last thing", " ".join(tail))

    def test_utterance_end_flushes_open_phrase(self):
        t, _ = self._transcriber()
        t.handle_message({"type": "Results", "is_final": True, "speech_final": False,
                          "channel": {"alternatives": [{"transcript": "hello there friend",
                                                        "words": [{"language": "en"}]}]}})
        self.assertFalse(any(s.is_final for s in list(t.text_queue.queue)))     # abierta
        t.handle_message({"type": "UtteranceEnd"})
        finals = [s for s in list(t.text_queue.queue) if s.is_final]
        self.assertEqual([(s.text, s.language) for s in finals], [("hello there friend", "en")])
        t.handle_message({"type": "UtteranceEnd"})                               # sin duplicar
        self.assertEqual(len([s for s in list(t.text_queue.queue) if s.is_final]), 1)

    def test_url_params(self):
        t, _ = self._transcriber(endpointing_ms=200, utterance_end_ms=500, keyterms=["Valorant", " ", "Jett"])
        u = t._url()
        self.assertIn("endpointing=200", u); self.assertIn("utterance_end_ms=1000", u)   # mínimo 1000
        self.assertIn("keyterm=Valorant", u); self.assertIn("keyterm=Jett", u)
        self.assertIn("language=multi", u); self.assertEqual(u.count("keyterm="), 2)

    def test_keepalive_when_no_audio_and_audio_sent(self):
        t, q = self._transcriber()
        ws = FakeWS()
        with mock.patch("websocket.create_connection", return_value=ws), mock.patch.object(td, "KEEPALIVE_EVERY", 0.3):
            t.start()
            time.sleep(1.0)
            q.put(np.zeros(1600, dtype=np.float32))
            time.sleep(0.3)
            t.stop(); time.sleep(0.7)
        kinds = [k for k, _ in ws.sent]
        self.assertIn(("text", json.dumps({"type": "KeepAlive"})), ws.sent)
        self.assertIn(("bin", 3200), ws.sent)          # 1600 muestras × 2 bytes

    def test_keyterm_rejected_retries_without_it(self):
        t, q = self._transcriber(keyterms=["Jett"])
        err = Exception("Handshake status 400"); err.status_code = 400
        ws, urls = FakeWS(), []
        def fake_conn(url, **kw):
            urls.append(url)
            if len(urls) == 1:
                raise err
            return ws
        with mock.patch("websocket.create_connection", fake_conn):
            t.start(); time.sleep(0.6); t.stop(); time.sleep(0.7)
        self.assertIn("keyterm=Jett", urls[0]); self.assertNotIn("keyterm", urls[1])


try:
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    import overlay as ov
    HAVE_QT = True
except Exception:       # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "sin PyQt6")
class OverlayRefresh(unittest.TestCase):
    def test_refreshes_are_coalesced(self):
        app = QApplication.instance() or QApplication(sys.argv)
        with mock.patch.object(ov, "CONFIG_PATH", os.path.join(tempfile.mkdtemp(), "c.json")):
            w = ov.TranslationOverlay()
            w.show()
            n = {"c": 0}
            real = w._render
            w._render = lambda: (n.__setitem__("c", n["c"] + 1), real())
            for i in range(50):
                w.upsert_entry(1, "hello world" * (i % 3 + 1), "en", False)
                w.set_translation(1, "hola mundo")
            QTest.qWait(150)
            self.assertEqual(n["c"], 1)                        # 100 peticiones → 1 pintado
            self.assertIn("hola mundo", w.text.toPlainText())
            w.close()

if __name__ == "__main__":
    unittest.main()

"""Respaldo NVIDIA: rota entre keys y entra solo cuando Deepgram no conecta (sin red)."""
import os, queue, sys, threading, time, unittest

for _mod in ("numpy", "riva.client", "websocket"):
    try:
        __import__(_mod)
    except ImportError:
        raise unittest.SkipTest("sin %s (app de escritorio)" % _mod)
from unittest import mock
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transcriber_deepgram as td
from transcriber import TranscriptSegment
from transcriber_nvidia import NvidiaTranscriber, build_fallback


class FakeService:
    def __init__(self, text=None):
        self.text, self.calls = text, 0

    def offline_recognize(self, wav, cfg, future=False):
        self.calls += 1
        if self.text is None:
            raise RuntimeError("sin respuesta")
        alt = mock.Mock(transcript=self.text)
        res = mock.Mock(results=[mock.Mock(alternatives=[alt])])
        return mock.Mock(result=lambda timeout: res)


def make(services):
    t = NvidiaTranscriber(queue.Queue(), ["k"] * len(services))
    t.services = services
    return t


AUDIO = np.zeros(1600, np.float32)


class Nvidia(unittest.TestCase):
    def test_rota_y_se_queda_en_la_key_que_responde(self):
        dead, ok = FakeService(), FakeService("hola")
        t = make([dead, ok])
        self.assertEqual(t._request(AUDIO), ("hola", "en"))
        self.assertEqual(t._request(AUDIO), ("hola", "en"))
        self.assertEqual((dead.calls, ok.calls, t.current), (1, 2, 1))

    def test_ninguna_responde_falla(self):
        with self.assertRaises(RuntimeError):
            make([FakeService(), FakeService()])._request(AUDIO)

    def test_sin_keys_no_hay_respaldo(self):
        self.assertIsNone(build_fallback(queue.Queue(), []))
        self.assertIsNone(build_fallback(queue.Queue(), [" "]))


class FakeFallback:
    """Motor por frases simulado: emite una frase con el id que le presta Deepgram."""
    def __init__(self):
        self.text_queue, self._uid, self.runs = None, 0, 0
        self._stop = threading.Event()

    def _loop(self, until=None):
        self.runs += 1
        self.text_queue.put(TranscriptSegment(self._uid, "respaldo", "en", 1.0, is_final=True))
        self._uid += 1
        until.wait(2)

    def stop(self):
        self._stop.set()


class DeepgramConRespaldo(unittest.TestCase):
    def test_deepgram_caido_usa_respaldo_y_vuelve_al_reconectar(self):
        fb = FakeFallback()
        t = td.DeepgramTranscriber(queue.Queue(), api_key="k", fallback=fb)
        t._uid = 7
        self.assertIs(fb.text_queue, t.text_queue)
        calls = []
        def fake_conn(url, **kw):
            calls.append(url)
            if len(calls) <= 2:
                raise ConnectionError("sin saldo")
            return mock.Mock(recv=mock.Mock(side_effect=lambda: time.sleep(0.05) or ""))
        with mock.patch("websocket.create_connection", fake_conn), \
                mock.patch.object(td, "FALLBACK_RETRY_SEC", 0.2):
            t.start(); time.sleep(2.0); t.stop(); time.sleep(0.7)
        self.assertEqual(fb.runs, 1)                    # 1er fallo = reintento corto; 2º = respaldo
        self.assertGreaterEqual(len(calls), 3)          # tras el plazo volvió a probar Deepgram
        seg = t.text_queue.get_nowait()
        self.assertEqual((seg.text, seg.utterance_id, t._uid), ("respaldo", 7, 8))
        self.assertTrue(fb._stop.is_set())


if __name__ == "__main__":
    unittest.main()

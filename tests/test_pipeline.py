"""Integración: transcriptor simulado → pipeline → traductor → overlay + doblaje (señales Qt entre hilos)."""
import os, queue, sys, tempfile, threading, time, unittest
from unittest import mock
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("pyaudiowpatch", MagicMock())
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import main, overlay as ov
import live_translator as lt, live_dubber as ld
from transcriber import TranscriptSegment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_dubber import FakePlayer, FakeSynth, wait_for

_APP = None


class Pipeline(unittest.TestCase):
    def test_end_to_end(self):
        global _APP
        _APP = QApplication.instance() or QApplication(sys.argv)
        with mock.patch.object(ov, "CONFIG_PATH", os.path.join(tempfile.mkdtemp(), "c.json")):
            w = ov.TranslationOverlay(); self.addCleanup(w.close)
            bridge = main.Bridge()
            bridge.upsert.connect(w.upsert_entry); bridge.set_trans.connect(w.set_translation)
            tr = lt.LiveTranslator(target="es", groq_key="")
            synth, player = FakeSynth(), FakePlayer(delay=0.01)
            dub = ld.LiveDubber(target="es", synth=synth, player=player, enabled=True); self.addCleanup(dub.stop)
            stop = threading.Event(); self.addCleanup(stop.set)
            worker = lt.LiveTranslationWorker(
                tr, bridge.set_trans.emit, stop, debounce=0.05,
                on_final=lambda uid, text, src, lang: dub.submit(uid, text) if text.strip() != src.strip() else None)

            class T:
                text_queue = queue.Queue()
            threading.Thread(target=main.pipeline, args=(T, tr, worker, bridge, stop), daemon=True).start()

            with mock.patch.object(tr, "_google", side_effect=lambda text, src: "ES:" + text):
                T.text_queue.put(TranscriptSegment(0, "hello wor", "en", 1.0, False))
                T.text_queue.put(TranscriptSegment(0, "hello world", "en", 1.0, False))
                T.text_queue.put(TranscriptSegment(0, "hello world.", "en", 1.0, True))
                T.text_queue.put(TranscriptSegment(1, "ya está en español", "es", 1.0, True))
                end = time.time() + 4
                while time.time() < end and not (
                        len(w.entries) == 2 and w.entries[0]["trans"] == "ES:hello world." and w.entries[1]["trans"] != "…"):
                    QTest.qWait(30)
                QTest.qWait(150)
            e0, e1 = w.entries
            self.assertEqual((e0["orig"], e0["trans"], e0["final"]), ("hello world.", "ES:hello world.", True))
            self.assertEqual((e1["trans"], e1["final"]), ("ya está en español", True))       # mismo idioma: sin traducir
            self.assertTrue(wait_for(lambda: synth.calls))
            self.assertEqual([c[0] for c in synth.calls], ["ES:hello world."])               # solo se dobla lo traducido
            self.assertEqual(synth.calls[0][1], "es-ES-ElviraNeural")

if __name__ == "__main__":
    unittest.main()

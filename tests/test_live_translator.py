import json, os, sys, threading, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live_translator as lt


class Resp:
    def __init__(self, text="", status=200, stream_chunks=None, body=""):
        self.status_code, self._text, self._chunks, self.text = status, text, stream_chunks, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"choices": [{"message": {"content": self._text}}]}

    def iter_lines(self, decode_unicode=True):
        for c in self._chunks:
            yield "data: " + json.dumps({"choices": [{"delta": {"content": c}}]})
        yield "data: [DONE]"


def make(**kw):
    return lt.LiveTranslator(target="es", groq_key="gsk_x", **kw)


class Translator(unittest.TestCase):
    def test_final_streams_uses_context_and_glossary(self):
        calls = []
        def post(url, headers=None, json=None, timeout=None, stream=False):
            calls.append(json)
            return Resp(stream_chunks=["Hola ", "a todos"]) if stream else Resp("x")
        t = make(glossary={"gg": "gg", "Black Mesa": "Mesa Negra"})
        seen = []
        with mock.patch.object(lt.requests, "post", post):
            out = t.translate_final("hello everyone", "en", seen.append)
            out2 = t.translate_final("second line", "en", None)
        self.assertEqual(out, "Hola a todos"); self.assertEqual(seen, ["Hola ", "Hola a todos"])
        self.assertEqual(calls[0]["model"], lt.BIG_MODEL); self.assertTrue(calls[0]["stream"])
        sys_prompt = calls[0]["messages"][0]["content"]
        self.assertIn('"Black Mesa" -> "Mesa Negra"', sys_prompt); self.assertIn('Never translate these terms: "gg"', sys_prompt)
        self.assertIn("gaming", sys_prompt)                                  # tono gamer por defecto
        # la 2.ª llamada lleva la 1.ª como contexto (user/assistant)
        roles = [m["role"] for m in calls[1]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(calls[1]["messages"][1]["content"], "hello everyone")

    def test_cache_and_same_language(self):
        n = {"c": 0}
        def post(*a, **k):
            n["c"] += 1; return Resp("Hola")
        t = make()
        with mock.patch.object(lt.requests, "post", post):
            t.translate_final("hello", "en"); t.translate_final("hello", "en")
            self.assertEqual(n["c"], 1)
            self.assertEqual(t.translate_final("hola amigo", "es"), "hola amigo")     # ya en destino
            self.assertEqual(n["c"], 1)
            self.assertEqual(t.translate_final("hola", "ES-419"), "hola")             # normaliza códigos

    def test_fallback_chain_and_circuit_breaker(self):
        models = []
        def post(url, headers=None, json=None, **k):
            models.append(json["model"]); raise RuntimeError("boom")
        t = make()
        with mock.patch.object(lt.requests, "post", post), \
             mock.patch.object(t, "_google", return_value="Hola desde Google"):
            self.assertEqual(t.translate_final("hello", "en"), "Hola desde Google")
        self.assertEqual(models[:2], [lt.BIG_MODEL, lt.FAST_MODEL])             # grande → rápido → Google
        with mock.patch.object(lt.requests, "post", post), mock.patch.object(t, "_google", return_value="G"):
            for i in range(3):
                t.translate_final(f"line {i}", "en")
        self.assertFalse(t.groq_ok)                                              # circuito abierto 30 s
        n = len(models)
        with mock.patch.object(lt.requests, "post", post), mock.patch.object(t, "_google", return_value="G"):
            t.translate_final("another", "en")
        self.assertEqual(len(models), n)                                         # ni siquiera intenta Groq

    def test_retired_model_is_skipped(self):
        seen = []
        def post(url, headers=None, json=None, **k):
            seen.append(json["model"])
            if json["model"] == lt.BIG_MODEL:
                return Resp(status=404, body="The model `llama-3.3` has been decommissioned")
            return Resp("Hola")
        t = make()
        with mock.patch.object(lt.requests, "post", post):
            t.translate_final("hello", "en"); t.translate_final("again", "en")
        self.assertEqual(seen, [lt.BIG_MODEL, lt.FAST_MODEL, lt.FAST_MODEL])     # el grande no se reintenta

    def test_no_key_uses_google_with_protected_glossary(self):
        t = lt.LiveTranslator(target="es", groq_key="", glossary={"Black Mesa": "Black Mesa"})
        class G:
            def __init__(self, **k): pass
            def translate(self, text): return text.replace("Join", "Únete a")
        import deep_translator
        with mock.patch.object(deep_translator, "GoogleTranslator", G):
            self.assertEqual(t.translate_final("Join Black Mesa", "en"), "Únete a Black Mesa")
        self.assertEqual(t.translate_partial("hi", "en") or "", t.translate_partial("hi", "en") or "")

    def test_total_failure_reports_the_cause_once_and_keeps_original(self):
        probs = []
        t = make(); t.on_problem = probs.append
        def post(*a, **k): raise RuntimeError("401 Client Error: Unauthorized for url: https://api.groq.com/x")
        with mock.patch.object(lt.requests, "post", post), mock.patch.object(t, "_google", side_effect=RuntimeError("bloqueado")), \
             mock.patch.object(t, "_mymemory", side_effect=RuntimeError("caído")):
            out = t.translate_final("hello", "en")
        self.assertEqual(out, "hello")                                   # sin traducción: queda el original…
        self.assertEqual(len(probs), 1); self.assertIn("rechazada", probs[0])      # …pero AVISA, con la causa
        probs.clear(); t2 = lt.LiveTranslator(target="es", groq_key=""); t2.on_problem = probs.append
        with mock.patch.object(t2, "_google", side_effect=RuntimeError("x")), mock.patch.object(t2, "_mymemory", side_effect=RuntimeError("y")):
            t2.translate_final("hello", "en")
        self.assertIn("falta groq_api_key", probs[0])
        probs.clear(); t3 = make(); t3.on_problem = probs.append
        with mock.patch.object(lt.requests, "post", side_effect=RuntimeError("429 Too Many Requests")), \
             mock.patch.object(t3, "_google", side_effect=RuntimeError("x")), mock.patch.object(t3, "_mymemory", side_effect=RuntimeError("y")):
            t3.translate_final("hello", "en")
        self.assertIn("saturado", probs[0])

    def test_no_problem_reported_when_something_works(self):
        probs = []
        t = lt.LiveTranslator(target="es", groq_key=""); t.on_problem = probs.append
        with mock.patch.object(t, "_google", side_effect=RuntimeError("bloqueado")), mock.patch.object(t, "_mymemory", return_value="Hola mundo"):
            self.assertEqual(t.translate_final("hello world", "en"), "Hola mundo")      # MyMemory salva la frase
        self.assertEqual(probs, [])

    def test_partial_failure_does_not_call_google_when_groq_configured(self):
        t = make()
        g = mock.Mock(return_value="G")
        with mock.patch.object(lt.requests, "post", side_effect=RuntimeError("x")), mock.patch.object(t, "_google", g):
            self.assertEqual(t.translate_partial("hello there", "en"), "")
        g.assert_not_called()


class Worker(unittest.TestCase):
    def _worker(self, tr, **kw):
        out, stop = [], threading.Event()
        w = lt.LiveTranslationWorker(tr, lambda uid, text: out.append((uid, text)), stop, **kw)
        return w, out, stop

    def test_finals_are_never_dropped_by_later_partials(self):
        class Slow:
            def translate_final(self, text, lang, on_delta=None):
                time.sleep(0.25); return "FINAL " + text
            def translate_partial(self, text, lang): return "PARCIAL " + text
        w, out, stop = self._worker(Slow(), debounce=0.05)
        w.submit(1, "uno", "en", True)
        w.submit(2, "dos parcial", "en", False)          # antes esto sobrescribía la final pendiente
        w.submit(3, "tres final", "en", True)
        time.sleep(1.0); stop.set()
        self.assertIn((1, "FINAL uno"), out); self.assertIn((3, "FINAL tres final"), out)

    def test_partials_are_debounced_and_latest_wins(self):
        calls = []
        class T:
            def translate_final(self, *a, **k): return "F"
            def translate_partial(self, text, lang): calls.append(text); return "P " + text
        w, out, stop = self._worker(T(), debounce=0.15)
        for txt in ("hel", "hello", "hello wor", "hello world"):
            w.submit(1, txt, "en", False); time.sleep(0.02)
        time.sleep(0.6); stop.set()
        self.assertEqual(calls, ["hello world"])          # una sola traducción, la más reciente
        self.assertEqual(out[-1], (1, "P hello world"))

    def test_late_partial_never_overwrites_final(self):
        release = threading.Event()
        class T:
            def translate_final(self, text, lang, on_delta=None): return "FINAL"
            def translate_partial(self, text, lang):
                release.wait(1.0); return "PARCIAL TARDÍO"
        w, out, stop = self._worker(T(), debounce=0.02)
        w.submit(1, "texto largo parcial", "en", False)
        time.sleep(0.15)                                   # el parcial ya está traduciéndose
        w.submit(1, "texto largo parcial final", "en", True)
        time.sleep(0.2); release.set(); time.sleep(0.3); stop.set()
        self.assertEqual(out[-1], (1, "FINAL"))
        self.assertNotIn((1, "PARCIAL TARDÍO"), out)

    def test_streaming_does_not_replace_visible_translation_with_fragments(self):
        class T:
            def translate_partial(self, text, lang): return "Esta es una traducción parcial larga"
            def translate_final(self, text, lang, on_delta=None):
                for s in ("Es", "Esta es", "Esta es una traducción final completa"):
                    on_delta(s); time.sleep(0.08)
                return "Esta es una traducción final completa"
        w, out, stop = self._worker(T(), debounce=0.02)
        w.submit(1, "this is a partial", "en", False); time.sleep(0.4)
        w.submit(1, "this is a final", "en", True); time.sleep(0.6); stop.set()
        texts = [t for _, t in out]
        self.assertNotIn("Es", texts); self.assertNotIn("Esta es", texts)       # fragmentos cortos no pisan lo visible
        self.assertEqual(texts[-1], "Esta es una traducción final completa")

if __name__ == "__main__":
    unittest.main()

"""Servidor (AWS) y cliente (PC) hablando de verdad por un WebSocket local, con Deepgram y la voz simulados."""
import asyncio, json, os, queue, sys, threading, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _mod in ("numpy", "websockets", "websocket"):
    try:
        __import__(_mod)
    except ImportError:
        raise unittest.SkipTest("sin %s (app de escritorio / servidor)" % _mod)
import numpy as np
import websocket
import live_protocol as P
import live_server as srv
import live_client as lc
from live_translator import LiveTranslator
from transcript import TranscriptSegment

TOKEN = "t0ken-de-prueba"


class FakeTranscriber:
    """Cuenta el audio recibido y, tras 3 fragmentos, 'reconoce' una frase (parcial + final)."""
    instances = []

    def __init__(self, audio_q, keys, lang):
        self.audio_q, self.lang = audio_q, lang
        self.text_queue = queue.Queue()
        self.bytes = 0
        self._stop = threading.Event()
        FakeTranscriber.instances.append(self)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        chunks = 0
        while not self._stop.is_set():
            try:
                data = self.audio_q.get(timeout=0.1)
            except queue.Empty:
                continue
            assert isinstance(data, bytes)            # el servidor entrega int16 en bytes
            self.bytes += len(data); chunks += 1
            if chunks == 3:
                self.text_queue.put(TranscriptSegment(0, "hello wor", "en", 1.0, False))
                time.sleep(0.05)
                self.text_queue.put(TranscriptSegment(0, "hello world.", "en", 1.0, True))
                self.text_queue.put(TranscriptSegment(1, "ya en español", "es", 1.0, True))


class FakeSynth:
    def synthesize(self, text, voice, rate_pct=0):
        return (np.sin(np.arange(2400) / 10) * 8000).astype(np.int16), 24000


def fake_translator(target, keys, tone):
    t = LiveTranslator(target=target, groq_key="", tone=tone, glossary=keys.get("glossary") or {})
    t._google = lambda text, src: "ES:" + text
    return t


def failing_translator(target, keys, tone):
    t = LiveTranslator(target=target, groq_key="", tone=tone)
    def boom(text, src): raise RuntimeError("bloqueado")
    t._google, t._mymemory = boom, boom
    return t


class ServerThread:
    def __init__(self, cfg, port=0, translator=None):
        cfg.port = port
        self.cfg = cfg
        self.server = srv.LiveServer(cfg, srv.Factories(transcriber=FakeTranscriber, translator=translator or fake_translator,
                                                       synth=FakeSynth))
        self.loop = asyncio.new_event_loop()
        self.port = None
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            fut = self.loop.create_future()
            self.task = self.loop.create_task(self.server.serve(fut))
            fut.add_done_callback(lambda f: (setattr(self, "port", f.result()), ready.set()))
            try:
                self.loop.run_until_complete(self.task)
            except asyncio.CancelledError:
                pass
            self.loop.close()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert ready.wait(5), "el servidor no arrancó"

    def stop(self):
        if self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.task.cancel)
            self.thread.join(5)


def wait_for(cond, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        FakeTranscriber.instances.clear()
        import tempfile
        self.keysfile = os.path.join(tempfile.mkdtemp(), "keys.json")
        json.dump({"deepgram_api_key": "dg", "groq_api_key": "", "glossary": {"gg": "gg"}}, open(self.keysfile, "w"))
        self.cfg = srv.ServerConfig(host="127.0.0.1", token=TOKEN, keys_file=self.keysfile)
        self.s = ServerThread(self.cfg)
        self.addCleanup(self.s.stop)
        self.clients = []

    def client(self, token=TOKEN, port=None, **settings):
        ev = {k: [] for k in ("upsert", "trans", "notice", "status", "dub", "gate", "dub_stop", "fatal")}
        q = queue.Queue()
        c = lc.RemoteSession(f"ws://127.0.0.1:{port or self.s.port}", token, q, settings,
                             on_upsert=lambda *a: ev["upsert"].append(a), on_trans=lambda *a: ev["trans"].append(a),
                             on_notice=ev["notice"].append, on_status=ev["status"].append,
                             on_dub=lambda *a: ev["dub"].append(a), on_gate=ev["gate"].append,
                             on_dub_stop=lambda *a: ev["dub_stop"].append(a), on_fatal=ev["fatal"].append,
                             max_backoff=0.4)
        c.ev, c.q = ev, q
        c.start(); self.addCleanup(c.stop); self.clients.append(c)
        return c

    @staticmethod
    def audio(c, n=4):
        for _ in range(n):
            c.q.put(np.full(1600, 0.1, dtype=np.float32))


class Protocol(unittest.TestCase):
    def test_dub_frame_roundtrip_and_validation(self):
        data = P.pack_dub(7, 24000, b"\x01\x00\x02\x00\x03")
        uid, sr, pcm = P.unpack_dub(data)
        self.assertEqual((uid, sr, pcm), (7, 24000, b"\x01\x00\x02\x00"))    # byte suelto descartado
        for bad in (b"", b"XX" + data[2:], P.pack_dub(1, 5, b"\x00\x00")):
            with self.assertRaises(P.ProtocolError):
                P.unpack_dub(bad)

    def test_decode_and_settings(self):
        for bad in ("no json", "[1]", '{"x":1}', "x" * (P.MAX_TEXT_FRAME + 1)):
            with self.assertRaises(P.ProtocolError):
                P.decode(bad)
        s = P.clean_settings({"target": "ZH-TW", "tone": "pirata", "dub": "si", "dub_gender": "male",
                              "dub_volume": 9, "lang": "EN-US", "dub_gate": False, "evil": 1})
        self.assertEqual(s, {"target": "zh-cn", "dub_gender": "male", "dub_volume": 2.0, "lang": "en", "dub_gate": False})
        self.assertIsNone(P.clean_settings({"lang": None})["lang"])


class Flow(Base):
    def test_audio_up_subtitles_down(self):
        c = self.client(target="es")
        self.assertTrue(wait_for(lambda: c.connected))
        self.audio(c, 4)
        self.assertTrue(wait_for(lambda: len(c.ev["upsert"]) >= 3 and any(t[1] == "ES:hello world." for t in c.ev["trans"])))
        up = c.ev["upsert"]
        self.assertEqual(up[0], (0, "hello wor", "en", False)); self.assertEqual(up[1], (0, "hello world.", "en", True))
        self.assertIn((1, "ya en español"), c.ev["trans"])                     # mismo idioma: sin traducir
        t = FakeTranscriber.instances[0]
        self.assertTrue(wait_for(lambda: t.bytes == 4 * 1600 * 2))             # int16 de 16 kHz, íntegro
        self.assertIn("connected", c.ev["status"])

    def test_settings_hello_and_set(self):
        c = self.client(target="fr", tone="formal", dub_gender="male")
        self.assertTrue(wait_for(lambda: c.connected and self.s.server.current))
        tr = self.s.server.current.translator
        self.assertEqual((tr.target, tr.tone), ("fr", "formal"))
        c.send_set(target="de", tone="funny", evil="x")
        self.assertTrue(wait_for(lambda: tr.target == "de" and tr.tone == "funny"))
        c.send_set(target="klingon"); time.sleep(0.2); self.assertEqual(tr.target, "de")   # inválido: ignorado

    def test_dubbing_audio_and_gate(self):
        c = self.client(target="es", dub=True, dub_gate=True)
        self.assertTrue(wait_for(lambda: c.connected)); self.audio(c, 4)
        self.assertTrue(wait_for(lambda: c.ev["dub"] and False in c.ev["gate"], 6))
        uid, sr, pcm = c.ev["dub"][0]
        self.assertEqual((sr, len(pcm)), (24000, 2400 * 2))
        self.assertEqual(c.ev["gate"][0], True)                                # primero se silencia, luego suena
        self.assertEqual(len(c.ev["dub"]), 1)                                  # solo lo traducido, no el español
        c.send_set(dub=False)
        self.assertTrue(wait_for(lambda: not self.s.server.current.dubber.enabled))

    def test_no_gate_when_client_uses_other_output(self):
        c = self.client(target="es", dub=True, dub_gate=False)
        self.assertTrue(wait_for(lambda: c.connected)); self.audio(c, 4)
        self.assertTrue(wait_for(lambda: c.ev["dub"], 6)); time.sleep(0.6)
        self.assertEqual(c.ev["gate"], [])

    def test_translation_failure_is_reported_instead_of_failing_silently(self):
        self.s.stop()
        self.s = ServerThread(self.cfg, translator=failing_translator); self.addCleanup(self.s.stop)
        c = self.client(target="es"); self.assertTrue(wait_for(lambda: c.connected)); self.audio(c, 4)
        self.assertTrue(wait_for(lambda: any(t[1] == "hello world." for t in c.ev["trans"])))      # se ve el original…
        self.assertTrue(wait_for(lambda: any("No pude traducir" in n for n in c.ev["notice"])))     # …y se avisa por qué
        self.assertEqual(sum("No pude traducir" in n for n in c.ev["notice"]), 1)                   # sin spam

    def test_glossary_reload(self):
        c = self.client(target="es")
        self.assertTrue(wait_for(lambda: c.connected and self.s.server.current))
        tr = self.s.server.current.translator
        self.assertEqual(tr.glossary, {"gg": "gg"})
        json.dump({"deepgram_api_key": "dg", "glossary": {"nuevo": "término"}}, open(self.keysfile, "w"))
        c.reload_glossary()
        self.assertTrue(wait_for(lambda: tr.glossary == {"nuevo": "término"}))


class Security(Base):
    def test_wrong_token_is_fatal_and_not_retried(self):
        c = self.client(token="incorrecto")
        self.assertTrue(wait_for(lambda: c.ev["fatal"]))
        self.assertIn("Token", c.ev["fatal"][0]); time.sleep(1.0)
        self.assertEqual(self.s.server.sessions_started, 0); self.assertTrue(c.fatal)
        self.assertEqual(c.ev["status"].count("connecting"), 1)                # no reintenta

    def test_takeover_replaces_old_client_without_fight(self):
        a = self.client(); self.assertTrue(wait_for(lambda: a.connected))
        b = self.client(); self.assertTrue(wait_for(lambda: b.connected and self.s.server.sessions_started == 2))
        self.assertTrue(wait_for(lambda: a.ev["fatal"])); self.assertIn("Otra conexión", a.ev["fatal"][0])
        time.sleep(0.8); self.assertTrue(b.connected); self.assertEqual(self.s.server.sessions_started, 2)   # a no vuelve a pelear

    def test_raw_clients_misbehaving(self):
        url = f"ws://127.0.0.1:{self.s.port}"
        ws = websocket.create_connection(url, timeout=5); ws.send("no es json")
        err = json.loads(ws.recv()); self.assertEqual((err["type"], err["fatal"], err["code"]), ("error", True, P.CLOSE_PROTOCOL)); ws.close()
        ws = websocket.create_connection(url, timeout=5); ws.send(json.dumps({"type": "hello", "version": 99, "token": TOKEN}))
        self.assertIn("incompatible", json.loads(ws.recv())["message"]); ws.close()
        ws = websocket.create_connection(url, timeout=5); ws.send_binary(b"\x00" * 10)               # audio antes del saludo
        self.assertEqual(json.loads(ws.recv())["code"], P.CLOSE_PROTOCOL); ws.close()
        ws = websocket.create_connection(url, timeout=5)
        ws.send(json.dumps({"type": "hello", "version": 1, "token": TOKEN}))
        self.assertEqual(json.loads(ws.recv())["type"], "ready")
        ws.send("basura"); ws.send(json.dumps({"type": "set", "target": 5})); ws.send_binary(b"\x01")   # impar
        ws.send_binary(b"\x00" * 200)
        time.sleep(0.3); self.assertTrue(self.s.server.current and not self.s.server.current.closed)    # sigue vivo
        ws.close()

    def test_hello_timeout(self):
        with mock.patch.object(srv, "HELLO_TIMEOUT", 0.3):
            ws = websocket.create_connection(f"ws://127.0.0.1:{self.s.port}", timeout=5)
            self.assertEqual(json.loads(ws.recv())["code"], P.CLOSE_PROTOCOL)
            ws.close()

    def test_server_without_deepgram_key(self):
        json.dump({"groq_api_key": "x"}, open(self.keysfile, "w"))
        c = self.client(); self.assertTrue(wait_for(lambda: c.ev["fatal"]))
        self.assertIn("deepgram", c.ev["fatal"][0])

    def test_server_refuses_to_start_without_token(self):
        with self.assertRaises(SystemExit):
            srv.LiveServer(srv.ServerConfig(token=""))


class Resilience(Base):
    def test_client_reconnects_after_server_restart(self):
        c = self.client(target="es"); self.assertTrue(wait_for(lambda: c.connected))
        port = self.s.port
        self.s.stop()
        self.assertTrue(wait_for(lambda: not c.connected))
        s2 = ServerThread(self.cfg, port=port); self.addCleanup(s2.stop)
        self.assertTrue(wait_for(lambda: c.connected, 8))
        self.audio(c, 4)
        self.assertTrue(wait_for(lambda: any(t[1] == "ES:hello world." for t in c.ev["trans"])))
        self.assertIn("reconnecting", c.ev["status"])

    def test_session_time_limit_allows_reconnect(self):
        self.cfg.max_session_minutes = 0.02          # ≈ 1.2 s
        c = self.client()
        self.assertTrue(wait_for(lambda: c.connected))
        self.assertTrue(wait_for(lambda: any("Límite" in n for n in c.ev["notice"]), 4))
        self.assertTrue(wait_for(lambda: self.s.server.sessions_started >= 2, 6))   # reconectó solo
        self.assertFalse(c.fatal)

    def test_audio_is_not_buffered_while_disconnected(self):
        q = queue.Queue()
        c = lc.RemoteSession("ws://127.0.0.1:1", TOKEN, q, max_backoff=0.2)
        c.start(); self.addCleanup(c.stop)
        for _ in range(50):
            q.put(np.zeros(1600, dtype=np.float32))
        self.assertTrue(wait_for(lambda: q.empty(), 3))                          # se descarta, no se acumula


class Proxies(Base):
    def test_translator_and_dubber_proxies_send_changes(self):
        c = self.client(target="es"); self.assertTrue(wait_for(lambda: c.connected and self.s.server.current))
        tr = lc.RemoteTranslator(c, "es", "gamer"); tr.set_target("zh-CN"); tr.set_tone("technical")
        srv_tr = self.s.server.current.translator
        self.assertTrue(wait_for(lambda: (srv_tr.target, srv_tr.tone) == ("zh-cn", "technical")))
        states = []
        d = lc.RemoteDubber(c, player=None, on_state=states.append, capture_device=lambda: "Altavoces (Realtek)")
        self.assertTrue(d.needs_gate)
        self.assertTrue(d.toggle()); self.assertEqual(states, [True])
        self.assertTrue(wait_for(lambda: self.s.server.current.dubber.enabled))
        d.set_device("Auriculares"); self.assertFalse(d.needs_gate)
        self.assertTrue(wait_for(lambda: self.s.server.current.dubber.gate_override is False))
        d.set_device("altavoces"); self.assertTrue(d.needs_gate)
        d.set_gender("male"); self.assertTrue(wait_for(lambda: self.s.server.current.dubber.gender == "male"))

    def test_client_player_gates_capture_and_skips_when_off(self):
        class P_:
            def __init__(self): self.played = []
            def play(self, pcm, sr, device, volume, cancel):
                self.played.append((len(pcm), sr)); time.sleep(0.15)
        calls, player = [], P_()
        d = lc.RemoteDubber(mock.Mock(), player=None, enabled=True, capture_device=lambda: "Altavoces")
        cp = lc.ClientDubPlayer(player, calls.append, d); self.addCleanup(cp.stop)
        cp.enqueue(24000, np.zeros(2400, dtype="<i2").tobytes())
        self.assertTrue(wait_for(lambda: player.played and calls[-1:] == [False]))
        self.assertEqual(calls[0], True)                                          # silencia al empezar y libera al terminar
        d.enabled = False; n = len(player.played)
        cp.enqueue(24000, b"\x00\x00" * 100); time.sleep(0.3); self.assertEqual(len(player.played), n)
        d.enabled = True; d.device = "Auriculares"; calls.clear()                 # otra salida: no se silencia la captura
        cp.enqueue(24000, np.zeros(2400, dtype="<i2").tobytes()); time.sleep(0.4)
        self.assertNotIn(True, calls)
        cp.server_gate(True); self.assertEqual(calls[-1], True); cp.server_gate(False); self.assertEqual(calls[-1], False)

if __name__ == "__main__":
    unittest.main()

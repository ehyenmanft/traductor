"""El cliente completo (overlay + bandeja + red) contra un servidor real, y el túnel SSH."""
import os, queue, socket, stat, stat as _st, sys, tempfile, textwrap, threading, time, unittest
from unittest import mock
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for _mod in ("numpy", "websockets", "websocket", "PyQt6"):
    try:
        __import__(_mod)
    except ImportError:
        raise unittest.SkipTest("sin %s (app de escritorio / servidor)" % _mod)
import numpy as np
sys.modules.setdefault("pyaudiowpatch", MagicMock())
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import client_main, overlay as ov, tunnel as tn
import live_server as srv
from test_remote import ServerThread, TOKEN, wait_for

_APP = None


class FakeCapture:
    def __init__(self):
        self.audio_queue = queue.Queue(); self.muted_log = []; self.current_device_name = "Altavoces (Realtek)"
        self.started = self.stopped = False
    def start(self): self.started = True
    def stop(self): self.stopped = True
    def set_muted(self, m): self.muted_log.append(bool(m))


class FakePlayer:
    def __init__(self): self.played = []
    def list_outputs(self): return ["Altavoces (Realtek)", "Auriculares"]
    def play(self, pcm, sr, device, volume, cancel):
        self.played.append((len(pcm), sr)); time.sleep(0.15)


def qwait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QTest.qWait(30)
        if cond():
            return True
    return False


class ClientApp(unittest.TestCase):
    def setUp(self):
        global _APP
        _APP = QApplication.instance() or QApplication(sys.argv)
        p = mock.patch.object(ov, "CONFIG_PATH", os.path.join(tempfile.mkdtemp(), "c.json")); p.start(); self.addCleanup(p.stop)
        d = tempfile.mkdtemp(); self.keys = os.path.join(d, "k.json")
        with open(self.keys, "w") as f:
            f.write('{"deepgram_api_key": "dg", "glossary": {}}')
        self.s = ServerThread(srv.ServerConfig(token=TOKEN, keys_file=self.keys)); self.addCleanup(self.s.stop)
        self.cap, self.player = FakeCapture(), FakePlayer()

    def make(self, token=TOKEN, **extra):
        cfg = {"token": token, "server": f"ws://127.0.0.1:{self.s.port}", **extra}
        c = client_main.build(cfg, _APP, capture_factory=lambda: self.cap, player=self.player)
        self.addCleanup(c.shutdown); self.addCleanup(c.overlay.close); self.addCleanup(c.tray.hide)
        c.session.start()
        return c

    def test_subtitles_arrive_in_the_overlay(self):
        c = self.make()
        self.assertTrue(qwait_until(lambda: c.session.connected))
        for _ in range(4):
            self.cap.audio_queue.put(np.full(1600, 0.1, dtype=np.float32))
        self.assertTrue(qwait_until(lambda: len(c.overlay.entries) == 2 and c.overlay.entries[0]["trans"] == "ES:hello world."))
        e0, e1 = c.overlay.entries
        self.assertEqual((e0["orig"], e0["final"], e0["lang"]), ("hello world.", True, "en"))
        self.assertEqual(e1["trans"], "ya en español")
        c.overlay._render()                                   # y se pinta sin errores
        self.assertIn("ES:hello world.", c.overlay.text.toPlainText() + "".join(l["segs"][0][0] for l in c.overlay.sub._lines) )

    def test_language_tone_and_hotkey_reach_the_server(self):
        c = self.make()
        self.assertTrue(qwait_until(lambda: c.session.connected and self.s.server.current))
        srv_tr = self.s.server.current.translator
        c.overlay.set_target_language("fr")                           # menú de idioma del overlay
        self.assertTrue(qwait_until(lambda: srv_tr.target == "fr"))
        tone_menu = next(a.menu() for a in c.tray.contextMenu().actions() if a.menu() and "Tono" in a.text())
        next(a for a in tone_menu.actions() if a.text() == "Técnico").trigger()
        self.assertTrue(qwait_until(lambda: srv_tr.tone == "technical"))
        c.overlay.handle_hotkey("f11")                                # F11 → doblaje
        self.assertTrue(qwait_until(lambda: self.s.server.current.dubber.enabled and c.dubber.enabled))
        self.assertEqual(c.overlay.btn_dub.text(), "🔊")
        c.overlay.btn_dub.click()
        self.assertTrue(qwait_until(lambda: not self.s.server.current.dubber.enabled and c.overlay.btn_dub.text() == "🔇"))

    def test_dubbing_plays_on_the_pc_and_mutes_capture(self):
        c = self.make(dub=True)
        self.assertTrue(qwait_until(lambda: c.session.connected))
        for _ in range(4):
            self.cap.audio_queue.put(np.full(1600, 0.1, dtype=np.float32))
        self.assertTrue(qwait_until(lambda: self.player.played and self.cap.muted_log[-1:] == [False], 8))
        self.assertEqual(self.player.played[0], (2400, 24000))
        self.assertIn(True, self.cap.muted_log)                       # silenció la captura mientras sonaba

    def test_server_turning_dubbing_off_updates_the_ui(self):
        c = self.make(dub=True)
        self.assertTrue(qwait_until(lambda: c.session.connected and self.s.server.current))
        self.assertEqual(c.overlay.btn_dub.text(), "🔊")
        self.s.loop.call_soon_threadsafe(lambda: self.s.server.current.dubber.set_enabled(False))
        self.assertTrue(qwait_until(lambda: c.overlay.btn_dub.text() == "🔇" and not c.dubber.enabled))

    def test_bad_token_shows_message_and_stops(self):
        c = self.make(token="malo")
        self.assertTrue(qwait_until(lambda: c.session.fatal))
        self.assertTrue(qwait_until(lambda: "Token" in c.overlay.title.text()))
        QTest.qWait(800); self.assertEqual(self.s.server.sessions_started, 0)

    def test_missing_token_is_a_clear_error(self):
        with self.assertRaises(client_main.ClientError) as cm:
            client_main.build({"server": "ws://x"}, _APP, capture_factory=FakeCapture, player=FakePlayer(), config_path="C:\\x\\config.json")
        self.assertIn("Falta el token", str(cm.exception)); self.assertIn("config.json", str(cm.exception))

    def test_ssh_section_builds_tunnel_but_not_with_no_tunnel(self):
        key = os.path.join(tempfile.mkdtemp(), "k.pem"); open(key, "w").close()
        cfg = {"token": TOKEN, "ssh": {"host": "1.2.3.4", "user": "ubuntu", "key": key, "local_port": 9999}}
        with mock.patch.object(client_main, "prepare_key", return_value="/copia/k.pem"):
            c = client_main.build(cfg, _APP, capture_factory=FakeCapture, player=FakePlayer())
            self.addCleanup(c.shutdown); self.addCleanup(c.overlay.close); self.addCleanup(c.tray.hide)
            self.assertIsNotNone(c.tunnel); self.assertIn("127.0.0.1:9999:127.0.0.1:8765", " ".join(c.tunnel.command()))
            c2 = client_main.build(cfg, _APP, no_tunnel=True, capture_factory=FakeCapture, player=FakePlayer())
            self.addCleanup(c2.shutdown); self.addCleanup(c2.overlay.close); self.addCleanup(c2.tray.hide)
            self.assertIsNone(c2.tunnel)
        with self.assertRaises(client_main.ClientError):
            client_main.build({**cfg, "ssh": {**cfg["ssh"], "key": "/no/existe.pem"}}, _APP, capture_factory=FakeCapture, player=FakePlayer())


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class Tunnel(unittest.TestCase):
    def fake_ssh(self):
        """Un 'ssh' de mentira: abre el puerto de -L y espera, como haría un túnel real."""
        d = tempfile.mkdtemp()
        py = os.path.join(d, "fake_ssh.py")
        open(py, "w").write(textwrap.dedent("""
            import socket, sys, time
            spec = sys.argv[sys.argv.index("-L") + 1]; port = int(spec.split(":")[1])
            s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port)); s.listen(5); time.sleep(60)
        """))
        sh = os.path.join(d, "ssh"); open(sh, "w").write(f'#!/bin/sh\nexec "{sys.executable}" "{py}" "$@"\n')
        os.chmod(sh, 0o755)
        return sh

    def test_command_is_secure_and_complete(self):
        t = tn.SshTunnel("3.147.64.254", "ubuntu", "/k/clave.pem", local_port=8765, remote_port=8765, ssh_exe="ssh")
        cmd = t.command()
        self.assertEqual(cmd[0:2], ["ssh", "-N"]); self.assertIn("127.0.0.1:8765:127.0.0.1:8765", cmd)   # solo local
        self.assertIn("BatchMode=yes", cmd); self.assertIn("StrictHostKeyChecking=accept-new", cmd)
        self.assertIn("ExitOnForwardFailure=yes", cmd); self.assertEqual(cmd[-1], "ubuntu@3.147.64.254")
        self.assertEqual(cmd[cmd.index("-i") + 1], "/k/clave.pem")
        self.assertNotIn("-i", tn.SshTunnel("h", key="").command())

    @unittest.skipIf(sys.platform == "win32", "usa un ssh de mentira en sh")
    def test_starts_recovers_and_stops(self):
        port, states = free_port(), []
        t = tn.SshTunnel("h", "u", "k", local_port=port, ssh_exe=self.fake_ssh(), retry_seconds=0.2, on_status=states.append)
        t.start(); self.addCleanup(t.stop)
        self.assertTrue(t.wait_ready(8))
        first = t._proc.pid
        t._proc.kill()                                                    # el túnel se cae…
        self.assertTrue(wait_for(lambda: t._proc.pid != first and tn.port_open(port), 10))   # …y se levanta solo
        self.assertTrue(any(s.startswith("failed") for s in states))
        pid = t._proc.pid; t.stop()
        self.assertTrue(wait_for(lambda: t._proc.poll() is not None, 5))

    def test_missing_ssh_reports_clearly(self):
        states = []
        t = tn.SshTunnel("h", ssh_exe="/no/hay/ssh", on_status=states.append); t.start()
        self.assertTrue(wait_for(lambda: "no-ssh" in states)); t.stop()

    @unittest.skipIf(sys.platform == "win32", "permisos POSIX")
    def test_prepare_key_copies_with_private_permissions(self):
        src = os.path.join(tempfile.mkdtemp(), "orig.pem"); open(src, "w").write("CLAVE"); os.chmod(src, 0o644)
        dest = tn.prepare_key(src, os.path.join(tempfile.mkdtemp(), "x"))
        self.assertEqual(open(dest).read(), "CLAVE"); self.assertEqual(_st.S_IMODE(os.stat(dest).st_mode), 0o600)
        self.assertEqual(_st.S_IMODE(os.stat(src).st_mode), 0o644)            # el original no se toca
        with self.assertRaises(FileNotFoundError):
            tn.prepare_key("/no/existe.pem", tempfile.mkdtemp())

if __name__ == "__main__":
    unittest.main()

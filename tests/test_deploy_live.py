"""Despliegue del servidor y del cliente: scripts, límites de recursos, autochequeo y 'ligereza' de las importaciones."""
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


class Scripts(unittest.TestCase):
    def test_bash_syntax_and_isolation_from_the_bot(self):
        for name in ("install_live.sh", "update_live.sh", "uninstall_live.sh"):
            r = subprocess.run(["bash", "-n", os.path.join(ROOT, "deploy_live", name)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        inst = read("deploy_live", "install_live.sh")
        self.assertIn("SERVICE=traductor-live", inst)                      # servicio propio, no traductor-bot
        self.assertIn(".venv-live", inst)                                   # entorno propio, no .venv-bot
        for guard in ("MemoryMax=600M", "CPUQuota=80%", "Nice=10", "NoNewPrivileges=true"):
            self.assertIn(guard, inst)                                       # protege al bot que comparte la instancia
        for forbidden in ("traductor-bot", ".venv-bot", "requirements-bot", "systemctl restart traductor-bot"):
            self.assertNotIn(forbidden, inst + read("deploy_live", "update_live.sh") + read("deploy_live", "uninstall_live.sh"))
        self.assertIn('"host": "127.0.0.1"', inst)                           # solo local: sin puertos abiertos
        self.assertNotIn("0.0.0.0", inst)

    def test_does_not_modify_the_bot_config(self):
        inst = read("deploy_live", "install_live.sh")
        self.assertNotRegex(inst, r'>\s*"?\$KEYS')                           # solo lee el config.json del bot
        self.assertIn("json.load(open(sys.argv[1]", inst)

    def test_requirements_are_split(self):
        server, client = read("requirements-live-server.txt"), read("requirements-client.txt")
        for pkg in ("websockets", "edge-tts", "av", "deep-translator"):
            self.assertIn(pkg, server)
        for pkg in ("PyQt6", "PyAudioWPatch", "websocket-client"):
            self.assertIn(pkg, client)
        for heavy in ("faster-whisper", "ctranslate2", "onnxruntime", "websockets", "edge-tts", "av"):
            self.assertNotIn(heavy, client)                                   # la PC no lleva nada pesado ni del servidor
        self.assertNotIn("PyQt6", server)


class Light(unittest.TestCase):
    """Se importa en un intérprete limpio con los paquetes pesados BLOQUEADOS."""

    def run_blocked(self, blocked, code):
        script = ("import importlib.abc, sys\n"
                  f"blocked = set({sorted(blocked)!r})\n"
                  "class B(importlib.abc.MetaPathFinder):\n"
                  "    def find_spec(self, name, path, target=None):\n"
                  "        if name.split('.')[0] in blocked: raise ImportError('BLOQUEADO ' + name)\n"
                  "sys.meta_path.insert(0, B())\n"
                  "from unittest.mock import MagicMock\nsys.modules['pyaudiowpatch'] = MagicMock()\n"
                  "import os; os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n" + code)
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])

    def test_client_needs_no_model_stack_and_no_server_libs(self):
        self.run_blocked({"faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "huggingface_hub",
                          "websockets", "edge_tts", "deep_translator"},
                         "import client_main, live_client, app_ui, overlay, style_dialog, subtitle_view, tunnel\n")

    def test_server_needs_no_qt_and_no_whisper(self):
        self.run_blocked({"PyQt6", "faster_whisper", "ctranslate2", "onnxruntime", "pyaudio", "keyboard"},
                         "import live_server, live_protocol, live_translator, live_dubber, transcriber_deepgram\n"
                         "import live_server as s; s.Factories()\n")


@unittest.skipUnless(all(__import__("importlib").util.find_spec(m) for m in ("websockets", "websocket", "numpy")), "sin dependencias del servidor")
class SelfCheck(unittest.TestCase):
    def test_selfcheck_script_against_a_real_server(self):
        from test_remote import ServerThread, TOKEN
        import live_server as srv
        d = tempfile.mkdtemp(); keys = os.path.join(d, "k.json")
        with open(keys, "w") as f:
            f.write('{"deepgram_api_key": "dg"}')
        s = ServerThread(srv.ServerConfig(token=TOKEN, keys_file=keys)); self.addCleanup(s.stop)
        def run(token):
            cfg = os.path.join(d, "live.json")
            with open(cfg, "w") as f:
                json.dump({"host": "127.0.0.1", "port": s.port, "token": token}, f)
            return subprocess.run([sys.executable, os.path.join(ROOT, "deploy_live", "selfcheck.py"), cfg],
                                  capture_output=True, text=True)
        ok = run(TOKEN); self.assertEqual(ok.returncode, 0, ok.stdout); self.assertIn("OK", ok.stdout)
        bad = run("incorrecto"); self.assertEqual(bad.returncode, 1); self.assertIn("FALLO", bad.stdout)

if __name__ == "__main__":
    unittest.main()

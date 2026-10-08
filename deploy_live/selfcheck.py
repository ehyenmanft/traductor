"""Comprueba que el servidor de traducción en vivo responde: conecta, saluda con el token y espera 'ready'.
Uso: python deploy_live/selfcheck.py [live_config.json]"""
import json
import sys

import websocket

path = sys.argv[1] if len(sys.argv) > 1 else "live_config.json"
cfg = json.load(open(path, encoding="utf-8"))
url = f"ws://{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 8765)}"
if cfg.get("host") in ("0.0.0.0", "::"):
    url = f"ws://127.0.0.1:{cfg.get('port', 8765)}"
try:
    ws = websocket.create_connection(url, timeout=8)
    ws.send(json.dumps({"type": "hello", "version": 1, "token": cfg["token"]}))
    msg = json.loads(ws.recv())
    ws.close()
except Exception as e:  # noqa: BLE001
    print(f"FALLO: no pude hablar con {url}: {type(e).__name__}: {e}")
    sys.exit(1)
if msg.get("type") == "ready":
    print(f"OK: el servidor responde en {url} (motor: {msg.get('engine')})")
    sys.exit(0)
print(f"FALLO: respuesta inesperada: {msg}")
sys.exit(1)

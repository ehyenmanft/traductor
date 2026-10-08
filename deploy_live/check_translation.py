"""Diagnóstico de la TRADUCCIÓN del servidor: prueba Groq (los dos modelos), Google y MyMemory con las
claves del servidor y muestra el resultado o el error exacto de cada uno. No imprime ninguna clave.

Uso (en AWS):  cd ~/traductor-live && .venv-live/bin/python deploy_live/check_translation.py"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_translator import BIG_MODEL, FAST_MODEL, LiveTranslator  # noqa: E402

cfg = json.load(open("live_config.json", encoding="utf-8"))
keys = json.load(open(cfg["keys_file"], encoding="utf-8"))
groq = str(keys.get("groq_api_key", "")).strip()
print(f"groq_api_key: {'configurada (' + str(len(groq)) + ' caracteres)' if groq else 'NO configurada'}")
t = LiveTranslator(target="es", groq_key=groq, tone="gamer", glossary=keys.get("glossary") or {})
text = "Hello everyone, welcome to the game. Watch out behind you!"
ok_any = False


def run(name, fn):
    global ok_any
    try:
        out = fn()
        print(f"  ✔ {name}: {out}")
        ok_any = True
    except Exception as e:  # noqa: BLE001
        print(f"  ✘ {name}: {type(e).__name__}: {str(e)[:200]}")


print("Probando traducir al español:")
if groq:
    try:
        import requests
        r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {groq}"}, timeout=10)
        print(f"  modelos que ofrece tu cuenta de Groq (HTTP {r.status_code}):",
              ", ".join(m["id"] for m in r.json().get("data", [])) if r.ok else r.text[:200])
    except Exception as e:  # noqa: BLE001
        print(f"  no pude listar modelos: {type(e).__name__}")
    for model in (BIG_MODEL, FAST_MODEL):
        run(f"Groq {model}", lambda m=model: t._groq(m, t._messages(text, "en", False), 120, 10.0))
run("Google Translate", lambda: t._google(text, "en"))
run("MyMemory", lambda: t._mymemory(text, "en"))
print("\nRESULTADO:", "al menos un traductor funciona ✔" if ok_any else "NINGÚN traductor funciona ✘ (mira los errores de arriba)")
sys.exit(0 if ok_any else 1)

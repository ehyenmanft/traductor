"""Mide la LATENCIA de cada traductor desde el servidor (lo que tarda en verse la traducción).
Repite llamadas seguidas, como en vivo, para ver si Groq empieza a responder 429 (límite por minuto).
No imprime ninguna clave.

Uso (en AWS):  cd ~/traductor-live && .venv-live/bin/python deploy_live/latency_check.py"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_translator import LiveTranslator  # noqa: E402

cfg = json.load(open("live_config.json", encoding="utf-8"))
keys = json.load(open(cfg["keys_file"], encoding="utf-8"))
t = LiveTranslator(target="es", groq_key=str(keys.get("groq_api_key", "")).strip(), tone="gamer",
                   glossary=keys.get("glossary") or {})
t._discovered = 0
t._discover_models()
print(f"modelos: finales={t.final_models} parciales={t.partial_model}")
text = "Watch out behind you, the enemy team is pushing mid right now"


def timed(name, fn, n=3):
    for i in range(n):
        first = [None]
        start = time.monotonic()
        try:
            out = fn(lambda _s: first.__setitem__(0, first[0] or time.monotonic() - start))
            total = time.monotonic() - start
            ttfb = f" primera palabra {first[0]:.2f}s" if first[0] else ""
            print(f"  {name} #{i + 1}: {total:.2f}s{ttfb} → {out[:60]}")
        except Exception as e:  # noqa: BLE001
            print(f"  {name} #{i + 1}: ✘ {time.monotonic() - start:.2f}s {type(e).__name__}: {str(e)[:120]}")


if t.groq_key:
    print("Groq (parcial, modelo rápido, 6 seguidas como en vivo):")
    timed("parcial", lambda cb: t._groq(t.partial_model, t._messages(text, "en", True), 120, 4.0), n=6)
    for m in t.final_models[:2]:
        print(f"Groq (frase final, {m}, streaming):")
        timed("final", lambda cb, m=m: t._groq(m, t._messages(text, "en", True), 200, 10.0, cb))
print("Google (deep_translator):")
timed("google", lambda cb: t._google(text, "en"))
print("MyMemory:")
timed("mymemory", lambda cb: t._mymemory(text, "en"), n=1)

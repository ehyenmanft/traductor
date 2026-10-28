"""
Acceso a Groq para el bot: elige solo los modelos que la cuenta ofrece AHORA (Groq retira modelos con el
tiempo: los llama fijos dieron 404) y llama al chat con los ajustes que necesitan los modelos que razonan.

    chat(key, messages, max_tokens=..., json_mode=False) -> str   (lanza RuntimeError si todos fallan)
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

BASE = "https://api.groq.com/openai/v1"
DEFAULTS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
_SKIP = ("whisper", "guard", "tts", "embed", "orpheus", "playai", "distil", "compound", "safeguard", "vision", "allam")
_cache: dict = {"at": 0.0, "models": []}
_dead: set[str] = set()


def pick(ids: list[str]) -> list[str]:
    """De los modelos que ofrece la cuenta, ordenados de mejor a más rápido para texto."""
    chat_ids = [i for i in ids if not any(k in i.lower() for k in _SKIP)]

    def score(i: str) -> int:
        tokens = set(re.split(r"[^a-z0-9.]+", i.lower()))
        return sum(w in tokens for w in ("llama", "70b", "versatile", "120b", "qwen3", "32b", "27b"))
    return sorted(chat_ids, key=lambda i: -score(i))


def models(key: str, force: bool = False) -> list[str]:
    forced = os.environ.get("GROQ_MODEL", "").strip()
    now = time.time()
    if force or now - _cache["at"] > 3600 or not _cache["models"]:
        _cache["at"] = now
        try:
            r = requests.get(BASE + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=10)
            r.raise_for_status()
            found = pick([m["id"] for m in r.json().get("data", []) if m.get("active", True)])
            _cache["models"] = found or list(DEFAULTS)
        except Exception:  # noqa: BLE001 — sin lista: los habituales
            _cache["models"] = _cache["models"] or list(DEFAULTS)
    out = [m for m in _cache["models"] if m not in _dead]
    return ([forced] if forced else []) + [m for m in out if m != forced]


def _body(model: str, messages: list[dict], max_tokens: int, json_mode: bool, temperature: float) -> dict:
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    low = model.lower()
    if "gpt-oss" in low:                      # razonan: poco esfuerzo y margen de tokens
        body.update(reasoning_effort="low", include_reasoning=False, max_tokens=max(max_tokens, 400))
    elif "qwen" in low:
        body.update(reasoning_effort="none", max_tokens=max(max_tokens, 400))
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def chat(key: str, messages: list[dict], max_tokens: int = 1024, json_mode: bool = False,
         timeout: float = 45.0, temperature: float = 0.2) -> str:
    errors = []
    for model in models(key):
        body = _body(model, messages, max_tokens, json_mode, temperature)
        try:
            for attempt in range(3):
                r = requests.post(BASE + "/chat/completions", timeout=timeout, json=body,
                                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
                if r.status_code == 429 and attempt < 2:      # límite por minuto: esperar y repetir
                    try:
                        wait = float(r.headers.get("retry-after", "") or 2.0)
                    except ValueError:
                        wait = 2.0
                    time.sleep(min(max(wait, 0.5), 6.0))
                    continue
                if r.status_code == 400 and "reasoning" in (r.text or "").lower() and "reasoning_effort" in body:
                    body.pop("reasoning_effort", None)
                    body.pop("include_reasoning", None)
                    continue
                break
            if r.status_code == 404:
                _dead.add(model)                              # retirado: no volver a intentarlo
                _cache["at"] = 0.0                            # y releer la lista en la próxima
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code} {(r.text or '')[:120]}")
            text = json.loads(r.content.decode("utf-8"))["choices"][0]["message"]["content"] or ""
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
            if text:
                return text
            errors.append(f"{model}: respuesta vacía")
        except Exception as e:  # noqa: BLE001 — siguiente modelo
            errors.append(f"{model}: {type(e).__name__}: {str(e)[:120]}")
    raise RuntimeError("Groq: " + " | ".join(errors[-3:]) if errors else "Groq: sin modelos disponibles")

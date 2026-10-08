"""
Traducción de segmentos con personalización:
  • tono (natural, formal, casual, gamer, técnico, humor)
  • glosario del usuario (términos fijos y términos que NO se traducen)
  • censura opcional de groserías
Cadena de motores: Groq en lote (con contexto) → Google → MyMemory.
Tono y glosario los respeta Groq; en Google/MyMemory se protegen los
términos del glosario con marcadores y se restauran después.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from subtitle_ass import censor_text, norm_lang
from subtitle_style import LANGUAGES, TONES, Segment

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = [m for m in (os.environ.get("GROQ_MODEL", ""), "llama-3.3-70b-versatile",
                           "llama-3.1-8b-instant") if m]
BATCH = 20

LANG_NAMES = {"es": "Spanish", "en": "English", "pt": "Portuguese", "fr": "French",
              "de": "German", "it": "Italian", "ja": "Japanese", "ko": "Korean",
              "zh-cn": "Simplified Chinese", "ru": "Russian"}
_GOOGLE = {"zh-cn": "zh-CN"}
_MYMEMORY = {"es": "es-ES", "en": "en-US", "pt": "pt-PT", "fr": "fr-FR",
             "de": "de-DE", "it": "it-IT", "ja": "ja-JP", "ko": "ko-KR",
             "zh-cn": "zh-CN", "ru": "ru-RU"}


def _google(text: str, src: str, target: str) -> str:
    from deep_translator import GoogleTranslator
    return GoogleTranslator(source="auto",
                            target=_GOOGLE.get(target, target)).translate(text)


def _mymemory(text: str, src: str, target: str) -> str:
    from deep_translator import MyMemoryTranslator
    if src not in _MYMEMORY:
        raise RuntimeError("MyMemory necesita idioma de origen conocido")
    return MyMemoryTranslator(source=_MYMEMORY[src],
                              target=_MYMEMORY[target]).translate(text)


def _bad(result: str | None) -> bool:
    r = (result or "").strip().lower()
    return (not r or "<html" in r or "error 500" in r or "server error" in r
            or "mymemory warning" in r or "query length limit" in r)


# ------------------------------------------------------------- glosario

def _protect(text: str, glossary: dict[str, str]):
    """Cambia los términos del glosario por marcadores que los motores no tocan."""
    slots = []
    for i, (src, dst) in enumerate(sorted(glossary.items(), key=lambda kv: -len(kv[0]))):
        pat = re.compile(re.escape(src), re.IGNORECASE)
        if pat.search(text):
            mark = f"Zq{i}x"
            text = pat.sub(mark, text)
            slots.append((mark, dst))
    return text, slots


def _restore(text: str, slots) -> str:
    for mark, dst in slots:
        text = re.sub(re.escape(mark), dst, text, flags=re.IGNORECASE)
    return text


# ----------------------------------------------------------------- Groq

def _system_prompt(target: str, tone: str, glossary: dict[str, str]) -> str:
    tdesc = TONES.get(tone, TONES["natural"])[1]
    p = (f"You translate video subtitles into {LANG_NAMES.get(target, target)}. "
         f"Style: {tdesc}. Keep each line short, natural and faithful; keep the "
         "meaning and emotion; do not merge or split lines; keep proper nouns, "
         "brand names and numbers. Lines are consecutive, use them as context. ")
    keep = [k for k, v in glossary.items() if k.lower() == v.lower()]
    fixed = [(k, v) for k, v in glossary.items() if k.lower() != v.lower()]
    if fixed:
        p += "Mandatory glossary (always translate these exactly): " + \
             "; ".join(f'"{k}" -> "{v}"' for k, v in fixed) + ". "
    if keep:
        p += "Never translate these terms, keep them as written: " + \
             ", ".join(f'"{k}"' for k in keep) + ". "
    p += ('Reply ONLY with JSON: {"translations":[{"id":<int>,"text":"<translation>"}]} '
          "containing every id received.")
    return p


def _groq_batch(key: str, lines: list[tuple[int, str, str]], target: str,
                tone: str, glossary: dict[str, str]) -> dict[int, str]:
    payload_lines = [{"id": i, "lang": src, "text": text} for i, src, text in lines]
    last_err = None
    for model in GROQ_MODELS:
        try:
            r = requests.post(
                GROQ_URL, timeout=45,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "temperature": 0.2, "max_tokens": 4096,
                      "response_format": {"type": "json_object"},
                      "messages": [
                          {"role": "system", "content": _system_prompt(target, tone, glossary)},
                          {"role": "user", "content": json.dumps({"lines": payload_lines}, ensure_ascii=False)}]})
            r.raise_for_status()
            data = json.loads(r.json()["choices"][0]["message"]["content"])
            out = {int(t["id"]): str(t["text"]).strip() for t in data["translations"]}
            return {i: t for i, t in out.items() if not _bad(t)}
        except Exception as e:  # noqa: BLE001 — se prueba el siguiente modelo
            last_err = e
            time.sleep(1.0)
    raise RuntimeError(f"Groq: {type(last_err).__name__}: {str(last_err)[:150]}")


# ----------------------------------------------------------- orquestación

def translate_segments(segs: list[Segment], target: str,
                       groq_api_key: str | None = None, tone: str = "natural",
                       glossary: dict[str, str] | None = None,
                       censor: bool = False, progress=None) -> list[str]:
    """Rellena seg.translation. Devuelve los problemas encontrados."""
    glossary = {k: v for k, v in (glossary or {}).items() if k and v}
    errors: list[str] = []
    for s in segs:
        s.translation = ""
    lock = threading.Lock()
    state = {"done": 0, "total": 1}

    def tick(n: int = 1):
        with lock:
            state["done"] += n
            if progress:
                progress(min(1.0, state["done"] / state["total"]))

    if target == "orig":                       # solo transcribir
        for s in segs:
            s.translation = s.text
    else:
        pending = []
        for i, s in enumerate(segs):
            if norm_lang(s.lang) == target:
                s.translation = s.text         # ya está en el idioma destino
            else:
                pending.append(i)

        state["total"] = max(1, len(pending))
        if groq_api_key and pending:
            for k in range(0, len(pending), BATCH):
                chunk = pending[k:k + BATCH]
                try:
                    got = _groq_batch(groq_api_key,
                                      [(i, norm_lang(segs[i].lang), segs[i].text) for i in chunk],
                                      target, tone, glossary)
                    for i, t in got.items():
                        if i in chunk:
                            segs[i].translation = t
                    tick(sum(1 for i in got if i in chunk))
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))

        cache: dict[tuple[str, str], str] = {}

        def fallback(i: int) -> str:
            try:
                return _fallback(i)
            finally:
                tick()

        def _fallback(i: int) -> str:
            s = segs[i]
            src = norm_lang(s.lang)
            text, slots = _protect(s.text, glossary)
            if (src, text) in cache:
                return _restore(cache[(src, text)], slots)
            for name, fn in (("Google", _google), ("MyMemory", _mymemory)):
                for attempt in range(2):
                    try:
                        out = fn(text, src, target)
                        if not _bad(out):
                            cache[(src, text)] = out
                            return _restore(out, slots)
                        errors.append(f"{name}: respuesta vacía/inválida")
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
                    time.sleep(0.4 * (attempt + 1))
            return ""

        todo = [i for i in pending if not segs[i].translation]
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, t in zip(todo, pool.map(fallback, todo)):
                segs[i].translation = t

    if progress:
        progress(1.0)
    if censor:
        for s in segs:
            s.translation = censor_text(s.translation)
    return errors

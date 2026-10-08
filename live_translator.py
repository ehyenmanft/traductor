"""
Traducción en vivo con contexto, tono y glosario.

- Frases FINALES: Groq con streaming (la traducción aparece palabra a palabra),
  con las últimas frases como contexto (coherencia de nombres y tono) y el
  glosario del usuario. Cadena de respaldo: modelo grande → modelo rápido →
  Google Translate. Nunca se descartan.
- PARCIALES: solo se traduce el más reciente, tras una pequeña espera
  (debounce) para no traducir texto que está cambiando, con el modelo rápido.
- La traducción final corrige a la del parcial en su sitio.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections import deque

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
FAST_MODEL = "llama-3.1-8b-instant"
BIG_MODEL = "llama-3.3-70b-versatile"

_MYMEMORY = {"es": "es-ES", "en": "en-US", "pt": "pt-PT", "fr": "fr-FR", "de": "de-DE",
             "it": "it-IT", "ja": "ja-JP", "ko": "ko-KR", "zh-cn": "zh-CN", "ru": "ru-RU"}

LANG_NAMES = {
    "es": "Spanish", "en": "English", "pt": "Portuguese", "fr": "French",
    "de": "German", "it": "Italian", "ja": "Japanese", "ko": "Korean",
    "zh-cn": "Simplified Chinese", "ru": "Russian",
}
TONES = {
    "gamer": ("Gamer", "natural gaming and streaming slang; keep terms such as gg, ult, gank, "
                       "push, clutch, nerf, buff, one shot, carry and heal when natural"),
    "natural": ("Natural", "natural, neutral and faithful to the speaker"),
    "formal": ("Formal", "formal and polite, professional register"),
    "casual": ("Casual", "casual and colloquial, like friends talking"),
    "technical": ("Técnico", "precise technical register; keep technical terms and units exact"),
    "funny": ("Humor", "witty and playful, keeping jokes and punchlines landing"),
}


def norm_lang(code: str) -> str:
    code = (code or "auto").lower().strip()
    if code.startswith("zh"):
        return "zh-cn"
    return code.split("-")[0] if code != "auto" else code


def _clean(text: str) -> str:
    text = (text or "").strip()
    return re.sub(r'^["\'«“]+|["\'»”]+$', "", text).strip()


def _bad(text: str | None) -> bool:
    r = (text or "").strip().lower()
    return (not r or "<html" in r or "error 500" in r or "server error" in r
            or "mymemory warning" in r or "query length limit" in r)


class LiveTranslator:
    def __init__(self, target: str = "es", groq_key: str = "", tone: str = "gamer",
                 glossary: dict[str, str] | None = None, context_size: int = 4,
                 final_models: list[str] | None = None, partial_model: str = FAST_MODEL):
        self.target = norm_lang(target)
        self.groq_key = (groq_key or os.environ.get("GROQ_API_KEY", "")).strip()
        self.tone = tone if tone in TONES else "gamer"
        self.glossary = dict(glossary or {})
        self.context: deque[tuple[str, str]] = deque(maxlen=max(0, context_size))
        self.final_models = final_models or [BIG_MODEL, FAST_MODEL]
        self.partial_model = partial_model
        self._cache: dict[tuple, str] = {}
        self._dead_models: set[str] = set()
        self._fail_until = 0.0
        self._fails = 0
        self._lock = threading.Lock()
        self.on_problem = None            # on_problem(mensaje): una frase NO se pudo traducir
        mode = "Groq (contexto + streaming)" if self.groq_key else "Google Translate"
        print(f"[translator] Modo {mode} · tono: {TONES[self.tone][0]}")

    # ---------- ajustes ----------

    def set_target(self, lang: str):
        lang = norm_lang(lang)
        if lang != self.target:
            self.target = lang
            self.reset_context()

    def set_tone(self, tone: str):
        if tone in TONES and tone != self.tone:
            self.tone = tone
            self.reset_context()

    def set_glossary(self, glossary: dict[str, str]):
        self.glossary = dict(glossary or {})
        self.reset_context()

    def reset_context(self):
        with self._lock:
            self.context.clear()
            self._cache.clear()

    @property
    def groq_ok(self) -> bool:
        return bool(self.groq_key) and time.monotonic() >= self._fail_until

    def _groq_failed(self):
        self._fails += 1
        if self._fails >= 4:                  # circuito abierto: 30 s solo Google
            self._fail_until = time.monotonic() + 30
            self._fails = 0
            print("[translator] Groq no responde; uso Google 30 s.")

    # ---------- prompt ----------

    def _system(self, src: str) -> str:
        p = (f"You are a real-time subtitle translator. Translate spoken language "
             f"({LANG_NAMES.get(src, 'any language')}) into {LANG_NAMES.get(self.target, self.target)}. "
             f"Style: {TONES[self.tone][1]}. Keep lines short, natural and faithful; keep proper "
             "nouns, names and numbers. The earlier turns are previous lines of the same "
             "conversation: use them for consistency, never repeat them. ")
        keep = [k for k, v in self.glossary.items() if k.lower() == v.lower()]
        fixed = [(k, v) for k, v in self.glossary.items() if k.lower() != v.lower()]
        if fixed:
            p += "Mandatory glossary (always translate exactly): " + "; ".join(
                f'"{k}" -> "{v}"' for k, v in fixed) + ". "
        if keep:
            p += "Never translate these terms: " + ", ".join(f'"{k}"' for k in keep) + ". "
        return p + "Output ONLY the translation, with no quotes, notes or explanations."

    def _messages(self, text: str, src: str, with_context: bool) -> list[dict]:
        msgs = [{"role": "system", "content": self._system(src)}]
        if with_context:
            with self._lock:                  # el hilo de finales modifica el contexto
                ctx = list(self.context)
            for s, t in ctx:
                msgs += [{"role": "user", "content": s}, {"role": "assistant", "content": t}]
        msgs.append({"role": "user", "content": text})
        return msgs

    # ---------- Groq ----------

    def _groq(self, model: str, messages: list[dict], max_tokens: int, timeout: float,
              on_delta=None) -> str:
        body = {"model": model, "messages": messages, "temperature": 0.1,
                "max_tokens": max_tokens, "stream": bool(on_delta)}
        headers = {"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"}
        r = requests.post(GROQ_URL, headers=headers, json=body, timeout=timeout,
                          stream=bool(on_delta))
        if r.status_code in (400, 404) and "model" in (r.text or "").lower():
            self._dead_models.add(model)      # modelo retirado: no volver a intentarlo
        r.raise_for_status()
        if not on_delta:
            return _clean(r.json()["choices"][0]["message"]["content"])
        out = ""
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
            except (ValueError, KeyError, IndexError):
                continue
            if delta:
                out += delta
                on_delta(out)
        return _clean(out)

    # ---------- Google (respaldo) con glosario protegido ----------

    def _protect(self, text: str):
        slots = []
        for i, (src, dst) in enumerate(sorted(self.glossary.items(), key=lambda kv: -len(kv[0]))):
            pat = re.compile(re.escape(src), re.IGNORECASE)
            if pat.search(text):
                mark = f"Zq{i}x"
                text = pat.sub(mark, text)
                slots.append((mark, dst))
        return text, slots

    @staticmethod
    def _restore(text: str, slots) -> str:
        for mark, dst in slots:
            text = re.sub(re.escape(mark), dst, text, flags=re.IGNORECASE)
        return text

    def _google(self, text: str, src: str) -> str:
        from deep_translator import GoogleTranslator
        protected, slots = self._protect(text)
        tgt = "zh-CN" if self.target == "zh-cn" else self.target
        out = GoogleTranslator(source="auto", target=tgt).translate(protected)
        return self._restore(out or "", slots)

    def _mymemory(self, text: str, src: str) -> str:
        """Tercer respaldo (gratuito, sin clave) por si Google bloquea la IP del servidor."""
        from deep_translator import MyMemoryTranslator
        if src not in _MYMEMORY or self.target not in _MYMEMORY:
            raise RuntimeError("MyMemory necesita un idioma de origen conocido")
        protected, slots = self._protect(text)
        out = MyMemoryTranslator(source=_MYMEMORY[src], target=_MYMEMORY[self.target]).translate(protected)
        return self._restore(out or "", slots)

    def _problem(self, errors: list[str]):
        """Avisa (a la interfaz) de por qué no se pudo traducir, con una causa corta y accionable."""
        joined = " | ".join(errors)
        if not self.groq_key:
            cause = "falta groq_api_key y Google no responde"
        elif "401" in joined or "403" in joined:
            cause = "clave de Groq rechazada (401/403)"
        elif "429" in joined:
            cause = "Groq saturado (429)"
        else:
            cause = "Groq y Google no responden"
        if self.on_problem:
            try:
                self.on_problem(f"⚠️ No pude traducir: {cause}")
            except Exception:  # noqa: BLE001
                pass

    # ---------- API ----------

    def translate_partial(self, text: str, src: str) -> str:
        """Rápida y barata: modelo pequeño, con contexto; sin streaming."""
        text = text.strip()
        if not text or norm_lang(src) == self.target:
            return text
        if self.groq_ok and self.partial_model not in self._dead_models:
            try:
                out = self._groq(self.partial_model, self._messages(text, norm_lang(src), True),
                                 160, 4.0)
                self._fails = 0
                if not _bad(out):
                    return out
            except Exception:  # noqa: BLE001
                self._groq_failed()
                return ""                     # un parcial perdido no justifica llamar a Google
        elif self.groq_key:
            return ""
        try:
            out = self._google(text, src)
            return "" if _bad(out) else out
        except Exception:  # noqa: BLE001
            return ""

    def translate_final(self, text: str, src: str, on_delta=None) -> str:
        """Frase terminada: contexto + streaming + respaldo. Siempre devuelve texto."""
        text = text.strip()
        src = norm_lang(src)
        if not text:
            return ""
        if src == self.target:
            return text
        key = (src, self.target, self.tone, text)
        if key in self._cache:
            return self._cache[key]

        result, errors = "", []
        if self.groq_ok:
            for model in self.final_models:
                if model in self._dead_models:
                    continue
                try:
                    result = self._groq(model, self._messages(text, src, True), 220, 8.0, on_delta)
                    if not _bad(result):
                        self._fails = 0
                        break
                    result = ""
                    errors.append(f"{model}: respuesta vacía")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{model}: {type(e).__name__}: {str(e)[:140]}")
                    print(f"[translator] {errors[-1]}")
                    self._groq_failed()
                    if not self.groq_ok:
                        break
        elif self.groq_key:
            errors.append("Groq en pausa (4 fallos seguidos)")
        for name, fn in (("Google", self._google), ("MyMemory", self._mymemory)):
            if result:
                break
            try:
                out = fn(text, src)
                if _bad(out):
                    errors.append(f"{name}: respuesta vacía")
                else:
                    result = out
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {type(e).__name__}: {str(e)[:140]}")
                print(f"[translator] {errors[-1]}")
        if not result:
            self._problem(errors)
        result = result or text
        with self._lock:
            self.context.append((text, result))
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[key] = result
        return result

    # compatibilidad con el Translator anterior
    def translate(self, text: str, source_language: str) -> str:
        return self.translate_final(text, source_language)


class LiveTranslationWorker:
    """Dos hilos: finales (cola ordenada, nunca se pierden) y parciales (solo el último)."""

    def __init__(self, translator: LiveTranslator, emit, stop: threading.Event,
                 debounce: float = 0.25, translate_partials: bool = True, on_final=None):
        self.tr, self.emit, self.stop = translator, emit, stop
        self.on_final = on_final                  # on_final(uid, traducción, original, idioma)
        self.debounce = debounce
        self.translate_partials = translate_partials
        self._finals: "queue.Queue[tuple[int, str, str]]" = queue.Queue()
        self._cond = threading.Condition()
        self._partial: tuple[int, str, str] | None = None
        self._finalized: deque[int] = deque(maxlen=64)
        self._shown: dict[int, str] = {}          # última traducción mostrada por frase
        threading.Thread(target=self._final_loop, daemon=True).start()
        threading.Thread(target=self._partial_loop, daemon=True).start()

    def submit(self, uid: int, text: str, lang: str, final: bool):
        if final:
            with self._cond:
                self._finalized.append(uid)
                if self._partial and self._partial[0] == uid:
                    self._partial = None
            self._finals.put((uid, text, lang))
        elif self.translate_partials and len(text.strip()) >= 3:
            with self._cond:
                self._partial = (uid, text, lang)
                self._cond.notify()

    # ---- finales ----
    def _final_loop(self):
        while not self.stop.is_set():
            try:
                uid, text, lang = self._finals.get(timeout=0.5)
            except queue.Empty:
                continue
            prev = self._shown.get(uid, "")
            last_push = [0.0]

            def on_delta(partial_text, uid=uid, prev=prev):
                # no pisar una traducción ya visible con 1-2 palabras sueltas
                if prev and len(partial_text) < min(14, len(prev) // 2):
                    return
                now = time.monotonic()
                if now - last_push[0] >= 0.06:
                    last_push[0] = now
                    self.emit(uid, partial_text)

            final_text = self.tr.translate_final(text, lang, on_delta)
            self.emit(uid, final_text)
            self._shown.pop(uid, None)
            if self.on_final:
                try:
                    self.on_final(uid, final_text, text, lang)
                except Exception as e:  # noqa: BLE001 — el doblaje nunca debe romper la traducción
                    print(f"[translator] on_final: {type(e).__name__}")

    # ---- parciales ----
    def _partial_loop(self):
        last: dict[int, str] = {}
        while not self.stop.is_set():
            with self._cond:
                if self._partial is None:
                    self._cond.wait(timeout=0.5)
                if self._partial is None:
                    continue
            time.sleep(self.debounce)             # que el texto se estabilice
            with self._cond:
                item, self._partial = self._partial, None
            if item is None:
                continue
            uid, text, lang = item
            if uid in self._finalized or last.get(uid) == text:
                continue
            out = self.tr.translate_partial(text, lang)
            if out and uid not in self._finalized:   # la final puede haber llegado mientras tanto
                last[uid] = text
                self._shown[uid] = out
                self.emit(uid, out)
            if len(last) > 64:
                last.clear()

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


def _fix_mojibake(text: str) -> str:
    """'cÃ³mo' → 'cómo': UTF-8 leído como latin-1/cp1252 en algún punto del camino."""
    if "Ã" not in text and "Â" not in text:
        return text
    for enc in ("cp1252", "latin-1"):
        try:
            return text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


def _clean(text: str) -> str:
    text = _fix_mojibake(text or "")
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    return re.sub(r'^["\'«“]+|["\'»”]+$', "", text).strip()


_CJK = ("ja", "ko", "zh-cn")


def _complete(src_text: str, out: str, src: str, target: str) -> bool:
    """¿La traducción conserva el contenido? Una frase larga que sale mucho más corta es señal de que el
    modelo omitió o resumió. (Entre idiomas CJK y no CJK las longitudes no son comparables: se exime.)"""
    if len(src_text) < 40 or src in _CJK or target in _CJK:
        return True
    return len(out.strip()) >= 0.45 * len(src_text.strip())


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
        self._partial_pause = 0.0
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
             "nouns, names and numbers. Translate EVERYTHING that was said, every word and "
             "every sentence: never omit, shorten, summarize or merge content, and keep "
             "interjections and filler that carry meaning. The earlier turns are previous "
             "lines of the same conversation: use them only for consistency, never repeat them. ")
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
              on_delta=None, retry_429: bool = False) -> str:
        body = {"model": model, "messages": messages, "temperature": 0.1,
                "max_tokens": max_tokens, "stream": bool(on_delta)}
        headers = {"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"}
        low = model.lower()
        if "gpt-oss" in low:                  # modelos que razonan: sin esto gastan los tokens "pensando"
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False
            body["max_tokens"] = max(max_tokens, 400)
        elif "qwen" in low:
            body["reasoning_effort"] = "none"
            body["max_tokens"] = max(max_tokens, 400)
        r = requests.post(GROQ_URL, headers=headers, json=body, timeout=timeout,
                          stream=bool(on_delta))
        if r.status_code == 429 and retry_429:           # límite por minuto: esperar y repetir, no perder la frase
            try:
                wait = float(r.headers.get("retry-after", "") or 2.0)
            except ValueError:
                wait = 2.0
            time.sleep(min(max(wait, 0.5), 5.0))
            r = requests.post(GROQ_URL, headers=headers, json=body, timeout=timeout,
                              stream=bool(on_delta))
        if r.status_code == 400 and "reasoning" in (r.text or "").lower():
            for k in ("reasoning_effort", "include_reasoning"):   # esa versión no admite el ajuste: sin él
                body.pop(k, None)
            r = requests.post(GROQ_URL, headers=headers, json=body, timeout=timeout,
                              stream=bool(on_delta))
        if r.status_code in (400, 404):
            if "model" in (r.text or "").lower() or r.status_code == 404:
                self._dead_models.add(model)  # modelo retirado: no volver a intentarlo
                self._discover_models()       # y buscar cuáles sí ofrece Groq ahora
            raise RuntimeError(f"{r.status_code} {(r.text or '')[:140]}")
        r.raise_for_status()
        if not on_delta:
            return _clean(r.json()["choices"][0]["message"]["content"])
        out = ""
        r.encoding = "utf-8"          # el flujo SSE no declara codificación: requests supondría latin-1 (acentos rotos)
        for raw in r.iter_lines():
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
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

    def _discover_models(self):
        """Los modelos de Groq se retiran con el tiempo: pregunta cuáles hay y elige el mejor y el rápido."""
        if time.time() - getattr(self, "_discovered", 0) < 600 or not self.groq_key:
            return
        self._discovered = time.time()
        try:
            r = requests.get(GROQ_URL.rsplit("/chat/", 1)[0] + "/models", timeout=8,
                             headers={"Authorization": f"Bearer {self.groq_key}"})
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("data", []) if m.get("active", True)]
        except Exception as e:  # noqa: BLE001
            print(f"[translator] no pude listar modelos de Groq: {type(e).__name__}: {str(e)[:100]}")
            return
        skip = ("whisper", "guard", "tts", "embed", "orpheus", "playai", "distil", "compound", "safeguard", "vision")
        chat = [i for i in ids if not any(k in i.lower() for k in skip)]
        if not chat:
            return

        def score(i, words):
            tokens = set(re.split(r"[^a-z0-9.]+", i.lower()))
            return sum(w in tokens for w in words)
        big = sorted(chat, key=lambda i: -(score(i, ("llama", "70b", "versatile", "120b", "qwen3", "32b", "27b"))))
        fast = sorted(chat, key=lambda i: -(score(i, ("8b", "instant", "20b", "small", "7b"))))
        self.final_models = list(dict.fromkeys(big[:2] + fast[:1]))
        self.partial_model = fast[0]
        self._dead_models.difference_update(self.final_models + [self.partial_model])
        print(f"[translator] modelos de Groq elegidos: {self.final_models} (parciales: {self.partial_model})")

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
        """Rápida y barata: modelo pequeño, con contexto; sin streaming. Sus fallos NUNCA frenan a las
        frases finales (pausa propia), para que los parciales no gasten el límite de Groq de las finales."""
        text = text.strip()
        if not text or norm_lang(src) == self.target:
            return text
        if self.groq_key:
            if time.monotonic() < self._partial_pause or self.partial_model in self._dead_models:
                return ""
            try:
                out = self._groq(self.partial_model, self._messages(text, norm_lang(src), True),
                                 min(400, 60 + 2 * len(text)), 4.0)
                return "" if _bad(out) else out
            except Exception:  # noqa: BLE001
                self._partial_pause = time.monotonic() + 8.0     # respira: sin parciales 8 s
                return ""                     # un parcial perdido no justifica llamar a Google
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

        result, errors, best = "", [], ""
        if self.groq_ok:
            tried: set[str] = set()
            while not result and self.groq_ok:
                pending = [m for m in self.final_models if m not in self._dead_models and m not in tried]
                if not pending:
                    break
                model = pending[0]          # se relee cada vuelta: _discover_models puede cambiar la lista
                tried.add(model)
                try:
                    out = self._groq(model, self._messages(text, src, True), min(1200, 150 + 3 * len(text)),
                                     10.0, on_delta, retry_429=True)
                    self._fails = 0
                    if _bad(out):
                        errors.append(f"{model}: respuesta vacía")
                    elif not _complete(text, out, src, self.target):
                        errors.append(f"{model}: traducción incompleta")
                        best = max(best, out, key=len)
                    else:
                        result = out
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{model}: {type(e).__name__}: {str(e)[:140]}")
                    print(f"[translator] {errors[-1]}")
                    self._groq_failed()
        elif self.groq_key:
            errors.append("Groq en pausa (4 fallos seguidos)")
        for name, fn in (("Google", self._google), ("MyMemory", self._mymemory)):
            if result:
                break
            try:
                out = fn(text, src)
                if _bad(out):
                    errors.append(f"{name}: respuesta vacía")
                elif _complete(text, out, src, self.target) or len(out) >= len(best):
                    result = out
                else:
                    best = out
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {type(e).__name__}: {str(e)[:140]}")
                print(f"[translator] {errors[-1]}")
        if not result and best:
            result = best                    # incompleta, pero es lo mejor que hay: mejor eso que el original
            print(f"[translator] aviso: traducción posiblemente incompleta ({len(best)}/{len(text)} caracteres)")
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
                 debounce: float = 0.25, translate_partials: bool = True, on_final=None,
                 min_gap: float = 1.0):
        self.tr, self.emit, self.stop = translator, emit, stop
        self.on_final = on_final                  # on_final(uid, traducción, original, idioma)
        self.debounce = debounce
        self.min_gap = min_gap                    # segundos mínimos entre dos traducciones de parciales
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
        last_call = 0.0
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
            if not self._finals.empty():          # primero lo definitivo: los parciales no compiten con las finales
                continue
            gap = self.min_gap - (time.monotonic() - last_call)
            if gap > 0:                            # techo de llamadas: no agotar el límite por minuto de Groq
                time.sleep(gap)
            last_call = time.monotonic()
            out = self.tr.translate_partial(text, lang)
            if out and uid not in self._finalized:   # la final puede haber llegado mientras tanto
                last[uid] = text
                self._shown[uid] = out
                self.emit(uid, out)
            if len(last) > 64:
                last.clear()

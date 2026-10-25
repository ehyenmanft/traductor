"""
Motor de traducción en tiempo real con contexto gamer:
- Prioridad 1: Groq API (llama-3.1-8b-instant) — Ultra-rápido (~120ms) y especializado en jerga de videojuegos.
- Prioridad 2: Google Translate (deep-translator) con detección automática.
- Incluye caché en memoria para llamadas tácticas y frases repetidas.
"""
import os
import re
import requests
from deep_translator import GoogleTranslator

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

LANG_NAMES = {
    "es": "Spanish",
    "en": "English",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh-cn": "Chinese",
    "ru": "Russian",
}


MYMEMORY_LANGS = {
    "es": "es-ES",
    "en": "en-US",
    "pt": "pt-BR",
    "fr": "fr-FR",
    "de": "de-DE",
    "it": "it-IT",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "ru": "ru-RU",
}


class Translator:
    def __init__(self, target_language: str = "es", groq_api_key: str | None = None):
        self.target = target_language.lower().strip()
        self.groq_key = (groq_api_key or os.environ.get("GROQ_API_KEY", "")).strip()
        self._cache: dict[tuple[str, str, str], tuple[str, str]] = {}
        self._groq_failures = 0
        if self.groq_key:
            print(f"[translator] Modo Gamer activo con Groq ({GROQ_MODEL})")
        else:
            print("[translator] Motor de traducción listo (Google Direct + MyMemory)")

    def set_target(self, target_language: str):
        target = target_language.lower().strip()
        if target != self.target:
            self.target = target

    def set_groq_key(self, api_key: str):
        self.groq_key = (api_key or "").strip()

    def _translate_groq(self, text: str, src: str, target: str) -> str:
        target_name = LANG_NAMES.get(target, target)
        src_name = LANG_NAMES.get(src, "any language") if src != "auto" else "any language"

        system_prompt = (
            f"You are a real-time subtitle translator for video games. "
            f"Translate spoken voice chat or game dialogue from {src_name} into natural, conversational {target_name}. "
            f"Preserve standard gaming slang and callouts naturally (e.g. ult/ulti, gank, push, clutch, revive, diff, one shot, agro, carry, heal, gg). "
            f"Output ONLY the translated text without quotes, notes, or explanations."
        )

        headers = {
            "Authorization": f"Bearer {self.groq_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.1,
            "max_tokens": 120,
        }

        resp = requests.post(GROQ_CHAT_URL, headers=headers, json=payload, timeout=3.5)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        content = re.sub(r'^["\'«]+|["\'»]+$', '', content).strip()
        return content

    def _translate_google_direct(self, text: str, src: str, target: str) -> tuple[str, str]:
        """Traducción directa y ligera vía Google Translate API (cliente extensión Chrome).
        No sufre bloqueo HTTP 429 como el scraping HTML y detecta el idioma origen."""
        for host in ("clients5.google.com", "translate.googleapis.com"):
            try:
                url = f"https://{host}/translate_a/t"
                params = {
                    "client": "dict-chrome-ex",
                    "sl": src if src != "auto" else "auto",
                    "tl": target,
                    "q": text,
                }
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                }
                resp = requests.get(url, params=params, headers=headers, timeout=3.0)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        if isinstance(data[0], list):
                            trans = "".join(part[0] for part in data if isinstance(part, list) and part)
                            det_lang = data[0][1] if len(data[0]) > 1 and data[0][1] else src
                            return trans.strip(), det_lang.lower().strip()
                        elif isinstance(data[0], str):
                            return data[0].strip(), src
            except Exception:
                continue
        return "", ""

    def _translate_mymemory(self, text: str, src: str, target: str) -> str:
        try:
            from deep_translator import MyMemoryTranslator
            s = MYMEMORY_LANGS.get(src, "en-US")
            t = MYMEMORY_LANGS.get(target, "es-ES")
            res = MyMemoryTranslator(source=s, target=t).translate(text)
            if res and not any(err in res.lower() for err in ["error 500", "server error", "mymemory warning"]):
                return res.strip()
        except Exception:
            pass
        return ""

    def _translate_google_legacy(self, text: str, src: str, target: str) -> str:
        try:
            res = GoogleTranslator(source=src if src != "auto" else "auto", target=target).translate(text)
            if res and not any(err in res.lower() for err in ["error 500", "server error", "<html"]):
                return res.strip()
        except Exception:
            pass
        return ""

    def translate(self, text: str, source_language: str = "auto") -> tuple[str, str]:
        """Traduce el texto y devuelve (texto_traducido, idioma_detectado)."""
        text = text.strip()
        if not text:
            return "", source_language

        src = (source_language or "auto").lower().strip()
        target = self.target

        # Si ya sabemos con certeza que está en el idioma destino
        if src != "auto" and src == target:
            return text, src

        key = (src, target, text)
        if key in self._cache:
            return self._cache[key]

        result = ""
        detected_lang = src

        # 1. Intentar con Groq Gamer si hay clave disponible
        if self.groq_key and self._groq_failures < 4:
            try:
                result = self._translate_groq(text, src, target)
                self._groq_failures = 0
            except Exception:
                self._groq_failures += 1
                result = ""

        # 2. Motor prioritario: Google Direct (rápido, sin 429, detecta idioma)
        if not result:
            res_trans, res_det = self._translate_google_direct(text, src, target)
            if res_trans:
                result = res_trans
                if res_det and res_det != "auto":
                    detected_lang = res_det

        # 3. Fallback a MyMemory
        if not result:
            result = self._translate_mymemory(text, detected_lang, target)

        # 4. Fallback legado
        if not result:
            result = self._translate_google_legacy(text, src, target)

        result = result or text
        out = (result, detected_lang)

        # Guardar en caché
        if len(self._cache) > 1000:
            self._cache.clear()
        self._cache[key] = out
        return out


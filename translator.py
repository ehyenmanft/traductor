"""
Motor de traducción en tiempo real con contexto gamer:
- Prioridad 1: Groq API (llama-3.1-8b-instant) — Ultra-rápido (~120ms) y especializado en jerga de videojuegos.
- Prioridad 2: Google Translate (deep-translator) — Fallback automático y gratuito.
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


class Translator:
    def __init__(self, target_language: str = "es", groq_api_key: str | None = None):
        self.target = target_language.lower().strip()
        self.groq_key = (groq_api_key or os.environ.get("GROQ_API_KEY", "")).strip()
        self._cache: dict[tuple[str, str, str], str] = {}
        self._groq_failures = 0
        if self.groq_key:
            print(f"[translator] Modo Gamer activo con Groq ({GROQ_MODEL})")
        else:
            print("[translator] Modo estándar activo con Google Translate")

    def set_target(self, target_language: str):
        target = target_language.lower().strip()
        if target != self.target:
            self.target = target

    def set_groq_key(self, api_key: str):
        self.groq_key = (api_key or "").strip()

    def _translate_groq(self, text: str, src: str, target: str) -> str:
        """Traduce usando LLaMA 3.1 8B Instant con contexto de videojuegos."""
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

        resp = requests.post(GROQ_CHAT_URL, headers=headers, json=payload, timeout=4.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()

        # Limpiar comillas iniciales/finales si el modelo las agregó
        content = re.sub(r'^["\'«]+|["\'»]+$', '', content).strip()
        return content

    def _translate_google(self, text: str, src: str, target: str) -> str:
        """Fallback tradicional usando Google Translate."""
        try:
            return GoogleTranslator(source=src, target=target).translate(text)
        except Exception:
            try:
                return GoogleTranslator(source="auto", target=target).translate(text)
            except Exception:
                return text

    def translate(self, text: str, source_language: str) -> str:
        text = text.strip()
        if not text:
            return ""

        src = (source_language or "auto").lower().strip()
        target = self.target
        if src == target:
            return text  # ya está en el idioma destino

        key = (src, target, text)
        if key in self._cache:
            return self._cache[key]

        result = ""
        # 1. Intentar con Groq Gamer si hay clave disponible
        if self.groq_key and self._groq_failures < 5:
            try:
                result = self._translate_groq(text, src, target)
                self._groq_failures = 0
            except Exception as e:
                self._groq_failures += 1
                # En caso de error puntual en Groq, cae a Google silenciosamente
                result = ""

        # 2. Fallback a Google Translate si no hay resultado
        if not result:
            result = self._translate_google(text, src, target)

        result = result or text

        # Guardar en caché
        if len(self._cache) > 1000:
            self._cache.clear()
        self._cache[key] = result
        return result

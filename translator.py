"""
Traducción con deep-translator (Google Translate, gratuito, requiere internet).
Si el idioma detectado ya es el idioma destino, se omite la traducción.
"""
from deep_translator import GoogleTranslator


class Translator:
    def __init__(self, target_language: str = "es"):
        self.target = target_language.lower().strip()
        self._cache: dict[tuple[str, str, str], str] = {}

    def set_target(self, target_language: str):
        target = target_language.lower().strip()
        if target != self.target:
            self.target = target

    def translate(self, text: str, source_language: str) -> str:
        src = (source_language or "auto").lower().strip()
        target = self.target
        if src == target:
            return text  # ya está en el idioma destino

        key = (src, target, text)
        if key in self._cache:
            return self._cache[key]

        try:
            result = GoogleTranslator(
                source=src, target=target
            ).translate(text)
        except Exception:
            # Si el código de idioma no le gusta al traductor,
            # reintenta con autodetección; si falla todo, devuelve el original.
            try:
                result = GoogleTranslator(source="auto", target=target).translate(text)
            except Exception:
                result = text

        result = result or text
        if len(self._cache) > 1000:
            self._cache.clear()
        self._cache[key] = result
        return result


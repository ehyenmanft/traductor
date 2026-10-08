"""
Tipos y limpieza de texto de transcripción, SIN dependencias pesadas (sin
faster-whisper). Los usan los transcriptores, el servidor de AWS y las pruebas.
"""
import re
from dataclasses import dataclass


# Frases que Whisper alucina con silencio/música (multiidioma)
_JUNK_PHRASES = (
    "thanks for watching", "thank you for watching", "please subscribe",
    "subtítulos realizados", "subtitulado por", "subtítulos por",
    "amara.org", "www.youtube", "suscríbete", "gracias por ver",
    "sous-titrage", "sous-titres", "untertitel", "ご視聴ありがとう",
)


def clean_text(text: str) -> str:
    """Limpia alucinaciones típicas de Whisper. Devuelve '' si es basura."""
    text = text.strip()
    if not text:
        return ""
    # Colapsa palabras repetidas 3+ veces seguidas ("you you you you")
    text = re.sub(r"\b(\w+)(?:\s+\1\b){2,}", r"\1 \1", text,
                  flags=re.IGNORECASE | re.UNICODE)
    low = text.lower()
    if any(j in low for j in _JUNK_PHRASES):
        return ""
    # Sin contenido real: vacío tras quitar puntuación (",,,,,") o
    # repetición larga de 1-2 caracteres ("aaaaaaaa")
    core = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    if not core:
        return ""
    if len(core) > 4 and len(set(core.lower())) <= 2:
        return ""
    return text


@dataclass
class TranscriptSegment:
    utterance_id: int
    text: str
    language: str
    language_prob: float
    is_final: bool

"""
Motor de RESPALDO de Deepgram: NVIDIA Canary 1B (build.nvidia.com, gRPC Riva).
Si Deepgram no conecta (sin saldo, caído, sin red), transcribe frase a frase
rotando entre varias keys de NVIDIA con timeout corto, hasta que Deepgram vuelve.

Mismo troceo por silencios que el motor Groq (frases finales, sin parciales).
"""
import numpy as np

from transcriber_groq import GroqTranscriber, RateLimiter, to_wav_bytes

NVIDIA_URI = "grpc.nvcf.nvidia.com:443"
CANARY_FUNCTION_ID = "b0e8b4a5-217c-40b7-9b96-17d84e666317"   # nvidia/canary-1b-asr
# Canary no autodetecta idioma; con es-ES traduce al español en vez de transcribir
_CANARY_LANG = {"en": "en-US", "es": "es-US", "de": "de-DE", "fr": "fr-FR"}


class NvidiaTranscriber(GroqTranscriber):
    TAG = "nvidia"
    MODEL = "canary-1b-asr"

    def __init__(self, audio_queue, api_keys: list[str],
                 language: str | None = None, timeout: float = 3.0):
        import riva.client
        if not api_keys:
            raise ValueError("Falta nvidia_api_keys")
        super().__init__(audio_queue, api_key=api_keys[0], language=language)
        self.lang = (language or "en").lower()
        self.cfg = riva.client.RecognitionConfig(
            language_code=_CANARY_LANG.get(self.lang, "en-US"),
            max_alternatives=1, enable_automatic_punctuation=True)
        self.services = [riva.client.ASRService(riva.client.Auth(
            use_ssl=True, uri=NVIDIA_URI, metadata_args=[
                ["function-id", CANARY_FUNCTION_ID],
                ["authorization", f"Bearer {k}"]])) for k in api_keys]
        self.current = 0          # key que respondió la última vez
        self.timeout = timeout
        # shortcut: ~40 req/min por key es el límite gratuito conocido de NVIDIA; ajustar si devuelve 429
        self.limiter = RateLimiter(max_per_min=40 * len(api_keys),
                                   max_counted_sec_hour=10**9, min_counted_sec=0)

    def _request(self, audio: np.ndarray) -> tuple[str, str]:
        wav = to_wav_bytes(audio)
        n = len(self.services)
        for i in range(n):
            idx = (self.current + i) % n
            try:
                r = self.services[idx].offline_recognize(
                    wav, self.cfg, future=True).result(timeout=self.timeout)
                self.current = idx
                text = " ".join(x.alternatives[0].transcript for x in r.results
                                if x.alternatives)
                return text, self.lang
            except Exception as e:
                print(f"[nvidia] Key #{idx + 1} no respondió ({type(e).__name__}).")
        raise RuntimeError("ninguna key de NVIDIA respondió")


def build_fallback(audio_queue, api_keys, language: str | None = None):
    """Respaldo para DeepgramTranscriber, o None si no hay keys o falta nvidia-riva-client."""
    keys = [k.strip() for k in (api_keys or []) if k and k.strip()]
    if not keys:
        return None
    try:
        return NvidiaTranscriber(audio_queue, api_keys=keys, language=language)
    except Exception as e:
        print(f"[nvidia] Respaldo no disponible ({e}).")
        return None

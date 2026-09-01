"""
Motor de transcripción remoto: Groq API (whisper-large-v3-turbo).
Precisión de large-v3 con ~400 ms de latencia total, CPU casi libre.

Diseño para respetar el plan gratuito de Groq:
- Solo segmentos FINALES (sin parciales): chunks de máx ~4.5 s,
  cerrados a los ~0.5 s de silencio. Groq responde tan rápido que
  se siente inmediato igualmente.
- Rate limiter deslizante: máx 18 req/min y presupuesto de
  audio-segundos/hora (cada request cuenta mínimo 10 s en Groq).
  Si el límite aprieta, los segmentos se alargan solos.
"""
import io
import os
import queue
import threading
import time
import wave
from collections import deque

import numpy as np
import requests

from transcriber import TranscriptSegment, clean_text

RATE = 16000
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"

# verbose_json devuelve el idioma como nombre ("english") → ISO para el traductor
_LANG_TO_ISO = {
    "english": "en", "spanish": "es", "portuguese": "pt", "french": "fr",
    "german": "de", "italian": "it", "russian": "ru", "japanese": "ja",
    "korean": "ko", "chinese": "zh-CN", "arabic": "ar", "hindi": "hi",
    "dutch": "nl", "polish": "pl", "turkish": "tr", "swedish": "sv",
    "ukrainian": "uk", "greek": "el", "czech": "cs", "romanian": "ro",
    "hungarian": "hu", "danish": "da", "finnish": "fi", "norwegian": "no",
    "hebrew": "iw", "thai": "th", "vietnamese": "vi", "indonesian": "id",
    "malay": "ms", "catalan": "ca", "tagalog": "tl", "urdu": "ur",
}


def lang_to_iso(name: str) -> str:
    name = (name or "").strip().lower()
    if len(name) <= 3:      # ya es un código ("en", "es"...)
        return name
    return _LANG_TO_ISO.get(name, "auto")


def to_wav_bytes(audio: np.ndarray, rate: int = RATE) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class RateLimiter:
    """Ventana deslizante: solicitudes/min y audio-segundos contados/hora
    (Groq cuenta mínimo 10 s por solicitud)."""

    def __init__(self, max_per_min: int = 18, max_counted_sec_hour: int = 6500,
                 min_counted_sec: float = 10.0):
        self.max_per_min = max_per_min
        self.max_counted_sec_hour = max_counted_sec_hour
        self.min_counted_sec = min_counted_sec
        self._reqs: deque[float] = deque()
        self._counted: deque[tuple[float, float]] = deque()

    def _prune(self, now: float):
        while self._reqs and now - self._reqs[0] > 60:
            self._reqs.popleft()
        while self._counted and now - self._counted[0][0] > 3600:
            self._counted.popleft()

    def allowed(self, duration_sec: float) -> bool:
        now = time.monotonic()
        self._prune(now)
        counted = max(self.min_counted_sec, duration_sec)
        return (len(self._reqs) < self.max_per_min
                and sum(c for _, c in self._counted) + counted
                <= self.max_counted_sec_hour)

    def record(self, duration_sec: float):
        now = time.monotonic()
        self._reqs.append(now)
        self._counted.append((now, max(self.min_counted_sec, duration_sec)))


class GroqTranscriber:
    """Misma interfaz que StreamingTranscriber: text_queue / start / stop."""

    def __init__(
        self,
        audio_queue: "queue.Queue[np.ndarray]",
        api_key: str | None = None,
        language: str | None = None,
        min_segment_sec: float = 0.5,
        max_segment_sec: float = 4.5,
        hard_max_sec: float = 15.0,
        silence_threshold: float = 0.01,
        silence_chunks_to_flush: int = 2,
    ):
        self.audio_queue = audio_queue
        self.text_queue: "queue.Queue[TranscriptSegment]" = queue.Queue()
        self._stop = threading.Event()
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not self.api_key:
            raise ValueError("Falta GROQ_API_KEY")
        self.forced_language = language
        self.limiter = RateLimiter()
        self.consecutive_errors = 0

        self.min_samples = int(min_segment_sec * RATE)
        self.max_samples = int(max_segment_sec * RATE)
        self.hard_max_samples = int(hard_max_sec * RATE)
        self.base_threshold = silence_threshold
        self.noise_floor = silence_threshold / 2
        self.silence_chunks_to_flush = silence_chunks_to_flush
        print(f"[groq] Motor remoto listo: {GROQ_MODEL}")

    # ---------- llamada a la API ----------

    def _request(self, audio: np.ndarray) -> tuple[str, str]:
        data = {"model": GROQ_MODEL, "response_format": "verbose_json",
                "temperature": 0}
        if self.forced_language:
            data["language"] = self.forced_language
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            files={"file": ("audio.wav", to_wav_bytes(audio), "audio/wav")},
            data=data,
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
        return j.get("text", ""), lang_to_iso(j.get("language", "auto"))

    def _transcribe_remote(self, audio: np.ndarray, uid: int):
        duration = len(audio) / RATE
        try:
            raw, lang = self._request(audio)
            self.consecutive_errors = 0
        except Exception as e:
            self.consecutive_errors += 1
            print(f"[groq] Error de red/API ({type(e).__name__}). "
                  f"Segmento perdido ({self.consecutive_errors} seguidos).")
            if self.consecutive_errors >= 5:
                print("[groq] Muchos fallos seguidos — considera reiniciar "
                      "con --engine local.")
            return
        finally:
            self.limiter.record(duration)
        text = clean_text(raw)
        if text:
            self.text_queue.put(TranscriptSegment(
                uid, text, lang, 1.0, is_final=True))

    # ---------- umbral dinámico (igual que el motor local) ----------

    def _dynamic_threshold(self, rms: float) -> float:
        thr = min(max(self.noise_floor * 3.0, self.base_threshold * 0.6), 0.05)
        if rms < thr:
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms
        return thr

    # ---------- bucle principal ----------

    def _loop(self):
        buffer: list[np.ndarray] = []
        buffered = 0
        silent_streak = 0
        utterance_id = 0

        while not self._stop.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            rms = float(np.sqrt(np.mean(chunk**2)))
            is_silent = rms < self._dynamic_threshold(rms)

            if is_silent and buffered == 0:
                continue

            buffer.append(chunk)
            buffered += len(chunk)
            silent_streak = silent_streak + 1 if is_silent else 0

            duration = buffered / RATE
            want_flush = (
                (silent_streak >= self.silence_chunks_to_flush
                 and buffered >= self.min_samples)
                or buffered >= self.max_samples
            )
            if not want_flush:
                continue

            # respeto de límites: si no hay presupuesto, seguir acumulando
            if not self.limiter.allowed(duration):
                if buffered < self.hard_max_samples:
                    continue
                # tope duro alcanzado: esperar presupuesto sin perder audio
                while (not self.limiter.allowed(duration)
                       and not self._stop.is_set()):
                    time.sleep(0.2)

            audio = np.concatenate(buffer)
            buffer, buffered, silent_streak = [], 0, 0
            self._transcribe_remote(audio, utterance_id)
            utterance_id += 1

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

"""
Transcripción en streaming con faster-whisper, con PARCIALES en vivo,
filtro anti-alucinaciones y umbral de silencio auto-calibrado.
"""
import os
import queue
import re
import threading
import time
from dataclasses import dataclass

import numpy as np
from faster_whisper import WhisperModel

RATE = 16000

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


def _enable_cuda_dlls():
    try:
        import nvidia.cublas.lib
        import nvidia.cudnn.lib
        for mod in (nvidia.cublas.lib, nvidia.cudnn.lib):
            os.add_dll_directory(os.path.dirname(mod.__file__))
    except Exception:
        pass


@dataclass
class TranscriptSegment:
    utterance_id: int
    text: str
    language: str
    language_prob: float
    is_final: bool


class StreamingTranscriber:
    def __init__(
        self,
        audio_queue: "queue.Queue[np.ndarray]",
        model_size: str = "tiny",
        device: str = "cpu",
        language: str | None = None,
        min_segment_sec: float = 0.6,
        max_segment_sec: float = 6.0,
        partial_stride_sec: float = 1.0,
        silence_threshold: float = 0.01,   # umbral base; se auto-calibra
        silence_chunks_to_flush: int = 2,
    ):
        self.audio_queue = audio_queue
        self.text_queue: "queue.Queue[TranscriptSegment]" = queue.Queue()
        self._stop = threading.Event()
        self.forced_language = language

        _enable_cuda_dlls()
        if device == "cpu":
            self.model = WhisperModel(
                model_size, device="cpu", compute_type="int8",
                cpu_threads=os.cpu_count() or 4,
            )
            self.device_used = "cpu"
        else:
            try:
                self.model = WhisperModel(model_size, device=device,
                                          compute_type="float16")
                self.device_used = getattr(self.model.model, "device", device)
            except Exception as e:
                print(f"[whisper] CUDA no disponible ({type(e).__name__}), usando CPU.")
                self.model = WhisperModel(
                    model_size, device="cpu", compute_type="int8",
                    cpu_threads=os.cpu_count() or 4,
                )
                self.device_used = "cpu"
        print(f"[whisper] Modelo '{model_size}' cargado en: {self.device_used}")

        self.min_samples = int(min_segment_sec * RATE)
        self.max_samples = int(max_segment_sec * RATE)
        self.partial_stride = int(partial_stride_sec * RATE)
        self.base_threshold = silence_threshold
        self.noise_floor = silence_threshold / 2
        self.silence_chunks_to_flush = silence_chunks_to_flush

    # ---------- transcripción con filtros de calidad ----------

    def _transcribe(self, audio: np.ndarray) -> tuple[str, str, float, float]:
        t0 = time.monotonic()
        segments, info = self.model.transcribe(
            audio,
            language=self.forced_language,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        parts = []
        for s in segments:
            # descarta segmentos que Whisper mismo considera no-voz o muy dudosos
            if getattr(s, "no_speech_prob", 0.0) > 0.6:
                continue
            if getattr(s, "avg_logprob", 0.0) < -1.2:
                continue
            t = s.text.strip()
            if t:
                parts.append(t)
        text = clean_text(" ".join(parts))
        return (text, info.language, info.language_probability,
                time.monotonic() - t0)

    # ---------- umbral dinámico ----------

    def _dynamic_threshold(self, rms: float) -> float:
        thr = min(max(self.noise_floor * 3.0, self.base_threshold * 0.6), 0.05)
        if rms < thr:  # actualizar piso de ruido con chunks silenciosos
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms
        return thr

    # ---------- bucle principal ----------

    def _loop(self):
        buffer: list[np.ndarray] = []
        buffered = 0
        silent_streak = 0
        last_partial_at = 0
        last_partial_text = ""
        utterance_id = 0
        effective_stride = self.partial_stride

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

            flush_final = (
                (silent_streak >= self.silence_chunks_to_flush
                 and buffered >= self.min_samples)
                or buffered >= self.max_samples
            )

            if flush_final:
                audio = np.concatenate(buffer)
                buffer, buffered, silent_streak = [], 0, 0
                last_partial_at = 0
                text, lang, prob, _ = self._transcribe(audio)
                # si no hay texto pero hubo parcial, cerrar con el parcial
                if not text and last_partial_text:
                    text = last_partial_text
                if text:
                    self.text_queue.put(TranscriptSegment(
                        utterance_id, text, lang, prob, is_final=True))
                    utterance_id += 1
                last_partial_text = ""
                continue

            if (buffered - last_partial_at >= effective_stride
                    and buffered >= self.min_samples
                    and not is_silent):
                last_partial_at = buffered
                text, lang, prob, elapsed = self._transcribe(
                    np.concatenate(buffer))
                effective_stride = max(
                    self.partial_stride, int(elapsed * 1.3 * RATE))
                # no emitir si el parcial no cambió (menos parpadeo y
                # menos llamadas de traducción)
                if text and text != last_partial_text:
                    last_partial_text = text
                    self.text_queue.put(TranscriptSegment(
                        utterance_id, text, lang, prob, is_final=False))

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

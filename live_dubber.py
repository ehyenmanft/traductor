"""
Doblaje de voz en vivo: lee en voz alta la traducción de cada frase final.

Piezas (intercambiables, para poder probar sin audio ni red):
  - Synth  : texto → audio (EdgeSynth usa edge-tts; necesita internet)
  - Player : audio → dispositivo de salida (PyAudioPlayer, Windows)
  - LiveDubber: cola corta + anti-realimentación + aceleración si hay atraso

ANTI-REALIMENTACIÓN: la app captura el audio del sistema (loopback del
dispositivo de salida por defecto). Si la voz sale por ese mismo dispositivo, la
captura la volvería a transcribir en bucle. Soluciones:
  • enviar la voz a OTRO dispositivo de salida (p. ej. unos audífonos), o
  • (por defecto) silenciar la captura mientras habla la voz.
"""
from __future__ import annotations

import asyncio
import io
import threading
import time
from collections import deque

import numpy as np

VOICES = {   # (mujer, hombre)
    "es": ("es-ES-ElviraNeural", "es-ES-AlvaroNeural"),
    "en": ("en-US-JennyNeural", "en-US-GuyNeural"),
    "pt": ("pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"),
    "fr": ("fr-FR-DeniseNeural", "fr-FR-HenriNeural"),
    "de": ("de-DE-KatjaNeural", "de-DE-ConradNeural"),
    "it": ("it-IT-ElsaNeural", "it-IT-DiegoNeural"),
    "ja": ("ja-JP-NanamiNeural", "ja-JP-KeitaNeural"),
    "ko": ("ko-KR-SunHiNeural", "ko-KR-InJoonNeural"),
    "zh-cn": ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"),
    "ru": ("ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"),
}
TAIL_SECONDS = 0.35        # la captura sigue silenciada un instante tras terminar la voz
MAX_RATE = 40              # % máximo de aceleración por atraso


def norm_lang(code: str) -> str:
    code = (code or "auto").lower().strip()
    return "zh-cn" if code.startswith("zh") else (code.split("-")[0] if code != "auto" else code)


# ------------------------------------------------------------------ audio

def decode_audio(data: bytes, rate: int = 24000) -> tuple[np.ndarray, int]:
    """mp3/wav/… en memoria → PCM int16 mono (usa PyAV, que ya instala faster-whisper)."""
    import av
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(container.streams.audio[0]):
            for f in resampler.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1))
        try:
            for f in resampler.resample(None):
                chunks.append(f.to_ndarray().reshape(-1))
        except Exception:  # noqa: BLE001 — versiones sin flush
            pass
    if not chunks:
        raise RuntimeError("audio vacío")
    return np.concatenate(chunks).astype(np.int16), rate


def prepare_pcm(pcm: np.ndarray, sr_in: int, sr_dev: int, channels: int,
                volume: float) -> bytes:
    """int16 mono → bytes int16 al formato del dispositivo (frecuencia, canales, volumen)."""
    from audio_capture import Resampler
    x = pcm.astype(np.float32) / 32768.0
    if sr_in != sr_dev:
        x = Resampler(sr_in, sr_dev).process(x)
    x = np.clip(x * float(volume), -1.0, 1.0)
    if channels > 1:
        x = np.repeat(x[:, None], channels, axis=1)
    return (x * 32767).astype(np.int16).tobytes()


class EdgeSynth:
    """Voces neuronales de Microsoft (edge-tts). Necesita internet."""

    def synthesize(self, text: str, voice: str, rate_pct: int = 0) -> tuple[np.ndarray, int]:
        try:
            import edge_tts
        except ImportError as e:
            raise RuntimeError("falta edge-tts (pip install edge-tts)") from e

        async def go() -> bytes:
            buf = bytearray()
            async for chunk in edge_tts.Communicate(text, voice, rate=f"{rate_pct:+d}%").stream():
                if chunk["type"] == "audio":
                    buf += chunk["data"]
            return bytes(buf)

        return decode_audio(asyncio.run(go()))


class PyAudioPlayer:
    """Reproduce en un dispositivo de salida de Windows (WASAPI) con PyAudio."""

    def _pa(self):
        import pyaudiowpatch as pyaudio
        return pyaudio, pyaudio.PyAudio()

    def list_outputs(self) -> list[str]:
        try:
            pyaudio, p = self._pa()
        except Exception:  # noqa: BLE001
            return []
        try:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
            names = []
            for i in range(p.get_device_count()):
                d = p.get_device_info_by_index(i)
                if d["hostApi"] == wasapi and d["maxOutputChannels"] > 0 \
                        and not d.get("isLoopbackDevice", False):
                    names.append(d["name"])
            return list(dict.fromkeys(names))
        finally:
            p.terminate()

    def play(self, pcm: np.ndarray, sr: int, device: str, volume: float,
             cancel: threading.Event):
        pyaudio, p = self._pa()
        try:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            info = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if device and device.lower() != "default":
                for i in range(p.get_device_count()):
                    d = p.get_device_info_by_index(i)
                    if (d["hostApi"] == wasapi["index"] and d["maxOutputChannels"] > 0
                            and device.lower() in d["name"].lower()
                            and not d.get("isLoopbackDevice", False)):
                        info = d
                        break
            ch = max(1, min(2, int(info["maxOutputChannels"])))
            sr_dev = int(info["defaultSampleRate"])
            data = prepare_pcm(pcm, sr, sr_dev, ch, volume)
            stream = p.open(format=pyaudio.paInt16, channels=ch, rate=sr_dev,
                            output=True, output_device_index=info["index"])
            try:
                step = int(sr_dev * 0.05) * ch * 2          # 50 ms: permite cancelar
                for i in range(0, len(data), step):
                    if cancel.is_set():
                        break
                    stream.write(data[i:i + step])
            finally:
                stream.stop_stream()
                stream.close()
        finally:
            p.terminate()


# ------------------------------------------------------------------ doblador

class LiveDubber:
    def __init__(self, target: str = "es", synth=None, player=None, gender: str = "female",
                 device: str = "", volume: float = 1.0, max_backlog: int = 2,
                 enabled: bool = False, on_gate=None, capture_device=None, on_notice=None,
                 max_failures: int = 3, on_state=None):
        self.target = norm_lang(target)
        self.synth = synth or EdgeSynth()
        self.player = player or PyAudioPlayer()
        self.gender = gender if gender in ("female", "male") else "female"
        self.device = device or ""
        self.volume = max(0.0, min(2.0, float(volume)))
        self.max_backlog = max(1, int(max_backlog))
        self.enabled = bool(enabled)
        self.on_gate = on_gate or (lambda muted: None)
        self.capture_device = capture_device or (lambda: "")
        self.on_notice = on_notice or (lambda msg: None)
        self.max_failures = max_failures
        self.on_state = on_state or (lambda on: None)    # avisa a la interfaz de cada cambio
        self.gate_override: bool | None = None           # None = decidir por el dispositivo
        self._failures = 0
        self._q: deque[tuple[int, str]] = deque()
        self._cond = threading.Condition()
        self._cancel = threading.Event()
        self._stop = threading.Event()
        self.dropped = 0
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- ajustes ----------

    def set_enabled(self, on: bool):
        changed = self.enabled != bool(on)
        self.enabled = bool(on)
        if changed:
            self.on_state(self.enabled)
        if not on:
            self.clear()
        else:
            self._failures = 0

    def toggle(self) -> bool:
        self.set_enabled(not self.enabled)
        return self.enabled

    def set_target(self, lang: str):
        lang = norm_lang(lang)
        if lang != self.target:
            self.target = lang
            self.clear()

    def set_gender(self, gender: str):
        if gender in ("female", "male"):
            self.gender = gender

    def set_device(self, device: str):
        self.device = device or ""

    def set_volume(self, v: float):
        self.volume = max(0.0, min(2.0, float(v)))

    def clear(self):
        with self._cond:
            self._q.clear()
        self._cancel.set()          # corta lo que esté sonando

    def stop(self):
        self._stop.set()
        self.clear()
        with self._cond:
            self._cond.notify_all()

    @property
    def needs_gate(self) -> bool:
        """¿La voz saldría por el dispositivo que se captura? Entonces hay que silenciar la captura."""
        if self.gate_override is not None:       # el cliente remoto sabe por dónde suena su voz
            return self.gate_override
        dev = self.device.strip().lower()
        if not dev or dev == "default":
            return True
        cap = (self.capture_device() or "").lower()
        return not cap or dev in cap or cap in dev

    # ---------- entrada ----------

    def submit(self, uid: int, text: str):
        text = (text or "").strip()
        if not self.enabled or not text or self.target not in VOICES:
            return
        with self._cond:
            self._q.append((uid, text))
            while len(self._q) > self.max_backlog:      # mejor perder lo viejo que ir atrasado
                self._q.popleft()
                self.dropped += 1
            self._cond.notify()

    # ---------- hilo ----------

    def _loop(self):
        while not self._stop.is_set():
            with self._cond:
                if not self._q:
                    self._cond.wait(timeout=0.5)
                    continue
                uid, text = self._q.popleft()
                backlog = len(self._q)
            if not self.enabled:
                continue
            self._cancel.clear()
            voice = VOICES[self.target][0 if self.gender == "female" else 1]
            rate = min(MAX_RATE, 15 * backlog + (5 if backlog else 0))
            try:
                pcm, sr = self.synth.synthesize(text, voice, rate)
                self._failures = 0
            except Exception as e:  # noqa: BLE001
                self._failure(f"voz: {type(e).__name__}")
                continue
            if not self.enabled or self._cancel.is_set():
                continue
            gate = self.needs_gate
            try:
                if gate:
                    self.on_gate(True)
                self.player.play(pcm, sr, self.device, self.volume, self._cancel)
            except Exception as e:  # noqa: BLE001
                self._failure(f"reproducción: {type(e).__name__}")
            finally:
                if gate:
                    time.sleep(TAIL_SECONDS)
                    self.on_gate(False)

    def _failure(self, what: str):
        self._failures += 1
        print(f"[dub] Error de {what} ({self._failures}/{self.max_failures})")
        if self._failures >= self.max_failures:
            self.set_enabled(False)
            self.on_notice("🔇 Doblaje desactivado: falló varias veces (¿sin internet?)")

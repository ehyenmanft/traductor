"""
Captura de audio del sistema (WASAPI loopback en Windows) con
detección de cambio de dispositivo: si cambias la salida por defecto
(altavoces → audífonos) o el dispositivo se desconecta, la captura
se reabre sola sobre la nueva salida.
"""
import queue
import threading
import time

import numpy as np
import pyaudiowpatch as pyaudio

TARGET_RATE = 16000  # Whisper/Deepgram trabajan a 16 kHz


class SystemAudioCapture:
    def __init__(self, chunk_seconds: float = 0.25,
                 device_check_interval: float = 3.0):
        self.chunk_seconds = chunk_seconds
        self.device_check_interval = device_check_interval
        self.audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _find_loopback_device(self, p: pyaudio.PyAudio) -> dict:
        """Encuentra el dispositivo loopback de la salida por defecto ACTUAL."""
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = p.get_device_info_by_index(
            wasapi_info["defaultOutputDevice"]
        )
        if not default_speakers.get("isLoopbackDevice", False):
            for loopback in p.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    return loopback
            raise RuntimeError(
                "No se encontró dispositivo loopback. "
                "Verifica que estés en Windows con WASAPI."
            )
        return default_speakers

    def _default_changed(self, current_name: str) -> bool:
        """Consulta con una instancia fresca cuál es la salida por defecto
        ahora (la lista de dispositivos de PyAudio es una foto del init)."""
        try:
            p = pyaudio.PyAudio()
            try:
                return self._find_loopback_device(p)["name"] != current_name
            finally:
                p.terminate()
        except Exception:
            return False  # ante la duda, no reconectar

    def _process(self, data: bytes, channels: int, native_rate: int):
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        if native_rate != TARGET_RATE:
            n_out = int(len(samples) * TARGET_RATE / native_rate)
            samples = np.interp(
                np.linspace(0, len(samples), n_out, endpoint=False),
                np.arange(len(samples)),
                samples,
            ).astype(np.float32)
        self.audio_queue.put(samples)

    def _capture_loop(self):
        while not self._stop.is_set():
            p = pyaudio.PyAudio()
            stream = None
            try:
                device = self._find_loopback_device(p)
                native_rate = int(device["defaultSampleRate"])
                channels = int(device["maxInputChannels"])
                frames_per_chunk = int(native_rate * self.chunk_seconds)

                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=native_rate,
                    frames_per_buffer=frames_per_chunk,
                    input=True,
                    input_device_index=device["index"],
                )
                current_name = device["name"]
                print(f"[audio] Capturando: {current_name} @ {native_rate} Hz")

                last_check = time.monotonic()
                while not self._stop.is_set():
                    # ¿cambió la salida por defecto? (entre lecturas)
                    now = time.monotonic()
                    if now - last_check >= self.device_check_interval:
                        last_check = now
                        if self._default_changed(current_name):
                            print("[audio] La salida por defecto cambió; "
                                  "reconectando a la nueva...")
                            break
                    data = stream.read(frames_per_chunk,
                                       exception_on_overflow=False)
                    self._process(data, channels, native_rate)

            except Exception as e:
                if self._stop.is_set():
                    return
                print(f"[audio] Captura interrumpida ({type(e).__name__}); "
                      f"reintentando en 1 s...")
                time.sleep(1.0)
            finally:
                try:
                    if stream is not None:
                        stream.stop_stream()
                        stream.close()
                except Exception:
                    pass
                p.terminate()

    def start(self):
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

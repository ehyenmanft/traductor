"""
Motor de transcripción remoto: Deepgram streaming (nova-3 multilingüe).
Websocket bidireccional: enviamos el audio continuamente y Deepgram
devuelve parciales en ~100-300 ms y el final al detectar la pausa
(endpointing del lado del servidor — mejor que nuestro VAD local).

Recupera el modo "subtítulos en vivo" con precisión de modelo grande
y CPU casi libre.
"""
import json
import queue
import threading
import time

import numpy as np

from transcriber import TranscriptSegment, clean_text

RATE = 16000
DG_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-3&encoding=linear16&sample_rate=16000&channels=1"
    "&interim_results=true&smart_format=true&endpointing=300"
)


class DeepgramTranscriber:
    """Misma interfaz que los otros motores: text_queue / start / stop."""

    def __init__(
        self,
        audio_queue: "queue.Queue[np.ndarray]",
        api_key: str,
        language: str | None = None,   # None = multilingüe con code-switching
    ):
        if not api_key:
            raise ValueError("Falta DEEPGRAM_API_KEY")
        self.audio_queue = audio_queue
        self.api_key = api_key
        self.language = language or "multi"
        self.text_queue: "queue.Queue[TranscriptSegment]" = queue.Queue()
        self._stop = threading.Event()

        # estado de la utterance en curso
        self._uid = 0
        self._final_parts: list[str] = []
        self._utt_langs: list[str] = []
        self._last_partial = ""
        print("[deepgram] Motor remoto listo: nova-3 "
              f"({'multilingüe' if self.language == 'multi' else self.language})")

    # ---------- manejo de mensajes (puro: testeable sin red) ----------

    def _majority_lang(self) -> str:
        if not self._utt_langs:
            return "auto"
        return max(set(self._utt_langs), key=self._utt_langs.count)

    def handle_message(self, msg: dict):
        if msg.get("type") != "Results":
            return
        try:
            alt = msg["channel"]["alternatives"][0]
        except (KeyError, IndexError):
            return
        text = (alt.get("transcript") or "").strip()
        langs = [w.get("language") for w in alt.get("words", [])
                 if w.get("language")]

        if msg.get("is_final"):
            if text:
                self._final_parts.append(text)
                self._utt_langs.extend(langs)
            current = " ".join(self._final_parts).strip()
            if msg.get("speech_final"):
                final_text = clean_text(current)
                if final_text:
                    self.text_queue.put(TranscriptSegment(
                        self._uid, final_text, self._majority_lang(),
                        1.0, is_final=True))
                    self._uid += 1
                self._final_parts, self._utt_langs = [], []
                self._last_partial = ""
                return
        else:
            current = " ".join(
                self._final_parts + ([text] if text else [])).strip()

        if current and current != self._last_partial:
            self._last_partial = current
            self.text_queue.put(TranscriptSegment(
                self._uid, current,
                self._majority_lang() if self._utt_langs or langs else "auto",
                1.0, is_final=False))
            if langs:
                # recordar idiomas vistos también en interinos
                self._utt_langs.extend(langs)

    # ---------- red ----------

    def _url(self) -> str:
        return DG_URL + f"&language={self.language}"

    def _run(self):
        import websocket  # websocket-client
        backoff = 1.0
        while not self._stop.is_set():
            try:
                ws = websocket.create_connection(
                    self._url(),
                    header=[f"Authorization: Token {self.api_key}"],
                    timeout=10,
                )
                ws.settimeout(1.0)
                print("[deepgram] Conectado.")
                backoff = 1.0

                recv_error = threading.Event()

                def receiver():
                    while not self._stop.is_set() and not recv_error.is_set():
                        try:
                            raw = ws.recv()
                        except Exception as e:
                            if not self._stop.is_set():
                                if "timed out" in str(e).lower():
                                    continue
                                recv_error.set()
                            return
                        if isinstance(raw, (str, bytes)) and raw:
                            try:
                                self.handle_message(json.loads(raw))
                            except (json.JSONDecodeError, TypeError):
                                pass

                rt = threading.Thread(target=receiver, daemon=True)
                rt.start()

                while not self._stop.is_set() and not recv_error.is_set():
                    try:
                        chunk = self.audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    pcm = (np.clip(chunk, -1.0, 1.0) * 32767).astype(
                        np.int16).tobytes()
                    ws.send_binary(pcm)

                try:
                    ws.send(json.dumps({"type": "CloseStream"}))
                    ws.close()
                except Exception:
                    pass

            except Exception as e:
                if self._stop.is_set():
                    return
                print(f"[deepgram] Conexión caída ({type(e).__name__}). "
                      f"Reintentando en {backoff:.0f} s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 15)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

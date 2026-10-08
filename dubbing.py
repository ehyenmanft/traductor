"""
Doblaje con voz IA (edge-tts, voces neuronales gratuitas de Microsoft).

Para cada subtítulo traducido se sintetiza la voz, se acelera si no cabe en
su hueco (hasta 1.8x) y se coloca en su momento exacto. El resultado es una
pista de audio que se mezcla con el audio original (que puede bajarse o
silenciarse). Si la síntesis falla, el video sale igual, sin doblaje.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

from subtitle_style import Segment

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
MAX_SEGMENTS = 150
MAX_SPEEDUP = 1.8


def edge_synth(text: str, voice: str, out_path: str):
    """Sintetiza `text` a un mp3 con edge-tts."""
    try:
        import edge_tts
    except ImportError as e:
        raise RuntimeError("falta el paquete edge-tts (pip install edge-tts)") from e

    async def go():
        await edge_tts.Communicate(text, voice).save(out_path)

    asyncio.run(go())


def _duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def make_dub_track(segs: list[Segment], target: str, gender: str, duration: float,
                   workdir: str, synth=edge_synth, progress=None) -> str:
    """Devuelve la ruta de un .wav del largo del video con la voz doblada."""
    if target not in VOICES:
        raise RuntimeError("no hay voz disponible para ese idioma")
    voice = VOICES[target][0 if gender == "female" else 1]
    items = sorted((s for s in segs if (s.translation or "").strip()), key=lambda s: s.start)
    if not items:
        raise RuntimeError("no hay texto para doblar")
    if len(items) > MAX_SEGMENTS:
        raise RuntimeError(f"demasiados subtítulos para doblar ({len(items)} > {MAX_SEGMENTS})")

    clips = []
    for i, s in enumerate(items):
        path = os.path.join(workdir, f"tts_{i}.mp3")
        synth(s.translation, voice, path)
        nxt = items[i + 1].start if i + 1 < len(items) else (duration or s.end + 5)
        slot = max(0.6, min(nxt - s.start, max(s.end - s.start, 0.6) + 1.5))
        d = _duration(path)
        speed = min(MAX_SPEEDUP, d / slot) if d > slot else 1.0
        clips.append((path, s.start, speed))
        if progress:
            progress(0.9 * (i + 1) / len(items))

    cmd = ["ffmpeg", "-y"]
    for path, _, _ in clips:
        cmd += ["-i", path]
    chains, labels = [], []
    for i, (_, start, speed) in enumerate(clips):
        f = "aformat=channel_layouts=mono"
        if speed > 1.01:
            f += f",atempo={speed:.3f}"
        chains.append(f"[{i}:a]{f},adelay={int(start * 1000)}:all=1[a{i}]")
        labels.append(f"[a{i}]")
    chains.append("".join(labels) + f"amix=inputs={len(clips)}:normalize=0:dropout_transition=0[out]")
    out = os.path.join(workdir, "dub.wav")
    cmd += ["-filter_complex", ";".join(chains), "-map", "[out]", "-ar", "44100"]
    if duration:
        cmd += ["-t", f"{duration:.2f}"]
    cmd.append(out)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg (doblaje) falló: " + res.stderr[-300:])
    if progress:
        progress(1.0)
    return out

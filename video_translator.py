"""
Pipeline de traducción de videos (sin GUI, sin Telegram):

    video ──ffmpeg──► audio ──Deepgram nova-3 (multi, detecta idioma)──► segmentos
          ──translate_engine (Groq → Google → MyMemory)──► segmentos traducidos
          ──subtitle_ass──► .ass ──ffmpeg──► video original + subtítulos
          (opcional) ──dubbing──► pista de voz IA mezclada con el audio

El video y el audio originales se conservan íntegros: sin doblaje el audio se
copia sin recodificar y los subtítulos solo se superponen.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field

import requests

import dubbing
from subtitle_ass import (build_ass, build_srt, fix_timing, norm_lang,  # noqa: F401
                          parse_srt_texts)
from subtitle_style import *  # noqa: F401,F403 — se re-exportan los catálogos
from subtitle_style import (LANGUAGES, ORIG_VOLS, Segment, SubtitleStyle)
from translate_engine import translate_segments  # noqa: F401

DG_REST_URL = "https://api.deepgram.com/v1/listen"
MAX_CHARS = 80          # máximo de caracteres originales por subtítulo
MAX_SECONDS = 6.0       # duración máxima por subtítulo


class SameLanguageError(Exception):
    """Todo el habla ya está en el idioma destino: no hay nada que traducir."""

    def __init__(self, langs: list[str]):
        super().__init__("same language")
        self.langs = langs


# --------------------------------------------------------------------------
# ffmpeg helpers
# --------------------------------------------------------------------------

def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} falló: {res.stderr[-800:]}")
    return res


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def probe(video: str) -> dict:
    """Devuelve width, height, duration (s) y si tiene audio."""
    out = _run(["ffprobe", "-v", "error", "-print_format", "json",
                "-show_streams", "-show_format", video]).stdout
    info = json.loads(out)
    vs = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not vs:
        raise RuntimeError("El archivo no contiene video.")
    w, h = int(vs["width"]), int(vs["height"])
    rot = 0     # con rotación de 90/270 ffmpeg intercambia ancho y alto
    for sd in vs.get("side_data_list", []):
        if "rotation" in sd:
            rot = abs(int(sd["rotation"]))
    rot = rot or abs(int(vs.get("tags", {}).get("rotate", 0)))
    if rot in (90, 270):
        w, h = h, w
    dur = _num(info["format"].get("duration")) or _num(vs.get("duration"))
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    return dict(width=w, height=h, duration=dur, has_audio=has_audio)


def extract_audio(video: str, out_path: str) -> str:
    """Audio mono 16 kHz en FLAC (liviano y sin pérdida para el ASR)."""
    _run(["ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
          "-c:a", "flac", out_path])
    return out_path


def extract_frame(video: str, out_path: str, duration: float,
                  width: int = 720) -> str:
    """Un fotograma del video (reducido) para las vistas previas del menú."""
    t = max(0.0, min(duration * 0.3, duration - 0.1)) if duration else 0.0
    _run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
          "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", out_path])
    return out_path


# --------------------------------------------------------------------------
# Deepgram (archivo)
# --------------------------------------------------------------------------

def transcribe_file(audio_path: str, api_key: str,
                    timeout: float = 600) -> list[Segment]:
    """Transcribe con nova-3 multilingüe; cada palabra trae su idioma."""
    params = {"model": "nova-3", "language": "multi", "smart_format": "true",
              "punctuate": "true"}
    with open(audio_path, "rb") as f:
        resp = requests.post(
            DG_REST_URL, params=params, data=f, timeout=timeout,
            headers={"Authorization": f"Token {api_key}",
                     "Content-Type": "audio/flac"})
    if resp.status_code != 200:
        raise RuntimeError(f"Deepgram {resp.status_code}: {resp.text[:300]}")
    return segments_from_deepgram(resp.json())


def _split_words(words: list[dict]) -> list[list[dict]]:
    """Agrupa palabras en subtítulos cortos: corta en puntuación, longitud,
    duración, pausas largas o cambio de idioma."""
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            text_len = sum(len(x["word"]) + 1 for x in cur)
            gap = w["start"] - cur[-1]["end"]
            dur = w["end"] - cur[0]["start"]
            lang_change = bool(w["language"] and cur[-1]["language"]
                               and w["language"] != cur[-1]["language"])
            if lang_change or gap > 1.2 or text_len + len(w["word"]) > MAX_CHARS or dur > MAX_SECONDS:
                groups.append(cur)
                cur = []
        cur.append(w)
        if re.search(r"[.!?…。！？]$", w["word"]) and \
                sum(len(x["word"]) + 1 for x in cur) > 25:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def segments_from_deepgram(data: dict) -> list[Segment]:
    """Función pura (testeable sin red): JSON de Deepgram → segmentos."""
    try:
        words = data["results"]["channels"][0]["alternatives"][0]["words"]
    except (KeyError, IndexError):
        return []
    norm = []
    for w in words:
        text = w.get("punctuated_word") or w.get("word") or ""
        if text.strip():
            norm.append(dict(word=text.strip(), start=float(w["start"]),
                             end=float(w["end"]), language=w.get("language")))
    segs = []
    for g in _split_words(norm):
        langs = [w["language"] for w in g if w["language"]]
        lang = max(set(langs), key=langs.count) if langs else "auto"
        segs.append(Segment(g[0]["start"], g[-1]["end"],
                            " ".join(w["word"] for w in g), lang,
                            words=[(w["word"], w["start"], w["end"]) for w in g]))
    return segs


def detected_languages(segs: list[Segment]) -> list[str]:
    """Idiomas detectados, del más al menos frecuente (por texto hablado)."""
    count: dict[str, int] = {}
    for s in segs:
        if s.lang != "auto":
            count[norm_lang(s.lang)] = count.get(norm_lang(s.lang), 0) + len(s.text)
    return sorted(count, key=count.get, reverse=True)


# --------------------------------------------------------------------------
# Vista previa (imagen con el estilo elegido sobre un fotograma real)
# --------------------------------------------------------------------------

SAMPLES = {
    "es": "Así se verán tus subtítulos en 4K", "en": "This is how your subtitles look in 4K",
    "pt": "Assim ficarão suas legendas em 4K", "fr": "Voici vos sous-titres en 4K",
    "de": "So sehen Ihre Untertitel in 4K aus", "it": "Ecco i tuoi sottotitoli in 4K",
    "ja": "字幕は4Kでこのように表示されます", "ko": "자막이 4K로 이렇게 표시됩니다",
    "zh-cn": "字幕将以4K这样显示", "ru": "Так выглядят субтитры в 4K",
}
PREVIEW_SECONDS = 4.0
PREVIEW_AT = 1.6     # instante mostrado: las animaciones/palabra activa se ven a medias


def render_preview(frame: str, st: SubtitleStyle, out_path: str,
                   workdir: str) -> str:
    """Pinta subtítulos de ejemplo sobre un fotograma con el estilo actual."""
    info = _run(["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_streams", frame]).stdout
    vs = json.loads(info)["streams"][0]
    w, h = int(vs["width"]), int(vs["height"])
    if st.target == "orig":
        seg = Segment(0, PREVIEW_SECONDS, SAMPLES["es"], "es", SAMPLES["es"])
    else:
        orig_lang = "en" if st.target != "en" else "es"
        seg = Segment(0, PREVIEW_SECONDS, SAMPLES[orig_lang], orig_lang,
                      SAMPLES.get(st.target, SAMPLES["en"]))
    with open(os.path.join(workdir, "prev.ass"), "w", encoding="utf-8") as f:
        f.write(build_ass([seg], st, w, h, PREVIEW_SECONDS))
    _run(["ffmpeg", "-y", "-loop", "1", "-framerate", "10", "-t", str(PREVIEW_SECONDS),
          "-i", os.path.abspath(frame), "-vf", "subtitles=prev.ass",
          "-ss", str(PREVIEW_AT), "-frames:v", "1", "-q:v", "3",
          os.path.abspath(out_path)], cwd=workdir)
    return out_path


# --------------------------------------------------------------------------
# Render final
# --------------------------------------------------------------------------

def burn_subtitles(video: str, ass_text: str, out_path: str, workdir: str,
                   duration: float = 0.0, max_mb: float | None = None,
                   dub_track: str | None = None, orig_gain: float = 1.0) -> str:
    """Superpone el .ass sobre el video. Imagen recodificada (x264); el audio
    se copia tal cual, o se mezcla con la pista de doblaje si se indica. Si
    max_mb está definido y el resultado lo excede, se recodifica con bitrate
    calculado para entrar en el límite."""
    ass_name = "subs.ass"   # relativo + cwd evita escapes de rutas en el filtro
    with open(os.path.join(workdir, ass_name), "w", encoding="utf-8") as f:
        f.write(ass_text)
    vf = f"subtitles={ass_name}"
    src, dst = os.path.abspath(video), os.path.abspath(out_path)

    def render(vopts: list[str]):
        if dub_track:
            afilt = (f"[0:a]volume={orig_gain}[o];[o][1:a]amix=inputs=2:normalize=0:"
                     "duration=first[a]" if orig_gain > 0 else "[1:a]anull[a]")
            cmd = ["ffmpeg", "-y", "-i", src, "-i", os.path.abspath(dub_track),
                   "-filter_complex", f"[0:v]{vf}[v];{afilt}", "-map", "[v]",
                   "-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf, "-map", "0:v:0",
                   "-map", "0:a?", "-map", "0:s?", "-c:a", "copy", "-c:s",
                   "copy" if out_path.endswith(".mkv") else "mov_text"]
        _run(cmd + ["-c:v", "libx264", *vopts, "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", dst], cwd=workdir)

    try:
        render(["-preset", "veryfast", "-crf", "20"])
    except RuntimeError:
        # audio/subs incompatibles con -c copy: ignorar pistas de subtítulos
        # y recodificar audio a AAC como último recurso
        _run(["ffmpeg", "-y", "-i", src, "-vf", vf, "-map", "0:v:0", "-map", "0:a?",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
              "-movflags", "+faststart", dst], cwd=workdir)

    if max_mb and duration and os.path.getsize(out_path) > max_mb * 1024 * 1024:
        total_kbps = max_mb * 8 * 1024 * 0.92 / duration
        v_kbps = max(150, int(total_kbps - 128))
        render(["-preset", "veryfast", "-b:v", f"{v_kbps}k",
                "-maxrate", f"{int(v_kbps * 1.4)}k", "-bufsize", f"{v_kbps * 2}k"])
    return out_path


# --------------------------------------------------------------------------
# Orquestación (en dos fases para poder cambiar de estilo/idioma sin re-transcribir)
# --------------------------------------------------------------------------

@dataclass
class Transcription:
    segments: list[Segment]
    info: dict
    sig: tuple | None = None          # con qué ajustes se tradujo (caché)
    cur_sig: tuple | None = None
    warnings: list[str] = field(default_factory=list)
    dub_cache: tuple | None = None    # (clave, ruta del .wav)


@dataclass
class Result:
    video_path: str
    srt_text: str
    languages: list[str] = field(default_factory=list)
    n_segments: int = 0
    warnings: list[str] = field(default_factory=list)


def transcribe_video(video: str, workdir: str, deepgram_key: str,
                     progress=lambda msg: None) -> Transcription:
    info = probe(video)
    if not info["has_audio"]:
        raise RuntimeError("El video no tiene pista de audio para transcribir.")
    progress("🎧 Extrayendo audio…")
    audio = extract_audio(video, os.path.join(workdir, "audio.flac"))
    progress("🗣️ Transcribiendo y detectando idioma (Deepgram)…")
    segs = transcribe_file(audio, deepgram_key)
    if not segs:
        raise RuntimeError("No se detectó habla en el video.")
    return Transcription(segs, info)


def apply_edited_srt(tr: Transcription, srt_text: str):
    """Reemplaza las traducciones por las de un .srt editado (mismo nº de
    bloques; los tiempos originales se conservan)."""
    texts = parse_srt_texts(srt_text)
    ordered = sorted(tr.segments, key=lambda s: s.start)
    if len(texts) != len(ordered):
        raise ValueError(f"El archivo tiene {len(texts)} subtítulos y el video "
                         f"{len(ordered)}. No agregues ni borres bloques.")
    for s, t in zip(ordered, texts):
        s.translation = t or s.translation
    tr.sig = tr.cur_sig        # las ediciones sobreviven a un cambio de estilo


def render_video(video: str, tr: Transcription, st: SubtitleStyle, workdir: str,
                 groq_key: str | None = None, max_mb: float | None = None,
                 progress=lambda msg: None,
                 glossary: dict[str, str] | None = None) -> Result:
    langs = detected_languages(tr.segments)
    if st.target != "orig" and langs and all(l == st.target for l in langs):
        raise SameLanguageError(langs)

    glossary = dict(glossary or {})
    sig = (st.target, st.tone if groq_key else "natural", st.censor,
           tuple(sorted(glossary.items())), bool(groq_key))
    tr.cur_sig = sig
    warnings: list[str] = []
    if tr.sig != sig:
        progress(f"🌐 Traduciendo {len(tr.segments)} subtítulos…")
        errors = translate_segments(tr.segments, st.target, groq_key or None,
                                    st.tone, glossary, st.censor)
        failed = [s for s in tr.segments if not s.translation]
        tr.warnings = []
        if len(failed) == len(tr.segments):
            raise RuntimeError(
                "No se pudo traducir con ningún motor (Groq/Google/MyMemory). "
                "Último error: " + (errors[-1] if errors else "desconocido") +
                ". Revisa la groq_api_key en config.json.")
        if failed:
            tr.warnings.append(f"{len(failed)} de {len(tr.segments)} subtítulos no se "
                               "pudieron traducir y quedaron en el idioma original.")
        if (st.tone != "natural" or glossary) and not groq_key:
            tr.warnings.append("El tono y el glosario completo requieren una groq_api_key.")
        tr.sig = None if failed else sig
    warnings += tr.warnings

    dub_track = None
    if st.dub != "off":
        key = (sig, st.dub)
        if st.target == "orig":
            warnings.append("El doblaje necesita un idioma destino (no 'solo transcribir').")
        elif tr.dub_cache and tr.dub_cache[0] == key and os.path.exists(tr.dub_cache[1]):
            dub_track = tr.dub_cache[1]
        else:
            progress("🎙️ Generando doblaje con voz IA…")
            try:
                dub_track = dubbing.make_dub_track(
                    tr.segments, st.target, st.dub, tr.info["duration"], workdir)
                tr.dub_cache = (key, dub_track)
            except Exception as e:  # noqa: BLE001 — el video sale sin doblaje
                warnings.append(f"No se pudo generar el doblaje: {str(e)[:150]}")

    progress("🎬 Incrustando subtítulos en el video…")
    ass = build_ass(tr.segments, st, tr.info["width"], tr.info["height"],
                    tr.info["duration"])
    out = burn_subtitles(video, ass, os.path.join(workdir, "traducido.mp4"),
                         workdir, tr.info["duration"], max_mb, dub_track,
                         ORIG_VOLS.get(st.orig_vol, ORIG_VOLS["low"])[1])
    return Result(out, build_srt(tr.segments), langs, len(tr.segments), warnings)

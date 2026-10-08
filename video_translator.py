"""
Pipeline de traducción de videos (sin GUI, sin Telegram):

    video ──ffmpeg──► audio ──Deepgram nova-3 (multi, detecta idioma)──► segmentos
          ──Translator (Groq / Google)──► segmentos traducidos
          ──► subtítulos .ass ──ffmpeg──► video original + subtítulos incrustados

El video y el audio originales se conservan íntegros: el audio se copia sin
recodificar y los subtítulos solo se superponen.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

from translator import Translator

DG_REST_URL = "https://api.deepgram.com/v1/listen"
MAX_CHARS = 80          # máximo de caracteres originales por subtítulo
MAX_SECONDS = 6.0       # duración máxima por subtítulo
MIN_SECONDS = 0.8       # duración mínima visible

LANGUAGES = {
    "es": "Español", "en": "English", "pt": "Português", "fr": "Français",
    "de": "Deutsch", "it": "Italiano", "ja": "日本語", "ko": "한국어",
    "zh-cn": "中文", "ru": "Русский",
}


@dataclass
class Segment:
    start: float
    end: float
    text: str
    lang: str = "auto"
    translation: str = ""


@dataclass
class SubtitleStyle:
    """Opciones de estilo que el usuario elige desde Telegram."""
    preset: str = "classic"      # ver PRESETS
    position: str = "bottom"     # bottom | middle | top
    size: str = "m"              # s | m | l  (relativo al alto del video)
    bilingual: bool = False      # mostrar también el texto original (más chico)
    target: str = "es"


# Colores ASS: &HAABBGGRR (alpha 00 = opaco)
PRESETS = {
    "classic": dict(label="Clásico", font="Arial", primary="&H00FFFFFF",
                    outline="&H00000000", back="&H80000000", bold=1,
                    border=1, outline_w=0.07, shadow=0.03),
    "yellow": dict(label="Cine amarillo", font="Arial", primary="&H0000E0FF",
                   outline="&H00000000", back="&H80000000", bold=1,
                   border=1, outline_w=0.08, shadow=0.03),
    "box": dict(label="Caja oscura", font="Arial", primary="&H00FFFFFF",
                outline="&HB0000000", back="&HB0000000", bold=0,
                border=3, outline_w=0.18, shadow=0),
    "gamer": dict(label="Gamer neón", font="Impact", primary="&H00FFFF00",
                  outline="&H00800080", back="&H80000000", bold=0,
                  border=1, outline_w=0.10, shadow=0.05),
    "minimal": dict(label="Minimal", font="Arial", primary="&H00FFFFFF",
                    outline="&H40000000", back="&H00000000", bold=0,
                    border=1, outline_w=0.04, shadow=0),
}
SIZES = {"s": 0.040, "m": 0.055, "l": 0.075}            # fracción del alto
ALIGN = {"bottom": 2, "middle": 5, "top": 8}            # numpad ASS


# --------------------------------------------------------------------------
# ffmpeg helpers
# --------------------------------------------------------------------------

def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} falló: {res.stderr[-800:]}")
    return res


def probe(video: str) -> dict:
    """Devuelve width, height, duration (s) y si tiene audio."""
    out = _run(["ffprobe", "-v", "error", "-print_format", "json",
                "-show_streams", "-show_format", video]).stdout
    info = json.loads(out)
    vs = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not vs:
        raise RuntimeError("El archivo no contiene video.")
    w, h = int(vs["width"]), int(vs["height"])
    # con rotación de 90/270 ffmpeg intercambia ancho y alto al decodificar
    rot = 0
    for sd in vs.get("side_data_list", []):
        if "rotation" in sd:
            rot = abs(int(sd["rotation"]))
    rot = rot or abs(int(vs.get("tags", {}).get("rotate", 0)))
    if rot in (90, 270):
        w, h = h, w
    dur = float(info["format"].get("duration") or vs.get("duration") or 0)
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    return dict(width=w, height=h, duration=dur, has_audio=has_audio)


def extract_audio(video: str, out_path: str) -> str:
    """Audio mono 16 kHz en FLAC (liviano y sin pérdida para el ASR)."""
    _run(["ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
          "-c:a", "flac", out_path])
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
                            " ".join(w["word"] for w in g), lang))
    return segs


# --------------------------------------------------------------------------
# Traducción
# --------------------------------------------------------------------------

def _norm_lang(code: str) -> str:
    code = (code or "auto").lower()
    if code.startswith("zh"):
        return "zh-cn"
    return code.split("-")[0] if code != "auto" else code


def translate_segments(segs: list[Segment], target: str,
                       groq_api_key: str | None = None) -> list[Segment]:
    tr = Translator(target_language=target, groq_api_key=groq_api_key)

    def one(s: Segment) -> str:
        return tr.translate(s.text, _norm_lang(s.lang))

    with ThreadPoolExecutor(max_workers=4) as pool:
        for s, t in zip(segs, pool.map(one, segs)):
            s.translation = t
    return segs


def detected_languages(segs: list[Segment]) -> list[str]:
    """Idiomas detectados, del más al menos frecuente (por texto hablado)."""
    count: dict[str, int] = {}
    for s in segs:
        if s.lang != "auto":
            count[s.lang] = count.get(s.lang, 0) + len(s.text)
    return sorted(count, key=count.get, reverse=True)


# --------------------------------------------------------------------------
# Subtítulos
# --------------------------------------------------------------------------

def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")") \
               .replace("\n", " ")


def _fix_timing(segs: list[Segment], duration: float) -> list[Segment]:
    """Duración mínima y sin solapamientos entre subtítulos."""
    out = sorted(segs, key=lambda s: s.start)
    for i, s in enumerate(out):
        s.end = max(s.end, s.start + MIN_SECONDS)
        if i + 1 < len(out):
            s.end = min(s.end, out[i + 1].start - 0.02)
        if duration:
            s.end = min(s.end, duration)
        s.end = max(s.end, s.start + 0.2)
    return out


def build_ass(segs: list[Segment], style: SubtitleStyle,
              width: int, height: int, duration: float = 0.0) -> str:
    p = PRESETS.get(style.preset, PRESETS["classic"])
    fs = max(14, round(height * SIZES.get(style.size, SIZES["m"])))
    outline = round(fs * p["outline_w"], 1)
    shadow = round(fs * p["shadow"], 1)
    margin_v = round(height * 0.05)
    margin_h = round(width * 0.05)
    align = ALIGN.get(style.position, 2)
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\n"
        f"PlayResX: {width}\nPlayResY: {height}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,"
        "ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,"
        "MarginR,MarginV,Encoding\n"
        f"Style: Default,{p['font']},{fs},{p['primary']},&H000000FF,"
        f"{p['outline']},{p['back']},{p['bold']},0,0,0,100,100,0,0,"
        f"{p['border']},{outline},{shadow},{align},{margin_h},{margin_h},"
        f"{margin_v},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,"
        "MarginV,Effect,Text\n"
    )
    lines = []
    for s in _fix_timing([Segment(x.start, x.end, x.text, x.lang, x.translation)
                          for x in segs], duration):
        txt = _esc(s.translation or s.text)
        same = _norm_lang(s.lang) == style.target or txt.strip() == s.text.strip()
        if style.bilingual and not same:
            small = max(10, round(fs * 0.62))
            txt += f"\\N{{\\fs{small}\\b0\\alpha&H30&}}{_esc(s.text)}"
        lines.append(f"Dialogue: 0,{_ts_ass(s.start)},{_ts_ass(s.end)},"
                     f"Default,,0,0,0,,{txt}")
    return head + "\n".join(lines) + "\n"


def build_srt(segs: list[Segment]) -> str:
    out = []
    for i, s in enumerate(_fix_timing(
            [Segment(x.start, x.end, x.text, x.lang, x.translation)
             for x in segs], 0), 1):
        out.append(f"{i}\n{_ts_srt(s.start)} --> {_ts_srt(s.end)}\n"
                   f"{s.translation or s.text}\n")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Render final
# --------------------------------------------------------------------------

def burn_subtitles(video: str, ass_text: str, out_path: str, workdir: str,
                   duration: float = 0.0, max_mb: float | None = None) -> str:
    """Superpone el .ass sobre el video. Imagen recodificada (x264),
    audio copiado tal cual. Si max_mb está definido y el resultado lo
    excede, se recodifica con bitrate calculado para entrar en el límite."""
    ass_name = "subs.ass"   # relativo + cwd evita escapes de rutas en el filtro
    with open(os.path.join(workdir, ass_name), "w", encoding="utf-8") as f:
        f.write(ass_text)
    vf = f"subtitles={ass_name}"

    def render(vopts: list[str]):
        _run(["ffmpeg", "-y", "-i", os.path.abspath(video), "-vf", vf,
              "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
              "-c:v", "libx264", *vopts, "-pix_fmt", "yuv420p",
              "-c:a", "copy", "-c:s", "copy" if out_path.endswith(".mkv") else "mov_text",
              "-movflags", "+faststart", os.path.abspath(out_path)],
             cwd=workdir)

    try:
        render(["-preset", "veryfast", "-crf", "20"])
    except RuntimeError:
        # contenedor/códec de audio o subs incompatible con -c copy: ignorar
        # pistas de subtítulos y recodificar audio a AAC como último recurso
        _run(["ffmpeg", "-y", "-i", os.path.abspath(video), "-vf", vf,
              "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264",
              "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
              os.path.abspath(out_path)], cwd=workdir)

    if max_mb and duration and os.path.getsize(out_path) > max_mb * 1024 * 1024:
        total_kbps = max_mb * 8 * 1024 * 0.92 / duration
        v_kbps = max(150, int(total_kbps - 128))
        render(["-preset", "veryfast", "-b:v", f"{v_kbps}k",
                "-maxrate", f"{int(v_kbps * 1.4)}k",
                "-bufsize", f"{v_kbps * 2}k"])
    return out_path


# --------------------------------------------------------------------------
# Orquestación
# --------------------------------------------------------------------------

@dataclass
class Result:
    video_path: str
    srt_text: str
    languages: list[str] = field(default_factory=list)
    n_segments: int = 0


def process_video(video: str, style: SubtitleStyle, workdir: str,
                  deepgram_key: str, groq_key: str | None = None,
                  max_mb: float | None = None,
                  progress=lambda msg: None) -> Result:
    info = probe(video)
    if not info["has_audio"]:
        raise RuntimeError("El video no tiene pista de audio para transcribir.")
    progress("🎧 Extrayendo audio…")
    audio = extract_audio(video, os.path.join(workdir, "audio.flac"))
    progress("🗣️ Transcribiendo y detectando idioma (Deepgram)…")
    segs = transcribe_file(audio, deepgram_key)
    if not segs:
        raise RuntimeError("No se detectó habla en el video.")
    progress(f"🌐 Traduciendo {len(segs)} subtítulos…")
    translate_segments(segs, style.target, groq_key)
    progress("🎬 Incrustando subtítulos en el video…")
    ass = build_ass(segs, style, info["width"], info["height"], info["duration"])
    out = burn_subtitles(video, ass, os.path.join(workdir, "traducido.mp4"),
                         workdir, info["duration"], max_mb)
    return Result(out, build_srt(segs), detected_languages(segs), len(segs))

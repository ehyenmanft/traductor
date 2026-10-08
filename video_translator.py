"""
Pipeline de traducción de videos (sin GUI, sin Telegram):

    video ──ffmpeg──► audio ──Deepgram nova-3 (multi, detecta idioma)──► segmentos
          ──Traducción (Groq → Google → MyMemory)──► segmentos traducidos
          ──► subtítulos .ass ──ffmpeg──► video original + subtítulos incrustados

El video y el audio originales se conservan íntegros: el audio se copia sin
recodificar y los subtítulos solo se superponen.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

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
_GOOGLE = {"zh-cn": "zh-CN"}
_MYMEMORY = {"es": "es-ES", "en": "en-US", "pt": "pt-PT", "fr": "fr-FR",
             "de": "de-DE", "it": "it-IT", "ja": "ja-JP", "ko": "ko-KR",
             "zh-cn": "zh-CN", "ru": "ru-RU"}


# --------------------------------------------------------------------------
# Estilo
# --------------------------------------------------------------------------

@dataclass
class SubtitleStyle:
    """Todas las opciones que el usuario puede elegir desde Telegram."""
    font: str = "sans"            # clave de FONTS
    color: str = "white"          # clave de COLORS
    outline: str = "med"          # none | thin | med | thick | xthick
    outline_color: str = "black"  # clave de COLORS (también es el color de la caja)
    bg: str = "none"              # none | soft | solid  (caja tras el texto)
    shadow: bool = True
    bold: bool = True
    italic: bool = False
    upper: bool = False
    align_h: str = "center"       # left | leftmid | center | rightmid | right
    align_v: str = "bottom"       # top | upper | middle | lower | bottom
    size: str = "m"               # xs | s | m | l | xl  (relativo al alto)
    bilingual: bool = False       # texto original, más chico, bajo la traducción
    target: str = "es"
    preset: str = "classic"       # solo informativo (último preset aplicado)


# Colores ASS: &HAABBGGRR
COLORS = {
    "white": ("Blanco", "&H00FFFFFF"), "yellow": ("Amarillo", "&H0000E0FF"),
    "cyan": ("Cian", "&H00FFFF00"), "green": ("Verde", "&H0000FF40"),
    "orange": ("Naranja", "&H000080FF"), "pink": ("Rosa", "&H00C080FF"),
    "red": ("Rojo", "&H000000FF"), "blue": ("Azul", "&H00FF3000"),
    "purple": ("Morado", "&H00A00080"), "black": ("Negro", "&H00000000"),
}
# (etiqueta, nombre de fuente instalada). Si falta, fontconfig usa otra similar.
FONTS = {
    "sans": ("Sans", "Liberation Sans"), "serif": ("Serif", "Liberation Serif"),
    "mono": ("Mono", "DejaVu Sans Mono"), "roboto": ("Roboto", "Roboto"),
    "impact": ("Impacto", "Anton"),
}
OUTLINES = {"none": ("Sin contorno", 0.0), "thin": ("Fino", 0.04),
            "med": ("Medio", 0.07), "thick": ("Grueso", 0.11),
            "xthick": ("Extra", 0.15)}
BGS = {"none": ("Sin fondo", None), "soft": ("Caja suave", "&H70"),
       "solid": ("Caja sólida", "&H00")}
SIZES = {"xs": ("XS", 0.032), "s": ("S", 0.042), "m": ("M", 0.055),
         "l": ("L", 0.072), "xl": ("XL", 0.095)}
VPOS = ("top", "upper", "middle", "lower", "bottom")        # 5 filas
HPOS = ("left", "leftmid", "center", "rightmid", "right")   # 5 columnas
ALIGN = {(v, h): None for v in VPOS for h in HPOS}          # posiciones válidas


def _layout(st: "SubtitleStyle", width: int, height: int) -> tuple[int, int, int, int]:
    """(alineación ASS, margen izq, margen der, margen vertical) para la
    posición elegida. Las intermedias se logran moviendo los márgenes."""
    mh, mv = round(width * 0.05), round(height * 0.05)
    row = {"top": 7, "upper": 7, "middle": 4, "lower": 1, "bottom": 1}[st.align_v]
    col = {"left": 0, "leftmid": 1, "center": 1, "rightmid": 1, "right": 2}[st.align_h]
    ml, mr = mh, mh
    if st.align_h == "leftmid":
        mr = round(width * 0.40)      # región 5–60 % → texto centrado a ~32 %
    elif st.align_h == "rightmid":
        ml = round(width * 0.40)      # región 40–95 % → texto centrado a ~68 %
    if st.align_v in ("upper", "lower"):
        mv = round(height * 0.25)     # a medio camino entre el borde y el centro
    return row + col, ml, mr, mv


# Estilos listos: cada uno es un paquete de campos de SubtitleStyle.
PRESETS = {
    "classic": dict(label="Clásico", font="sans", color="white", outline="med",
                    outline_color="black", bg="none", shadow=True, bold=True,
                    italic=False, upper=False),
    "yellow": dict(label="Cine amarillo", font="sans", color="yellow",
                   outline="med", outline_color="black", bg="none",
                   shadow=True, bold=True, italic=False, upper=False),
    "box": dict(label="Caja oscura", font="roboto", color="white",
                outline="med", outline_color="black", bg="soft", shadow=False,
                bold=False, italic=False, upper=False),
    "gamer": dict(label="Gamer neón", font="impact", color="cyan",
                  outline="thick", outline_color="purple", bg="none",
                  shadow=True, bold=False, italic=False, upper=True),
    "comic": dict(label="Cómic", font="impact", color="yellow",
                  outline="xthick", outline_color="black", bg="none",
                  shadow=True, bold=False, italic=False, upper=True),
    "elegant": dict(label="Elegante", font="serif", color="white",
                    outline="thin", outline_color="black", bg="none",
                    shadow=True, bold=False, italic=True, upper=False),
    "retro": dict(label="Retro terminal", font="mono", color="green",
                  outline="med", outline_color="black", bg="solid",
                  shadow=False, bold=True, italic=False, upper=False),
    "minimal": dict(label="Minimal", font="sans", color="white",
                    outline="thin", outline_color="black", bg="none",
                    shadow=False, bold=False, italic=False, upper=False),
}


def apply_preset(style: SubtitleStyle, name: str) -> SubtitleStyle:
    """Aplica un estilo listo conservando posición, tamaño e idioma."""
    p = {k: v for k, v in PRESETS[name].items() if k != "label"}
    return replace(style, preset=name, **p)


# --------------------------------------------------------------------------
# Datos
# --------------------------------------------------------------------------

@dataclass
class Segment:
    start: float
    end: float
    text: str
    lang: str = "auto"
    translation: str = ""


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
    # con rotación de 90/270 ffmpeg intercambia ancho y alto al decodificar
    rot = 0
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
                            " ".join(w["word"] for w in g), lang))
    return segs


# --------------------------------------------------------------------------
# Traducción (con respaldos y errores visibles)
# --------------------------------------------------------------------------

def _norm_lang(code: str) -> str:
    code = (code or "auto").lower()
    if code.startswith("zh"):
        return "zh-cn"
    return code.split("-")[0] if code != "auto" else code


def _google(text: str, src: str, target: str) -> str:
    from deep_translator import GoogleTranslator
    return GoogleTranslator(source="auto",
                            target=_GOOGLE.get(target, target)).translate(text)


def _mymemory(text: str, src: str, target: str) -> str:
    from deep_translator import MyMemoryTranslator
    if src not in _MYMEMORY:
        raise RuntimeError("MyMemory necesita idioma de origen conocido")
    return MyMemoryTranslator(source=_MYMEMORY[src],
                              target=_MYMEMORY[target]).translate(text)


def _bad(result: str | None, original: str) -> bool:
    r = (result or "").strip().lower()
    return (not r or "<html" in r or "error 500" in r or "server error" in r
            or "mymemory warning" in r or "query length limit" in r)


def translate_segments(segs: list[Segment], target: str,
                       groq_api_key: str | None = None) -> list[str]:
    """Traduce cada segmento. Devuelve la lista de problemas encontrados
    (vacía si todo salió bien). Cadena: Groq → Google → MyMemory."""
    tr = Translator(target_language=target, groq_api_key=groq_api_key)
    errors: list[str] = []
    cache: dict[tuple[str, str], str] = {}

    def one(s: Segment) -> str:
        src = _norm_lang(s.lang)
        if src == target:
            return s.text                      # ya está en el idioma destino
        if (src, s.text) in cache:
            return cache[(src, s.text)]
        engines = []
        if tr.groq_key:
            engines.append(("Groq", lambda: tr._translate_groq(s.text, src, target)))
        engines += [("Google", lambda: _google(s.text, src, target)),
                    ("MyMemory", lambda: _mymemory(s.text, src, target))]
        for name, fn in engines:
            for attempt in range(2):
                try:
                    out = fn()
                    if not _bad(out, s.text):
                        cache[(src, s.text)] = out
                        return out
                    errors.append(f"{name}: respuesta vacía/inválida")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
                time.sleep(0.4 * (attempt + 1))
        return ""                              # sin traducir

    with ThreadPoolExecutor(max_workers=4) as pool:
        for s, t in zip(segs, pool.map(one, segs)):
            s.translation = t
    return errors


def detected_languages(segs: list[Segment]) -> list[str]:
    """Idiomas detectados, del más al menos frecuente (por texto hablado)."""
    count: dict[str, int] = {}
    for s in segs:
        if s.lang != "auto":
            count[_norm_lang(s.lang)] = count.get(_norm_lang(s.lang), 0) + len(s.text)
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


def build_ass(segs: list[Segment], st: SubtitleStyle,
              width: int, height: int, duration: float = 0.0) -> str:
    fs = max(12, round(height * SIZES.get(st.size, SIZES["m"])[1]))
    font = FONTS.get(st.font, FONTS["sans"])[1]
    primary = COLORS.get(st.color, COLORS["white"])[1]
    ocol = COLORS.get(st.outline_color, COLORS["black"])[1]
    box_alpha = BGS.get(st.bg, BGS["none"])[1]
    if box_alpha:
        # BorderStyle 3: la "caja" usa OutlineColour; Outline es el relleno
        border, outline = 3, round(fs * 0.22, 1)
        ocol = box_alpha + ocol[3:]
        shadow = 0
    else:
        border = 1
        outline = round(fs * OUTLINES.get(st.outline, OUTLINES["med"])[1], 1)
        shadow = round(fs * 0.05, 1) if st.shadow else 0
    back = "&H80000000" if st.shadow else "&HFF000000"
    align, margin_l, margin_r, margin_v = _layout(st, width, height)
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\n"
        f"PlayResX: {width}\nPlayResY: {height}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,"
        "ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,"
        "MarginR,MarginV,Encoding\n"
        f"Style: Default,{font},{fs},{primary},&H000000FF,{ocol},{back},"
        f"{-1 if st.bold else 0},{-1 if st.italic else 0},0,0,100,100,0,0,"
        f"{border},{outline},{shadow},{align},{margin_l},{margin_r},"
        f"{margin_v},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,"
        "MarginV,Effect,Text\n"
    )
    lines = []
    for s in _fix_timing([Segment(x.start, x.end, x.text, x.lang, x.translation)
                          for x in segs], duration):
        main = _esc(s.translation or s.text)
        if st.upper:
            main = main.upper()
        same = _norm_lang(s.lang) == st.target or main.strip().lower() == s.text.strip().lower()
        if st.bilingual and not same:
            small = max(9, round(fs * 0.62))
            orig = _esc(s.text).upper() if st.upper else _esc(s.text)
            main += f"\\N{{\\fs{small}\\b0\\alpha&H30&}}{orig}"
        lines.append(f"Dialogue: 0,{_ts_ass(s.start)},{_ts_ass(s.end)},"
                     f"Default,,0,0,0,,{main}")
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
# Vista previa (imagen con el estilo elegido sobre un fotograma real)
# --------------------------------------------------------------------------

SAMPLES = {
    "es": "Así se verán tus subtítulos", "en": "This is how your subtitles look",
    "pt": "Assim ficarão suas legendas", "fr": "Voici vos sous-titres",
    "de": "So sehen Ihre Untertitel aus", "it": "Ecco come saranno i sottotitoli",
    "ja": "字幕はこのように表示されます", "ko": "자막이 이렇게 표시됩니다",
    "zh-cn": "字幕将这样显示", "ru": "Так будут выглядеть субтитры",
}


def render_preview(frame: str, st: SubtitleStyle, out_path: str,
                   workdir: str) -> str:
    """Pinta subtítulos de ejemplo sobre un fotograma con el estilo actual."""
    info = _run(["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_streams", frame]).stdout
    vs = json.loads(info)["streams"][0]
    w, h = int(vs["width"]), int(vs["height"])
    orig_lang = "en" if st.target != "en" else "es"
    seg = Segment(0, 5, SAMPLES[orig_lang], orig_lang, SAMPLES.get(st.target, SAMPLES["en"]))
    with open(os.path.join(workdir, "prev.ass"), "w", encoding="utf-8") as f:
        f.write(build_ass([seg], st, w, h, 5))
    _run(["ffmpeg", "-y", "-i", os.path.abspath(frame), "-vf", "subtitles=prev.ass",
          "-frames:v", "1", "-q:v", "3", os.path.abspath(out_path)], cwd=workdir)
    return out_path


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
# Orquestación (en dos fases para poder cambiar de idioma sin re-transcribir)
# --------------------------------------------------------------------------

@dataclass
class Transcription:
    segments: list[Segment]
    info: dict


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


def render_video(video: str, tr: Transcription, st: SubtitleStyle, workdir: str,
                 groq_key: str | None = None, max_mb: float | None = None,
                 progress=lambda msg: None) -> Result:
    langs = detected_languages(tr.segments)
    if langs and all(l == st.target for l in langs):
        raise SameLanguageError(langs)
    progress(f"🌐 Traduciendo {len(tr.segments)} subtítulos…")
    errors = translate_segments(tr.segments, st.target, groq_key)
    failed = [s for s in tr.segments if not s.translation]
    warnings: list[str] = []
    if len(failed) == len(tr.segments):
        raise RuntimeError(
            "No se pudo traducir con ningún motor (Groq/Google/MyMemory). "
            "Último error: " + (errors[-1] if errors else "desconocido") +
            ". Agrega una groq_api_key gratuita en config.json.")
    if failed:
        warnings.append(f"{len(failed)} de {len(tr.segments)} subtítulos no se "
                        "pudieron traducir y quedaron en el idioma original.")
    progress("🎬 Incrustando subtítulos en el video…")
    ass = build_ass(tr.segments, st, tr.info["width"], tr.info["height"],
                    tr.info["duration"])
    out = burn_subtitles(video, ass, os.path.join(workdir, "traducido.mp4"),
                         workdir, tr.info["duration"], max_mb)
    return Result(out, build_srt(tr.segments), langs, len(tr.segments), warnings)

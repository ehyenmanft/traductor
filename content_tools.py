"""
Herramientas de creación de contenido para el bot (todo con ffmpeg y Pillow, sin dependencias nuevas).

  reel_blur      16:9 → 9:16 conservando todo el cuadro sobre un fondo desenfocado (los subtítulos quemados se ven)
  reel_crop      recorte central 9:16 (llena la pantalla; recorta los lados) — se aplica ANTES de quemar subtítulos
  jump_cuts      quita los silencios (cortes secos tipo «jump cut»)
  split_clips    parte un video largo en clips de hasta N segundos (para reels/shorts/TikTok)
  thumbnail      el fotograma más nítido con un título opcional
  carousel_*     carrusel de imágenes (1080×1350) con los puntos clave del video
  caption_pack   título, descripción y hashtags para publicar
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass

import groq_models
from video_translator import _run, _run_progress, probe

REEL_W, REEL_H = 1080, 1920
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]


def _frac(on_frac, lo=0.0, hi=1.0):
    return (lambda f: on_frac(lo + (hi - lo) * f)) if on_frac else None


# ----------------------------------------------------------------- formato vertical

def reel_blur(video: str, out: str, on_frac=None) -> str:
    """Todo el cuadro original centrado sobre una copia ampliada y desenfocada de sí mismo."""
    info = probe(video)
    fg_h = max(2, int(REEL_W * info["height"] / info["width"]) // 2 * 2)
    fc = (f"[0:v]split[a][b];"
          f"[a]scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,crop={REEL_W}:{REEL_H},"
          f"boxblur=30:6,eq=brightness=-0.08[bg];"
          f"[b]scale={REEL_W}:{fg_h}:flags=lanczos[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
    cmd = ["ffmpeg", "-y", "-i", video, "-filter_complex", fc, "-map", "[v]"]
    cmd += ["-map", "0:a?"] if info["has_audio"] else []
    _run_progress(cmd + ENC + [out], duration=info["duration"], on_frac=on_frac)
    return out


def reel_crop(video: str, out: str, focus: float = 0.5, on_frac=None) -> str:
    """Recorte 9:16 a pantalla completa. `focus` (0 izq … 1 der) elige qué franja se conserva."""
    info = probe(video)
    focus = max(0.0, min(1.0, focus))
    vf = (f"scale=-2:{REEL_H}:flags=lanczos,crop={REEL_W}:{REEL_H}:(iw-{REEL_W})*{focus:.3f}:0,setsar=1"
          if info["width"] / info["height"] > REEL_W / REEL_H else
          f"scale={REEL_W}:-2:flags=lanczos,crop={REEL_W}:{REEL_H},setsar=1")   # ya era vertical o más alto
    cmd = ["ffmpeg", "-y", "-i", video, "-vf", vf]
    cmd += ["-map", "0:v", "-map", "0:a?"]
    _run_progress(cmd + ENC + [out], duration=info["duration"], on_frac=on_frac)
    return out


# ----------------------------------------------------------------- cortes

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def speech_intervals(duration: float, silences: list[tuple[float, float]], pad: float = 0.08,
                     min_len: float = 0.25) -> list[tuple[float, float]]:
    """Complementa los silencios: tramos con voz, con un respiro (pad) a cada lado y sin micro-cortes."""
    keep, t = [], 0.0
    for s, e in sorted(silences):
        if s - t > 0:
            keep.append((t, s))
        t = max(t, e)
    if duration - t > 0:
        keep.append((t, duration))
    padded = [(max(0.0, a - pad), min(duration, b + pad)) for a, b in keep if b - a >= min_len]
    merged: list[list[float]] = []
    for a, b in padded:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def detect_silences(video: str, noise_db: float = -32.0, min_silence: float = 0.55) -> list[tuple[float, float]]:
    res = subprocess.run(["ffmpeg", "-nostats", "-i", video, "-vn", "-af",
                          f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
                         capture_output=True, text=True)
    out, start = [], None
    for line in res.stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:                                    # silencio hasta el final del archivo
        out.append((start, 1e9))
    return out


@dataclass
class CutResult:
    path: str | None
    kept: float
    removed: float
    note: str = ""


def jump_cuts(video: str, out: str, noise_db: float = -32.0, min_silence: float = 0.55,
              pad: float = 0.08, on_frac=None) -> CutResult:
    """Quita los silencios. Si casi no hay (menos de ~3 %), no reencoda: path=None."""
    info = probe(video)
    if not info["has_audio"]:
        raise RuntimeError("El video no tiene audio: no hay silencios que detectar.")
    dur = info["duration"]
    sil = [(s, min(e, dur)) for s, e in detect_silences(video, noise_db, min_silence)]
    keep = speech_intervals(dur, sil, pad)
    if not keep:
        raise RuntimeError("Todo el audio se detectó como silencio (prueba con un video con voz).")
    kept = sum(b - a for a, b in keep)
    if dur - kept < max(0.5, 0.03 * dur):
        return CutResult(None, dur, 0.0, "No encontré silencios que valga la pena quitar.")
    expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)
    script = (f"[0:v]select='{expr}',setpts=N/FRAME_RATE/TB[v];"
              f"[0:a]aselect='{expr}',asetpts=N/SR/TB[a]")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(script)
        script_path = f.name
    try:
        _run_progress(["ffmpeg", "-y", "-i", video, "-filter_complex_script", script_path,
                       "-map", "[v]", "-map", "[a]"] + ENC + [out], duration=dur, on_frac=on_frac)
    finally:
        os.remove(script_path)
    return CutResult(out, kept, dur - kept)


def split_clips(video: str, outdir: str, max_seconds: float = 55.0, max_clips: int = 10,
                on_frac=None) -> list[str]:
    """Clips iguales de ≤ max_seconds (cortes exactos). Ideal para reels/shorts de 60 s."""
    info = probe(video)
    dur = info["duration"]
    n = max(1, min(max_clips, math.ceil(dur / max_seconds)))
    seg = dur / n
    paths = []
    for i in range(n):
        out = os.path.join(outdir, f"clip_{i + 1:02d}.mp4")
        _run_progress(["ffmpeg", "-y", "-ss", f"{i * seg:.3f}", "-i", video, "-t", f"{seg:.3f}"]
                      + ENC + [out], duration=seg, on_frac=_frac(on_frac, i / n, (i + 1) / n))
        paths.append(out)
    return paths


# ----------------------------------------------------------------- imágenes

def _font_path(family: str, bold: bool = True) -> str | None:
    try:
        res = subprocess.run(["fc-match", "-f", "%{file}", f"{family}:{'bold' if bold else 'regular'}"],
                             capture_output=True, text=True, timeout=5)
        return res.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _font(size: int, family: str = "Anton", bold: bool = True):
    from PIL import ImageFont
    for fam in (family, "Liberation Sans", "DejaVu Sans"):
        p = _font_path(fam, bold)
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def thumbnail(video: str, out: str, title: str = "", samples: int = 12) -> str:
    """El fotograma más nítido (varianza del laplaciano) de varios puntos del video, con título opcional."""
    from PIL import Image, ImageDraw, ImageFilter, ImageStat
    info = probe(video)
    dur = info["duration"]
    best, best_score = None, -1.0
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(samples):
            t = dur * (0.08 + 0.84 * i / max(1, samples - 1))
            p = os.path.join(tmp, f"f{i}.jpg")
            try:
                _run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-q:v", "2", p])
            except RuntimeError:
                continue
            if not os.path.exists(p):
                continue
            img = Image.open(p).convert("RGB")
            small = img.resize((320, int(320 * img.height / img.width))).convert("L")
            edges = small.filter(ImageFilter.FIND_EDGES)
            score = ImageStat.Stat(edges).var[0] + ImageStat.Stat(small).stddev[0]   # nítido y con contraste
            if score > best_score:
                best, best_score = img.copy(), score
    if best is None:
        raise RuntimeError("No pude sacar fotogramas del video.")
    if title.strip():
        d = ImageDraw.Draw(best)
        w, h = best.size
        size = max(28, w // 12)
        font = _font(size)
        lines = textwrap.wrap(title.strip().upper(), width=max(8, int(w / (size * 0.55))))[:3]
        line_h = int(size * 1.15)
        y = h - line_h * len(lines) - h // 12
        for ln in lines:
            tw = d.textlength(ln, font=font)
            x = (w - tw) / 2
            d.text((x, y), ln, font=font, fill="white", stroke_width=max(3, size // 9), stroke_fill="black")
            y += line_h
    best.save(out, quality=92)
    return out


THEMES = {   # fondo (arriba, abajo), texto, acento
    "oscuro": ((18, 18, 24), (40, 28, 70), (255, 255, 255), (255, 214, 10)),
    "claro": ((250, 247, 240), (232, 226, 214), (24, 24, 28), (214, 40, 57)),
    "neon": ((6, 6, 20), (4, 40, 52), (240, 255, 255), (0, 240, 255)),
    "calido": ((255, 140, 66), (214, 40, 90), (255, 255, 255), (255, 240, 200)),
}
CARD_W, CARD_H = 1080, 1350


def _gradient(size, top, bottom):
    from PIL import Image
    w, h = size
    img = Image.new("RGB", size, top)
    px = img.load()
    for y in range(h):
        k = y / max(1, h - 1)
        row = tuple(int(top[i] + (bottom[i] - top[i]) * k) for i in range(3))
        for x in range(w):
            px[x, y] = row
    return img


def _fit_text(draw, text: str, font_family: str, box_w: int, box_h: int, max_size: int, min_size: int,
              bold: bool = True):
    """El mayor tamaño de letra con el que el texto, ajustado a líneas, cabe en la caja."""
    for size in range(max_size, min_size - 1, -4):
        font = _font(size, font_family, bold)
        avg = max(1.0, draw.textlength("abcdefghijklmnopqrstuvwxyzáéíóú", font=font) / 31)
        lines = []
        for para in text.split("\n"):
            lines += textwrap.wrap(para, width=max(6, int(box_w / avg))) or [""]
        if len(lines) * size * 1.22 <= box_h and all(draw.textlength(l, font=font) <= box_w for l in lines):
            return font, lines, size
    font = _font(min_size, font_family, bold)
    avg = max(1.0, draw.textlength("abcdefghijklmnopqrstuvwxyzáéíóú", font=font) / 31)
    return font, textwrap.wrap(text, width=max(6, int(box_w / avg)))[: max(1, int(box_h / (min_size * 1.22)))], min_size


def render_carousel(slides: list[dict], outdir: str, theme: str = "oscuro", handle: str = "") -> list[str]:
    """Una imagen 1080×1350 por diapositiva: título grande + texto, numeración y flecha «desliza»."""
    from PIL import ImageDraw
    top, bottom, fg, accent = THEMES.get(theme, THEMES["oscuro"])
    n = len(slides)
    paths = []
    for i, sl in enumerate(slides):
        img = _gradient((CARD_W, CARD_H), top, bottom)
        d = ImageDraw.Draw(img)
        pad = 90
        title, body = str(sl.get("title", "")).strip(), str(sl.get("body", "")).strip()
        d.rounded_rectangle((pad, 110, pad + 150, 122), radius=6, fill=accent)          # barra de acento
        y = 170
        if i == 0:                                       # portada: solo título, enorme
            font, lines, size = _fit_text(d, title.upper(), "Anton", CARD_W - 2 * pad, 760, 150, 64)
            for ln in lines:
                d.text((pad, y), ln, font=font, fill=fg)
                y += int(size * 1.15)
            if body:
                f2, l2, s2 = _fit_text(d, body, "Liberation Sans", CARD_W - 2 * pad, 220, 48, 30, bold=False)
                y = max(y + 40, CARD_H - 420)
                for ln in l2:
                    d.text((pad, y), ln, font=f2, fill=accent)
                    y += int(s2 * 1.25)
        else:
            font, lines, size = _fit_text(d, title, "Anton", CARD_W - 2 * pad, 330, 96, 52)
            for ln in lines:
                d.text((pad, y), ln, font=font, fill=accent)
                y += int(size * 1.15)
            y += 40
            f2, l2, s2 = _fit_text(d, body, "Liberation Sans", CARD_W - 2 * pad, CARD_H - y - 230, 56, 30, bold=False)
            for ln in l2:
                d.text((pad, y), ln, font=f2, fill=fg)
                y += int(s2 * 1.3)
        foot = _font(34, "Liberation Sans", False)
        d.text((pad, CARD_H - 100), handle or "", font=foot, fill=accent)
        label = f"{i + 1}/{n}" + ("   →" if i < n - 1 else "")
        d.text((CARD_W - pad - d.textlength(label, font=foot), CARD_H - 100), label, font=foot, fill=fg)
        out = os.path.join(outdir, f"carrusel_{i + 1:02d}.png")
        img.save(out)
        paths.append(out)
    return paths


# ----------------------------------------------------------------- textos (IA)

LANG_NAMES = {"es": "Spanish", "en": "English", "pt": "Portuguese", "fr": "French", "de": "German",
              "it": "Italian", "ja": "Japanese", "ko": "Korean", "zh-cn": "Simplified Chinese", "ru": "Russian"}


def _json_chat(key: str, system: str, user: str, max_tokens: int = 1500) -> dict:
    raw = groq_models.chat(key, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                           max_tokens=max_tokens, json_mode=True, temperature=0.5, timeout=60)
    try:
        return json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise RuntimeError("La IA no devolvió un JSON válido.")
        return json.loads(m.group(0))


def _clip_text(text: str, limit: int = 9000) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?…。！？])\s+", re.sub(r"\s+", " ", text).strip()) if s.strip()]


def fallback_slides(text: str, n: int = 6) -> list[dict]:
    """Sin IA: reparte las frases del video en diapositivas (portada con la primera frase)."""
    ss = sentences(text) or [text.strip() or "…"]
    cover = {"title": textwrap.shorten(ss[0], 70, placeholder="…"), "body": ""}
    rest = ss[1:] or ss
    per = max(1, math.ceil(len(rest) / max(1, n - 1)))
    slides = [cover]
    for i in range(0, len(rest), per):
        chunk = " ".join(rest[i:i + per])
        slides.append({"title": f"{len(slides)}", "body": textwrap.shorten(chunk, 260, placeholder="…")})
        if len(slides) >= n:
            break
    return slides


def carousel_slides(text: str, groq_key: str | None, lang: str = "es", n: int = 6) -> tuple[list[dict], str]:
    """(diapositivas, aviso). Con groq_key: portada gancho + puntos clave + cierre. Sin ella o si falla: reparto simple."""
    text = _clip_text(text)
    n = max(3, min(10, n))
    if not groq_key:
        return fallback_slides(text, n), "Sin groq_api_key: reparto simple de frases."
    try:
        data = _json_chat(groq_key,
                          f"You turn a video transcript into an Instagram carousel in {LANG_NAMES.get(lang, 'Spanish')}. "
                          f"Return JSON: {{\"slides\":[{{\"title\":\"…\",\"body\":\"…\"}}]}} with exactly {n} slides. "
                          "Slide 1 is a scroll-stopping hook (title ≤ 9 words, body empty or a short subtitle). "
                          f"Slides 2-{n - 1}: one key idea each (title ≤ 6 words, body 1-2 short sentences, ≤ 200 characters). "
                          f"Slide {n}: a closing call to action. Use only facts from the transcript; be faithful, no inventions.",
                          text)
        slides = [{"title": str(s.get("title", "")).strip(), "body": str(s.get("body", "")).strip()}
                  for s in data["slides"] if isinstance(s, dict) and (s.get("title") or s.get("body"))]
        if len(slides) < 2:
            raise RuntimeError("pocas diapositivas")
        return slides[:10], ""
    except Exception as e:  # noqa: BLE001
        return fallback_slides(text, n), f"La IA no respondió ({str(e)[:80]}): reparto simple de frases."


def caption_pack(text: str, groq_key: str | None, lang: str = "es") -> str:
    """Títulos con gancho, descripción y hashtags listos para pegar en Reels/TikTok/Shorts."""
    if not groq_key:
        raise RuntimeError("Esta función necesita una groq_api_key en config.json.")
    data = _json_chat(groq_key,
                      f"You write social media copy in {LANG_NAMES.get(lang, 'Spanish')} for a short video, from its "
                      "transcript. Return JSON: {\"titles\":[3 hooks ≤ 60 chars],\"description\":\"2-3 sentences + a call to "
                      "action\",\"hashtags\":[8 relevant hashtags without #, no spaces],\"hook_first_3s\":\"one line to say "
                      "or show in the first 3 seconds\"}. Be faithful to the transcript.", _clip_text(text))
    titles = [str(t).strip() for t in data.get("titles", []) if str(t).strip()][:3]
    tags = ["#" + re.sub(r"[^\w]", "", str(h)) for h in data.get("hashtags", [])]
    tags = [t for t in tags if len(t) > 1][:10]
    out = []
    if titles:
        out.append("🎯 Títulos:\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles)))
    if data.get("hook_first_3s"):
        out.append("⚡ Gancho (primeros 3 s):\n" + str(data["hook_first_3s"]).strip())
    if data.get("description"):
        out.append("📝 Descripción:\n" + str(data["description"]).strip())
    if tags:
        out.append("#️⃣ Hashtags:\n" + " ".join(tags))
    if not out:
        raise RuntimeError("La IA no devolvió contenido.")
    return "\n\n".join(out)


def shrink(video: str, out: str, max_mb: float, on_frac=None) -> str:
    """Si el video pesa más que el límite de envío, lo recodifica a un bitrate que entre (una pasada)."""
    if os.path.getsize(video) <= max_mb * 1024 * 1024:
        return video
    info = probe(video)
    dur = max(info["duration"], 1.0)
    v_kbps = max(150, int(max_mb * 8 * 1024 * 0.9 / dur - 128))
    _run_progress(["ffmpeg", "-y", "-i", video, "-map", "0:v", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                   "-b:v", f"{v_kbps}k", "-maxrate", f"{int(v_kbps * 1.4)}k", "-bufsize", f"{v_kbps * 2}k",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out],
                  duration=dur, on_frac=on_frac)
    return out


def transcript_text(segments) -> str:
    """El texto del video para la IA: la traducción si existe, si no el original."""
    return " ".join((getattr(s, "translation", "") or getattr(s, "text", "")).strip() for s in segments).strip()

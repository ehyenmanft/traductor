#!/usr/bin/env python3
"""
Herramientas de video del traductor desde la línea de comandos (para los bots de Claude y para ti).
Usa las claves de config.json (o de las variables DEEPGRAM_API_KEY / GROQ_API_KEY); nunca las muestra.

  cli_video.py styles                               estilos, fuentes y colores disponibles
  cli_video.py subtitle VIDEO [--style kidcut] [--target es] [--pos top] [--out OUT]   subtitula traduciendo
  cli_video.py reel VIDEO [--mode blur|crop]        formato vertical 9:16
  cli_video.py cuts VIDEO                           quita los silencios
  cli_video.py clips VIDEO [--seconds 55]           parte en clips
  cli_video.py thumb VIDEO [--title TEXTO]          miniatura
  cli_video.py carousel VIDEO [--theme oscuro]      carrusel de imágenes con las ideas del video
  cli_video.py texts VIDEO                          títulos, descripción y hashtags

Cada orden imprime al final líneas «RESULTADO: ruta» con lo que generó (dentro de ./salidas/).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import replace

import content_tools as ct
import subtitle_style as ss
import translate_engine
import video_translator as vt

APP_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(APP_DIR, "salidas")
KEEP_DAYS = 2


def cfg(env: str, key: str, default: str = "") -> str:
    val = os.environ.get(env, "")
    if val:
        return val
    try:
        with open(os.path.join(APP_DIR, "config.json"), encoding="utf-8") as f:
            return str(json.load(f).get(key, default) or default)
    except (OSError, ValueError):
        return default


def say(msg: str):
    print(msg, flush=True)


def progress(pct: float, text: str = ""):
    if text:
        print(f"[{pct:5.1f}%] {text}", file=sys.stderr, flush=True)


def out_dir(video: str) -> str:
    """Carpeta de salida propia de esta ejecución; borra las de más de KEEP_DAYS días."""
    os.makedirs(OUT_ROOT, exist_ok=True)
    cutoff = time.time() - KEEP_DAYS * 86400
    for name in os.listdir(OUT_ROOT):
        path = os.path.join(OUT_ROOT, name)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in os.path.splitext(os.path.basename(video))[0])[:40]
    d = os.path.join(OUT_ROOT, f"{time.strftime('%Y%m%d-%H%M%S')}_{stem}")
    os.makedirs(d, exist_ok=True)
    return d


def need_video(path: str) -> str:
    if not os.path.isfile(path):
        raise SystemExit(f"No encuentro el video: {path}")
    return os.path.abspath(path)


def need_key(name: str, env: str, key: str) -> str:
    val = cfg(env, key)
    if not val:
        raise SystemExit(f"Falta {name}: ponla en config.json ({key}) o en la variable {env}.")
    return val


def build_style(args) -> ss.SubtitleStyle:
    st = ss.SubtitleStyle(target=args.target, tone=args.tone, dub=args.dub)
    if args.style:
        key = ss.resolve_preset(args.style)
        if not key:
            raise SystemExit(f"No conozco el estilo «{args.style}». Mira la lista con: cli_video.py styles")
        st = ss.apply_preset(st, key)
    if args.pos:
        v, _, h = args.pos.partition("-")
        v, h = (v, h or "center")
        if (v, h) not in vt.ALIGN:
            raise SystemExit(f"Posición inválida «{args.pos}». Usa vertical[-horizontal]: "
                             f"{'/'.join(ss.VPOS)} y {'/'.join(ss.HPOS)}")
        st = replace(st, align_v=v, align_h=h)
    if args.size:
        if args.size not in ss.SIZES:
            raise SystemExit(f"Tamaño inválido «{args.size}»: {', '.join(ss.SIZES)}")
        st = replace(st, size=args.size)
    return st


def transcribe(video: str, workdir: str) -> vt.Transcription:
    return vt.transcribe_video(video, workdir, need_key("la clave de Deepgram", "DEEPGRAM_API_KEY", "deepgram_api_key"),
                               progress)


def translated(video: str, workdir: str, target: str, tone: str = "natural") -> vt.Transcription:
    tr = transcribe(video, workdir)
    translate_engine.translate_segments(tr.segments, target, cfg("GROQ_API_KEY", "groq_api_key") or None, tone, {}, False)
    return tr


def result(path: str):
    say(f"RESULTADO: {path}")


# ------------------------------------------------------------------ órdenes

def cmd_styles(args):
    say("ESTILOS (usa --style NOMBRE; sin emoji y sin importar mayúsculas):")
    for k, p in ss.PRESETS.items():
        say(f"  {k:10s} {p['label']}")
    say("\nFUENTES: " + ", ".join(f"{k} ({v[0]})" for k, v in ss.FONTS.items()))
    say("COLORES: " + ", ".join(ss.COLORS))
    say("POSICIONES (--pos): vertical " + "/".join(ss.VPOS) + " · horizontal " + "/".join(ss.HPOS) + "  (ej. top, bottom-left)")
    say("TAMAÑOS (--size): " + ", ".join(ss.SIZES))
    say("IDIOMAS (--target): " + ", ".join(ss.LANGUAGES))
    say("TONOS (--tone): " + ", ".join(ss.TONES))


def cmd_subtitle(args):
    video, st = need_video(args.video), build_style(args)
    work = out_dir(video)
    tr = transcribe(video, work)
    res = vt.render_video(video, tr, st, work, cfg("GROQ_API_KEY", "groq_api_key") or None, args.max_mb, progress, {})
    final = args.out or os.path.join(work, "subtitulado.mp4")
    if os.path.abspath(final) != os.path.abspath(res.video_path):
        os.makedirs(os.path.dirname(os.path.abspath(final)), exist_ok=True)
        shutil.copyfile(res.video_path, final)
    srt = os.path.join(work, "subtitulos.srt")
    with open(srt, "w", encoding="utf-8") as f:
        f.write(res.srt_text)
    langs = ", ".join(ss.LANGUAGES.get(x, x) for x in res.languages) or "?"
    say(f"Idioma(s) detectado(s): {langs} → {ss.LANGUAGES.get(st.target, st.target)} · estilo: "
        f"{ss.PRESETS.get(st.preset, {}).get('label', 'personalizado')}")
    for w in res.warnings:
        say("AVISO: " + w)
    result(final)
    result(srt)


def cmd_reel(args):
    video = need_video(args.video)
    work = out_dir(video)
    out = args.out or os.path.join(work, "reel.mp4")
    (ct.reel_blur if args.mode == "blur" else ct.reel_crop)(video, out, on_frac=lambda f: progress(100 * f, "reel"))
    result(out)


def cmd_cuts(args):
    video = need_video(args.video)
    work = out_dir(video)
    res = ct.jump_cuts(video, args.out or os.path.join(work, "sin_silencios.mp4"),
                       on_frac=lambda f: progress(100 * f, "quitando silencios"))
    if res.path is None:
        say(res.note)
        return
    say(f"Quité {res.removed:.1f} s de silencios ({res.kept:.1f} s → {res.kept - res.removed:.1f} s)")
    result(res.path)


def cmd_clips(args):
    video = need_video(args.video)
    d = os.path.join(out_dir(video), "clips")
    os.makedirs(d, exist_ok=True)
    for p in ct.split_clips(video, d, args.seconds, args.max_clips, lambda f: progress(100 * f, "clips")):
        result(p)


def cmd_thumb(args):
    video = need_video(args.video)
    out = args.out or os.path.join(out_dir(video), "miniatura.jpg")
    result(ct.thumbnail(video, out, args.title or ""))


def cmd_carousel(args):
    video = need_video(args.video)
    work = out_dir(video)
    tr = translated(video, work, args.target)
    slides, note = ct.carousel_slides(ct.transcript_text(tr.segments), cfg("GROQ_API_KEY", "groq_api_key") or None,
                                      args.target, args.slides)
    if note:
        say("AVISO: " + note)
    for p in ct.render_carousel(slides, work, args.theme, args.handle or cfg("CONTENT_HANDLE", "handle")):
        result(p)


def cmd_texts(args):
    video = need_video(args.video)
    tr = translated(video, out_dir(video), args.target)
    say(ct.caption_pack(ct.transcript_text(tr.segments), cfg("GROQ_API_KEY", "groq_api_key") or None, args.target))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("styles", help="lista estilos, fuentes y colores").set_defaults(fn=cmd_styles)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("video")
        p.set_defaults(fn=fn)
        return p

    p = add("subtitle", cmd_subtitle, "subtitula y traduce")
    p.add_argument("--style"); p.add_argument("--target", default="es", choices=list(ss.LANGUAGES))
    p.add_argument("--tone", default="natural", choices=list(ss.TONES))
    p.add_argument("--dub", default="off", choices=list(ss.DUBS))
    p.add_argument("--pos"); p.add_argument("--size"); p.add_argument("--out")
    p.add_argument("--max-mb", type=float, default=None)
    p = add("reel", cmd_reel, "formato 9:16")
    p.add_argument("--mode", choices=["blur", "crop"], default="blur"); p.add_argument("--out")
    p = add("cuts", cmd_cuts, "quita silencios"); p.add_argument("--out")
    p = add("clips", cmd_clips, "parte en clips")
    p.add_argument("--seconds", type=float, default=55.0); p.add_argument("--max-clips", type=int, default=10)
    p = add("thumb", cmd_thumb, "miniatura"); p.add_argument("--title"); p.add_argument("--out")
    p = add("carousel", cmd_carousel, "carrusel")
    p.add_argument("--theme", default="oscuro", choices=list(ct.THEMES)); p.add_argument("--target", default="es", choices=list(ss.LANGUAGES))
    p.add_argument("--slides", type=int, default=6); p.add_argument("--handle")
    p = add("texts", cmd_texts, "títulos y hashtags"); p.add_argument("--target", default="es", choices=list(ss.LANGUAGES))
    return ap


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        args.fn(args)
    except (RuntimeError, SystemExit) as e:
        if isinstance(e, SystemExit) and not isinstance(e.code, str):
            raise
        print(f"ERROR: {e if isinstance(e, RuntimeError) else e.code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Bot de Telegram: traduce videos y devuelve el video con subtítulos incrustados.

Flujo:
  1. Envías un video. El bot muestra una VISTA PREVIA real (un fotograma de tu
     video con subtítulos de ejemplo) y un menú que se actualiza en vivo:
     estilos listos, fuente, color, contorno, posición (5x5), tamaño,
     animaciones, karaoke (palabra activa), palabras por pantalla, efectos,
     tono de traducción, doblaje con voz IA y plantillas propias.
  2. ✅ Confirmar → Deepgram detecta el idioma, se traduce y se superpone la
     traducción SIN quitar nada del video ni del audio original.
  3. Debajo del resultado: "✏️ Editar texto" (corriges el .srt y lo reenvías)
     y "🎨 Cambiar estilo y repetir" (sin volver a transcribir).

Comandos: /glosario /conservar /reiniciar /ayuda

Configuración (variables de entorno o config.json junto al script):
  TELEGRAM_BOT_TOKEN  / "telegram_bot_token"   (de @BotFather)
  DEEPGRAM_API_KEY    / "deepgram_api_key"
  GROQ_API_KEY        / "groq_api_key"         (traducción con tono/contexto)
  ALLOWED_USERS       / "telegram_allowed_users"  ids o @usuarios separados por coma
  TELEGRAM_API_URL    (opcional) servidor local de Bot API → videos de hasta 2 GB
"""
from __future__ import annotations

import asyncio
import glob
import itertools
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import replace

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
                      InputMediaPhoto, Update)
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, PersistenceInput,
                          PicklePersistence, filters)

from video_translator import (ALIGN, ANIMS, BGS, COLORS, DUBS, FONTS, HIGHLIGHTS,
                              HPOS, LANGUAGES, LOOK_FIELDS, ORIG_VOLS, OUTLINES,
                              PRESETS, PROGRESS, SIZES, SPACINGS, TONES, VPOS,
                              WORDS, SameLanguageError, SubtitleStyle,
                              apply_edited_srt, apply_preset, build_srt,
                              extract_frame, probe, render_preview,
                              render_video, transcribe_video)

log = logging.getLogger("traductor-bot")

CLOUD_DOWNLOAD_LIMIT = 20 * 1024 * 1024   # límite de getFile en la Bot API pública
SEND_LIMIT_MB = 49                        # límite de subida de bots: 50 MB
JOB_TTL = 2 * 3600                        # un video/menú sin usar caduca a las 2 h
MAX_TEMPLATES = 8
TEMPLATE_FIELDS = LOOK_FIELDS + ("size", "align_h", "align_v")
_job_ids = itertools.count(1)
_file_ids = itertools.count(1)
APP_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# configuración
# --------------------------------------------------------------------------

def _cfg(env: str, key: str, default: str = "") -> str:
    val = os.environ.get(env, "").strip()
    if val:
        return val
    try:
        with open(os.path.join(APP_DIR, "config.json"), encoding="utf-8") as f:
            return str(json.load(f).get(key, default) or default).strip()
    except (OSError, ValueError):
        return default


def allowed_users() -> set[int | str]:
    """Ids numéricos o @usernames (en minúsculas, sin @)."""
    raw = _cfg("ALLOWED_USERS", "telegram_allowed_users")
    out: set[int | str] = set()
    for x in raw.replace(";", ",").split(","):
        x = x.strip().lstrip("@").lower()
        if x:
            out.add(int(x) if x.isdigit() else x)
    return out


# --------------------------------------------------------------------------
# menú
# --------------------------------------------------------------------------

COLOR_EMOJI = {"white": "⚪", "yellow": "🟡", "cyan": "🔵", "green": "🟢",
               "orange": "🟠", "pink": "🩷", "red": "🔴", "blue": "🔷",
               "purple": "🟣", "black": "⚫"}
PAGES = {"font": "🔤 Fuente", "color": "🎨 Color", "outline": "✏️ Contorno y fondo",
         "pos": "📍 Posición", "size": "🔠 Tamaño", "extras": "⚙️ Extras",
         "anim": "🎞️ Animación y karaoke", "fx": "✨ Efectos",
         "tone": "🗣️ Tono y glosario", "dub": "🎙️ Doblaje IA",
         "tpl": "⭐ Mis plantillas", "lang": "🌐 Idioma"}
GLOSSARY_HELP = ("Glosario: /glosario hola=hello fija cómo traducir un término. "
                 "/conservar Nombre lo deja sin traducir. /glosario borrar X, "
                 "/glosario limpiar. (Lo respeta mejor Groq.)")


def _chunk(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def keyboard(job_id: int, st: SubtitleStyle, page: str = "main",
             templates=()) -> InlineKeyboardMarkup:
    def opt(label, key, val, selected=False):
        return InlineKeyboardButton(("✓ " if selected else "") + label,
                                    callback_data=f"o|{job_id}|{key}|{val}|{page}")

    def header(text):
        return [[InlineKeyboardButton(f"— {text} —", callback_data=f"n|{job_id}")]]

    def tog(label, key):
        on = getattr(st, key)
        return [opt(f"{label}: {'Sí' if on else 'No'}", key, "0" if on else "1")]

    def colors(key, current):
        return _chunk([opt(COLOR_EMOJI[k], key, k, current == k) for k in COLORS], 5)

    rows: list[list[InlineKeyboardButton]] = []
    if page == "main":
        rows += _chunk([opt(p["label"], "preset", k, st.preset == k)
                        for k, p in PRESETS.items()], 3)
        rows += _chunk([InlineKeyboardButton(label, callback_data=f"p|{job_id}|{k}")
                        for k, label in PAGES.items()], 2)
    elif page == "font":
        rows += _chunk([opt(l, "font", k, st.font == k) for k, (l, _) in FONTS.items()], 3)
    elif page == "color":
        rows += header("Color del texto")
        rows += _chunk([opt(f"{COLOR_EMOJI[k]} {l}", "color", k, st.color == k)
                        for k, (l, _) in COLORS.items()], 3)
    elif page == "outline":
        rows += header("Grosor del contorno")
        rows += _chunk([opt(l, "outline", k, st.outline == k)
                        for k, (l, _) in OUTLINES.items()], 3)
        rows += header("Color del contorno / caja")
        rows += colors("outline_color", st.outline_color)
        rows += header("Fondo (con caja no hay contorno)")
        rows.append([opt(l, "bg", k, st.bg == k) for k, (l, _) in BGS.items()])
    elif page == "pos":
        rows += header("Posición en pantalla (cuadrícula del video)")
        for v in VPOS:
            rows.append([opt("🟩" if (st.align_v, st.align_h) == (v, h) else "⬜",
                             "pos", f"{v}-{h}") for h in HPOS])
    elif page == "size":
        rows.append([opt(l, "size", k, st.size == k) for k, (l, _) in SIZES.items()])
    elif page == "extras":
        rows += [tog("𝗡 Negrita", "bold"), tog("𝘐 Cursiva", "italic"),
                 tog("AA MAYÚSCULAS", "upper"), tog("🌑 Sombra", "shadow"),
                 tog("📝 Texto original debajo", "bilingual")]
    elif page == "anim":
        rows += header("Animación de entrada")
        rows += _chunk([opt(l, "anim", k, st.anim == k) for k, l in ANIMS.items()], 3)
        rows += header("Palabra activa (karaoke)")
        rows += _chunk([opt(l, "highlight", k, st.highlight == k)
                        for k, l in HIGHLIGHTS.items()], 2)
        rows += header("Palabras por pantalla")
        rows.append([opt(l.replace(" palabras", "").replace(" palabra", "")
                         .replace("Frase completa", "Frase"), "words", str(k),
                         st.words == k) for k, l in WORDS.items()])
        rows += header("Color de la palabra activa / barra")
        rows += colors("hl_color", st.hl_color)
    elif page == "fx":
        rows += [tog("💡 Neón / resplandor", "neon"),
                 tog("🔑 Resaltar palabras clave", "keywords"),
                 tog("🤐 Censurar groserías", "censor")]
        rows += header("Color de palabras clave")
        rows += colors("kw_color", st.kw_color)
        rows += header("Espaciado de letras")
        rows.append([opt(l, "spacing", k, st.spacing == k) for k, (l, _) in SPACINGS.items()])
        rows += header("Barra de progreso")
        rows.append([opt(l, "progress", k, st.progress == k) for k, l in PROGRESS.items()])
    elif page == "tone":
        rows += header("Tono de la traducción (usa Groq)")
        rows += _chunk([opt(l, "tone", k, st.tone == k) for k, (l, _) in TONES.items()], 3)
        rows.append([InlineKeyboardButton("📖 Cómo usar el glosario", callback_data=f"h|{job_id}")])
    elif page == "dub":
        rows += header("Doblar con voz IA")
        rows.append([opt(l, "dub", k, st.dub == k) for k, l in DUBS.items()])
        rows += header("Audio original al doblar")
        rows.append([opt(l, "orig_vol", k, st.orig_vol == k) for k, (l, _) in ORIG_VOLS.items()])
    elif page == "tpl":
        rows += header("Guarda tu estilo actual y reutilízalo")
        rows.append([InlineKeyboardButton("💾 Guardar estilo actual", callback_data=f"t|{job_id}|save")])
        for i, (name, _) in enumerate(templates):
            rows.append([InlineKeyboardButton(f"⭐ {name}", callback_data=f"t|{job_id}|apply|{i}"),
                         InlineKeyboardButton("🗑", callback_data=f"t|{job_id}|del|{i}")])
    elif page == "lang":
        rows += _chunk([opt(n, "target", c, st.target == c)
                        for c, n in LANGUAGES.items()], 3)

    if page == "main":
        rows.append([InlineKeyboardButton("✅ Confirmar", callback_data=f"go|{job_id}"),
                     InlineKeyboardButton("✖️ Cancelar", callback_data=f"x|{job_id}")])
    else:
        rows.append([InlineKeyboardButton("⬅️ Volver", callback_data=f"p|{job_id}|main"),
                     InlineKeyboardButton("✅ Confirmar", callback_data=f"go|{job_id}")])
    return InlineKeyboardMarkup(rows)


def menu_text(st: SubtitleStyle, note: str = "") -> str:
    pos = {"top": "arriba", "upper": "arriba-centro", "middle": "centro",
           "lower": "centro-abajo", "bottom": "abajo"}[st.align_v]
    hor = {"left": "izquierda", "leftmid": "izq.-centro", "center": "centrado",
           "rightmid": "centro-der.", "right": "derecha"}[st.align_h]
    extras = [x for x, on in (("negrita", st.bold), ("cursiva", st.italic),
                              ("mayúsculas", st.upper), ("sombra", st.shadow),
                              ("neón", st.neon), ("palabras clave", st.keywords),
                              ("censura", st.censor), ("con texto original", st.bilingual))
              if on]
    motion = [ANIMS[st.anim]] if st.anim != "none" else []
    if st.highlight != "none":
        motion.append(HIGHLIGHTS[st.highlight].lower())
    if st.words:
        motion.append(WORDS[st.words])
    if st.progress != "off":
        motion.append(PROGRESS[st.progress].lower())
    text = (
        "👆 Vista previa (animaciones y barra se ven en el video final).\n\n"
        f"🌐 {LANGUAGES[st.target]} · 🗣️ {TONES[st.tone][0]}"
        + (f" · 🎙️ {DUBS[st.dub].lower()}" if st.dub != "off" else "") + "\n"
        f"🔤 {FONTS[st.font][0]} · 🎨 {COLORS[st.color][0]} · "
        f"✏️ {OUTLINES[st.outline][0].lower()} · {BGS[st.bg][0].lower()}\n"
        f"📍 {pos}, {hor} · 🔠 {SIZES[st.size][0]}"
        + (f"\n🎞️ {', '.join(motion)}" if motion else "")
        + (f"\n⚙️ {', '.join(extras)}" if extras else ""))
    return (note + "\n\n" + text) if note else text


# --------------------------------------------------------------------------
# utilidades de jobs
# --------------------------------------------------------------------------

def _purge_jobs(jobs: dict):
    now = time.time()
    for jid in [j for j, v in jobs.items()
                if now - v["created"] > JOB_TTL and not v["busy"]]:
        shutil.rmtree(jobs.pop(jid)["dir"], ignore_errors=True)


def _purge_orphans(max_age: float = 3 * 3600):
    """Carpetas temporales que quedaron de una ejecución anterior."""
    for d in glob.glob(os.path.join(tempfile.gettempdir(), "trad_*")):
        try:
            if time.time() - os.path.getmtime(d) > max_age:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


async def _purge_task(ctx: ContextTypes.DEFAULT_TYPE):
    _purge_jobs(ctx.application.bot_data.setdefault("jobs", {}))
    _purge_orphans()


def _drop_job(jobs: dict, job_id: int):
    job = jobs.pop(job_id, None)
    if job:
        shutil.rmtree(job["dir"], ignore_errors=True)


async def _set_status(msg, text: str, markup=None):
    """Edita el pie de la foto (menú) o el texto (mensaje de estado)."""
    try:
        if msg.photo:
            await msg.edit_caption(caption=text, reply_markup=markup)
        else:
            await msg.edit_text(text, reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def _refresh_preview(msg, job: dict, text: str, markup):
    path = await asyncio.to_thread(
        render_preview, job["frame"], job["style"],
        os.path.join(job["dir"], f"preview{next(_file_ids)}.jpg"), job["dir"])
    with open(path, "rb") as f:
        data = f.read()
    os.remove(path)
    try:
        await msg.edit_media(InputMediaPhoto(data, caption=text), reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


def set_option(st: SubtitleStyle, key: str, val: str) -> SubtitleStyle:
    """Devuelve el estilo con la opción aplicada (valida contra los catálogos)."""
    if key == "preset" and val in PRESETS:
        return apply_preset(st, val)
    tables = {"font": FONTS, "color": COLORS, "outline": OUTLINES, "outline_color": COLORS,
              "bg": BGS, "size": SIZES, "target": LANGUAGES, "anim": ANIMS,
              "highlight": HIGHLIGHTS, "hl_color": COLORS, "kw_color": COLORS,
              "spacing": SPACINGS, "progress": PROGRESS, "tone": TONES, "dub": DUBS,
              "orig_vol": ORIG_VOLS}
    value = None
    if key in tables and val in tables[key]:
        value = val
    elif key == "words" and val.isdigit() and int(val) in WORDS:
        value = int(val)
    elif key == "pos":
        v, _, h = val.partition("-")
        if (v, h) in ALIGN:
            return replace(st, align_v=v, align_h=h)
    elif key in ("bold", "italic", "upper", "shadow", "bilingual", "neon",
                 "keywords", "censor"):
        value = val == "1"
    if value is None:
        return st
    # los ajustes de aspecto convierten el preset en "personalizado"
    extra = {"preset": "custom"} if key in LOOK_FIELDS else {}
    return replace(st, **{key: value}, **extra)


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------

async def authorized(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    users = ctx.application.bot_data["allowed"]
    user = update.effective_user
    uid = user.id if user else 0
    uname = (user.username or "").lower() if user else ""
    if users and uid not in users and uname not in users:
        if update.effective_message:
            await update.effective_message.reply_text(
                f"⛔ No autorizado. Tu id es {uid}; pídele al dueño que lo agregue.")
        return False
    return True


HELP = (
    "👋 Envíame un video y lo devuelvo con la traducción superpuesta; el idioma "
    "original se detecta solo.\n\n"
    "Antes de procesar verás una vista previa y podrás elegir:\n"
    "• 13 estilos listos (Hormozi, Beast, Karaoke, Neón…) o crear el tuyo\n"
    "• fuente, color, contorno, caja, posición 5×5, tamaño, espaciado\n"
    "• karaoke (palabra activa), 1-5 palabras por pantalla, animaciones\n"
    "• palabras clave en color, neón, barra de progreso, censura\n"
    "• tono de traducción, glosario, solo transcribir\n"
    "• doblaje con voz IA\n"
    "• ⭐ plantillas propias\n\n"
    "Después del resultado: ✏️ editar el texto y 🎨 repetir con otro estilo.\n\n"
    "Comandos: /glosario hola=hello · /conservar Nombre · /reiniciar · /ayuda")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP + f"\n\nTu id de Telegram: {update.effective_user.id}")


async def cmd_glossary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update, ctx):
        return
    gl: dict = ctx.user_data.setdefault("glossary", {})
    parts = (update.message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        body = "\n".join(f"• {k} → {v}" + (" (no traducir)" if k.lower() == v.lower() else "")
                         for k, v in gl.items()) or "(vacío)"
        await update.message.reply_text(
            f"📖 Tu glosario:\n{body}\n\n" + GLOSSARY_HELP)
    elif arg.lower() == "limpiar":
        gl.clear()
        await update.message.reply_text("Glosario vacío.")
    elif arg.lower().startswith("borrar "):
        key = arg[7:].strip()
        found = [k for k in gl if k.lower() == key.lower()]
        for k in found:
            del gl[k]
        await update.message.reply_text("Borrado." if found else "No estaba en el glosario.")
    elif "=" in arg:
        src, _, dst = arg.partition("=")
        if src.strip() and dst.strip() and len(gl) < 100:
            gl[src.strip()] = dst.strip()
            await update.message.reply_text(f"✅ {src.strip()} → {dst.strip()}")
        else:
            await update.message.reply_text("Formato: /glosario origen=destino")
    else:
        await update.message.reply_text("Formato: /glosario origen=destino")


async def cmd_keep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update, ctx):
        return
    parts = (update.message.text or "").split(maxsplit=1)
    term = parts[1].strip() if len(parts) > 1 else ""
    if not term:
        await update.message.reply_text("Uso: /conservar Nombre (no se traducirá)")
        return
    ctx.user_data.setdefault("glossary", {})[term] = term
    await update.message.reply_text(f"✅ «{term}» no se traducirá.")


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update, ctx):
        return
    ctx.user_data.pop("style", None)
    await update.message.reply_text("Estilo guardado reiniciado a los valores base.")


async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update, ctx):
        return
    msg = update.message
    media = msg.video or msg.video_note or msg.document or msg.animation
    if media is None or (msg.document and not (msg.document.mime_type or "")
                         .startswith("video/")):
        return
    local_api = bool(ctx.application.bot_data.get("local_api"))
    if (media.file_size or 0) > CLOUD_DOWNLOAD_LIMIT and not local_api:
        await msg.reply_text(
            "⚠️ Telegram solo permite a los bots descargar videos de hasta 20 MB. "
            "Envía uno más corto/comprimido, o configura TELEGRAM_API_URL con un "
            "servidor local de Bot API para archivos grandes.")
        return

    jobs = ctx.application.bot_data.setdefault("jobs", {})
    _purge_jobs(jobs)
    status = await msg.reply_text("⏳ Descargando video y preparando vista previa…")
    workdir = tempfile.mkdtemp(prefix="trad_")
    try:
        name = getattr(media, "file_name", None) or "video.mp4"
        src = os.path.join(workdir, "entrada" + (os.path.splitext(name)[1] or ".mp4"))
        await (await ctx.bot.get_file(media.file_id)).download_to_drive(src)
        info = await asyncio.to_thread(probe, src)
        frame = await asyncio.to_thread(
            extract_frame, src, os.path.join(workdir, "frame.jpg"), info["duration"])

        job_id = next(_job_ids)
        st = replace(ctx.user_data.get("style") or SubtitleStyle())
        job = dict(user=update.effective_user.id, style=st, dir=workdir,
                   video=src, frame=frame, info=info, tr=None, busy=False,
                   created=time.time(), lock=asyncio.Lock())
        jobs[job_id] = job
        preview = await asyncio.to_thread(
            render_preview, frame, st, os.path.join(workdir, "preview0.jpg"), workdir)
        with open(preview, "rb") as f:
            await msg.reply_photo(f, caption=menu_text(st),
                                  reply_markup=keyboard(job_id, st))
        await status.delete()
    except Exception as e:  # noqa: BLE001
        log.exception("fallo preparando video")
        shutil.rmtree(workdir, ignore_errors=True)
        await status.edit_text(f"❌ No pude preparar el video: {str(e)[:400]}")


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|")
    jobs = ctx.application.bot_data.get("jobs", {})
    job = jobs.get(int(parts[1])) if len(parts) > 1 else None
    if job is None:
        await q.answer("Este menú expiró; reenvía el video.", show_alert=True)
        return
    if job["user"] != q.from_user.id:
        await q.answer("Este video es de otra persona.", show_alert=True)
        return
    job_id, st, kind = int(parts[1]), job["style"], parts[0]
    templates = ctx.user_data.get("templates", [])

    if kind == "n":                       # encabezados del menú
        await q.answer()
        return
    if kind == "h":
        await q.answer(GLOSSARY_HELP[:195], show_alert=True)
        return
    if kind == "x":
        await q.answer()
        _drop_job(jobs, job_id)
        await _set_status(q.message, "Cancelado.")
        return
    if job["busy"]:
        await q.answer("Procesando, espera un momento…")
        return

    if kind == "p":                       # cambiar de página del menú
        await q.answer()
        await q.message.edit_reply_markup(keyboard(job_id, st, parts[2], templates))
        return

    if kind == "o":                       # cambiar una opción + nueva vista previa
        key, val, page = parts[2], parts[3], parts[4]
        async with job["lock"]:
            job["style"] = st = set_option(st, key, val)
            ctx.user_data["style"] = replace(st)
            await q.answer("Actualizando vista previa…")
            await _refresh_preview(q.message, job, menu_text(st),
                                   keyboard(job_id, st, page, templates))
        return

    if kind == "t":                       # plantillas propias
        action = parts[2]
        tpls = ctx.user_data.setdefault("templates", [])
        if action == "save":
            if len(tpls) >= MAX_TEMPLATES:
                await q.answer(f"Máximo {MAX_TEMPLATES} plantillas; borra alguna.", show_alert=True)
                return
            n = 1
            while any(name == f"Mi estilo {n}" for name, _ in tpls):
                n += 1
            tpls.append((f"Mi estilo {n}", {f: getattr(st, f) for f in TEMPLATE_FIELDS}))
            await q.answer(f"Guardado como «Mi estilo {n}»")
        elif action == "del" and parts[3].isdigit() and int(parts[3]) < len(tpls):
            tpls.pop(int(parts[3]))
            await q.answer("Borrada")
        elif action == "apply" and parts[3].isdigit() and int(parts[3]) < len(tpls):
            async with job["lock"]:
                job["style"] = st = replace(st, preset="custom", **tpls[int(parts[3])][1])
                ctx.user_data["style"] = replace(st)
                await q.answer("Aplicando…")
                await _refresh_preview(q.message, job, menu_text(st),
                                       keyboard(job_id, st, "tpl", tpls))
            return
        await q.message.edit_reply_markup(keyboard(job_id, st, "tpl", tpls))
        return

    if kind == "re":                      # repetir con otro estilo (sin re-transcribir)
        await q.answer()
        path = await asyncio.to_thread(
            render_preview, job["frame"], st,
            os.path.join(job["dir"], f"preview{next(_file_ids)}.jpg"), job["dir"])
        with open(path, "rb") as f:
            await q.message.reply_photo(f, caption=menu_text(st),
                                        reply_markup=keyboard(job_id, st, "main", templates))
        os.remove(path)
        return

    if kind == "ed":                      # editar el texto traducido
        if job["tr"] is None or not any(s.translation for s in job["tr"].segments):
            await q.answer("No hay traducción para editar.", show_alert=True)
            return
        await q.answer()
        path = os.path.join(job["dir"], "traduccion_editable.srt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_srt(job["tr"].segments))
        with open(path, "rb") as f:
            sent = await q.message.reply_document(
                f, filename="traduccion.srt",
                caption="✏️ Edita SOLO el texto (no agregues ni borres bloques, no "
                        "cambies números ni tiempos) y respóndeme a este mensaje con "
                        "el archivo. Rehago el video con tu texto y el estilo actual.")
        ctx.application.bot_data.setdefault("edit_msgs", {})[sent.message_id] = job_id
        return

    if kind == "go":
        job["busy"] = True
        await q.answer("Procesando…")
        await _set_status(q.message, "⏳ En cola…")
        try:
            async with ctx.application.bot_data["sem"]:   # un video pesado a la vez
                await run_job(ctx, q.message, job_id, job,
                              glossary=dict(ctx.user_data.get("glossary", {})))
        finally:
            job["busy"] = False


async def on_srt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """El usuario devuelve un .srt editado: se rehace el video con ese texto."""
    if not await authorized(update, ctx):
        return
    msg = update.message
    jobs = ctx.application.bot_data.get("jobs", {})
    job_id = None
    if msg.reply_to_message:
        job_id = ctx.application.bot_data.get("edit_msgs", {}).get(msg.reply_to_message.message_id)
    if job_id is None:   # sin responder: el video más reciente del usuario
        mine = [j for j, v in jobs.items() if v["user"] == update.effective_user.id and v["tr"]]
        job_id = max(mine) if mine else None
    job = jobs.get(job_id)
    if job is None or job["tr"] is None:
        await msg.reply_text("No encuentro el video de ese .srt (caduca a las 2 h). "
                             "Envía el video de nuevo.")
        return
    if job["busy"]:
        await msg.reply_text("Ese video se está procesando; espera un momento.")
        return
    try:
        data = await (await ctx.bot.get_file(msg.document.file_id)).download_as_bytearray()
        apply_edited_srt(job["tr"], bytes(data).decode("utf-8-sig", errors="replace"))
    except ValueError as e:
        await msg.reply_text(f"⚠️ {e}")
        return
    job["busy"] = True
    status = await msg.reply_text("✏️ Aplicando tu edición…")
    try:
        async with ctx.application.bot_data["sem"]:
            await run_job(ctx, status, job_id, job, menu=False,
                          glossary=dict(ctx.user_data.get("glossary", {})))
    finally:
        job["busy"] = False


async def run_job(ctx: ContextTypes.DEFAULT_TYPE, msg, job_id: int, job: dict,
                  menu: bool = True, glossary: dict | None = None):
    bot, chat_id = ctx.bot, msg.chat_id
    loop = asyncio.get_running_loop()
    st: SubtitleStyle = job["style"]
    last = {"txt": ""}

    def progress(text: str):               # se llama desde el hilo de trabajo
        if text != last["txt"]:
            last["txt"] = text
            asyncio.run_coroutine_threadsafe(_set_status(msg, text), loop)

    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        if job["tr"] is None:              # solo se transcribe una vez por video
            job["tr"] = await asyncio.to_thread(
                transcribe_video, job["video"], job["dir"],
                ctx.application.bot_data["dg_key"], progress)
        result = await asyncio.to_thread(
            render_video, job["video"], job["tr"], st, job["dir"],
            ctx.application.bot_data["groq_key"] or None, SEND_LIMIT_MB, progress,
            glossary or {})

        await _set_status(msg, "📤 Subiendo resultado…")
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
        langs = ", ".join(LANGUAGES.get(l, l) for l in result.languages) or "?"
        caption = f"✅ Listo. Idioma(s) detectado(s): {langs} → {LANGUAGES[st.target]}"
        if result.warnings:
            caption += "\n⚠️ " + " ".join(result.warnings)
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎨 Cambiar estilo y repetir", callback_data=f"re|{job_id}"),
            InlineKeyboardButton("✏️ Editar texto", callback_data=f"ed|{job_id}")]])
        with open(result.video_path, "rb") as f:
            await bot.send_video(chat_id, f, caption=caption[:1000],
                                 supports_streaming=True, read_timeout=300,
                                 write_timeout=300, connect_timeout=30,
                                 reply_markup=buttons)
        srt = os.path.join(job["dir"], "traduccion.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write(result.srt_text)
        with open(srt, "rb") as f:
            await bot.send_document(chat_id, f, caption="Subtítulos (.srt)")
        await msg.delete()
        job["created"] = time.time()       # el video queda 2 h para editar / repetir
    except SameLanguageError as e:
        names = ", ".join(LANGUAGES.get(l, l) for l in e.langs)
        note = (f"ℹ️ El video está en {names}, igual que el idioma destino: no hay "
                "nada que traducir. Elige otro idioma en 🌐 Idioma (o «Solo "
                "transcribir») y vuelve a Confirmar.")
        if menu:
            await _set_status(msg, menu_text(st, note), keyboard(job_id, st, "lang"))
        else:
            await _set_status(msg, note)
    except Exception as e:  # noqa: BLE001 — se informa al usuario, el menú sigue
        log.exception("fallo procesando video")
        if menu:
            await _set_status(msg, menu_text(st, f"❌ Error: {str(e)[:450]}"),
                              keyboard(job_id, st))
        else:
            await _set_status(msg, f"❌ Error: {str(e)[:450]}")


def build_app() -> Application:
    token = _cfg("TELEGRAM_BOT_TOKEN", "telegram_bot_token")
    dg_key = _cfg("DEEPGRAM_API_KEY", "deepgram_api_key")
    if not token:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN (créalo con @BotFather).")
    if not dg_key or dg_key.startswith("TU_API_KEY"):
        raise SystemExit("Falta DEEPGRAM_API_KEY.")
    api_url = os.environ.get("TELEGRAM_API_URL", "").strip()

    # estilo, plantillas y glosario de cada usuario sobreviven a los reinicios
    persistence = PicklePersistence(
        filepath=os.path.join(APP_DIR, "bot_state.pickle"), update_interval=30,
        store_data=PersistenceInput(bot_data=False, chat_data=False, callback_data=False))
    b = Application.builder().token(token).persistence(persistence)
    if api_url:
        b = b.base_url(api_url.rstrip("/") + "/bot").base_file_url(
            api_url.rstrip("/") + "/file/bot").local_mode(True)
    app = b.build()
    app.bot_data.update(dg_key=dg_key, groq_key=_cfg("GROQ_API_KEY", "groq_api_key"),
                        allowed=allowed_users(), sem=asyncio.Semaphore(1),
                        local_api=bool(api_url))
    if not app.bot_data["allowed"]:
        log.warning("ALLOWED_USERS vacío: cualquiera que encuentre el bot "
                    "gastará tu API de Deepgram. Usa /start para ver tu id.")

    app.add_handler(CommandHandler(["start", "id", "ayuda", "help"], cmd_start))
    app.add_handler(CommandHandler("glosario", cmd_glossary))
    app.add_handler(CommandHandler("conservar", cmd_keep))
    app.add_handler(CommandHandler("reiniciar", cmd_reset))
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.VIDEO_NOTE | filters.ANIMATION |
        filters.Document.VIDEO, on_video))
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("srt") | filters.Document.MimeType("application/x-subrip"),
        on_srt))
    app.add_handler(CallbackQueryHandler(on_button))
    if app.job_queue:     # limpieza automática de videos temporales
        app.job_queue.run_repeating(_purge_task, interval=600, first=30)
    _purge_orphans()
    return app


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_app().run_polling()


if __name__ == "__main__":
    main()

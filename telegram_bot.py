"""
Bot de Telegram: traduce videos y devuelve el video con subtítulos incrustados.

Flujo:
  1. Envías un video (o archivo de video) al bot.
  2. Te muestra una VISTA PREVIA real (un fotograma de tu video con
     subtítulos de ejemplo) y un menú: estilos listos, fuente, color,
     contorno/fondo, posición (9 puntos), tamaño, extras e idioma destino.
     La vista previa se actualiza con cada cambio.
  3. Pulsas ✅ Confirmar → extrae audio, Deepgram detecta idioma y
     transcribe, se traduce y se superpone la traducción SIN quitar nada
     del video ni del audio original.

Configuración (variables de entorno o config.json junto al script):
  TELEGRAM_BOT_TOKEN  / "telegram_bot_token"   (de @BotFather)
  DEEPGRAM_API_KEY    / "deepgram_api_key"
  GROQ_API_KEY        / "groq_api_key"         (opcional, traducción mejor)
  ALLOWED_USERS       / "telegram_allowed_users"  ids o @usuarios separados por coma
  TELEGRAM_API_URL    (opcional) servidor local de Bot API → videos de hasta 2 GB
"""
from __future__ import annotations

import asyncio
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
                          ContextTypes, MessageHandler, filters)

from video_translator import (ALIGN, BGS, COLORS, FONTS, LANGUAGES, OUTLINES,
                              PRESETS, SIZES, SameLanguageError, SubtitleStyle,
                              apply_preset, extract_frame, probe,
                              render_preview, render_video, transcribe_video)

log = logging.getLogger("traductor-bot")

CLOUD_DOWNLOAD_LIMIT = 20 * 1024 * 1024   # límite de getFile en la Bot API pública
SEND_LIMIT_MB = 49                        # límite de subida de bots: 50 MB
JOB_TTL = 2 * 3600                        # un menú sin confirmar caduca a las 2 h
_job_ids = itertools.count(1)
_file_ids = itertools.count(1)


# --------------------------------------------------------------------------
# configuración
# --------------------------------------------------------------------------

def _cfg(env: str, key: str, default: str = "") -> str:
    val = os.environ.get(env, "").strip()
    if val:
        return val
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(path, encoding="utf-8") as f:
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
POS_LABEL = {("top", "left"): "↖️", ("top", "center"): "⬆️", ("top", "right"): "↗️",
             ("middle", "left"): "⬅️", ("middle", "center"): "⏺",
             ("middle", "right"): "➡️", ("bottom", "left"): "↙️",
             ("bottom", "center"): "⬇️", ("bottom", "right"): "↘️"}
PAGES = {"font": "🔤 Fuente", "color": "🎨 Color", "outline": "✏️ Contorno y fondo",
         "pos": "📍 Posición", "size": "🔠 Tamaño", "extras": "⚙️ Extras",
         "lang": "🌐 Idioma"}


def _chunk(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def keyboard(job_id: int, st: SubtitleStyle, page: str = "main") -> InlineKeyboardMarkup:
    def opt(label, key, val, selected=False):
        return InlineKeyboardButton(("✓ " if selected else "") + label,
                                    callback_data=f"o|{job_id}|{key}|{val}|{page}")

    def header(text):
        return [[InlineKeyboardButton(f"— {text} —", callback_data=f"n|{job_id}")]]

    rows: list[list[InlineKeyboardButton]] = []
    if page == "main":
        rows += _chunk([opt(p["label"], "preset", k, st.preset == k)
                        for k, p in PRESETS.items()], 2)
        names = list(PAGES)
        rows += _chunk([InlineKeyboardButton(PAGES[k], callback_data=f"p|{job_id}|{k}")
                        for k in names[:6]], 2)
        rows.append([InlineKeyboardButton(PAGES["lang"], callback_data=f"p|{job_id}|lang")])
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
        rows += _chunk([opt(COLOR_EMOJI[k], "outline_color", k, st.outline_color == k)
                        for k in COLORS], 5)
        rows += header("Fondo (con caja no hay contorno)")
        rows.append([opt(l, "bg", k, st.bg == k) for k, (l, _) in BGS.items()])
    elif page == "pos":
        rows += header("Posición en pantalla")
        for v in ("top", "middle", "bottom"):
            rows.append([opt(POS_LABEL[(v, h)], "pos", f"{v}-{h}",
                             (st.align_v, st.align_h) == (v, h))
                         for h in ("left", "center", "right")])
    elif page == "size":
        rows.append([opt(l, "size", k, st.size == k) for k, (l, _) in SIZES.items()])
    elif page == "extras":
        def tog(label, key):
            on = getattr(st, key)
            return [opt(f"{label}: {'Sí' if on else 'No'}", key, "0" if on else "1")]
        rows += [tog("𝗡 Negrita", "bold"), tog("𝘐 Cursiva", "italic"),
                 tog("AA MAYÚSCULAS", "upper"), tog("🌑 Sombra", "shadow"),
                 tog("📝 Mostrar texto original debajo", "bilingual")]
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
    pos = {"top": "arriba", "middle": "centro", "bottom": "abajo"}[st.align_v]
    hor = {"left": "izquierda", "center": "centrado", "right": "derecha"}[st.align_h]
    extras = [x for x, on in (("negrita", st.bold), ("cursiva", st.italic),
                              ("mayúsculas", st.upper), ("sombra", st.shadow),
                              ("con texto original", st.bilingual)) if on]
    text = (
        "👆 Así se verá (vista previa). Ajusta y pulsa ✅ Confirmar.\n\n"
        f"🌐 Traducir a: {LANGUAGES[st.target]} (el idioma original se detecta solo)\n"
        f"🔤 {FONTS[st.font][0]} · 🎨 {COLORS[st.color][0]} · "
        f"✏️ contorno {OUTLINES[st.outline][0].lower()} · {BGS[st.bg][0].lower()}\n"
        f"📍 {pos}, {hor} · 🔠 tamaño {SIZES[st.size][0]}"
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


def _drop_job(jobs: dict, job_id: int):
    job = jobs.pop(job_id, None)
    if job:
        shutil.rmtree(job["dir"], ignore_errors=True)


async def _edit_caption(msg, text: str, markup=None):
    try:
        await msg.edit_caption(caption=text, reply_markup=markup)
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
    tables = {"font": FONTS, "color": COLORS, "outline": OUTLINES,
              "outline_color": COLORS, "bg": BGS, "size": SIZES,
              "target": LANGUAGES}
    if key in tables and val in tables[key]:
        # el idioma no cambia el estilo visual: conserva el preset
        extra = {} if key == "target" else {"preset": "custom"}
        return replace(st, **{key: val}, **extra)
    if key == "pos":
        v, _, h = val.partition("-")
        if (v, h) in ALIGN:
            return replace(st, align_v=v, align_h=h, preset="custom")
    if key in ("bold", "italic", "upper", "shadow", "bilingual"):
        return replace(st, **{key: val == "1"}, preset="custom")
    return st


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


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Envíame un video y lo devuelvo con la traducción superpuesta.\n"
        "Detecto el idioma solo. Antes de procesar verás una vista previa y "
        "podrás elegir estilo, fuente, color, contorno, posición, tamaño e "
        "idioma destino.\n\n"
        f"Tu id de Telegram: {update.effective_user.id}")


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

    if kind == "n":                       # encabezados del menú
        await q.answer()
        return

    if kind == "x":
        await q.answer()
        _drop_job(jobs, job_id)
        await _edit_caption(q.message, "Cancelado.")
        return

    if job["busy"]:
        await q.answer("Procesando, espera un momento…")
        return

    if kind == "p":                       # cambiar de página del menú
        await q.answer()
        await q.message.edit_reply_markup(keyboard(job_id, st, parts[2]))
        return

    if kind == "o":                       # cambiar una opción + nueva vista previa
        key, val, page = parts[2], parts[3], parts[4]
        async with job["lock"]:
            job["style"] = st = set_option(st, key, val)
            ctx.user_data["style"] = replace(st)
            await q.answer("Actualizando vista previa…")
            await _refresh_preview(q.message, job, menu_text(st),
                                   keyboard(job_id, st, page))
        return

    if kind == "go":
        job["busy"] = True
        await q.answer("Procesando…")
        await _edit_caption(q.message, "⏳ En cola…")
        try:
            async with ctx.application.bot_data["sem"]:   # un video pesado a la vez
                await run_job(ctx, q.message, job_id, job)
        finally:
            job["busy"] = False


async def run_job(ctx: ContextTypes.DEFAULT_TYPE, msg, job_id: int, job: dict):
    bot, chat_id = ctx.bot, msg.chat_id
    jobs = ctx.application.bot_data["jobs"]
    loop = asyncio.get_running_loop()
    st: SubtitleStyle = job["style"]
    last = {"txt": ""}

    def progress(text: str):               # se llama desde el hilo de trabajo
        if text != last["txt"]:
            last["txt"] = text
            asyncio.run_coroutine_threadsafe(_edit_caption(msg, text), loop)

    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        if job["tr"] is None:              # solo se transcribe una vez por video
            job["tr"] = await asyncio.to_thread(
                transcribe_video, job["video"], job["dir"],
                ctx.application.bot_data["dg_key"], progress)
        result = await asyncio.to_thread(
            render_video, job["video"], job["tr"], st, job["dir"],
            ctx.application.bot_data["groq_key"] or None, SEND_LIMIT_MB, progress)

        await _edit_caption(msg, "📤 Subiendo resultado…")
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
        langs = ", ".join(LANGUAGES.get(l, l) for l in result.languages) or "?"
        caption = (f"✅ Listo. Idioma(s) detectado(s): {langs} → {LANGUAGES[st.target]}")
        if result.warnings:
            caption += "\n⚠️ " + " ".join(result.warnings)
        with open(result.video_path, "rb") as f:
            await bot.send_video(chat_id, f, caption=caption[:1000],
                                 supports_streaming=True, read_timeout=300,
                                 write_timeout=300, connect_timeout=30)
        srt = os.path.join(job["dir"], "traduccion.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write(result.srt_text)
        with open(srt, "rb") as f:
            await bot.send_document(chat_id, f, caption="Subtítulos (.srt)")
        await msg.delete()
        _drop_job(jobs, job_id)
    except SameLanguageError as e:
        names = ", ".join(LANGUAGES.get(l, l) for l in e.langs)
        await _edit_caption(
            msg, menu_text(st, f"ℹ️ El video está en {names}, igual que el idioma "
                               "destino: no hay nada que traducir. Elige otro "
                               "idioma en 🌐 Idioma y vuelve a Confirmar."),
            keyboard(job_id, st, "lang"))
    except Exception as e:  # noqa: BLE001 — se informa al usuario, el menú sigue
        log.exception("fallo procesando video")
        await _edit_caption(msg, menu_text(st, f"❌ Error: {str(e)[:450]}"),
                            keyboard(job_id, st))


def build_app() -> Application:
    token = _cfg("TELEGRAM_BOT_TOKEN", "telegram_bot_token")
    dg_key = _cfg("DEEPGRAM_API_KEY", "deepgram_api_key")
    if not token:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN (créalo con @BotFather).")
    if not dg_key or dg_key.startswith("TU_API_KEY"):
        raise SystemExit("Falta DEEPGRAM_API_KEY.")
    api_url = os.environ.get("TELEGRAM_API_URL", "").strip()

    b = Application.builder().token(token)
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

    app.add_handler(CommandHandler(["start", "id"], cmd_start))
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.VIDEO_NOTE | filters.ANIMATION |
        filters.Document.VIDEO, on_video))
    app.add_handler(CallbackQueryHandler(on_button))
    return app


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_app().run_polling()


if __name__ == "__main__":
    main()

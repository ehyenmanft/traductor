"""
Bot de Telegram: traduce videos y devuelve el video con subtítulos incrustados.

Flujo:
  1. Envías un video (o archivo de video) al bot.
  2. Te muestra un menú con opciones: estilo, posición, tamaño, idioma
     destino y si quieres también el texto original.
  3. Pulsas ✅ Confirmar → extrae audio, Deepgram detecta idioma y
     transcribe, se traduce con el motor del traductor y se superpone la
     traducción SIN quitar nada del video ni del audio original.

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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from video_translator import (LANGUAGES, PRESETS, SIZES, SubtitleStyle,
                              process_video)

log = logging.getLogger("traductor-bot")

CLOUD_DOWNLOAD_LIMIT = 20 * 1024 * 1024   # límite de getFile en la Bot API pública
SEND_LIMIT_MB = 49                        # límite de subida de bots: 50 MB
_job_ids = itertools.count(1)


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

def _mark(cond: bool, label: str) -> str:
    return f"✓ {label}" if cond else label


def keyboard(job_id: int, st: SubtitleStyle) -> InlineKeyboardMarkup:
    def btn(label, key, val, selected):
        return InlineKeyboardButton(_mark(selected, label),
                                    callback_data=f"o|{job_id}|{key}|{val}")

    presets = [btn(p["label"], "preset", k, st.preset == k)
               for k, p in PRESETS.items()]
    rows = [presets[i:i + 2] for i in range(0, len(presets), 2)]
    rows.append([btn(l, "position", k, st.position == k) for k, l in
                 (("top", "⬆️ Arriba"), ("middle", "⏺ Centro"), ("bottom", "⬇️ Abajo"))])
    rows.append([btn(l, "size", k, st.size == k) for k, l in
                 (("s", "Aa chico"), ("m", "Aa medio"), ("l", "Aa grande"))])
    rows.append([btn("📝 Con texto original: " + ("Sí" if st.bilingual else "No"),
                     "bilingual", "0" if st.bilingual else "1", False)])
    langs = [btn(name, "target", code, st.target == code)
             for code, name in LANGUAGES.items()]
    rows += [langs[i:i + 3] for i in range(0, len(langs), 3)]
    rows.append([InlineKeyboardButton("✅ Confirmar", callback_data=f"go|{job_id}"),
                 InlineKeyboardButton("✖️ Cancelar", callback_data=f"x|{job_id}")])
    return InlineKeyboardMarkup(rows)


def menu_text(st: SubtitleStyle) -> str:
    return (
        "🎬 *Video recibido.* Elige cómo quieres los subtítulos y pulsa *Confirmar*.\n\n"
        f"• Idioma destino: *{LANGUAGES[st.target]}* (el idioma original se detecta solo)\n"
        f"• Estilo: *{PRESETS[st.preset]['label']}*\n"
        f"• Texto original debajo: *{'sí' if st.bilingual else 'no'}*"
    )


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
        "Detecto el idioma solo, y antes de procesar puedes elegir estilo, "
        "posición, tamaño e idioma destino.\n\n"
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
    job_id = next(_job_ids)
    st = SubtitleStyle(target=ctx.user_data.get("target", "es"))
    ctx.application.bot_data.setdefault("jobs", {})[job_id] = dict(
        file_id=media.file_id, style=st, user=update.effective_user.id,
        name=getattr(media, "file_name", None) or "video.mp4", busy=False)
    await msg.reply_text(menu_text(st), parse_mode="Markdown",
                         reply_markup=keyboard(job_id, st), quote=True)


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
    job_id, st = int(parts[1]), job["style"]

    if parts[0] == "x":
        jobs.pop(job_id, None)
        await q.answer()
        await q.edit_message_text("Cancelado.")
        return

    if parts[0] == "o":
        key, val = parts[2], parts[3]
        if key == "bilingual":
            st.bilingual = val == "1"
        elif key == "target":
            st.target = val
            ctx.user_data["target"] = val
        elif key == "preset" and val in PRESETS:
            st.preset = val
        elif key == "position" and val in ("top", "middle", "bottom"):
            st.position = val
        elif key == "size" and val in SIZES:
            st.size = val
        await q.answer()
        await q.edit_message_text(menu_text(st), parse_mode="Markdown",
                                  reply_markup=keyboard(job_id, st))
        return

    if parts[0] == "go":
        if job["busy"]:
            await q.answer("Ya se está procesando.")
            return
        job["busy"] = True
        await q.answer("Procesando…")
        await q.edit_message_text("⏳ En cola…")
        chat_id = q.message.chat_id
        # un solo procesamiento pesado por vez por proceso del bot
        async with ctx.application.bot_data["sem"]:
            try:
                await run_job(ctx, q.message, job)
            finally:
                jobs.pop(job_id, None)


async def run_job(ctx: ContextTypes.DEFAULT_TYPE, status_msg, job: dict):
    bot = ctx.bot
    chat_id = status_msg.chat_id
    loop = asyncio.get_running_loop()
    workdir = tempfile.mkdtemp(prefix="trad_")
    last = {"txt": ""}

    def progress(text: str):
        # llamado desde el hilo de trabajo
        if text != last["txt"]:
            last["txt"] = text
            asyncio.run_coroutine_threadsafe(
                status_msg.edit_text(text), loop)

    try:
        await status_msg.edit_text("⬇️ Descargando video…")
        src = os.path.join(workdir, "entrada" + (
            os.path.splitext(job["name"])[1] or ".mp4"))
        tg_file = await bot.get_file(job["file_id"])
        await tg_file.download_to_drive(src)

        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        result = await asyncio.to_thread(
            process_video, src, job["style"], workdir,
            ctx.application.bot_data["dg_key"],
            ctx.application.bot_data["groq_key"] or None,
            SEND_LIMIT_MB, progress)

        await status_msg.edit_text("📤 Subiendo resultado…")
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
        langs = ", ".join(LANGUAGES.get(l, l) for l in result.languages) or "?"
        caption = (f"✅ Listo. Idioma(s) detectado(s): {langs} → "
                   f"{LANGUAGES[job['style'].target]}")
        with open(result.video_path, "rb") as f:
            await bot.send_video(chat_id, f, caption=caption,
                                 supports_streaming=True, read_timeout=300,
                                 write_timeout=300, connect_timeout=30)
        srt = os.path.join(workdir, "traduccion.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write(result.srt_text)
        with open(srt, "rb") as f:
            await bot.send_document(chat_id, f, caption="Subtítulos (.srt)")
        await status_msg.delete()
    except Exception as e:  # noqa: BLE001 — se informa al usuario
        log.exception("fallo procesando video")
        await status_msg.edit_text(f"❌ Error: {str(e)[:500]}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


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

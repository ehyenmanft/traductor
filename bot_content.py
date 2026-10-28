"""
Botones de creación de contenido del bot (reel 9:16, quitar silencios, clips, carrusel, textos, miniatura).
Se cuelgan del mensaje del video traducido: trabajan sobre `job["result"]` y el texto traducido.
"""
from __future__ import annotations

import asyncio
import os
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ChatAction

import content_tools as ct
from subtitle_ass import build_ass
from video_translator import burn_subtitles, probe

ACTIONS = {   # clave → (etiqueta del botón)
    "reel": "📱 Reel 9:16",
    "reelc": "📱 Reel (recorte)",
    "cuts": "✂️ Sin silencios",
    "clips": "🎞 Clips de 55 s",
    "car": "🖼 Carrusel",
    "caps": "📝 Títulos y #",
    "thumb": "🖼 Miniatura",
}
THEME_LABELS = {"oscuro": "🌙 Oscuro", "claro": "☀️ Claro", "neon": "💠 Neón", "calido": "🔥 Cálido"}
HELP = ("🎬 *Formatos*\n"
        "• *Reel 9:16*: todo el cuadro sobre fondo difuminado (se ven los subtítulos).\n"
        "• *Reel (recorte)*: pantalla completa recortando los lados; los subtítulos se rehacen para vertical.\n"
        "• *Sin silencios*: quita las pausas.\n• *Clips*: parte el video en trozos de hasta 55 s.\n"
        "• *Carrusel*: imágenes 1080×1350 con las ideas del video.\n"
        "• *Títulos y #*: títulos, descripción y hashtags.\n• *Miniatura*: el mejor fotograma.")


def content_rows(job_id: int) -> list[list[InlineKeyboardButton]]:
    def b(k):
        return InlineKeyboardButton(ACTIONS[k], callback_data=f"ct|{job_id}|{k}")
    return [[b("reel"), b("reelc")], [b("cuts"), b("clips")], [b("car"), b("caps"), b("thumb")]]


def theme_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"ct|{job_id}|car:{k}")
                                  for k, label in THEME_LABELS.items()]])


async def _send_video(bot, chat_id: int, path: str, caption: str, bd: dict):
    if os.path.getsize(path) > bd["send_mb"] * 1024 * 1024:       # no cabe en Telegram: se recodifica a su tamaño
        path = await asyncio.to_thread(ct.shrink, path, path + ".small.mp4", bd["send_mb"])
    await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
    info = await asyncio.to_thread(probe, path)
    with open(path, "rb") as f:
        await bot.send_video(chat_id, f, caption=caption[:1000], supports_streaming=True,
                             width=info["width"], height=info["height"], duration=int(info["duration"]),
                             read_timeout=bd["upload_timeout"], write_timeout=bd["upload_timeout"], connect_timeout=30)


async def on_content(q, ctx, job: dict, job_id: int, action: str, progress_cls, set_status):
    """Ejecuta una acción de contenido. `progress_cls` y `set_status` vienen del bot (evita importar en círculo)."""
    bd = ctx.application.bot_data
    src = job.get("result")
    if not src or not os.path.exists(src) or job.get("tr") is None:
        await q.answer("Primero traduce el video (✅ Confirmar).", show_alert=True)
        return
    base, _, arg = action.partition(":")
    if base == "car" and not arg:                      # elegir el tema del carrusel
        await q.answer()
        await q.message.reply_text("🎨 Elige el estilo del carrusel:", reply_markup=theme_keyboard(job_id))
        return
    if base not in ACTIONS:
        await q.answer("Acción desconocida.")
        return
    await q.answer("Trabajando…")
    chat_id, bot, st = q.message.chat_id, ctx.bot, job["style"]
    loop = asyncio.get_running_loop()
    status = await q.message.reply_text(f"⏳ {ACTIONS[base]}…")
    prog = progress_cls(status, loop)
    text_for = lambda msg: (lambda f: prog(100 * f, msg))   # noqa: E731
    out_dir = job["dir"]
    n = int(time.time())
    job["busy"] = True
    try:
        async with bd["sem"]:
            if base == "reel":
                out = await asyncio.to_thread(ct.reel_blur, src, os.path.join(out_dir, f"reel_{n}.mp4"),
                                              text_for("📱 Creando el reel…"))
                await _send_video(bot, chat_id, out, "📱 Reel 9:16 listo (fondo difuminado).", bd)
            elif base == "reelc":
                cropped = await asyncio.to_thread(ct.reel_crop, job["video"], os.path.join(out_dir, f"crop_{n}.mp4"),
                                                  0.5, text_for("📱 Recortando a vertical…"))
                ass = build_ass(job["tr"].segments, st, 1080, 1920, job["tr"].info["duration"])
                dub = job["tr"].dub_cache[1] if (st.dub != "off" and job["tr"].dub_cache
                                                 and os.path.exists(job["tr"].dub_cache[1])) else None
                out = await asyncio.to_thread(burn_subtitles, cropped, ass, os.path.join(out_dir, f"reelc_{n}.mp4"),
                                              out_dir, job["tr"].info["duration"], bd["send_mb"], dub, 0.25,
                                              text_for("🎬 Incrustando subtítulos en vertical…"))
                await _send_video(bot, chat_id, out, "📱 Reel 9:16 a pantalla completa (recortado).", bd)
            elif base == "cuts":
                res = await asyncio.to_thread(ct.jump_cuts, src, os.path.join(out_dir, f"cortes_{n}.mp4"),
                                              -32.0, 0.55, 0.08, text_for("✂️ Quitando silencios…"))
                if res.path is None:
                    await status.edit_text("ℹ️ " + res.note)
                    return
                await _send_video(bot, chat_id, res.path,
                                  f"✂️ Quité {res.removed:.0f} s de silencios (de {res.kept:.0f} s a "
                                  f"{res.kept - res.removed:.0f} s).", bd)
            elif base == "clips":
                clip_dir = os.path.join(out_dir, f"clips_{n}")
                os.makedirs(clip_dir)
                paths = await asyncio.to_thread(ct.split_clips, src, clip_dir, 55.0, 10, text_for("🎞 Cortando clips…"))
                for i, p in enumerate(paths, 1):
                    await _send_video(bot, chat_id, p, f"🎞 Clip {i}/{len(paths)}", bd)
            elif base == "car":
                text = ct.transcript_text(job["tr"].segments)
                slides, note = await asyncio.to_thread(ct.carousel_slides, text, bd["groq_key"] or None, st.target
                                                       if st.target != "orig" else "es", 6)
                paths = await asyncio.to_thread(ct.render_carousel, slides, out_dir, arg or "oscuro",
                                                bd.get("handle", ""))
                await bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
                opened = [open(p, "rb") for p in paths]
                try:
                    await bot.send_media_group(chat_id, [InputMediaPhoto(f) for f in opened[:10]])
                finally:
                    for f in opened:
                        f.close()
                if note:
                    await q.message.reply_text("ℹ️ " + note)
            elif base == "caps":
                text = ct.transcript_text(job["tr"].segments)
                lang = st.target if st.target != "orig" else "es"
                msg = await asyncio.to_thread(ct.caption_pack, text, bd["groq_key"] or None, lang)
                await q.message.reply_text(msg[:4000])
            elif base == "thumb":
                title = ""
                if bd["groq_key"]:
                    try:
                        data = await asyncio.to_thread(
                            ct._json_chat, bd["groq_key"],
                            "Write ONE catchy thumbnail title, max 5 words, in the language of the text. "
                            "Return JSON {\"title\": \"...\"}.", ct._clip_text(ct.transcript_text(job["tr"].segments), 3000), 200)
                        title = str(data.get("title", ""))[:50]
                    except Exception:  # noqa: BLE001 — sin título también sirve
                        title = ""
                out = await asyncio.to_thread(ct.thumbnail, src, os.path.join(out_dir, f"mini_{n}.jpg"), title)
                with open(out, "rb") as f:
                    await bot.send_photo(chat_id, f, caption="🖼 Miniatura" + (f": {title}" if title else ""))
        await status.delete()
        job["created"] = time.time()                  # alarga la vida del trabajo
    except Exception as e:  # noqa: BLE001 — se informa; el resto de botones siguen
        await set_status(status, f"❌ {ACTIONS.get(base, 'Acción')}: {str(e)[:400]}")
    finally:
        job["busy"] = False

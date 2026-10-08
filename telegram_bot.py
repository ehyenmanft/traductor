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

Comandos: /glosario /conservar /almacenamiento /reiniciar /ayuda

Configuración (variables de entorno o config.json junto al script):
  TELEGRAM_BOT_TOKEN  / "telegram_bot_token"   (de @BotFather)
  DEEPGRAM_API_KEY    / "deepgram_api_key"
  GROQ_API_KEY        / "groq_api_key"         (traducción con tono/contexto)
  ALLOWED_USERS       / "telegram_allowed_users"  ids o @usuarios separados por coma
  TELEGRAM_API_URL    / "telegram_api_url" (opcional) servidor local de Bot API → videos de hasta 2 GB
  También acepta enlaces (Drive, Dropbox, YouTube, archivo directo) de hasta 2 GB / 90 min.
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

from fetch import download_url, extract_url
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
MAX_MINUTES = 90                          # duración máxima aceptada por video
LINK_MAX_MB = 2000                        # tamaño máximo al descargar por enlace
JOB_TTL = 2 * 3600                        # un video/menú sin usar caduca a las 2 h (config: keep_minutes)
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


def bar(pct: float, width: int = 14) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100 * width))
    return "▰" * filled + "▱" * (width - filled) + f" {int(pct)}%"


def _elapsed(t0: float) -> str:
    s = int(time.time() - t0)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


class Progress:
    """Barra con porcentaje; se llama desde hilos de trabajo y limita las
    ediciones de Telegram (~1 cada 2.5 s) para no toparse con el flood control."""

    def __init__(self, msg, loop, markup=None):
        self.msg, self.loop, self.markup = msg, loop, markup
        self.t0 = time.time()
        self.pct, self.text, self.last_t, self.last_pct = 0.0, "", 0.0, -1

    def __call__(self, pct: float, text: str = ""):
        self.pct = max(self.pct, pct)                  # nunca retrocede
        text = text or self.text
        now = time.time()
        changed = int(self.pct) != self.last_pct or text != self.text
        stage_change = text != self.text and now - self.last_t >= 1.0
        self.text = text
        if changed and (now - self.last_t >= 2.5 or stage_change):
            self.last_t, self.last_pct = now, int(self.pct)
            asyncio.run_coroutine_threadsafe(self._send(), self.loop)

    async def _send(self):
        try:
            await _set_status(self.msg, f"{self.text}\n{bar(self.pct)}  ⏱ {_elapsed(self.t0)}",
                              self.markup)
        except Exception:  # noqa: BLE001 — flood control o mensaje borrado: se ignora
            pass


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


LOCAL_MEDIA_DIRS = {"videos", "documents", "animations", "video_notes", "photos",
                    "audios", "voice", "temp"}


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _local_media_files(data_dir: str):
    """Archivos de descargas del servidor local (no toca su base de datos)."""
    for root, _, files in os.walk(data_dir):
        if os.path.basename(root) in LOCAL_MEDIA_DIRS:
            for f in files:
                yield os.path.join(root, f)


def _active_dirs(app) -> set[str]:
    bd = app.bot_data
    return {j["dir"] for j in bd.get("jobs", {}).values()} | set(bd.get("inflight", set()))


def storage_report(app) -> str:
    bd = app.bot_data
    total, used, free = shutil.disk_usage("/")
    pct = 100 * used / total
    active = _active_dirs(app)
    mine = glob.glob(os.path.join(tempfile.gettempdir(), "trad_*"))
    act = [d for d in mine if d in active]
    orph = [d for d in mine if d not in active]
    lines = ["💾 Almacenamiento del servidor",
             f"{bar(pct)}",
             f"Usado {_fmt(used)} de {_fmt(total)} · libres {_fmt(free)}", "",
             f"🗂️ Temporales del bot: {len(mine)} carpeta(s) · {_fmt(sum(map(dir_size, mine)))}",
             f"   • de videos abiertos (editar/repetir): {len(act)} · {_fmt(sum(map(dir_size, act)))}",
             f"   • huérfanas: {len(orph)} · {_fmt(sum(map(dir_size, orph)))}"]
    data_dir = bd.get("tg_data_dir")
    if data_dir and os.path.isdir(data_dir):
        media = sum(os.path.getsize(f) for f in _local_media_files(data_dir) if os.path.exists(f))
        lines.append(f"📥 Servidor local de Telegram: {_fmt(dir_size(data_dir))} "
                     f"(descargas borrables: {_fmt(media)})")
    lines.append(f"\n⏱️ Los videos abiertos caducan solos a los {JOB_TTL // 60} min.")
    return "\n".join(lines)


def clean_storage(app) -> dict:
    """Borra temporales que no estén en uso. Devuelve un resumen."""
    bd = app.bot_data
    jobs: dict = bd.setdefault("jobs", {})
    freed = dirs = files = skipped = 0
    for jid in list(jobs):
        job = jobs[jid]
        if job["busy"]:
            skipped += 1
            continue
        freed += dir_size(job["dir"])
        shutil.rmtree(job["dir"], ignore_errors=True)
        jobs.pop(jid, None)
        dirs += 1
    keep = _active_dirs(app)
    for d in glob.glob(os.path.join(tempfile.gettempdir(), "trad_*")):
        if d not in keep:
            freed += dir_size(d)
            shutil.rmtree(d, ignore_errors=True)
            dirs += 1
    data_dir = bd.get("tg_data_dir")
    if data_dir and os.path.isdir(data_dir):
        for f in list(_local_media_files(data_dir)):
            try:
                freed += os.path.getsize(f)
                os.remove(f)
                files += 1
            except OSError:
                pass
    return dict(freed=freed, dirs=dirs, files=files, skipped=skipped)


def storage_keyboard(confirm: bool = False) -> InlineKeyboardMarkup:
    if confirm:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Sí, borrar", callback_data="s|yes"),
            InlineKeyboardButton("✖️ No", callback_data="s|no")]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🧹 Borrar temporales", callback_data="s|ask"),
        InlineKeyboardButton("🔄 Actualizar", callback_data="s|refresh")]])


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
    "Comandos: /glosario hola=hello · /conservar Nombre · /almacenamiento · /reiniciar · /ayuda")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP + f"\n\nTu id de Telegram: {update.effective_user.id}")


async def cmd_storage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update, ctx):
        return
    text = await asyncio.to_thread(storage_report, ctx.application)
    await update.message.reply_text(text, reply_markup=storage_keyboard())


async def _on_storage_button(q, ctx: ContextTypes.DEFAULT_TYPE, action: str):
    users = ctx.application.bot_data["allowed"]
    uname = (q.from_user.username or "").lower()
    if users and q.from_user.id not in users and uname not in users:
        await q.answer("No autorizado.", show_alert=True)
        return
    app = ctx.application
    if action == "ask":
        await q.answer()
        n = len(app.bot_data.get("jobs", {}))
        await q.message.edit_text(
            f"⚠️ ¿Borrar los temporales?\n\nSe eliminan los videos guardados de {n} menú(s) "
            "abierto(s) (ya no podrás editar/repetir esos videos) y las descargas del servidor "
            "local. No se tocan los videos que se están procesando.\n\n"
            + await asyncio.to_thread(storage_report, app),
            reply_markup=storage_keyboard(confirm=True))
        return
    note = ""
    if action == "yes":
        res = await asyncio.to_thread(clean_storage, app)
        note = (f"🧹 Listo: liberé {_fmt(res['freed'])} ({res['dirs']} carpeta(s), "
                f"{res['files']} archivo(s) del servidor local)."
                + (f" Omití {res['skipped']} en proceso." if res["skipped"] else "") + "\n\n")
    await q.answer("Actualizado" if action != "yes" else "Borrado")
    try:
        await q.message.edit_text(note + await asyncio.to_thread(storage_report, app),
                                  reply_markup=storage_keyboard())
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


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


def _check_disk(need_bytes: int):
    free = shutil.disk_usage(tempfile.gettempdir()).free
    if free < need_bytes:
        raise RuntimeError(f"Poco espacio en el servidor ({free / 1e9:.1f} GB libres, "
                           f"necesito ~{need_bytes / 1e9:.1f} GB). Inténtalo en unos minutos.")


async def _start_job(update: Update, ctx: ContextTypes.DEFAULT_TYPE, fetch, first_text: str,
                     need_bytes: int = 2 * 1024 ** 3):
    """Crea el trabajo: obtiene el video (fetch), valida, saca un fotograma y
    muestra la vista previa con el menú. `fetch(workdir, status)` devuelve la ruta."""
    msg = update.message
    jobs = ctx.application.bot_data.setdefault("jobs", {})
    _purge_jobs(jobs)
    status = await msg.reply_text(first_text)
    workdir = tempfile.mkdtemp(prefix="trad_")
    inflight: set = ctx.application.bot_data.setdefault("inflight", set())
    inflight.add(workdir)          # protegida mientras se descarga/prepara
    try:
        _check_disk(need_bytes)
        src = await fetch(workdir, status)
        info = await asyncio.to_thread(probe, src)
        max_min = ctx.application.bot_data["max_minutes"]
        if info["duration"] > max_min * 60:
            raise RuntimeError(f"El video dura {info['duration'] / 60:.0f} min; el máximo "
                               f"es {max_min} min.")
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
    finally:
        inflight.discard(workdir)


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
            f"⚠️ Ese video pesa {(media.file_size or 0) / 1e6:.0f} MB y Telegram solo deja a "
            "los bots descargar hasta 20 MB.\n\n"
            "✅ Súbelo a Google Drive, Dropbox o YouTube y **pégame el enlace** en este chat: "
            "lo descargo yo mismo (hasta 2 GB).\n"
            "(El dueño del bot también puede activar el servidor local de Telegram para "
            "recibir videos grandes directamente.)")
        return

    async def fetch(workdir: str, status) -> str:
        name = getattr(media, "file_name", None) or "video.mp4"
        src = os.path.join(workdir, "entrada" + (os.path.splitext(name)[1] or ".mp4"))
        tg = await ctx.bot.get_file(media.file_id)
        local_path = tg.file_path or ""
        if local_api and os.path.isabs(local_path) and os.path.exists(local_path):
            try:                       # el servidor local ya lo tiene: se mueve, no se copia
                shutil.move(local_path, src)
                return src
            except OSError:
                pass
        await tg.download_to_drive(src)
        if local_api and os.path.isabs(local_path):
            try:
                os.remove(local_path)  # libera el espacio del servidor local
            except OSError:
                pass
        return src

    await _start_job(update, ctx, fetch, "⏳ Descargando video y preparando vista previa…",
                     need_bytes=int((media.file_size or 0) * 4) + 1024 ** 3)


async def on_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Un enlace pegado en el chat: el bot descarga el video por su cuenta."""
    url = extract_url(update.message.text or "")
    if not url or not await authorized(update, ctx):
        return
    loop = asyncio.get_running_loop()

    async def fetch(workdir: str, status) -> str:
        prog = Progress(status, loop)
        prog.text = "⬇️ Descargando el enlace…"
        allow_private = bool(os.environ.get("ALLOW_PRIVATE_LINKS"))
        return await asyncio.to_thread(
            download_url, url, workdir, lambda f: prog(100 * f, "⬇️ Descargando el enlace…"),
            LINK_MAX_MB, allow_private)

    await _start_job(update, ctx, fetch, "🔗 Enlace recibido, descargando…",
                     need_bytes=3 * 1024 ** 3)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|")
    if parts[0] == "s":                   # panel de almacenamiento (no ligado a un video)
        await _on_storage_button(q, ctx, parts[1])
        return
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
        tg = await ctx.bot.get_file(msg.document.file_id)
        data = await tg.download_as_bytearray()
        if ctx.application.bot_data.get("local_api") and os.path.isabs(tg.file_path or ""):
            try:
                os.remove(tg.file_path)        # el servidor local no borra sus descargas
            except OSError:
                pass
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
    progress = Progress(msg, loop)         # se llama desde hilos de trabajo
    bd = ctx.application.bot_data

    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        if job["tr"] is None:              # solo se transcribe una vez por video
            job["tr"] = await asyncio.to_thread(
                transcribe_video, job["video"], job["dir"],
                bd["dg_key"], progress)
        result = await asyncio.to_thread(
            render_video, job["video"], job["tr"], st, job["dir"],
            bd["groq_key"] or None, bd["send_mb"], progress, glossary or {})

        progress.pct = 99
        await _set_status(msg, f"📤 Subiendo resultado…\n{bar(99)}  ⏱ {_elapsed(progress.t0)}")
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
                                 supports_streaming=True, read_timeout=bd["upload_timeout"],
                                 write_timeout=bd["upload_timeout"], connect_timeout=30,
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
    api_url = _cfg("TELEGRAM_API_URL", "telegram_api_url")
    global JOB_TTL
    JOB_TTL = int(float(_cfg("KEEP_MINUTES", "keep_minutes", str(JOB_TTL // 60)) or 120) * 60)

    # estilo, plantillas y glosario de cada usuario sobreviven a los reinicios
    persistence = PicklePersistence(
        filepath=os.path.join(APP_DIR, "bot_state.pickle"), update_interval=30,
        store_data=PersistenceInput(bot_data=False, chat_data=False, callback_data=False))
    async def post_init(application: Application):    # menú "/" de Telegram
        await application.bot.set_my_commands([
            ("start", "Ayuda y tu id"), ("almacenamiento", "Ver espacio y borrar temporales"),
            ("glosario", "Términos fijos de traducción"), ("conservar", "No traducir un término"),
            ("reiniciar", "Restablecer el estilo guardado")])

    b = Application.builder().token(token).persistence(persistence).post_init(post_init)
    if api_url:
        b = b.base_url(api_url.rstrip("/") + "/bot").base_file_url(
            api_url.rstrip("/") + "/file/bot").local_mode(True)
    app = b.build()
    app.bot_data.update(dg_key=dg_key, groq_key=_cfg("GROQ_API_KEY", "groq_api_key"),
                        allowed=allowed_users(), sem=asyncio.Semaphore(1),
                        local_api=bool(api_url),
                        tg_data_dir=(_cfg("TELEGRAM_DATA_DIR", "telegram_data_dir",
                                          "/var/lib/telegram-bot-api") if api_url else ""),
                        # con servidor local el límite de subida es de ~2 GB
                        send_mb=1900 if api_url else SEND_LIMIT_MB,
                        upload_timeout=3600 if api_url else 300,
                        max_minutes=int(_cfg("MAX_MINUTES", "max_minutes", str(MAX_MINUTES)) or MAX_MINUTES))
    if not app.bot_data["allowed"]:
        log.warning("ALLOWED_USERS vacío: cualquiera que encuentre el bot "
                    "gastará tu API de Deepgram. Usa /start para ver tu id.")

    app.add_handler(CommandHandler(["start", "id", "ayuda", "help"], cmd_start))
    app.add_handler(CommandHandler("glosario", cmd_glossary))
    app.add_handler(CommandHandler("conservar", cmd_keep))
    app.add_handler(CommandHandler("reiniciar", cmd_reset))
    app.add_handler(CommandHandler(["almacenamiento", "espacio", "storage"], cmd_storage))
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.VIDEO_NOTE | filters.ANIMATION |
        filters.Document.VIDEO, on_video))
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("srt") | filters.Document.MimeType("application/x-subrip"),
        on_srt))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_link))
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

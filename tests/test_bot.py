import asyncio, os, subprocess, sys, tempfile, time, unittest
from unittest import mock
from unittest.mock import AsyncMock, MagicMock
os.environ.update(TELEGRAM_BOT_TOKEN="123:abc", DEEPGRAM_API_KEY="x")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import telegram_bot as tb
import video_translator as vt
import translate_engine as te
from test_video_translator import FAKE


def make_ctx(job_user=1):
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.application.bot_data = {"jobs": {}, "sem": asyncio.Semaphore(1), "dg_key": "x",
                                "groq_key": "", "allowed": set(), "edit_msgs": {},
                                "send_mb": 49, "upload_timeout": 300, "max_minutes": 90}
    ctx.bot.send_video = AsyncMock(); ctx.bot.send_document = AsyncMock()
    ctx.bot.send_chat_action = AsyncMock()
    return ctx


def make_query(data, user=1):
    q = MagicMock(); q.data = data; q.from_user.id = user
    q.answer = AsyncMock()
    m = q.message; m.photo = [object()]; m.chat_id = 5
    for n in ("edit_caption", "edit_media", "edit_reply_markup", "edit_text", "delete", "reply_photo", "reply_document"):
        setattr(m, n, AsyncMock())
    m.reply_document.return_value.message_id = 99
    return q


class BotFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.src = os.path.join(self.d, "in.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=4", "-f", "lavfi",
                        "-i", "sine=d=4", "-c:v", "libx264", "-c:a", "aac", "-shortest", self.src],
                       check=True, capture_output=True)
        info = vt.probe(self.src)
        frame = vt.extract_frame(self.src, os.path.join(self.d, "frame.jpg"), info["duration"])
        self.ctx = make_ctx()
        tr = vt.Transcription(vt.segments_from_deepgram(FAKE), info)
        self.job = dict(user=1, style=vt.SubtitleStyle(), dir=self.d, video=self.src, frame=frame,
                        info=info, tr=tr, busy=False, created=time.time(), lock=asyncio.Lock())
        self.ctx.application.bot_data["jobs"][1] = self.job

    async def press(self, data):
        q = make_query(data)
        await tb.on_button(MagicMock(callback_query=q), self.ctx)
        return q

    async def test_options_templates_pages(self):
        q = await self.press("o|1|preset|hormozi|main")
        q.message.edit_media.assert_awaited()
        self.assertEqual(self.job["style"].highlight, "color")
        await self.press("o|1|words|2|anim"); self.assertEqual(self.job["style"].words, 2)
        await self.press("o|1|dub|male|dub"); self.assertEqual(self.job["style"].dub, "male")
        q = await self.press("p|1|anim"); q.message.edit_reply_markup.assert_awaited()
        await self.press("t|1|save|"); self.assertEqual(len(self.ctx.user_data["templates"]), 1)
        await self.press("o|1|preset|classic|main"); self.assertEqual(self.job["style"].highlight, "none")
        await self.press("t|1|apply|0"); self.assertEqual(self.job["style"].highlight, "color")
        await self.press("t|1|del|0"); self.assertEqual(self.ctx.user_data["templates"], [])
        q = await self.press("h|1"); self.assertTrue(q.answer.await_args.kwargs["show_alert"])

    async def test_full_job_edit_repeat(self):
        te_patch = mock.patch.object(te, "_google", side_effect=lambda t, s, g: "TRAD " + t)
        with te_patch:
            self.job["style"] = vt.apply_preset(vt.SubtitleStyle(target="fr"), "beast")
            q = await self.press("go|1")
        self.ctx.bot.send_video.assert_awaited()
        kb = self.ctx.bot.send_video.await_args.kwargs["reply_markup"]
        self.assertEqual([b.callback_data for b in kb.inline_keyboard[0]], ["re|1", "ed|1"])
        self.assertTrue(os.path.exists(self.d))                       # el job se conserva
        # editar texto
        q = await self.press("ed|1")
        q.message.reply_document.assert_awaited()
        self.assertEqual(self.ctx.application.bot_data["edit_msgs"][99], 1)
        srt = vt.build_srt(self.job["tr"].segments).replace("TRAD Hello everyone.", "Texto corregido")
        update = MagicMock(); update.effective_user.id = 1
        update.message.reply_to_message.message_id = 99
        update.message.reply_text = AsyncMock(return_value=make_query("x").message)
        self.ctx.bot.get_file = AsyncMock(return_value=MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(srt.encode()))))
        self.ctx.bot.send_video.reset_mock()
        await tb.on_srt(update, self.ctx)
        self.assertEqual(self.job["tr"].segments[0].translation, "Texto corregido")
        self.ctx.bot.send_video.assert_awaited()
        # repetir con otro estilo
        q = await self.press("re|1")
        q.message.reply_photo.assert_awaited()

    async def test_same_language_returns_menu(self):
        self.job["tr"] = vt.Transcription([vt.Segment(0, 1, "hola", "es")], self.job["info"])
        q = await self.press("go|1")
        args = q.message.edit_caption.await_args_list[-1]
        self.assertIn("no hay nada que traducir", args.kwargs["caption"])
        self.assertFalse(self.job["busy"])

    async def test_progress_bar_throttled_monotonic(self):
        self.assertEqual(tb.bar(0, 10), "▱▱▱▱▱▱▱▱▱▱ 0%")
        self.assertEqual(tb.bar(50, 10), "▰▰▰▰▰▱▱▱▱▱ 50%")
        self.assertEqual(tb.bar(100, 10), "▰▰▰▰▰▰▰▰▰▰ 100%")
        msg = make_query("x").message
        prog = tb.Progress(msg, asyncio.get_running_loop())
        prog(10, "🎧 Extrayendo audio…")
        await asyncio.sleep(0.05)
        self.assertIn("10%", msg.edit_caption.await_args.kwargs["caption"])
        self.assertIn("Extrayendo", msg.edit_caption.await_args.kwargs["caption"])
        n = msg.edit_caption.await_count
        prog(11, "🎧 Extrayendo audio…"); prog(5, "🎧 Extrayendo audio…")   # limitado y sin retroceder
        await asyncio.sleep(0.05)
        self.assertEqual(msg.edit_caption.await_count, n)
        self.assertEqual(prog.pct, 11)

    async def test_link_flow(self):
        update = MagicMock(); update.effective_user.id = 1
        update.message.text = "mira https://ejemplo.com/v.mp4 gracias"
        status = make_query("x").message
        update.message.reply_text = AsyncMock(return_value=status)
        update.message.reply_photo = AsyncMock()
        self.ctx.user_data = {}
        def fake_dl(url, workdir, on_frac, max_mb, allow_private):
            on_frac(0.5); import shutil; dst = os.path.join(workdir, "entrada.mp4"); shutil.copy(self.src, dst); return dst
        with mock.patch.object(tb, "download_url", fake_dl):
            await tb.on_link(update, self.ctx)
        update.message.reply_photo.assert_awaited()          # llegó a la vista previa
        self.assertTrue(any(j["dir"] != self.d for j in self.ctx.application.bot_data["jobs"].values()))
        # texto sin enlace: se ignora
        update.message.text = "hola"; update.message.reply_text.reset_mock()
        await tb.on_link(update, self.ctx)
        update.message.reply_text.assert_not_awaited()

    async def test_too_long_video_rejected(self):
        update = MagicMock(); update.effective_user.id = 1
        status = make_query("x").message
        update.message.reply_text = AsyncMock(return_value=status)
        self.ctx.application.bot_data["max_minutes"] = 0
        async def fetch(workdir, st):
            import shutil; dst = os.path.join(workdir, "v.mp4"); shutil.copy(self.src, dst); return dst
        await tb._start_job(update, self.ctx, fetch, "x")
        self.assertIn("máximo", status.edit_text.await_args.args[0])

    def _storage_env(self):
        tmp = tempfile.mkdtemp()
        data = tempfile.mkdtemp()
        orphan = os.path.join(tmp, "trad_orphan"); os.makedirs(orphan); open(orphan + "/v.mp4", "wb").write(b"x" * 5000)
        inflight = os.path.join(tmp, "trad_inflight"); os.makedirs(inflight); open(inflight + "/v.mp4", "wb").write(b"x" * 100)
        busy = os.path.join(tmp, "trad_busy"); os.makedirs(busy); open(busy + "/v.mp4", "wb").write(b"x" * 100)
        idle = os.path.join(tmp, "trad_idle"); os.makedirs(idle); open(idle + "/v.mp4", "wb").write(b"x" * 3000)
        os.makedirs(data + "/tok/videos"); os.makedirs(data + "/tok/documents")
        open(data + "/tok/videos/f.mp4", "wb").write(b"x" * 7000); open(data + "/tok/documents/a.srt", "wb").write(b"x" * 10)
        open(data + "/tok/td.binlog", "wb").write(b"estado")
        bd = self.ctx.application.bot_data
        bd["jobs"] = {5: dict(user=1, dir=busy, busy=True, created=time.time()),
                      6: dict(user=1, dir=idle, busy=False, created=time.time())}
        bd["inflight"] = {inflight}; bd["tg_data_dir"] = data
        return tmp, data, dict(orphan=orphan, inflight=inflight, busy=busy, idle=idle)

    async def test_storage_report_and_clean(self):
        tmp, data, d = self._storage_env()
        with mock.patch.object(tb.tempfile, "gettempdir", return_value=tmp):
            text = tb.storage_report(self.ctx.application)
            self.assertIn("Almacenamiento", text); self.assertIn("huérfanas: 1", text)
            self.assertIn("Servidor local de Telegram", text); self.assertIn("▰", text + "▰") 
            res = tb.clean_storage(self.ctx.application)
        self.assertFalse(os.path.exists(d["orphan"])); self.assertFalse(os.path.exists(d["idle"]))
        self.assertTrue(os.path.exists(d["busy"])); self.assertTrue(os.path.exists(d["inflight"]))   # en uso: intactas
        self.assertFalse(os.path.exists(data + "/tok/videos/f.mp4")); self.assertFalse(os.path.exists(data + "/tok/documents/a.srt"))
        self.assertTrue(os.path.exists(data + "/tok/td.binlog"))                                    # estado intacto
        self.assertEqual((res["files"], res["skipped"]), (2, 1))
        self.assertGreaterEqual(res["freed"], 5000 + 3000 + 7000)
        self.assertEqual(list(self.ctx.application.bot_data["jobs"]), [5])

    async def test_storage_command_and_buttons(self):
        tmp, data, d = self._storage_env()
        update = MagicMock(); update.effective_user.id = 1
        update.message.reply_text = AsyncMock()
        with mock.patch.object(tb.tempfile, "gettempdir", return_value=tmp):
            await tb.cmd_storage(update, self.ctx)
            kb = update.message.reply_text.await_args.kwargs["reply_markup"]
            self.assertEqual([b.callback_data for b in kb.inline_keyboard[0]], ["s|ask", "s|refresh"])
            q = await self.press("s|ask")
            self.assertIn("¿Borrar", q.message.edit_text.await_args.args[0])
            self.assertTrue(os.path.exists(d["orphan"]))                       # aún no se borra
            q = await self.press("s|yes")
            self.assertIn("Listo: liberé", q.message.edit_text.await_args.args[0])
            self.assertFalse(os.path.exists(d["orphan"]))
            q = await self.press("s|no")
        self.ctx.application.bot_data["allowed"] = {99}                         # usuario no autorizado
        q = make_query("s|yes", user=1)
        await tb.on_button(MagicMock(callback_query=q), self.ctx)
        self.assertTrue(q.answer.await_args.kwargs["show_alert"])

    async def test_low_disk_rejected(self):
        update = MagicMock(); update.effective_user.id = 1
        status = make_query("x").message
        update.message.reply_text = AsyncMock(return_value=status)
        fetch = AsyncMock()
        with mock.patch.object(tb.shutil, "disk_usage", return_value=MagicMock(free=1000)):
            await tb._start_job(update, self.ctx, fetch, "x")
        self.assertIn("Poco espacio", status.edit_text.await_args.args[0])
        fetch.assert_not_awaited()

    async def test_big_telegram_file_suggests_link(self):
        update = MagicMock(); update.effective_user.id = 1
        update.message.video.file_size = 80 * 1024 * 1024
        update.message.reply_text = AsyncMock()
        await tb.on_video(update, self.ctx)
        self.assertIn("enlace", update.message.reply_text.await_args.args[0])

    async def test_foreign_user_and_expired(self):
        q = make_query("o|1|font|mono|main", user=2)
        await tb.on_button(MagicMock(callback_query=q), self.ctx)
        self.assertTrue(q.answer.await_args.kwargs["show_alert"])
        q = make_query("o|77|font|mono|main")
        await tb.on_button(MagicMock(callback_query=q), self.ctx)
        self.assertIn("expiró", q.answer.await_args.args[0])

if __name__ == "__main__":
    unittest.main()

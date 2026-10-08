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
                                "groq_key": "", "allowed": set(), "edit_msgs": {}}
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

    async def test_foreign_user_and_expired(self):
        q = make_query("o|1|font|mono|main", user=2)
        await tb.on_button(MagicMock(callback_query=q), self.ctx)
        self.assertTrue(q.answer.await_args.kwargs["show_alert"])
        q = make_query("o|77|font|mono|main")
        await tb.on_button(MagicMock(callback_query=q), self.ctx)
        self.assertIn("expiró", q.answer.await_args.args[0])

if __name__ == "__main__":
    unittest.main()

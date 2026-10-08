import functools, http.server, os, shutil, subprocess, sys, tempfile, threading, unittest

if not shutil.which("ffmpeg"):
    raise unittest.SkipTest("sin ffmpeg")
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch


class Fetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = tempfile.mkdtemp()
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:d=2", "-c:v", "libx264",
                        os.path.join(cls.web, "clip.mp4")], check=True, capture_output=True)
        open(os.path.join(cls.web, "page.html"), "w").write("<html>no es un video</html>")
        h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=cls.web)
        h.log_message = lambda *a, **k: None
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_direct_download_with_progress(self):
        d = tempfile.mkdtemp(); seen = []
        path = fetch.download_url(f"http://127.0.0.1:{self.port}/clip.mp4", d, seen.append, allow_private=True)
        self.assertEqual(os.path.getsize(path), os.path.getsize(os.path.join(self.web, "clip.mp4")))
        self.assertEqual(seen[-1], 1.0)

    def test_size_limit(self):
        with self.assertRaises(RuntimeError):
            fetch.download_url(f"http://127.0.0.1:{self.port}/clip.mp4", tempfile.mkdtemp(), max_mb=0, allow_private=True)

    def test_html_page_goes_to_ytdlp(self):
        with mock.patch.object(fetch, "_ytdlp", return_value="/tmp/x.mp4") as y:
            out = fetch.download_url(f"http://127.0.0.1:{self.port}/page.html", tempfile.mkdtemp(), allow_private=True)
        self.assertEqual(out, "/tmp/x.mp4"); y.assert_called_once()

    def test_private_addresses_blocked(self):
        for u in ("http://127.0.0.1/v.mp4", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/v.mp4"):
            with self.assertRaises(RuntimeError):
                fetch.download_url(u, tempfile.mkdtemp())

    def test_url_helpers(self):
        self.assertEqual(fetch.extract_url("mira https://x.com/a.mp4, ok"), "https://x.com/a.mp4")
        self.assertIsNone(fetch.extract_url("sin enlace"))
        self.assertIn("dl=1", fetch.normalize("https://www.dropbox.com/s/abc/v.mp4?dl=0"))
        self.assertEqual(fetch.normalize("https://drive.google.com/file/d/AbC_1/view?usp=sharing"),
                         "https://drive.google.com/uc?export=download&id=AbC_1")

if __name__ == "__main__":
    unittest.main()

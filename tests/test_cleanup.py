import os, subprocess, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def touch(path, minutes_old):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("x")
    t = time.time() - minutes_old * 60
    os.utime(path, (t, t))


class Cleanup(unittest.TestCase):
    def test_only_old_media_and_workdirs_removed(self):
        data, tmp = tempfile.mkdtemp(), tempfile.mkdtemp()
        old_vid = f"{data}/123:ABC/videos/file_1.mp4"; new_vid = f"{data}/123:ABC/videos/file_2.mp4"
        old_doc = f"{data}/123:ABC/documents/file_3.srt"; state = f"{data}/123:ABC/td.binlog"
        for p, age in ((old_vid, 90), (new_vid, 5), (old_doc, 90), (state, 9999)):
            touch(p, age)
        touch(f"{tmp}/trad_old/video.mp4", 600); touch(f"{tmp}/trad_new/video.mp4", 10); touch(f"{tmp}/otro/x", 600)
        for d, age in (("trad_old", 600), ("trad_new", 10), ("otro", 600)):
            t = time.time() - age * 60; os.utime(f"{tmp}/{d}", (t, t))
        subprocess.run(["bash", f"{ROOT}/deploy/cleanup.sh"], check=True,
                       env={**os.environ, "TELEGRAM_DATA": data, "TMP_DIR": tmp})
        self.assertFalse(os.path.exists(old_vid)); self.assertFalse(os.path.exists(old_doc))
        self.assertTrue(os.path.exists(new_vid)); self.assertTrue(os.path.exists(state))   # estado intacto
        self.assertFalse(os.path.exists(f"{tmp}/trad_old")); self.assertTrue(os.path.exists(f"{tmp}/trad_new"))
        self.assertTrue(os.path.exists(f"{tmp}/otro"))

if __name__ == "__main__":
    unittest.main()

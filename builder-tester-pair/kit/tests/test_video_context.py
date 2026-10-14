#!/usr/bin/env python3
"""Tests for kit/bin/video-context. Needs ffmpeg; the speech test also needs whisper-cli + a model (skipped otherwise,
never reported as passed). Fixtures are generated: a colour test pattern with a burned-in timecode, and the public-domain
jfk.wav sample shipped with whisper.cpp when it can be found.

Run:  python3 kit/tests/test_video_context.py"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin" / "video-context"
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAVE_WHISPER = bool(shutil.which("whisper-cli")) and any(
    os.path.isfile(os.path.join(d, "ggml-small.en.bin")) for d in ("/user/shared/tools/whisper-models", os.path.expanduser("~/.cache/whisper-cpp")))
JFK = next((p for p in ("/user/shared/tools/whisper.cpp/samples/jfk.wav",) if os.path.isfile(p)), None)


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def vc(video, *extra):
    r = subprocess.run([sys.executable, "-I", str(BIN), str(video), *extra], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class VideoContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = pathlib.Path(tempfile.mkdtemp())
        pattern = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10"]
        ff(*pattern, "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", cls.d / "noaudio.mp4")
        ff(*pattern, "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", cls.d / "silent.mp4")
        (cls.d / "link.mp4").write_text("https://scribehow.com/embed-preview/abc\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_not_a_video_is_refused_not_guessed(self):
        out_dir = self.d / "o_link"
        code, out = vc(self.d / "link.mp4", "--out", str(out_dir))
        self.assertNotEqual(code, 0)
        self.assertIn("could not read", out)
        self.assertFalse(out_dir.exists(), "no pack for a non-video")

    def test_no_audio_track_is_flagged(self):
        out_dir = self.d / "o_noaudio"
        code, out = vc(self.d / "noaudio.mp4", "--out", str(out_dir))
        self.assertEqual(code, 0, out)
        self.assertIn("NO AUDIO TRACK", out)
        pack = next(out_dir.glob("*"))
        self.assertGreaterEqual(len(list((pack / "frames").glob("*.jpg"))), 3)      # 6 s clip, one frame every 2 s
        self.assertTrue((pack / "context.md").read_text().count("NO AUDIO TRACK") >= 1)

    @unittest.skipUnless(HAVE_WHISPER, "whisper-cli / model not installed")
    def test_silence_is_an_empty_transcript_not_hallucinated_speech(self):
        code, out = vc(self.d / "silent.mp4")
        self.assertEqual(code, 0, out)
        self.assertIn("EMPTY TRANSCRIPT", out)                       # [BLANK_AUDIO] must not become a requirement
        self.assertNotIn("BLANK_AUDIO", (next((self.d / "ctx").glob("*")) / "context.md").read_text().split("## Full transcript")[0].split("## Timeline")[-1])

    @unittest.skipUnless(HAVE_WHISPER and JFK, "whisper-cli / model / jfk.wav not available")
    def test_speech_is_transcribed_and_frames_match_their_timestamps(self):
        ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10", "-i", JFK, "-t", "13", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", self.d / "speech.mp4")
        code, out = vc(self.d / "speech.mp4", "--source", "unit test", "--speaker", "tester")
        self.assertEqual(code, 0, out)
        pack = next(p for p in (self.d / "ctx").glob("*") if "country" in (p / "context.md").read_text().lower())
        md = (pack / "context.md").read_text()
        self.assertIn("ask what you can do for your country", md.replace("’", "'"))
        self.assertIn("Flags (read first)\n\n- none", md)
        self.assertTrue(list(pack.glob("sheet_*.jpg")), "contact sheet written")
        names = sorted(p.name for p in (pack / "frames").glob("*.jpg"))
        self.assertGreaterEqual(len(names), 6, "short clips still get ~8 frames")
        for n in names:                                              # file name time == real position in the video
            self.assertTrue(re.fullmatch(r"f_\d{4}_\d{2}\.jpg", n), n)
        again_code, again = vc(self.d / "speech.mp4")
        self.assertIn("already done", again)                         # same bytes -> reused, never re-run

    def test_delete_source_removes_video_and_note_only_after_a_complete_pack(self):
        v = self.d / "todelete.mp4"
        shutil.copyfile(self.d / "noaudio.mp4", v)
        (self.d / "todelete.mp4.txt").write_text("note")
        out_dir = self.d / "o_del"
        code, out = vc(v, "--out", str(out_dir), "--delete-source")
        self.assertEqual(code, 0, out)
        self.assertFalse(v.exists(), "the video is deleted once the pack is built")
        self.assertFalse((self.d / "todelete.mp4.txt").exists(), "and its note")
        pack = next(out_dir.glob("*"))
        self.assertTrue((pack / "context.md").is_file() and (pack / ".done").is_file(), "the pack stays")
        # a file that is not a video is refused and must NOT be deleted
        bad = self.d / "notavideo.mp4"
        shutil.copyfile(self.d / "link.mp4", bad)
        code, _ = vc(bad, "--out", str(self.d / "o_del2"), "--delete-source")
        self.assertNotEqual(code, 0)
        self.assertTrue(bad.exists(), "a failed intake never deletes the source")

    def test_pack_is_group_writable_for_the_other_agent(self):
        code, _ = vc(self.d / "noaudio.mp4", "--force")
        self.assertEqual(code, 0)
        pack = next((self.d / "ctx").glob("*"))
        self.assertTrue((pack / "context.md").stat().st_mode & 0o040, "group can read context.md")


if __name__ == "__main__":
    unittest.main()

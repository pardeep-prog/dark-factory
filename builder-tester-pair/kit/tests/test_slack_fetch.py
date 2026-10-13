#!/usr/bin/env python3
"""Tests for kit/bin/slack-fetch against a local mock of the Slack API (no network, no real token).
127.0.0.1 stands in for slack.com (SLACK_FETCH_ALLOW_LOCAL=1); 'localhost' is the 'foreign host' that must NOT get the token.

Run:  python3 kit/tests/test_slack_fetch.py"""
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin" / "slack-fetch"
TOKEN = "xoxb-TEST-NOT-A-REAL-TOKEN-0123456789"
PAYLOAD = os.urandom(3 * 1024 * 1024 + 17)
SEEN = {"foreign_auth": None, "dl_auth": None}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path.startswith("/api/auth.test"):
            return self._json({"ok": True, "team": "Mock Team", "user": "mock-bot"})
        if self.path.startswith("/api/conversations.list"):
            return self._json({"ok": True, "channels": [{"name": "chairside", "is_member": True},
                                                        {"name": "random", "is_member": False}]})
        if self.path.startswith("/api/files.info"):
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._json({"ok": False, "error": "invalid_auth"})
            fid = self.path.split("file=")[1].split("&")[0]
            if fid == "FNOSCOPE":
                return self._json({"ok": False, "error": "missing_scope"})
            if fid == "FTEXT":
                return self._json({"ok": True, "file": {"name": "notes.txt", "size": 5, "mimetype": "text/plain",
                                                        "url_private_download": f"http://127.0.0.1:{port}/dl"}})
            path = {"FVIDEO": "/dl", "FHTML": "/html", "FREDIR": "/redir", "FSHORT": "/dl"}[fid]
            size = len(PAYLOAD) + (999 if fid == "FSHORT" else 0)
            return self._json({"ok": True, "file": {"name": "rec.mp4", "size": size, "mimetype": "video/mp4", "user": "U1",
                                                    "permalink": "https://example.invalid/p1",
                                                    "url_private_download": f"http://127.0.0.1:{port}{path}"}})
        if self.path == "/dl":
            SEEN["dl_auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
        elif self.path == "/html":
            b = b"<html>Sign in to Slack</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path == "/redir":                      # Slack-like redirect to another host
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/foreign")
            self.end_headers()
        elif self.path == "/foreign":
            SEEN["foreign_auth"] = self.headers.get("Authorization", "(none)")
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)


class SlackFetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.api = f"http://127.0.0.1:{cls.srv.server_address[1]}/api"
        cls.d = pathlib.Path(tempfile.mkdtemp())
        cls.tok = cls.d / "token"
        cls.tok.write_text(TOKEN + "\n")
        os.chmod(cls.tok, 0o600)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def run_(self, *args, token=True, **env):
        e = dict(os.environ, SLACK_FETCH_ALLOW_LOCAL="1", SLACK_FETCH_TOKEN_FILE=str(self.tok if token else self.d / "none"), **env)
        r = subprocess.run([sys.executable, "-I", str(BIN), *args, "--api", self.api, "--out", str(self.d / "out")],
                           capture_output=True, text=True, env=e)
        self.assertNotIn(TOKEN, r.stdout + r.stderr, "the token must never be printed")
        return r

    def test_downloads_whole_file_and_writes_sidecar(self):
        r = self.run_("FVIDEO")
        self.assertEqual(r.returncode, 0, r.stderr)
        f = next((self.d / "out").glob("FVIDEO_rec.mp4"))
        self.assertEqual(f.read_bytes(), PAYLOAD)
        self.assertIn("https://example.invalid/p1", (self.d / "out" / "FVIDEO_rec.mp4.txt").read_text())
        self.assertEqual(SEEN["dl_auth"], f"Bearer {TOKEN}")
        self.assertFalse(list((self.d / "out").glob("*.part")), "no half-written file left")

    def test_whoami_lists_only_channels_the_bot_is_in_and_never_the_token(self):
        r = self.run_("--whoami")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("workspace=Mock Team bot=mock-bot", r.stdout)
        self.assertIn("member of: chairside", r.stdout)
        self.assertNotIn("random", r.stdout)

    def test_no_token_is_exit_3_with_the_owner_step(self):
        r = self.run_("FVIDEO", token=False)
        self.assertEqual(r.returncode, 3)
        self.assertIn("owner", r.stderr)

    def test_world_readable_token_file_is_refused(self):
        loose = self.d / "loose"
        loose.write_text(TOKEN)
        os.chmod(loose, 0o644)
        e = dict(os.environ, SLACK_FETCH_TOKEN_FILE=str(loose))
        r = subprocess.run([sys.executable, "-I", str(BIN), "--check"], capture_output=True, text=True, env=e)
        self.assertEqual(r.returncode, 3)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)

    def test_missing_scope_and_not_a_video_and_html_and_short_read(self):
        self.assertEqual(self.run_("FNOSCOPE").returncode, 4)
        self.assertEqual(self.run_("FTEXT").returncode, 5)
        self.assertEqual(self.run_("FHTML").returncode, 6)
        self.assertEqual(self.run_("FSHORT").returncode, 6)
        self.assertFalse(list((self.d / "out").glob("FSHORT*")), "a short read leaves nothing behind")

    def test_token_is_not_sent_to_a_non_slack_host_on_redirect(self):
        SEEN["foreign_auth"] = None
        r = self.run_("FREDIR")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(SEEN["foreign_auth"], "(none)", "the redirect target must not receive the Authorization header")


if __name__ == "__main__":
    unittest.main()

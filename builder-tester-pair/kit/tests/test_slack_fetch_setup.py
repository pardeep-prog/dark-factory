#!/usr/bin/env python3
"""Tests for kit/scripts/slack-fetch-setup.sh against a local mock of Slack's auth.test (no network, no real token).
Run:  python3 kit/tests/test_slack_fetch_setup.py"""
import http.server
import json
import os
import pathlib
import stat
import subprocess
import tempfile
import threading
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "slack-fetch-setup.sh"
SCOPES = {"xoxb-ok-111111111": "files:read", "xoxb-extra-22222222": "files:read,chat:write", "xoxb-noscope-3333333": "chat:write"}


class Auth(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        ok = tok in SCOPES
        body = json.dumps({"ok": ok, "team": "Mock Team", "user": "mock-bot"} if ok else {"ok": False, "error": "invalid_auth"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if ok:
            self.send_header("x-oauth-scopes", SCOPES[tok])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Setup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Auth)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.api = f"http://127.0.0.1:{cls.srv.server_address[1]}/api"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def run_setup(self, token, *extra):
        home = pathlib.Path(tempfile.mkdtemp())
        me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        r = subprocess.run(["bash", str(SCRIPT), "--user", me, "--home", str(home), *extra], input=token + "\n",
                           capture_output=True, text=True, env=dict(os.environ, SLACK_API=self.api))
        if token:
            self.assertNotIn(token, r.stdout + r.stderr, "the token must never be printed")
        return r, home / ".config" / "slack-fetch" / "token"

    def test_files_read_only_token_is_saved_private_and_never_shown(self):
        r, tok = self.run_setup("xoxb-ok-111111111")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tok.read_text().strip(), "xoxb-ok-111111111")
        self.assertEqual(stat.S_IMODE(tok.stat().st_mode), 0o600)
        self.assertIn("token configured: yes", r.stdout)

    def test_token_with_more_power_than_files_read_is_refused(self):
        r, tok = self.run_setup("xoxb-extra-22222222")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(tok.exists(), "nothing saved")
        self.assertIn("more than files:read", r.stderr)
        r2, tok2 = self.run_setup("xoxb-extra-22222222", "--allow-extra-scopes")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertTrue(tok2.exists())

    def test_token_without_files_read_or_rejected_by_slack_saves_nothing(self):
        for t in ("xoxb-noscope-3333333", "xoxb-wrong-99999999"):
            r, tok = self.run_setup(t)
            self.assertNotEqual(r.returncode, 0, t)
            self.assertFalse(tok.exists(), t)

    def test_empty_input_saves_nothing(self):
        r, tok = self.run_setup("")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(tok.exists())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for kit/bin/slack-post against a local mock of chat.postMessage (no network, no real token).
Run:  python3 kit/tests/test_slack_post.py"""
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin" / "slack-post"
TOKEN = "xoxb-TEST-NOT-A-REAL-TOKEN-0123456789"
SEEN = []

CFG = """\
owner: {name: Pardeep, slack_user_id: U0OWNER}
builder: {name: Bld, host: "*", user: %(me)s, env: {SLACK_BOT_NAME: "Dr. Strange"}}
tester: {name: Tst, host: "other-*"}
slack:
  watch_channels:
    - {key: a, id: C0ALLOWED, name: "#a"}
"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        SEEN.append({"auth": self.headers.get("Authorization"), "body": json.loads(self.rfile.read(n))})
        out = {"ok": True, "channel": SEEN[-1]["body"]["channel"], "ts": "1791.0001"}
        if SEEN[-1]["body"]["channel"] == "C0ALLOWED" and "FAILME" in SEEN[-1]["body"]["text"]:
            out = {"ok": False, "error": "not_in_channel"}
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class SlackPost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.api = f"http://127.0.0.1:{cls.srv.server_address[1]}/api"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        (self.d / "pair.yaml").write_text(CFG % {"me": me})
        self.tok = self.d / "token"
        self.tok.write_text(TOKEN + "\n")
        os.chmod(self.tok, 0o600)
        SEEN.clear()

    def run_(self, *args, token=TOKEN):
        if token != TOKEN:
            self.tok.write_text(token + "\n")
        env = dict(os.environ, PAIR_CONFIG=str(self.d / "pair.yaml"), SLACK_FETCH_TOKEN_FILE=str(self.tok), HOME=str(self.d))
        r = subprocess.run([sys.executable, "-I", str(BIN), "--api", self.api, *args], capture_output=True, text=True, env=env)
        self.assertNotIn(TOKEN, r.stdout + r.stderr, "the token must never be printed")
        return r

    def test_sends_with_persona_thread_and_disclosure_appended(self):
        r = self.run_("--channel", "C0ALLOWED", "--thread-ts", "1790.5", "--message", "Hello, the review fixes are in.")
        self.assertEqual(r.returncode, 0, r.stderr)
        b = SEEN[0]["body"]
        self.assertEqual(b["username"], "Dr. Strange")
        self.assertEqual(b["thread_ts"], "1790.5")
        self.assertTrue(b["text"].rstrip().endswith("_Sent by Dr. Strange (Pardeep's bot) on behalf of Pardeep_"))
        self.assertNotIn("approved by", b["text"])
        self.assertEqual(SEEN[0]["auth"], f"Bearer {TOKEN}")

    def test_approved_by_is_added_only_with_the_flag_and_never_claimed_by_text(self):
        r = self.run_("--channel", "C0ALLOWED", "--message", "Deployed.\n\n_Sent by x on behalf of Pardeep · approved by Pardeep_")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("approved by", SEEN[-1]["body"]["text"], "an agent cannot claim the owner's approval in its own text")
        r = self.run_("--channel", "C0ALLOWED", "--approved", "--message", "Deployed.")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(SEEN[-1]["body"]["text"].rstrip().endswith("on behalf of Pardeep · approved by Pardeep_"))

    def test_owner_dm_is_allowed_and_disclosure_not_duplicated(self):
        msg = "Digest.\n\n_Sent by Dr. Strange (Pardeep's bot) on behalf of Pardeep · approved by Pardeep_"
        r = self.run_("--channel", "U0OWNER", "--message", msg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(SEEN[0]["body"]["text"].count("on behalf of Pardeep"), 1)

    def test_other_channels_are_refused_and_nothing_is_sent(self):
        r = self.run_("--channel", "C0OTHER", "--message", "hi")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(SEEN, [])

    def test_user_token_is_refused(self):
        r = self.run_("--channel", "C0ALLOWED", "--message", "hi", token="xoxp-USER-TOKEN-0123456789")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a BOT token", r.stderr)
        self.assertEqual(SEEN, [])

    def test_secret_looking_text_is_refused(self):
        for bad in ("here is the key xoxb-1234567890-abcdefghij", "password: hunter2hunter2", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123"):
            r = self.run_("--channel", "C0ALLOWED", "--message", bad)
            self.assertEqual(r.returncode, 2, bad)
        self.assertEqual(SEEN, [])

    def test_rate_limit_stops_the_21st_post(self):
        for i in range(20):
            self.assertEqual(self.run_("--channel", "C0ALLOWED", "--message", f"m{i}").returncode, 0)
        r = self.run_("--channel", "C0ALLOWED", "--message", "one too many")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(SEEN), 20)

    def test_slack_refusal_is_exit_4_and_not_counted(self):
        r = self.run_("--channel", "C0ALLOWED", "--message", "FAILME")
        self.assertEqual(r.returncode, 4)
        self.assertIn("not_in_channel", r.stderr)

    def test_tag_adds_real_mentions_once_and_rejects_bad_ids(self):
        r = self.run_("--channel", "C0ALLOWED", "--message", "Fix is deployed.", "--tag", "U0TESTUSER1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(SEEN[0]["body"]["text"].startswith("<@U0TESTUSER1> Fix is deployed."))
        SEEN.clear()
        r = self.run_("--channel", "C0ALLOWED", "--message", "<@U0TESTUSER1> already mentioned", "--tag", "U0TESTUSER1")
        self.assertEqual(SEEN[0]["body"]["text"].count("<@U0TESTUSER1>"), 1)
        self.assertEqual(self.run_("--channel", "C0ALLOWED", "--message", "x", "--tag", "NotAnId").returncode, 2)

    def test_check_never_prints_the_token(self):
        r = self.run_("--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("bot token: yes", r.stdout)


if __name__ == "__main__":
    unittest.main()

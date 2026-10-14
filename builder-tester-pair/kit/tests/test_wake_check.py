#!/usr/bin/env python3
"""Tests for kit/bin/wake-check against a local mock of conversations.history (no network, no real token).
Run:  python3 kit/tests/test_wake_check.py"""
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin" / "wake-check"
TOKEN = "xoxb-TEST-NOT-A-REAL-TOKEN-0123456789"
MSGS = {}

CFG = """\
owner: {name: Pardeep, slack_user_id: U0OWNER}
builder: {name: "Dr. Strange", host: "*", user: %(me)s}
tester: {name: Wong, host: "other-*"}
paths: {shared_root: %(root)s}
slack:
  bot_user_id: U0BOT
  watch_channels:
    - {key: team, id: C0TEAM, name: "#team"}
    - {key: owner-dm, id: D0DM, name: "owner dm"}
"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        ch = q["channel"][0]
        oldest = float(q.get("oldest", ["0"])[0])
        out = {"ok": True, "messages": [m for m in MSGS.get(ch, []) if float(m["ts"]) > oldest]}
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def m(text, **kw):
    return {"ts": f"{time.time():.6f}", "user": "U0SOMEONE", "text": text, **kw}


class Wake(unittest.TestCase):
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
        (self.d / "pair.yaml").write_text(CFG % {"me": me, "root": self.d})
        self.tok = self.d / "token"
        self.tok.write_text(TOKEN + "\n")
        os.chmod(self.tok, 0o600)
        MSGS.clear()

    def run_(self, *args):
        env = dict(os.environ, PAIR_CONFIG=str(self.d / "pair.yaml"), SLACK_FETCH_TOKEN_FILE=str(self.tok), SLACK_API=self.api, HOME=str(self.d))
        r = subprocess.run([sys.executable, "-I", str(BIN), *args], capture_output=True, text=True, env=env)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        return r

    def start(self):
        r = self.run_()                      # first run records the starting point
        self.assertEqual(r.returncode, 1, r.stdout)
        time.sleep(0.05)

    def test_quiet_until_something_for_the_agent_arrives(self):
        self.start()
        MSGS["C0TEAM"] = [m("lunch anyone?")]
        self.assertEqual(self.run_().returncode, 1, "chatter in a team channel that does not name the agents must not wake it")
        MSGS["C0TEAM"] = [m("<@U0BOT> can you look at this")]
        r = self.run_()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("wake: slack team", r.stdout)

    def test_owner_dm_always_wakes_and_bot_posts_never_do(self):
        self.start()
        MSGS["D0DM"] = [m("deploy it", user="U0OWNER")]
        self.assertEqual(self.run_().returncode, 0)
        self.run_("--ack")
        time.sleep(0.05)
        MSGS["D0DM"] = [m("Sent by me", bot_id="B0BOT", user="U0BOT")]
        MSGS["C0TEAM"] = [m("<@U0BOT> hi", bot_id="B0BOT")]
        self.assertEqual(self.run_().returncode, 1, "the bot's own messages must never wake it")

    def test_ack_moves_the_marker_so_the_same_message_wakes_once(self):
        self.start()
        MSGS["C0TEAM"] = [m("Wong please check")]
        self.assertEqual(self.run_().returncode, 0)
        self.assertEqual(self.run_().returncode, 0, "without --ack the event is not lost")
        self.run_("--ack")
        self.assertEqual(self.run_().returncode, 1)


if __name__ == "__main__":
    unittest.main()

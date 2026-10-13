#!/usr/bin/env python3
"""Tests for `chat-approval scan` (the owner's "approve <namespace> <minutes>" in the Slack DM opens a deploy window) and kit/bin/deploy-window,
against a local mock of conversations.history / chat.postMessage (no network, no real token).
Run:  python3 kit/tests/test_chat_approval_scan.py"""
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

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
TOKEN = "xoxb-TEST-NOT-A-REAL-TOKEN-0123456789"
STATE = {"messages": [], "posts": []}

CFG = """\
owner: {name: Pardeep, short_name: Pardeep, slack_user_id: U0OWNER}
builder: {name: Bld, host: "*", user: %(me)s, env: {SLACK_BOT_NAME: "Dr. Strange"}}
tester: {name: Tst, host: "other-*"}
paths: {approvals: %(appr)s}
slack:
  watch_channels:
    - {key: owner-dm, id: D0OWNERDM, name: "owner dm"}
"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, out):
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        assert urllib.parse.urlparse(self.path).path.endswith("conversations.history")
        self._send({"ok": True, "messages": STATE["messages"]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        STATE["posts"].append(json.loads(self.rfile.read(n)))
        self._send({"ok": True, "channel": STATE["posts"][-1]["channel"], "ts": "1791.0001"})


def msg(text, user="U0OWNER", age_s=60, **extra):
    ts = f"{time.time() - age_s:.6f}"
    return {"ts": ts, "user": user, "text": text, **extra}


class Scan(unittest.TestCase):
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
        self.appr = self.d / "approvals"
        me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        (self.d / "pair.yaml").write_text(CFG % {"me": me, "appr": self.appr})
        self.tok = self.d / "token"
        self.tok.write_text(TOKEN + "\n")
        os.chmod(self.tok, 0o600)
        STATE["messages"], STATE["posts"] = [], []

    def run_(self, tool, *args):
        env = dict(os.environ, PAIR_CONFIG=str(self.d / "pair.yaml"), SLACK_FETCH_TOKEN_FILE=str(self.tok), SLACK_API=self.api,
                   HOME=str(self.d), PAIR_APPROVALS_DIR=str(self.appr))
        return subprocess.run([sys.executable, "-I", str(BIN / tool), *args], capture_output=True, text=True, env=env)

    def windows(self):
        return sorted(self.appr.glob("appr-*.json")) if self.appr.exists() else []

    def test_owner_message_opens_a_window_and_confirms_in_the_dm(self):
        STATE["messages"] = [msg("<@U0C7KRB4TBR|Dr Strange> approve chairside-dev 60")]
        r = self.run_("chat-approval", "scan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        w = self.windows()
        self.assertEqual(len(w), 1)
        rec = json.loads(w[0].read_text())
        self.assertEqual(rec["scope"], "chairside-dev")
        self.assertAlmostEqual(rec["expires"] - rec["created"], 3600, delta=2)
        self.assertEqual(STATE["posts"][-1]["channel"], "D0OWNERDM")
        self.assertIn("chairside-dev", STATE["posts"][-1]["text"])
        self.assertNotIn("approved by", STATE["posts"][-1]["text"], "the confirmation must not claim the owner approved it")
        self.assertNotIn(TOKEN, r.stdout + r.stderr)

    def test_a_bot_authored_or_other_users_message_never_opens_a_window(self):
        STATE["messages"] = [msg("approve chairside-dev 60", user="U0C7KRB4TBR", bot_id="B0BOT"),
                             msg("approve chairside-dev 60", user="U0SOMEONE"),
                             msg("approve chairside-dev 60", subtype="bot_message")]
        self.run_("chat-approval", "scan")
        self.assertEqual(self.windows(), [])

    def test_single_use_old_messages_protected_namespace_and_long_windows_are_refused(self):
        STATE["messages"] = [msg("approve chairside-dev 60")]
        self.run_("chat-approval", "scan")
        self.run_("chat-approval", "scan")
        self.assertEqual(len(self.windows()), 1, "the same message must not open a second window")
        STATE["messages"] = [msg("approve chairside-dev 60", age_s=3600), msg("approve kube-system 30"), msg("approve chairside-dev 500"),
                             msg("please approve chairside-dev 30")]
        self.run_("chat-approval", "scan")
        self.assertEqual(len(self.windows()), 1)

    def test_deploy_window_is_read_only_and_tells_the_truth(self):
        r = self.run_("deploy-window", "chairside-dev")
        self.assertEqual((r.returncode, r.stdout.strip()), (1, "closed"))
        STATE["messages"] = [msg("approve chairside-dev 60")]
        self.run_("chat-approval", "scan")
        before = [p.read_text() for p in self.windows()]
        r = self.run_("deploy-window", "chairside-dev")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("open until", r.stdout)
        self.assertEqual(self.run_("deploy-window", "other-scope").returncode, 1)
        self.assertEqual(before, [p.read_text() for p in self.windows()], "deploy-window must never change a window")


if __name__ == "__main__":
    unittest.main()

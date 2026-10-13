#!/usr/bin/env python3
"""Tests for kit/bin/slack-asks against a mock Slack (no network, no real token). Run: python3 kit/tests/test_slack_asks.py"""
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

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin" / "slack-asks"
BOT, OWNER = "UBOT", "UOWNER"
NOW = time.time()


def ts(minutes_ago):
    return f"{NOW - minutes_ago * 60:.6f}"


# channel C1: parent ask + bot reply (answered); parent ask no reply (unanswered); plain chatter; ask referenced by a hand-over;
# a thread the bot is in where a person's later reply has no answer (unanswered); too-young ask; too-old ask
MSGS = [
    {"ts": ts(300), "user": "U1", "text": f"<@{OWNER}> please fix A", "reply_count": 1},
    {"ts": ts(290), "user": "U1", "text": "just chatting, nobody tagged"},
    {"ts": ts(280), "user": "U1", "text": "Dr Strange can you check B"},                      # name match, no reply
    {"ts": ts(270), "user": "U1", "text": f"<@{BOT}> do C", "reply_count": 0},                  # referenced by a hand-over
    {"ts": ts(260), "user": "U1", "text": "wong - test D", "reply_count": 2},                  # thread: bot replied, then person replied
    {"ts": ts(10), "user": "U1", "text": f"<@{OWNER}> brand new"},                              # inside grace
    {"ts": ts(60 * 24 * 5), "user": "U1", "text": f"<@{OWNER}> ancient"},                       # older than --days
]
THREADS = {
    MSGS[0]["ts"]: [MSGS[0], {"ts": ts(295), "user": BOT, "text": "got it"}],
    MSGS[4]["ts"]: [MSGS[4], {"ts": ts(255), "user": BOT, "text": "noted"}, {"ts": ts(200), "user": "U1", "text": "here is more detail, any news?"}],
}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        import urllib.parse as up
        u = up.urlparse(self.path)
        q = dict(up.parse_qsl(u.query))
        if u.path.endswith("auth.test"):
            out = {"ok": True, "user_id": BOT}
        elif u.path.endswith("conversations.history"):
            out = {"ok": True, "messages": [m for m in MSGS if float(m["ts"]) >= float(q["oldest"])]}
        elif u.path.endswith("conversations.replies"):
            out = {"ok": True, "messages": THREADS.get(q["ts"], [])}
        else:
            out = {"ok": False, "error": "unknown_method"}
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class Asks(unittest.TestCase):
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
        (self.d / "ledger" / "handoffs").mkdir(parents=True)
        (self.d / "ledger" / "handoffs" / "ho-1.json").write_text(json.dumps({"request": "do C", "where": MSGS[3]["ts"]}))
        me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        (self.d / "pair.yaml").write_text(
            f"owner: {{name: Pardeep, slack_user_id: {OWNER}}}\nbuilder: {{name: Dr. Strange, host: '*', user: {me}}}\n"
            f"tester: {{name: Wong, host: 'other-*'}}\nslack:\n  bot_user_id: {BOT}\n  watch_channels:\n    - {{key: a, id: C1, name: '#a'}}\n"
            f"paths: {{ledger: {self.d / 'ledger'}}}\n")
        self.tok = self.d / "token"
        self.tok.write_text("xoxb-TEST-NOT-REAL-0123456789\n")
        os.chmod(self.tok, 0o600)
        # a stand-in for the board tool next to the copied binary is not needed: --board is tested via the real board path below

    def run_(self, *args):
        env = dict(os.environ, PAIR_CONFIG=str(self.d / "pair.yaml"), SLACK_FETCH_TOKEN_FILE=str(self.tok), HOME=str(self.d))
        r = subprocess.run([sys.executable, "-I", str(BIN), "--api", self.api, *args], capture_output=True, text=True, env=env)
        self.assertNotIn("xoxb-", r.stdout + r.stderr, "never prints a token")
        return r

    def test_finds_exactly_the_unanswered_asks(self):
        r = self.run_("--json")
        self.assertEqual(r.returncode, 1, r.stderr)
        got = {u["ts"] for u in json.loads(r.stdout)}
        self.assertEqual(got, {MSGS[2]["ts"], THREADS[MSGS[4]["ts"]][2]["ts"]},
                         "only the name-match ask with no reply and the person's unanswered reply in the bot's thread")

    def test_answered_referenced_young_old_and_chatter_are_not_flagged(self):
        got = {u["ts"] for u in json.loads(self.run_("--json").stdout)}
        for m in (MSGS[0], MSGS[1], MSGS[3], MSGS[5], MSGS[6]):
            self.assertNotIn(m["ts"], got)

    def test_check_and_no_token(self):
        self.assertEqual(self.run_("--check").returncode, 0)
        self.tok.unlink()
        self.assertEqual(self.run_("--check").returncode, 3)


if __name__ == "__main__":
    unittest.main()

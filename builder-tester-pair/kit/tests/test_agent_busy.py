#!/usr/bin/env python3
"""Tests for kit/bin/agent-busy: busy/idle comes from the action ledger (work in flight), not from terminal silence.
Run:  python3 kit/tests/test_agent_busy.py"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

KIT = pathlib.Path(__file__).resolve().parents[1]
BIN = KIT / "bin" / "agent-busy"
sys.path.insert(0, str(KIT / "lib"))
import pairconf  # noqa: E402


class Busy(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        self.cfgp = self.d / "pair.yaml"
        self.cfgp.write_text(json.dumps({"builder": {"name": "B", "host": "*", "user": me}, "tester": {"name": "T", "host": "other-*"},
                                         "paths": {"shared_root": str(self.d)}}))
        self.ledger = pathlib.Path(str(pairconf.ledger_dir(pairconf.load(str(self.cfgp)))))
        self.ledger.mkdir(parents=True, exist_ok=True)

    def entry(self, minutes_ago, phase, agent="builder"):
        ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
        f = self.ledger / f"{ts:%Y-%m-%d}.jsonl"
        with open(f, "a") as fh:
            fh.write(json.dumps({"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "agent": agent, "phase": phase, "tool": "Bash"}) + "\n")

    def run_(self):
        env = dict(os.environ, PAIR_CONFIG=str(self.cfgp), HOME=str(self.d))
        r = subprocess.run([sys.executable, "-I", str(BIN)], capture_output=True, text=True, env=env)
        return r.returncode, r.stdout.strip()

    def test_no_entries_is_idle(self):
        self.assertEqual(self.run_(), (1, "idle"))

    def test_recent_activity_is_busy(self):
        self.entry(1, "done")
        rc, out = self.run_()
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("busy"))

    def test_a_long_unfinished_tool_call_is_busy_even_though_the_terminal_is_silent(self):
        self.entry(20, "attempt")                 # started 20 minutes ago, no `done`: a build or battery in flight
        rc, out = self.run_()
        self.assertEqual(rc, 0, out)
        self.assertIn("has not finished", out)

    def test_an_old_unfinished_attempt_is_a_crashed_call_not_work(self):
        self.entry(120, "attempt")
        self.assertEqual(self.run_(), (1, "idle"))

    def test_finished_and_quiet_is_idle_and_the_other_role_does_not_count(self):
        self.entry(10, "done")
        self.entry(0, "attempt", agent="tester")      # the tester is busy; this builder is not
        self.assertEqual(self.run_(), (1, "idle"))

    def test_an_unreadable_config_is_unknown_never_idle(self):
        self.cfgp.write_text("not json {")
        rc, out = self.run_()
        self.assertEqual(rc, 2, out)


if __name__ == "__main__":
    unittest.main()

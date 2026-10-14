#!/usr/bin/env python3
"""Runs the schedule selftest as a unit test: python3 kit/tests/test_listener_schedule.py"""
import pathlib
import subprocess
import sys
import unittest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib" / "listener_schedule.py"


class Schedule(unittest.TestCase):
    def test_selftest_passes(self):
        r = subprocess.run([sys.executable, "-I", str(LIB), "--selftest"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL PASS", r.stdout)


if __name__ == "__main__":
    unittest.main()

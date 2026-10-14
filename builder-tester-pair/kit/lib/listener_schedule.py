#!/usr/bin/env python3
"""listener_schedule — when should the agent's repeating check-in (the "listener") run, and how often?

pair.yaml:
  schedule:
    tick_minutes: 3                 # the default interval when no window matches (and the only setting if no windows are given)
    timezone: Asia/Kolkata          # IANA name; the windows below are read in this zone (default UTC)
    listener_windows:               # FIRST match wins
      - {days: [sat, sun], mode: off}                 # weekends: no listener
      - {from: "10:00", to: "19:00", mode: off}       # the owner's working day: no listener, the owner works by hand
      - {mode: every, minutes: 45}                    # every other time: a check-in every 45 minutes
    # a window may have `days` (mon..sun), `from`/`to` ("HH:MM", `to` is exclusive, from > to wraps past midnight), or neither (always)

  listener_schedule.py                 prints "off" or "every <N>" for now
  listener_schedule.py --at "2026-10-14 11:00"   same, for that local time in the configured zone (for checking a schedule)
  listener_schedule.py --selftest

With no `schedule.listener_windows` the answer is always "every <tick_minutes>": nothing changes for pairs that do not set windows.
The watchdog (kit/scripts/agent-watchdog.sh) asks this every minute: "off" stops the agent once it is idle, "every N" keeps it running
with an N-minute check-in. It costs no tokens: the decision is made by a shell loop, not by the model."""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__))))
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _hm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def window_matches(w, local):
    days = w.get("days")
    if days and DAYS[local.weekday()] not in [str(d).lower()[:3] for d in days]:
        return False
    if w.get("from") is not None or w.get("to") is not None:
        a, b, now = _hm(w.get("from", "00:00")), _hm(w.get("to", "24:00")), local.hour * 60 + local.minute
        if a <= b:
            return a <= now < b
        return now >= a or now < b            # wraps past midnight
    return True


def decide(sched, now_utc=None):
    """sched: the `schedule` mapping. Returns ('off', 0) or ('every', minutes)."""
    sched = sched or {}
    default = int(sched.get("tick_minutes") or 3)
    windows = sched.get("listener_windows") or []
    if not windows:
        return ("every", default)
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(str(sched.get("timezone") or "UTC"))
    except Exception:
        tz = datetime.timezone.utc
    now = (now_utc or datetime.datetime.now(datetime.timezone.utc)).astimezone(tz)
    for w in windows:
        if isinstance(w, dict) and window_matches(w, now):
            if str(w.get("mode", "every")).lower() == "off":
                return ("off", 0)
            return ("every", int(w.get("minutes") or default))
    return ("every", default)


def selftest():
    sched = {"tick_minutes": 3, "timezone": "Asia/Kolkata", "listener_windows": [
        {"days": ["sat", "sun"], "mode": "off"}, {"from": "10:00", "to": "19:00", "mode": "off"}, {"mode": "every", "minutes": 45}]}
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    at = lambda s: datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=ist)
    cases = [("2026-10-14 11:00", ("off", 0)),        # Wednesday, working day
             ("2026-10-14 09:59", ("every", 45)),     # just before
             ("2026-10-14 10:00", ("off", 0)),
             ("2026-10-14 18:59", ("off", 0)),
             ("2026-10-14 19:00", ("every", 45)),     # window end is exclusive
             ("2026-10-14 03:00", ("every", 45)),     # night
             ("2026-10-17 12:00", ("off", 0)),        # Saturday
             ("2026-10-17 21:00", ("off", 0)),        # Saturday evening: still off (weekend rule is first)
             ("2026-10-18 23:59", ("off", 0)),        # Sunday
             ("2026-10-19 00:00", ("every", 45))]     # Monday just after midnight
    bad = 0
    for t, want in cases:
        got = decide(sched, at(t))
        bad += got != want
        print("PASS" if got == want else "FAIL", t, got)
    wrap = {"listener_windows": [{"from": "22:00", "to": "06:00", "mode": "off"}], "tick_minutes": 5, "timezone": "UTC"}
    for t, want in (("2026-10-14 23:00", ("off", 0)), ("2026-10-14 05:59", ("off", 0)), ("2026-10-14 06:00", ("every", 5))):
        got = decide(wrap, datetime.datetime.strptime(t, "%Y-%m-%d %H:%M").replace(tzinfo=datetime.timezone.utc))
        bad += got != want
        print("PASS" if got == want else "FAIL", "wrap", t, got)
    got = decide({"tick_minutes": 10})
    bad += got != ("every", 10)
    print("PASS" if got == ("every", 10) else "FAIL", "no windows -> every tick_minutes", got)
    print("ALL PASS" if not bad else f"{bad} FAILED")
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    import pairconf
    cfg = pairconf.load()
    sched = pairconf.get(cfg, "schedule") or {}
    when = None
    if "--at" in sys.argv:
        s = sys.argv[sys.argv.index("--at") + 1]
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(str(sched.get("timezone") or "UTC"))
        except Exception:
            tz = datetime.timezone.utc
        when = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    mode, n = decide(sched, when)
    print("off" if mode == "off" else f"every {n}")


if __name__ == "__main__":
    main()

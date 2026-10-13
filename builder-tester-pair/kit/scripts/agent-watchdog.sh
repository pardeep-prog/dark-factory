#!/usr/bin/env bash
# agent-watchdog.sh — keeps this workspace's agent (builder or tester, from pair.yaml) running unattended.
# Runs in its own tmux session "watchdog-<agent>", started by start-agent.sh. No cron needed.
#  - every minute: a pending chat restart request ("<name> restart" → kit/bin/restart-request) → after 30 s, a
#    fresh claude process. Message ids already honoured are kept, so a re-read never restarts again.
#  - every 10 min: if the agent's tmux session is gone, or claude has exited, start it again
#  - at schedule.fresh_start_utc hours: replace the agent with a FRESH claude process (fresh context AND a fresh
#    usage allowance; /clear alone keeps the old process, which refuses all work once its budget is used up)
#  - builder only, if paths.heartbeat is set: a heartbeat file older than schedule.stall_minutes means the loop
#    stopped → fresh process, at most once per 2 h
# Survives the owner being locked out of the workspace UI; not a workspace rebuild (then run start-agent.sh once).
# Log: ~/.cache/agent-watchdog.log    Stop: tmux kill-session -t watchdog-<agent>   (~ = the agent's own HOME)
set -uo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PC() { python3 "$KIT/kit/lib/pairconf.py" "$@"; }
ROLE="$(PC role)"
case "$ROLE" in builder|tester) ;; *) echo "this host is neither builder.host nor tester.host in pair.yaml"; exit 1 ;; esac
# tmux session name: the agent's name as a slug ("Test Agent" -> test-agent), so it is one safe word everywhere
NAME="$(PC get "$ROLE.name" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_-]+/-/g; s/^-+|-+$//g')"
NAME="${NAME:-$ROLE}"
CMD="/$(PC get "$ROLE.start_command")"
ROOT="$(PC get paths.shared_root)"
HOURS="$(PC get schedule.fresh_start_utc | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin) or []))' 2>/dev/null)"
STALL_MIN="$(PC get schedule.stall_minutes)"; STALL_MIN="${STALL_MIN:-0}"
HEARTBEAT=""; [ "$ROLE" = builder ] && HEARTBEAT="$(PC get paths.heartbeat)"
STALL_COOLDOWN_MIN=120
# HOME and PAIR_CONFIG are passed explicitly: tmux starts commands from the tmux server's environment, not ours, and a
# workspace can hold several agents (each with its own HOME, see agent-home.sh) that must never read each other's pair.yaml.
UMASK_PFX=""; [ -n "$(PC get paths.share_group)" ] && UMASK_PFX="umask 002; "   # builder+tester as 2 users in one workspace
PMODE="$(PC get "$ROLE.permission_mode")"; PMODE="${PMODE:-auto}"     # same setting as start-agent.sh
case "$PMODE" in auto|bypassPermissions|acceptEdits|default|plan) ;; *) PMODE=auto ;; esac
printf -v LAUNCH '%sHOME=%q PAIR_CONFIG=%q PAIR_AGENT_SESSION=%q claude --permission-mode %q %q; exec bash' "$UMASK_PFX" "$HOME" "$(PC path)" "$NAME" "$PMODE" "$CMD"
LOG="$HOME/.cache/agent-watchdog.log"; mkdir -p "$(dirname "$LOG")"
STAMP="$HOME/.cache/agent-watchdog.last-refresh"
LAST_STALL="$HOME/.cache/agent-watchdog.last-stall-refresh"
REQ="$HOME/.cache/agent-watchdog.restart-request"
DONE_IDS="$HOME/.cache/agent-watchdog.restart-done"
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }
age_min() { [ -f "$1" ] && echo $(( ( $(date +%s) - $(stat -c %Y "$1") ) / 60 )) || echo 999999; }

# a fresh process, started in the shared root so the project's .mcp.json, CLAUDE.md and pair.yaml load
# The process being replaced may hold the channel-listener lock; it is gone after this, so release the lock or the
# new agent stands down until the lock goes stale (lock_stale_minutes).
LOCK="$HOME/.claude/channel-listener/state/listener.lock"
release_lock() { [ -f "$LOCK" ] && { log "released listener lock of the replaced process: $(head -c 200 "$LOCK")"; rm -f "$LOCK"; }; return 0; }
refresh() { release_lock; tmux respawn-pane -k -c "$ROOT" -t "$NAME" "$LAUNCH"; }

check_restart_request() {
  [ -f "$REQ" ] || return 0
  [ $(( $(date +%s) - $(stat -c %Y "$REQ") )) -ge 30 ] || return 0
  local id; id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' "$REQ" 2>/dev/null)
  rm -f "$REQ"
  if [ -z "$id" ]; then log "restart request unreadable — dropped"; return 0; fi
  if grep -qxF "$id" "$DONE_IDS" 2>/dev/null; then log "restart request $id already honoured — dropped"; return 0; fi
  echo "$id" >> "$DONE_IDS"
  if tmux has-session -t "$NAME" 2>/dev/null; then log "restart requested from chat ($id) — fresh $CMD in $NAME"; refresh; fi
}

# An expired Claude login shows as "Login expired · Please run /login" on the agent's screen. A fresh process shows the
# same screen, so restarting only churns: log it (once an hour) and leave the restart branches alone. Only the owner's
# /login fixes it; `agents.sh status` prints login=EXPIRED and the exact command.
login_expired() { tmux capture-pane -p -t "$NAME" -S -40 2>/dev/null | tail -n 25 \
  | grep -qiE 'login expired|please run /login|invalid api key|oauth token has expired|not logged in'; }
LAST_LOGIN_LOG="$HOME/.cache/agent-watchdog.last-login-log"
note_login_expired() { [ "$(age_min "$LAST_LOGIN_LOG")" -gt 60 ] || return 0
  log "LOGIN EXPIRED in $NAME: the owner must attach (tmux attach -t $NAME) and run /login; restarting would not help"; touch "$LAST_LOGIN_LOG"; }

# Argus keep-alive (TESTER only). The Argus login is a session with a refresh token: ANY `argus cloud-*` command refreshes the
# access token and rolls the refresh window forward (measured 2026-10-13: a session untouched for 4 days still worked and was
# rewritten). So one cheap read-only call every ~30 min keeps the tester signed in for good, and the bare per-app token file that
# older helpers read (~/.config/argus/tokens/<app>.author, an access token that goes stale in about an hour) is re-written from the
# session each time, file to file, never printed. Re-check condition: at most once per 30 minutes (last-argus-ka file age).
LAST_ARGUS="$HOME/.cache/agent-watchdog.last-argus-ka"; LAST_ARGUS_WARN="$HOME/.cache/agent-watchdog.last-argus-warn"
argus_keepalive() {
  [ "$ROLE" = tester ] || return 0
  local url sess bin suffix apps app ok=0
  url="$(PC get argus.url)"; sess="$HOME/.config/argus/session.json"
  [ -n "$url" ] && [ -f "$sess" ] || return 0
  [ "$(age_min "$LAST_ARGUS")" -ge 30 ] || return 0
  touch "$LAST_ARGUS"
  bin="$HOME/.local/bin/argus"; [ -x "$bin" ] || bin="$(command -v argus || true)"; [ -n "$bin" ] || return 0
  suffix="$(PC get argus.instance_suffix)"
  apps="$(PC get argus.apps | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin) or []))' 2>/dev/null)"
  for app in $apps; do
    if "$bin" cloud-executor-status --control-plane "$url" --instance-id "$app${suffix:+-$suffix}" >/dev/null 2>&1; then
      ok=1
      python3 - "$sess" "$HOME/.config/argus/tokens/$app.author" <<'PY' 2>/dev/null
import json, os, sys
tok = json.load(open(sys.argv[1])).get("access_token", "")
if tok:
    os.makedirs(os.path.dirname(sys.argv[2]), exist_ok=True)
    fd = os.open(sys.argv[2] + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    os.replace(sys.argv[2] + ".new", sys.argv[2])
PY
    fi
  done
  if [ "$ok" = 0 ] && [ -n "$apps" ] && [ "$(age_min "$LAST_ARGUS_WARN")" -gt 60 ]; then
    log "ARGUS SESSION could not refresh: the owner runs 'argus cloud-login --scope author' for this tester (a restart will not fix it)"; touch "$LAST_ARGUS_WARN"
  fi
}

# Unanswered-asks safety net (BUILDER only): every ~30 min, kit/bin/slack-asks reads the watched channels itself with the bot token
# (read only) and puts ONE board need on the dashboard for each Slack request that tags/names the agents, is older than 2 hours and
# got no bot reply and no hand-over (at most 3 new per run). It does not depend on the agent's own judgement, so a missed or
# mis-judged message still surfaces. Re-check condition: at most once per 30 minutes (last-asks file age); needs the bot token.
LAST_ASKS="$HOME/.cache/agent-watchdog.last-asks"
asks_check() {
  [ "$ROLE" = builder ] || return 0
  [ -f "$HOME/.config/slack-fetch/token" ] || return 0
  [ "$(age_min "$LAST_ASKS")" -ge 30 ] || return 0
  touch "$LAST_ASKS"
  local out; out="$(HOME="$HOME" PAIR_CONFIG="$(PC path)" timeout 120 python3 "$KIT/kit/bin/slack-asks" --board --grace-min 120 --max-new 3 2>&1 | tail -3 | tr '\n' ' ')"
  log "slack-asks: ${out:-no output}"
}

# Owner approvals typed in Slack (BUILDER only): every minute `chat-approval scan` reads the owner's DM with the bot (bot token, read only) and turns
# an exact "approve <namespace> <minutes>" from the OWNER's account into a deploy window, then confirms in that DM. Runs here, in a kit process,
# so the owner's message is enough and the agent never touches an approval. Skipped when there is no bot token or no owner-dm channel.
approvals_scan() {
  [ "$ROLE" = builder ] || return 0
  [ -f "$HOME/.config/slack-fetch/token" ] || return 0
  local out; out="$(HOME="$HOME" PAIR_CONFIG="$(PC path)" timeout 90 python3 "$KIT/kit/bin/chat-approval" scan 2>&1 | tail -2 | tr '\n' ' ')"
  case "$out" in "no new approvals"*|"") ;; *) log "chat-approval: $out" ;; esac
}

is_fresh_hour() { for h in $HOURS; do [ "$(date -u +%H)" = "$h" ] && return 0; done; return 1; }

log "watchdog started for $NAME ($ROLE)"
n=0
while true; do
  check_restart_request
  argus_keepalive
  asks_check
  approvals_scan
  if [ $(( n++% 10 )) -ne 0 ]; then sleep 60; continue; fi
  if ! tmux has-session -t "$NAME" 2>/dev/null; then
    log "session $NAME missing — starting $CMD"
    release_lock
    tmux new -d -c "$ROOT" -s "$NAME" "$LAUNCH"
    date -u +%F-%H > "$STAMP"
  elif ! pgrep -x claude -P "$(tmux display -p -t "$NAME" '#{pane_pid}')" >/dev/null; then
    log "claude not running in $NAME — fresh $CMD"
    refresh
    date -u +%F-%H > "$STAMP"
  elif login_expired; then
    note_login_expired
  elif is_fresh_hour && [ "$(cat "$STAMP" 2>/dev/null)" != "$(date -u +%F-%H)" ]; then
    log "scheduled fresh start: $CMD in $NAME"
    refresh
    date -u +%F-%H > "$STAMP"
  elif [ -n "$HEARTBEAT" ] && [ "$STALL_MIN" -gt 0 ] && [ "$(age_min "$HEARTBEAT")" -gt "$STALL_MIN" ] \
       && [ "$(age_min "$LAST_STALL")" -gt "$STALL_COOLDOWN_MIN" ]; then
    log "heartbeat $HEARTBEAT is $(age_min "$HEARTBEAT") min old — loop stalled, fresh $CMD"
    refresh
    touch "$LAST_STALL"
  fi
  sleep 60
done

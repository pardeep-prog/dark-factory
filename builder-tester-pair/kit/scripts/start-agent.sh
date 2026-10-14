#!/usr/bin/env bash
# start-agent.sh — one command per workspace: starts the watchdog, then this workspace's agent (builder or tester,
# decided by hostname in pair.yaml) in the shared root with `claude --permission-mode auto "/<start_command>"`.
# Detach with Ctrl-b then d; it keeps running. Run again after a workspace rebuild.
set -euo pipefail
# ONE-SHOT mode (`--once --task "<what to do>"` or `--task-file FILE`): NO watchdog and NO repeating listener loop. The agent does its setup
# check, works through the task and stops, so nothing keeps spending tokens afterwards. Use it to clear a queue (for example every open
# request) without leaving the 3-minute check-in running; the normal command (no flags) is unchanged.
ONCE=0; TASK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --once) ONCE=1 ;;
    --task) TASK="${2:-}"; shift ;;
    --task-file) TASK="$(cat "${2:-/dev/null}")"; shift ;;
    *) echo "unknown option $1 (use --once --task \"...\" or --once --task-file FILE)"; exit 2 ;;
  esac
  shift
done
[ "$ONCE" = 1 ] && [ -z "$TASK" ] && { echo "--once needs --task \"...\" or --task-file FILE"; exit 2; }
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PC() { python3 "$KIT/kit/lib/pairconf.py" "$@"; }
ROLE="$(PC role)"
case "$ROLE" in builder|tester) ;; *) echo "This host ($(hostname)) is neither builder.host nor tester.host in $(PC path)."; exit 1 ;; esac
# tmux session name: the agent's name as a slug ("Test Agent" -> test-agent), so it is one safe word everywhere
NAME="$(PC get "$ROLE.name" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_-]+/-/g; s/^-+|-+$//g')"
NAME="${NAME:-$ROLE}"
START_CMD="$(PC get "$ROLE.start_command")"
CMD="/$START_CMD"
ROOT="$(PC get paths.shared_root)"
# Optional per-agent HOME (`<role>.agent_home`): lets several pairs share one workspace. See agent-home.sh.
CFG="$(PC path)"
AH="$(bash "$KIT/kit/scripts/agent-home.sh" path)"
if [ -n "$AH" ]; then
  [ -d "$AH" ] || { echo "$AH does not exist: run  bash $KIT/kit/scripts/agent-home.sh init  (then ./install.sh with HOME=$AH)"; exit 1; }
  export HOME="$AH"
fi
export PAIR_CONFIG="$CFG"
# Optional `paths.share_group` (builder and tester as two Linux users in ONE workspace, see tester-users.sh): the tmux
# sessions must carry that group (a group added after login is not in this shell yet) and write group-writable files.
SG="$(PC get paths.share_group)"
if [ -n "$SG" ] && [ -z "${PAIR_SG_REEXEC:-}" ] && ! id -nG | tr ' ' '\n' | grep -qx "$SG"; then
  export PAIR_SG_REEXEC=1; exec sg "$SG" -c "bash $(printf %q "${BASH_SOURCE[0]}")"
fi
UMASK_PFX=""; [ -n "$SG" ] && UMASK_PFX="umask 002; "
WD="watchdog-$NAME"   # one watchdog per agent, so two agents in a workspace never share (or stop) each other's
if [ "$ONCE" = 0 ]; then
  tmux has-session -t "$WD" 2>/dev/null || tmux new -d -c "$ROOT" -s "$WD" "HOME=$(printf %q "$HOME") PAIR_CONFIG=$(printf %q "$CFG") bash $KIT/kit/scripts/agent-watchdog.sh"
else
  CMD="Do your normal setup check and read your rules (the steps of ~/.claude/commands/${START_CMD}.md), but do NOT start the repeating listener loop, schedule no recurring job, and start no watchdog. Then do exactly this task and nothing more, following the usual hand-over flow with your counterpart (use the handoff tool; if you must wait for the other agent, check 'handoff list' about every 5 minutes with sleep, no faster). When the task is done, or you are blocked, write a short summary, tell the owner in the owner DM as your rules say, and stop. TASK: ${TASK}"
fi
if [ -t 0 ] && [ -t 1 ]; then ATTACH=1; else ATTACH=0; fi   # no terminal (e.g. `! …` from Claude): start detached
if tmux has-session -t "$NAME" 2>/dev/null; then
  [ "$ATTACH" = 1 ] && exec tmux attach -t "$NAME"
  echo "$NAME is already running in tmux session '$NAME' (attach: tmux attach -t $NAME)"; exit 0
fi
# No agent session exists, so a listener lock left by a replaced/killed agent is stale: release it, or the new agent
# stands down until it goes stale (lock_stale_minutes).
LOCK="$HOME/.claude/channel-listener/state/listener.lock"
[ -f "$LOCK" ] && { echo "released a listener lock left by the previous agent: $(head -c 200 "$LOCK")"; rm -f "$LOCK"; }
# `<role>.permission_mode` (default auto): how the agent's Claude Code asks for approval. `bypassPermissions` skips every
# approval prompt AND the auto-mode classifier (hooks and permission deny rules are separate and stay in place). The owner
# chooses it per role in pair.yaml, knowingly; an agent never changes it.
PMODE="$(PC get "$ROLE.permission_mode")"; PMODE="${PMODE:-auto}"
case "$PMODE" in auto|bypassPermissions|acceptEdits|default|plan) ;; *) echo "invalid $ROLE.permission_mode '$PMODE' (auto|bypassPermissions|acceptEdits|default|plan)"; exit 1 ;; esac
printf -v LAUNCH '%sHOME=%q PAIR_CONFIG=%q PAIR_AGENT_SESSION=%q claude --permission-mode %q %q; exec bash' "$UMASK_PFX" "$HOME" "$CFG" "$NAME" "$PMODE" "$CMD"
echo "starting $NAME ($ROLE): claude --permission-mode $PMODE \"$CMD\" in tmux session '$NAME' (Ctrl-b then d to leave it running)"
if [ "$ATTACH" = 1 ]; then
  exec tmux new -c "$ROOT" -s "$NAME" "$LAUNCH"
fi
tmux new -d -c "$ROOT" -s "$NAME" "$LAUNCH"
echo "started detached — watch it: tmux attach -t $NAME"

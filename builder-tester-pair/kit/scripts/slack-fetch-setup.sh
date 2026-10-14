#!/usr/bin/env bash
# slack-fetch-setup.sh — the OWNER runs this once, in their own terminal, to give the TESTER agents a Slack token with
# files:read so `slack-fetch` can download videos of any size. An agent must never run it: it handles a credential.
#
#   PAIRS_DIR=/path/to/pairs bash kit/scripts/slack-fetch-setup.sh   # every tester in $PAIRS_DIR/*/pair.yaml (or PAIR_CONFIG's parent folder)
#   bash kit/scripts/slack-fetch-setup.sh --user wong --home /home/coder/agents/wong [--user vision --home ...]
#   bash kit/scripts/slack-fetch-setup.sh --allow-extra-scopes # accept a token that has more than files:read
#
# The token is typed at a hidden prompt (never echoed, never in argv or shell history), checked with Slack's auth.test
# (prints only the workspace and bot name and the scope list), then written mode 600 into each tester's
# ~/.config/slack-fetch/token through stdin. Nothing it prints contains the token. Refuses a token that holds more power
# than files:read unless you say so: a tester that can only read files cannot post, edit or delete anything.
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
API="${SLACK_API:-https://slack.com/api}"
USERS=(); HOMES=(); EXTRA=0
while [ $# -gt 0 ]; do case "$1" in
  --user) USERS+=("$2"); shift 2 ;; --home) HOMES+=("$2"); shift 2 ;;
  --allow-extra-scopes) EXTRA=1; shift ;; -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
  *) echo "unknown argument: $1" >&2; exit 2 ;; esac; done

if [ ${#USERS[@]} -eq 0 ]; then                       # discover every tester from the pair configs
  while IFS=$'\t' read -r u h; do USERS+=("$u"); HOMES+=("$h"); done < <(python3 - "$KIT" <<'PY'
import glob, os, sys
sys.path.insert(0, sys.argv[1] + "/kit/lib")
import pairconf
seen = set()
# The pairs folder is read from PAIRS_DIR, else from the folder that holds this pair's own pair.yaml (PAIR_CONFIG); with neither, nothing is guessed.
pairs_dir = os.environ.get("PAIRS_DIR") or (os.path.dirname(os.path.dirname(os.path.abspath(os.environ["PAIR_CONFIG"]))) if os.environ.get("PAIR_CONFIG") else "")
for p in sorted(glob.glob(os.path.join(pairs_dir, "*", "pair.yaml"))) if pairs_dir else []:
    try:
        c = pairconf.load(p)
    except (Exception, SystemExit):
        continue
    u, h = pairconf.get(c, "tester.user"), pairconf.get(c, "tester.agent_home")
    if u and h and u not in seen:
        seen.add(u); print(f"{u}\t{os.path.expanduser(str(h))}")
PY
)
fi
[ ${#USERS[@]} -gt 0 ] || { echo "no tester users found: pass --user NAME --home DIR" >&2; exit 2; }
[ ${#USERS[@]} -eq ${#HOMES[@]} ] || { echo "give one --home per --user" >&2; exit 2; }
echo "Will install the token for: ${USERS[*]}"

printf 'Paste the Slack token (input is hidden, nothing is shown): ' >&2
IFS= read -rs TOKEN; echo >&2
[ -n "$TOKEN" ] || { echo "empty input, nothing done" >&2; exit 1; }

# Validate with auth.test; the token goes in a header read from stdin, never on the command line.
# The response body goes to a private mktemp file (mode 600, unpredictable name, removed on every exit): a fixed /tmp/.name.$$ could be
# pre-created by another local user as a symlink and curl would write through it as the owner.
BODYF="$(mktemp "${TMPDIR:-/tmp}/slack-setup.XXXXXXXX")"; trap 'rm -f "$BODYF"' EXIT
RESULT="$(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl -sS -m 20 -D - -K - -X POST "$API/auth.test" -o "$BODYF" || true)"
BODY="$(cat "$BODYF" 2>/dev/null || true)"
SCOPES="$(printf '%s' "$RESULT" | tr -d '\r' | awk -F': ' 'tolower($1)=="x-oauth-scopes"{print $2}')"
read -r OK WHO < <(printf '%s' "$BODY" | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
print("yes" if d.get("ok") else "no", (d.get("team","?")+"/"+d.get("user","?")).replace(" ","_"))')
if [ "$OK" != yes ]; then unset TOKEN; echo "Slack did not accept this token (auth.test failed). Nothing was saved." >&2; exit 1; fi
echo "Slack accepts it: workspace/bot = $WHO ; scopes = ${SCOPES:-unknown}"
case ",${SCOPES// /}," in *,files:read,*) ;; *) unset TOKEN; echo "This token does not have files:read. Nothing saved." >&2; exit 1 ;; esac
if [ "$EXTRA" = 0 ] && [ "${SCOPES// /}" != "files:read" ]; then
  unset TOKEN; echo "This token has more than files:read (${SCOPES}). A tester only needs to read files: create a token with just files:read, or re-run with --allow-extra-scopes if you accept that. Nothing saved." >&2; exit 1
fi

for i in "${!USERS[@]}"; do
  u="${USERS[$i]}"; h="${HOMES[$i]}"; dir="$h/.config/slack-fetch"
  if [ "$u" = "$(id -un)" ]; then RUN=(env); else RUN=(sudo -u "$u" env); fi
  "${RUN[@]}" mkdir -p "$dir"; "${RUN[@]}" chmod 700 "$dir"
  printf '%s\n' "$TOKEN" | "${RUN[@]}" sh -c 'umask 077; cat > "$1/token.new" && mv "$1/token.new" "$1/token"' _ "$dir"
  state="$("${RUN[@]}" env HOME="$h" python3 -I "$KIT/kit/bin/slack-fetch" --check 2>&1 || true)"
  echo "  $u: $state  ($dir/token, mode 600)"
done
unset TOKEN
echo "Done. The token was never printed. Undo any time: delete ~/.config/slack-fetch/token for that tester, or revoke the app in Slack."

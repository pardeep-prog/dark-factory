#!/usr/bin/env bash
# move-tester.sh — PREPARES the pair.yaml change for moving the tester into its own workspace. It applies nothing:
# it writes <pair>/pair.yaml.separate.proposed next to the live file and prints the diff. The OWNER reviews it and
# copies it over (`cp pair.yaml.separate.proposed pair.yaml`) at the moment the new workspace is ready, because the
# change flips guard.workspace_mode to `separate` (the strict check) for BOTH agents.
#
#   bash kit/scripts/move-tester.sh --pair chairside --host-pattern 'coder-<new-workspace-id>-*'
#
# In the NEW workspace, run `hostname` to get coder-<id>-<hash>-<x>, and use coder-<id>-* as the pattern.
# Changes (see docs/SETUP.md "Moving the tester to its own workspace later"): tester.host = the pattern; removed:
# tester.user, tester.uid, paths.share_group, guard.block_privilege_escalation; guard.workspace_mode: single -> separate.
set -euo pipefail
PAIR=""; PAT=""; DIR="${PAIRS_DIR:-/user/shared/pairs}"
while [ $# -gt 0 ]; do case "$1" in
  --pair) PAIR="$2"; shift 2 ;; --host-pattern) PAT="$2"; shift 2 ;; --pairs-dir) DIR="$2"; shift 2 ;;
  -h|--help) sed -n '2,12p' "$0"; exit 0 ;; *) echo "unknown argument: $1" >&2; exit 2 ;; esac; done
[ -n "$PAIR" ] && [ -n "$PAT" ] || { echo "need --pair NAME and --host-pattern 'coder-<id>-*'" >&2; exit 2; }
case "$PAT" in coder-*-\*) ;; *) echo "pattern should look like coder-<workspace-id>-*" >&2; exit 2 ;; esac
SRC="$DIR/$PAIR/pair.yaml"; OUT="$SRC.separate.proposed"
[ -f "$SRC" ] || { echo "no such file: $SRC" >&2; exit 1; }
python3 - "$SRC" "$OUT" "$PAT" <<'PY'
import re, sys
src, out, pat = sys.argv[1:4]
section, drop_top = None, {("paths", "share_group"), ("guard", "block_privilege_escalation"), ("tester", "user"), ("tester", "uid")}
res, seen = [], set()
for line in open(src).read().splitlines():
    m_top = re.match(r"^([A-Za-z_][\w]*):", line)
    if m_top:
        section = m_top.group(1)
    m_key = re.match(r"^  ([A-Za-z_][\w]*):", line)
    key = (section, m_key.group(1)) if m_key else None
    if key in drop_top:
        seen.add(key); continue
    if key == ("tester", "host"):
        line = re.sub(r'host:\s*"[^"]*"|host:\s*\S+', f'host: "{pat}"', line, count=1); seen.add(key)
    if key == ("guard", "workspace_mode"):
        line = re.sub(r"workspace_mode:\s*\w+", "workspace_mode: separate", line, count=1); seen.add(key)
    res.append(line)
need = {("tester", "host"), ("guard", "workspace_mode")}
if not need <= seen:
    sys.exit("could not find tester.host and guard.workspace_mode in " + src)
open(out, "w").write("\n".join(res) + "\n")
PY
echo "wrote $OUT (the live $SRC is untouched). Diff:"
diff -u "$SRC" "$OUT" || true
cat <<EOF

Next, in this order (owner):
  1. In the NEW workspace: mount/confirm the same shared folder ($DIR), install Claude, then run ./setup --steps and
     start the tester there with its own sign-ins (Claude /login, gh, Argus author device codes).
  2. Here: cp "$OUT" "$SRC"   (the strict check then applies to both agents), then ./setup-pair render and restart the builder.
  3. Here: stop the old same-workspace tester:  agents.sh stop <tester name>
EOF

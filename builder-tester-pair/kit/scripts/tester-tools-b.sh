#!/usr/bin/env bash
# tester-tools-b.sh — OWNER runs this in the TESTER workspace (needs sudo) to give the testers the toolchain the builder
# workspace already has: the browser for Chairside's node test battery, and PostgreSQL 16 + pgvector for Engram's local
# test run. Idempotent. It installs packages and links caches; it signs nothing in and reads no credential.
#
#   sudo -v && bash kit/scripts/tester-tools-b.sh [--dry-run]
#
# The browser and node modules are staged on the shared disk (/user/shared/tools/tester-cache, copied once from the old
# tester home) and LINKED into each tester's ~/.cache, so nothing is downloaded and nothing is built.
set -uo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
run() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
say() { printf '\n== %s\n' "$*"; }
CACHE="${TESTER_CACHE:-/user/shared/tools/tester-cache}"
HOMES=("${CHAIRSIDE_TESTER_HOME:-/home/coder/agents/wong}" "${ENGRAM_TESTER_HOME:-/home/coder/agents/vision}")

say "1/3 system packages (lost when the workspace image restarts: re-run this after a rebuild)"
PKGS="postgresql-16 postgresql-16-pgvector postgresql-client-16 libnss3 libgbm1 libasound2t64 libatk-bridge2.0-0t64 libcups2t64 libatk1.0-0t64 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0 libcairo2 libdrm2 ffmpeg"
MISSING=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"; done
if [ -n "$MISSING" ]; then echo "install:$MISSING"; run sudo apt-get update -qq; run sudo apt-get install -y $MISSING >/dev/null; else echo "ok   all packages present"; fi

say "2/3 the browser and node modules for the Chairside battery, linked into the tester home"
if [ ! -d "$CACHE/ms-playwright" ] || [ ! -d "$CACHE/chairside-deploy" ]; then
  echo "MISSING $CACHE/{ms-playwright,chairside-deploy}: they are staged from workspace A (ask the builder side to re-copy them)"; exit 1
fi
for h in "${HOMES[@]}"; do
  [ -d "$h" ] || { echo "skip $h (no such home: run agent-home.sh init first)"; continue; }
  run mkdir -p "$h/.cache"
  for d in ms-playwright chairside-deploy; do
    if [ -e "$h/.cache/$d" ] && [ ! -L "$h/.cache/$d" ]; then echo "keep $h/.cache/$d (a real folder is already there)"
    else run ln -sfn "$CACHE/$d" "$h/.cache/$d"; echo "link $h/.cache/$d -> $CACHE/$d"; fi
  done
done

say "3/3 check"
for p in postgresql-16 postgresql-16-pgvector libnss3 libgbm1 ffmpeg; do dpkg -s "$p" >/dev/null 2>&1 && echo "ok   $p" || echo "MISSING $p"; done
[ -x /usr/lib/postgresql/16/bin/initdb ] && echo "ok   postgres 16 server binaries" || echo "MISSING /usr/lib/postgresql/16/bin/initdb"
ls "$CACHE/ms-playwright" 2>/dev/null | sed 's/^/ok   browser: /'
echo
echo "Go for Engram's tests is fetched by the test script on first use through the agent's own login (GOTOOLCHAIN=auto)."

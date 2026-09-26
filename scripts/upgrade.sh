#!/usr/bin/env bash
# Upgrade NanoGate in place: backup -> pull -> dependencies -> dashboard build -> restart -> readiness check.
# If the new version does not become ready, the previous code is checked out and restarted automatically.
# Schema migrations are additive, so the previous code keeps working with the upgraded database.
# Usage: scripts/upgrade.sh [git-ref]   (default: the current branch's upstream)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "local changes present: commit or stash them first" >&2; exit 1; }
PREV="$(git rev-parse HEAD)"
echo "== backup"
.venv/bin/python scripts/backup.py --kind pre-upgrade
echo "== fetch"
git fetch --quiet
if [[ -n "${1:-}" ]]; then git checkout --quiet "$1"; else git merge --ff-only --quiet '@{u}'; fi
NEW="$(git rev-parse HEAD)"
[[ "$PREV" != "$NEW" ]] || { echo "already up to date ($NEW)"; exit 0; }
echo "== $PREV -> $NEW"
build() {
  .venv/bin/pip install -q -r requirements.txt
  (cd frontend && PATH="$ROOT/.runtime/node/bin:$PATH" npm ci --silent && PATH="$ROOT/.runtime/node/bin:$PATH" npm run build --silent)
}
rollback() {
  echo "== upgrade failed: rolling back to $PREV" >&2
  git checkout --quiet "$PREV"
  build || true
  scripts/gateway.sh restart || true
  exit 1
}
build || rollback
scripts/gateway.sh restart || rollback
.venv/bin/python scripts/wait_ready.py || rollback
echo "== upgraded to $(git log --oneline -1)"

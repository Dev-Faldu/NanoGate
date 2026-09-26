#!/usr/bin/env bash
# Restore NanoGate from a backup archive (made on the Operations page, by the schedule, or by upgrade.sh).
# The current database and keys are moved aside first, so a restore can itself be undone.
# Usage: scripts/restore.sh var/backups/bk_....tar.gz
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="${1:?usage: scripts/restore.sh <backup.tar.gz>}"
[[ -f "$ARCHIVE" ]] || { echo "no such file: $ARCHIVE" >&2; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
tar -xzf "$ARCHIVE" -C "$TMP"
for f in manifest.json nanogate.db key_pepper receipt_hmac.key; do
  [[ -f "$TMP/$f" ]] || { echo "not a NanoGate backup: $f missing" >&2; exit 1; }
done
"$ROOT/.venv/bin/python" - "$TMP/nanogate.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "database integrity check failed"
print("backup database: integrity ok,", c.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], "receipts")
PY
echo "restoring $(python3 -c "import json;m=json.load(open('$TMP/manifest.json'));print(m['backup_id'], 'from commit', m.get('commit'))")"
"$ROOT/scripts/gateway.sh" stop || true
ASIDE="$ROOT/var/pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ASIDE" && chmod 700 "$ASIDE"
for f in nanogate.db nanogate.db-wal nanogate.db-shm key_pepper receipt_hmac.key; do
  [[ -e "$ROOT/var/$f" ]] && mv "$ROOT/var/$f" "$ASIDE/"
done
install -m 600 "$TMP/nanogate.db" "$ROOT/var/nanogate.db"
install -m 600 "$TMP/key_pepper" "$ROOT/var/key_pepper"
install -m 600 "$TMP/receipt_hmac.key" "$ROOT/var/receipt_hmac.key"
echo "previous state kept in $ASIDE"
[[ -d "$TMP/config" ]] && echo "note: the backup also holds policies/pricing in config/ ($(ls "$TMP/config" | tr '\n' ' ')); compare with config/ before copying"
"$ROOT/scripts/gateway.sh" start

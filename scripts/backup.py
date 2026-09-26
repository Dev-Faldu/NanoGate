"""Take a backup from the command line (used by upgrade.sh; the gateway also backs up on schedule).
Usage: .venv/bin/python scripts/backup.py [--kind manual|pre-upgrade]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from nanogate.db import Database  # noqa: E402
from nanogate.events import bus  # noqa: E402
from nanogate.ops import AuditLog, Backups, OpsSettings  # noqa: E402
from nanogate.services import git_commit  # noqa: E402
from nanogate.settings import get_settings  # noqa: E402


class _Svc:
    def __init__(self):
        self.settings = get_settings()
        self.db = Database(self.settings.db_path)
        self.db.migrate()
        self.commit = git_commit()
        self.bus = bus
        self.ops_settings = OpsSettings(self.db)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="manual", choices=["manual", "pre-upgrade"])
    svc = _Svc()
    b = Backups(svc, AuditLog(svc.db, svc.bus)).create(ap.parse_args().kind)
    print(f"backup {b['backup_id']}: {svc.settings.data_dir / 'backups' / b['filename']} ({b['bytes']} bytes, sha256 {b['sha256'][:16]}…)")

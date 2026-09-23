"""Create API keys for every tenant/department in config/policies.yaml.

Raw keys are written ONCE to var/dev_keys.json (mode 0600, git-ignored) and never printed.
Only HMAC hashes are stored in the database. Re-running is idempotent unless --rotate is given.

Keys created:
  admin                acme/it   scopes [admin, chat]   dashboard login
  app:<tenant>/<dept>  scopes [chat]                      for OpenAI SDK clients
  playground:<t>/<d>   scopes [chat]                      used server-side by the dashboard playground
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from nanogate.auth import KeyStore, load_pepper  # noqa: E402
from nanogate.db import Database  # noqa: E402
from nanogate.policy import PolicyEngine  # noqa: E402
from nanogate.settings import get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotate", action="store_true", help="revoke existing keys and issue new ones")
    args = ap.parse_args()
    s = get_settings()
    out = s.data_dir / "dev_keys.json"
    db = Database(s.db_path)
    db.migrate()
    pe = PolicyEngine(db, s.policies_file)
    pe.load()
    ks = KeyStore(db, load_pepper(s.data_dir / "key_pepper"))
    if out.exists() and not args.rotate:
        existing = json.loads(out.read_text())
        live = {r["key_id"] for r in db.all("SELECT key_id FROM api_keys WHERE revoked_at IS NULL")}
        if all(v["key_id"] in live for v in existing["keys"].values()):
            print(f"keys already provisioned ({len(existing['keys'])}) in {out.relative_to(ROOT)} (not printed)")
            return 0
    if args.rotate:
        for r in db.all("SELECT key_id FROM api_keys WHERE revoked_at IS NULL"):
            ks.revoke(r["key_id"])
    keys: dict[str, dict] = {}
    kid, raw = ks.create("acme", "it", "admin", ["admin", "chat"], label="admin")
    keys["admin"] = {"key_id": kid, "key": raw, "tenant": "acme", "department": "it", "scopes": ["admin", "chat"]}
    for tenant, t in pe.tenants.items():
        for dept in t["departments"]:
            for kind in ("app", "playground"):
                kid, raw = ks.create(tenant, dept, "member", ["chat"], label=f"{kind}:{tenant}/{dept}")
                keys[f"{kind}:{tenant}/{dept}"] = {"key_id": kid, "key": raw, "tenant": tenant, "department": dept,
                                                   "scopes": ["chat"]}
    tmp = out.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"note": "Development keys. Never commit. Hashes only are stored in the database.", "keys": keys}, f, indent=2)
    tmp.replace(out)
    print(f"provisioned {len(keys)} keys -> {out.relative_to(ROOT)} (mode 0600, not printed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

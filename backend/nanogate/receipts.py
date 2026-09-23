"""Decision receipts: canonical JSON, SHA-256 hash chain, HMAC-SHA256 seal.

hash_n = SHA256(prev_hash_n || canonical_json(body_n)),   hmac_n = HMAC(server_key, hash_n)
Any mutation of a stored body, reordering, or deletion inside the chain is detectable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path

from .db import Database

GENESIS = "0" * 64


def canonical(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


class ReceiptStore:
    def __init__(self, db: Database, key_file: Path):
        self.db = db
        if key_file.exists():
            self.key = key_file.read_bytes()
        else:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            self.key = secrets.token_bytes(32)
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(self.key)
        self.key_id = hashlib.sha256(self.key).hexdigest()[:12]
        self._lock = threading.Lock()

    @staticmethod
    def new_id() -> str:
        return "rcpt_" + uuid.uuid4().hex[:20]

    def seal(self, receipt_id: str, request_id: str, tenant_id: str, department_id: str, reason: str, body: dict) -> dict:
        with self._lock, self.db.tx() as c:
            last = c.execute("SELECT hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else GENESIS
            body = {**body, "integrity": {**body.get("integrity", {}), "previous_receipt_hash": prev,
                                          "hmac_key_id": self.key_id, "algorithm": "sha256-chain+hmac-sha256"}}
            canon = canonical(body)
            h = hashlib.sha256((prev + canon).encode()).hexdigest()
            mac = hmac.new(self.key, h.encode(), hashlib.sha256).hexdigest()
            ts = time.time()
            c.execute("INSERT INTO receipts(receipt_id, request_id, ts, tenant_id, department_id, reason, body_json, prev_hash,"
                      " hash, hmac) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (receipt_id, request_id, ts, tenant_id, department_id, reason, canon, prev, h, mac))
        return {"receipt_id": receipt_id, "hash": h, "prev_hash": prev, "hmac": mac}

    def get(self, receipt_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM receipts WHERE receipt_id=?", (receipt_id,))
        if not r:
            return None
        return {"receipt_id": r["receipt_id"], "request_id": r["request_id"], "seq": r["seq"], "ts": r["ts"],
                "reason": r["reason"], "body": json.loads(r["body_json"]), "hash": r["hash"], "prev_hash": r["prev_hash"],
                "hmac": r["hmac"]}

    def verify(self, receipt_id: str) -> dict:
        r = self.db.one("SELECT * FROM receipts WHERE receipt_id=?", (receipt_id,))
        if not r:
            return {"receipt_id": receipt_id, "valid": False, "checks": {"exists": False}}
        recomputed = hashlib.sha256((r["prev_hash"] + r["body_json"]).encode()).hexdigest()
        mac = hmac.new(self.key, r["hash"].encode(), hashlib.sha256).hexdigest()
        prev_row = self.db.one("SELECT hash FROM receipts WHERE seq<? ORDER BY seq DESC LIMIT 1", (r["seq"],))
        expected_prev = prev_row["hash"] if prev_row else GENESIS
        nxt = self.db.one("SELECT prev_hash FROM receipts WHERE seq>? ORDER BY seq ASC LIMIT 1", (r["seq"],))
        body_prev = json.loads(r["body_json"]).get("integrity", {}).get("previous_receipt_hash")
        checks = {
            "exists": True,
            "body_hash_matches": recomputed == r["hash"],
            "hmac_valid": hmac.compare_digest(mac, r["hmac"]),
            "chain_link_valid": r["prev_hash"] == expected_prev and body_prev == r["prev_hash"],
            "successor_link_valid": (nxt is None) or (nxt["prev_hash"] == r["hash"]),
        }
        return {"receipt_id": receipt_id, "valid": all(checks.values()), "checks": checks, "hash": r["hash"],
                "recomputed_hash": recomputed, "prev_hash": r["prev_hash"], "verified_at": time.time()}

    def verify_chain(self, limit: int | None = None) -> dict:
        rows = self.db.all("SELECT seq, receipt_id, body_json, prev_hash, hash, hmac FROM receipts ORDER BY seq"
                           + (f" LIMIT {int(limit)}" if limit else ""))
        prev = GENESIS
        bad = []
        for r in rows:
            h = hashlib.sha256((r["prev_hash"] + r["body_json"]).encode()).hexdigest()
            mac = hmac.new(self.key, r["hash"].encode(), hashlib.sha256).hexdigest()
            if r["prev_hash"] != prev or h != r["hash"] or not hmac.compare_digest(mac, r["hmac"]):
                bad.append(r["receipt_id"])
            prev = r["hash"]
        return {"receipts": len(rows), "valid": not bad, "invalid_receipts": bad[:50], "head": prev}

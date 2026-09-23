"""API-key authentication and server-side identity.

Key format:  ng_live_<key_id>_<secret>. Only HMAC-SHA256(pepper, key) is stored.
Identity (tenant, department, role, scopes, policy) comes exclusively from the key record.
Client-supplied identity headers are never authoritative: if they disagree with the key's
identity the request is rejected (TENANT_SPOOF_REJECTED / DEPARTMENT_SPOOF_REJECTED).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .db import Database
from .reason_codes import Reason

TENANT_HEADERS = ("x-tenant", "x-tenant-id", "openai-organization-tenant")
DEPT_HEADERS = ("x-department", "x-department-id", "x-dept")
ROLE_HEADERS = ("x-role", "x-user-role")


@dataclass
class Identity:
    key_id: str
    key_hash: str
    tenant_id: str
    department_id: str
    role: str
    scopes: list[str]
    policy_id: str
    label: str | None = None

    def has(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes

    def public(self) -> dict:
        return {"key_id": self.key_id, "key_hash": self.key_hash[:16], "tenant_id": self.tenant_id,
                "department_id": self.department_id, "role": self.role, "scopes": self.scopes,
                "policy_id": self.policy_id}


class AuthError(Exception):
    def __init__(self, reason: Reason, status: int, message: str, identity: Identity | None = None):
        super().__init__(message)
        self.reason, self.status, self.message, self.identity = reason, status, message, identity


def load_pepper(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    p = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(p)
    return p


class KeyStore:
    def __init__(self, db: Database, pepper: bytes):
        self.db, self.pepper = db, pepper

    def hash_key(self, raw: str) -> str:
        return hmac.new(self.pepper, raw.encode(), hashlib.sha256).hexdigest()

    def create(self, tenant_id: str, department_id: str, role: str, scopes: list[str], label: str = "",
               ttl_s: float | None = None) -> tuple[str, str]:
        key_id = secrets.token_hex(4)
        raw = f"ng_live_{key_id}_{secrets.token_urlsafe(24)}"
        now = time.time()
        self.db.execute(
            "INSERT INTO api_keys(key_id,key_hash,prefix,tenant_id,department_id,role,scopes,label,created_at,expires_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (key_id, self.hash_key(raw), raw[:16], tenant_id, department_id, role, json.dumps(scopes), label, now,
             now + ttl_s if ttl_s else None))
        return key_id, raw

    def revoke(self, key_id: str) -> None:
        self.db.execute("UPDATE api_keys SET revoked_at=? WHERE key_id=?", (time.time(), key_id))

    def expire_now(self, key_id: str) -> None:
        self.db.execute("UPDATE api_keys SET expires_at=? WHERE key_id=?", (time.time() - 1, key_id))

    def list(self) -> list[dict]:
        return self.db.all("SELECT key_id,prefix,tenant_id,department_id,role,scopes,label,created_at,expires_at,revoked_at"
                           " FROM api_keys ORDER BY created_at")

    def resolve(self, raw: str | None, policy_for: callable) -> Identity:
        if not raw:
            raise AuthError(Reason.AUTH_INVALID, 401, "missing API key")
        h = self.hash_key(raw)
        row = self.db.one("SELECT * FROM api_keys WHERE key_hash=?", (h,))
        if not row:
            raise AuthError(Reason.AUTH_INVALID, 401, "invalid API key")
        if row["revoked_at"]:
            raise AuthError(Reason.AUTH_REVOKED, 401, "API key revoked")
        if row["expires_at"] and row["expires_at"] < time.time():
            raise AuthError(Reason.AUTH_EXPIRED, 401, "API key expired")
        return Identity(key_id=row["key_id"], key_hash=h, tenant_id=row["tenant_id"],
                        department_id=row["department_id"], role=row["role"], scopes=json.loads(row["scopes"]),
                        policy_id=policy_for(row["tenant_id"], row["department_id"]), label=row["label"])


def bearer(authorization: str | None, x_api_key: str | None = None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return x_api_key


def check_spoofing(ident: Identity, headers: dict[str, str]) -> None:
    """Identity headers are advisory at best; a mismatch with the authenticated principal is an attack."""
    h = {k.lower(): v for k, v in headers.items()}
    for name in TENANT_HEADERS:
        if name in h and h[name].strip() != ident.tenant_id:
            raise AuthError(Reason.TENANT_SPOOF_REJECTED, 403,
                            f"header {name} does not match authenticated tenant", ident)
    for name in DEPT_HEADERS:
        if name in h and h[name].strip().lower() != ident.department_id.lower():
            raise AuthError(Reason.DEPARTMENT_SPOOF_REJECTED, 403,
                            f"header {name} does not match authenticated department", ident)
    for name in ROLE_HEADERS:
        if name in h and h[name].strip().lower() != ident.role.lower():
            raise AuthError(Reason.DEPARTMENT_SPOOF_REJECTED, 403,
                            f"header {name} does not match authenticated role", ident)


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    """Token bucket per key; capacity/refill from the department policy (requests per minute)."""
    buckets: dict[str, _Bucket] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, key_id: str, rpm: int) -> bool:
        now = time.monotonic()
        with self.lock:
            b = self.buckets.get(key_id)
            if b is None:
                b = self.buckets[key_id] = _Bucket(float(rpm), now)
            b.tokens = min(float(rpm), b.tokens + (now - b.updated) * rpm / 60.0)
            b.updated = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True
            return False

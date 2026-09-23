"""The single authoritative policy engine.

YAML seeds version 1 of each policy; publishing stores a new immutable version in the DB.
Every other module consumes `PolicyDecision` and never re-implements policy logic.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .db import Database
from .reason_codes import Reason

DATA_CLASSES = ["Public", "Internal", "Confidential", "Restricted", "Secret"]
ROUTES = ["cache", "local", "local_large", "remote"]


def class_rank(c: str) -> int:
    return DATA_CLASSES.index(c)


class RemoteCfg(BaseModel):
    enabled: bool = False
    max_data_class: Literal["Public", "Internal", "Confidential", "Restricted", "Secret"] = "Public"


class DlpCfg(BaseModel):
    pii_action: Literal["allow", "local_only", "redact", "tokenize", "deny", "human_review"] = "local_only"
    secret_action: Literal["deny", "redact", "human_review"] = "deny"
    fail_mode: Literal["closed", "local_only"] = "closed"
    output_scan: bool = True
    output_pii_action: Literal["allow", "allow_if_in_input", "redact", "block"] = "redact"


class CacheCfg(BaseModel):
    enabled: bool = True
    max_data_class: Literal["Public", "Internal", "Confidential", "Restricted", "Secret"] = "Internal"
    ttl_s: int = 86400


class RetentionCfg(BaseModel):
    store_raw_prompts: bool = False
    receipt_days: int = 365


class BudgetCfg(BaseModel):
    monthly_usd: float = 50.0
    monthly_tokens: int = 5_000_000
    max_tokens_per_request: int = 1024


class RateCfg(BaseModel):
    rpm: int = 120


class RouterCfg(BaseModel):
    abstain_action: Literal["local_flagged", "refuse"] = "local_flagged"
    threshold: float | None = None  # None -> validation-selected threshold from the router artifact


class Policy(BaseModel):
    policy_id: str = ""
    version: int = 1
    description: str = ""
    routes: list[Literal["cache", "local", "local_large", "remote"]] = Field(default_factory=lambda: ["cache", "local"])
    remote: RemoteCfg = RemoteCfg()
    dlp: DlpCfg = DlpCfg()
    cache: CacheCfg = CacheCfg()
    retention: RetentionCfg = RetentionCfg()
    budget: BudgetCfg = BudgetCfg()
    rate_limit: RateCfg = RateCfg()
    router: RouterCfg = RouterCfg()
    knowledge_sources: list[str] = Field(default_factory=list)

    @property
    def version_tag(self) -> str:
        return f"{self.policy_id}-v{self.version}"

    def body(self) -> dict:
        return self.model_dump(exclude={"policy_id", "version"})

    def body_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True).encode()).hexdigest()


@dataclass
class PolicyDecision:
    policy_id: str
    policy_version: str
    data_class: str
    action: str                                   # allow | local_only | redact | tokenize | deny | human_review
    allowed_routes: list[str]
    denied_routes: dict[str, str]                 # route -> reason
    reason_codes: list[str]
    egress_permitted: bool
    egress_reason: str
    cache_eligible: bool
    findings_summary: dict[str, int] = field(default_factory=dict)
    transform: str | None = None                  # redact | tokenize | None

    @property
    def denied(self) -> bool:
        return self.action in ("deny", "human_review")

    def as_dict(self) -> dict:
        return {
            "policy_id": self.policy_id, "policy_version": self.policy_version, "data_class": self.data_class,
            "action": self.action, "allowed_routes": self.allowed_routes, "denied_routes": self.denied_routes,
            "reason_codes": self.reason_codes, "egress": {"permitted": self.egress_permitted, "reason": self.egress_reason},
            "cache_eligible": self.cache_eligible, "findings": self.findings_summary, "transform": self.transform,
        }


class PolicyEngine:
    def __init__(self, db: Database, yaml_path: Path):
        self.db = db
        self.yaml_path = yaml_path
        self.tenants: dict = {}
        self._current: dict[str, Policy] = {}
        self.on_publish: list = []   # callbacks(policy_id, old_tag, new_tag)

    # ---- loading / versioning -------------------------------------------------------------
    def load(self) -> None:
        doc = yaml.safe_load(self.yaml_path.read_text())
        self.tenants = doc["tenants"]
        now = time.time()
        with self.db.tx() as c:
            for tid, t in self.tenants.items():
                c.execute("INSERT OR IGNORE INTO tenants(tenant_id, display_name, created_at) VALUES (?,?,?)",
                          (tid, t["display_name"], now))
                for did, d in t["departments"].items():
                    c.execute("INSERT OR REPLACE INTO departments(tenant_id, department_id, display_name, policy_id)"
                              " VALUES (?,?,?,?)", (tid, did, d["display_name"], d["policy"]))
            for pid, body in doc["policies"].items():
                exists = c.execute("SELECT 1 FROM policies WHERE policy_id=?", (pid,)).fetchone()
                if not exists:
                    p = Policy(policy_id=pid, version=1, **body)
                    c.execute("INSERT INTO policies(policy_id, version, version_tag, status, body_json, body_sha256,"
                              " published_at, published_by) VALUES (?,?,?,?,?,?,?,?)",
                              (pid, 1, p.version_tag, "published", json.dumps(p.body()), p.body_sha256(), now, "seed:yaml"))
        self._reload_current()

    def _reload_current(self) -> None:
        rows = self.db.all("SELECT p.* FROM policies p JOIN (SELECT policy_id, MAX(version) v FROM policies "
                           "WHERE status='published' GROUP BY policy_id) m ON p.policy_id=m.policy_id AND p.version=m.v")
        self._current = {r["policy_id"]: Policy(policy_id=r["policy_id"], version=r["version"],
                                                **json.loads(r["body_json"])) for r in rows}

    def policy_for(self, tenant_id: str, department_id: str) -> str:
        return self.tenants[tenant_id]["departments"][department_id]["policy"]

    def get(self, policy_id: str) -> Policy:
        return self._current[policy_id]

    def list(self) -> list[dict]:
        out = []
        for pid, p in sorted(self._current.items()):
            depts = [f"{t}/{d}" for t, tv in self.tenants.items() for d, dv in tv["departments"].items() if dv["policy"] == pid]
            versions = self.db.all("SELECT version, version_tag, status, body_sha256, published_at, published_by FROM policies"
                                   " WHERE policy_id=? ORDER BY version DESC", (pid,))
            out.append({"policy_id": pid, "current_version": p.version_tag, "body": p.body(), "departments": depts,
                        "versions": versions})
        return out

    def publish(self, policy_id: str, body: dict, actor: str) -> Policy:
        old = self._current.get(policy_id)
        new_version = (old.version + 1) if old else 1
        p = Policy(policy_id=policy_id, version=new_version, **body)   # validates
        with self.db.tx() as c:
            c.execute("UPDATE policies SET status='superseded' WHERE policy_id=? AND status='published'", (policy_id,))
            c.execute("INSERT INTO policies(policy_id, version, version_tag, status, body_json, body_sha256, published_at,"
                      " published_by) VALUES (?,?,?,?,?,?,?,?)",
                      (policy_id, new_version, p.version_tag, "published", json.dumps(p.body()), p.body_sha256(),
                       time.time(), actor))
        self._reload_current()
        for cb in self.on_publish:
            cb(policy_id, old.version_tag if old else None, p.version_tag)
        return p

    # ---- evaluation -------------------------------------------------------------------------
    def evaluate(self, policy_id: str, data_class: str, findings: dict[str, int], has_secret: bool,
                 has_pii: bool, dlp_available: bool, remote_connector_state: str) -> PolicyDecision:
        p = self.get(policy_id)
        codes: list[str] = []
        denied: dict[str, str] = {}
        allowed = list(p.routes)
        action, transform = "allow", None

        if not dlp_available:
            if p.dlp.fail_mode == "closed":
                return PolicyDecision(p.policy_id, p.version_tag, "Unknown", "deny", [], {r: "DLP unavailable" for r in ROUTES},
                                      [Reason.DLP_UNAVAILABLE_FAIL_CLOSED.value], False, "DLP unavailable (fail closed)",
                                      False, findings)
            codes.append(Reason.DLP_UNAVAILABLE_FAIL_CLOSED.value)
            data_class = "Restricted"  # unknown content is treated as sensitive

        if has_secret:
            if p.dlp.secret_action == "deny":
                return PolicyDecision(p.policy_id, p.version_tag, "Secret", "deny", [], {r: "secret detected" for r in ROUTES},
                                      [Reason.SECRET_BLOCKED.value], False, "secret material detected", False, findings)
            if p.dlp.secret_action == "human_review":
                return PolicyDecision(p.policy_id, p.version_tag, "Secret", "human_review", [], {r: "human review" for r in ROUTES},
                                      [Reason.SECRET_BLOCKED.value, Reason.POLICY_BLOCK.value], False,
                                      "secret material requires human review", False, findings)
            transform, action = "redact", "redact"
            codes.append(Reason.SECRET_BLOCKED.value)

        if has_pii:
            a = p.dlp.pii_action
            if a in ("deny", "human_review"):
                return PolicyDecision(p.policy_id, p.version_tag, data_class, a, [], {r: f"PII policy: {a}" for r in ROUTES},
                                      [Reason.POLICY_BLOCK.value], False, f"PII action {a}", False, findings)
            if a in ("redact", "tokenize"):
                transform, action = a, a
                codes.append(Reason.PII_REDACTED.value)
                # After transformation the payload no longer contains the identifiers.
                data_class = "Internal" if not has_secret else data_class
            elif a == "local_only":
                action = "local_only"

        # Remote egress eligibility
        egress_ok, egress_reason = True, "permitted by policy"
        if "remote" not in allowed:
            egress_ok, egress_reason = False, "remote route not in policy"
        elif not p.remote.enabled:
            egress_ok, egress_reason = False, "remote disabled for department"
        elif class_rank(data_class) > class_rank(p.remote.max_data_class) or action == "local_only":
            egress_ok, egress_reason = False, f"data class {data_class} exceeds remote limit {p.remote.max_data_class}"
        if not egress_ok and "remote" in allowed:
            allowed.remove("remote")
        if not egress_ok:
            denied["remote"] = egress_reason
        if class_rank(data_class) >= class_rank("Confidential") or action == "local_only":
            codes.append(Reason.SENSITIVE_LOCAL_ONLY.value)
            egress_ok = False
            denied["remote"] = f"sensitive data ({data_class}) never leaves the device"
            if "remote" in allowed:
                allowed.remove("remote")

        if egress_ok and remote_connector_state in ("disabled",):
            codes.append(Reason.REMOTE_DISABLED.value)

        cache_ok = p.cache.enabled and "cache" in allowed and class_rank(data_class) <= class_rank(p.cache.max_data_class) \
            and transform is None
        if not cache_ok and "cache" in allowed:
            allowed.remove("cache")
            denied["cache"] = f"data class {data_class} not cache-eligible" if p.cache.enabled else "cache disabled"
        for r in ROUTES:
            if r not in p.routes and r not in denied:
                denied[r] = "not in policy route set"

        return PolicyDecision(p.policy_id, p.version_tag, data_class, action, allowed, denied, codes,
                              egress_ok, egress_reason, cache_ok, findings, transform)

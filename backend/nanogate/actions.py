"""Administrative actions shared by the dashboard API and the assistant (after the user confirms).

Every action validates its input, performs one real change, and writes an audit-log entry. Raw API keys are returned
exactly once, to the caller that created them, and are never logged or stored.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any

ROLES = {
    "app": ("member", ["chat"]),            # an application using the OpenAI API
    "person": ("member", ["chat"]),         # an employee using the NanoGate chat app
    "auditor": ("auditor", ["audit"]),      # read-only dashboard access
    "admin": ("admin", ["admin", "chat"]),  # full dashboard access
}
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
REMOTE_MODES = ("disabled", "mock", "live", "outage-test")


class ActionError(ValueError):
    pass


class Actions:
    def __init__(self, svc):
        self.svc = svc

    @property
    def audit(self):
        return self.svc.ops.audit

    # ---- organisation ---------------------------------------------------------------------------------
    def org(self) -> list[dict]:
        pe, db = self.svc.policies, self.svc.db
        period = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
        keys = db.all("SELECT tenant_id, department_id, COUNT(*) n FROM api_keys WHERE revoked_at IS NULL AND "
                      "(label IS NULL OR label NOT LIKE 'playground%') GROUP BY tenant_id, department_id")
        kc = {(k["tenant_id"], k["department_id"]): k["n"] for k in keys}
        budgets = {(b["tenant_id"], b["department_id"]): b for b in db.all("SELECT * FROM budget_accounts WHERE period=?", (period,))}
        out = []
        for tid, t in pe.tenants.items():
            depts = []
            for did, d in t["departments"].items():
                pol = pe.get(d["policy"])
                b = budgets.get((tid, did))
                depts.append({"department_id": did, "display_name": d["display_name"], "policy_id": d["policy"],
                              "policy_version": pol.version_tag, "keys": kc.get((tid, did), 0),
                              "budget_usd": pol.budget.monthly_usd, "spent_usd": round(b["spent_usd"], 6) if b else 0.0,
                              "remote_allowed": pol.remote.enabled and "remote" in pol.routes,
                              "retention_days": pol.retention.receipt_days})
            out.append({"tenant_id": tid, "display_name": t["display_name"], "departments": depts})
        return out

    def create_tenant(self, tenant_id: str, display_name: str, actor=None, source: str = "dashboard") -> dict:
        if not ID_RE.match(tenant_id):
            raise ActionError("tenant id: lowercase letters, digits, - or _, 2-32 characters")
        if tenant_id in self.svc.policies.tenants:
            raise ActionError(f"tenant {tenant_id} already exists")
        self.svc.db.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES (?,?,?)",
                            (tenant_id, display_name, time.time()))
        self.svc.policies.tenants[tenant_id] = {"display_name": display_name, "departments": {}}
        self.audit.record("tenant.create", tenant_id, actor, source, display_name=display_name)
        return {"tenant_id": tenant_id, "display_name": display_name}

    def create_department(self, tenant_id: str, department_id: str, display_name: str, policy_id: str, actor=None,
                          source: str = "dashboard") -> dict:
        pe = self.svc.policies
        if tenant_id not in pe.tenants:
            raise ActionError(f"unknown tenant {tenant_id}")
        if not ID_RE.match(department_id):
            raise ActionError("department id: lowercase letters, digits, - or _, 2-32 characters")
        if department_id in pe.tenants[tenant_id]["departments"]:
            raise ActionError(f"{tenant_id}/{department_id} already exists")
        if policy_id not in pe._current:
            raise ActionError(f"unknown policy {policy_id} (have: {', '.join(sorted(pe._current))})")
        self.svc.db.execute("INSERT INTO departments(tenant_id, department_id, display_name, policy_id) VALUES (?,?,?,?)",
                            (tenant_id, department_id, display_name, policy_id))
        pe.tenants[tenant_id]["departments"][department_id] = {"display_name": display_name, "policy": policy_id}
        # the dashboard's "Try it" composer sends requests as a department principal
        self.svc.keys.create(tenant_id, department_id, "member", ["chat"], label=f"playground:{tenant_id}/{department_id}")
        self.audit.record("department.create", f"{tenant_id}/{department_id}", actor, source,
                          display_name=display_name, policy=policy_id)
        return {"tenant_id": tenant_id, "department_id": department_id, "display_name": display_name, "policy_id": policy_id}

    def move_department(self, tenant_id: str, department_id: str, policy_id: str, actor=None, source: str = "dashboard") -> dict:
        pe = self.svc.policies
        d = pe.tenants.get(tenant_id, {}).get("departments", {}).get(department_id)
        if not d:
            raise ActionError(f"unknown department {tenant_id}/{department_id}")
        if policy_id not in pe._current:
            raise ActionError(f"unknown policy {policy_id}")
        old = d["policy"]
        self.svc.db.execute("UPDATE departments SET policy_id=? WHERE tenant_id=? AND department_id=?",
                            (policy_id, tenant_id, department_id))
        d["policy"] = policy_id
        self.audit.record("department.policy", f"{tenant_id}/{department_id}", actor, source, old=old, new=policy_id)
        return {"tenant_id": tenant_id, "department_id": department_id, "policy_id": policy_id}

    # ---- keys -----------------------------------------------------------------------------------------
    def keys(self, tenant: str | None = None, department: str | None = None, include_revoked: bool = True) -> list[dict]:
        import json
        rows = self.svc.keys.list()
        out = []
        for r in rows:
            if (r.get("label") or "").startswith("playground"):
                continue
            if tenant and r["tenant_id"] != tenant or department and r["department_id"] != department:
                continue
            if not include_revoked and r["revoked_at"]:
                continue
            last = self.svc.db.one("SELECT MAX(ts) t, COUNT(*) n FROM requests WHERE key_id=?", (r["key_id"],))
            scopes = json.loads(r["scopes"])
            kind = "admin" if "admin" in scopes else "auditor" if "audit" in scopes else \
                "person" if (r["label"] or "").startswith("person:") else "app"
            state = "revoked" if r["revoked_at"] else "expired" if r["expires_at"] and r["expires_at"] < time.time() else "active"
            out.append({**r, "scopes": scopes, "kind": kind, "state": state, "last_used": last["t"], "requests": last["n"]})
        return out

    def create_key(self, tenant_id: str, department_id: str, kind: str, name: str, days: int | None = None, actor=None,
                   source: str = "dashboard") -> dict:
        if kind not in ROLES:
            raise ActionError(f"kind must be one of {', '.join(ROLES)}")
        if department_id not in self.svc.policies.tenants.get(tenant_id, {}).get("departments", {}):
            raise ActionError(f"unknown department {tenant_id}/{department_id}")
        name = (name or "").strip()[:60]
        if not name:
            raise ActionError("give the key a name (the app or person it is for)")
        if days is not None and not (1 <= int(days) <= 3650):
            raise ActionError("expiry must be 1-3650 days")
        role, scopes = ROLES[kind]
        label = f"{kind}:{name}"
        key_id, raw = self.svc.keys.create(tenant_id, department_id, role, scopes, label=label,
                                           ttl_s=int(days) * 86400 if days else None)
        self.audit.record("key.create", key_id, actor, source, tenant=tenant_id, department=department_id, kind=kind,
                          name=name, expires_days=days)
        return {"key_id": key_id, "api_key": raw, "label": label, "kind": kind, "tenant_id": tenant_id,
                "department_id": department_id, "scopes": scopes,
                "note": "Copy this key now. NanoGate stores only a keyed hash and cannot show it again."}

    def revoke_key(self, key_id: str, actor=None, source: str = "dashboard") -> dict:
        r = self.svc.db.one("SELECT key_id, label, revoked_at FROM api_keys WHERE key_id=?", (key_id,))
        if not r:
            raise ActionError(f"unknown key {key_id}")
        if actor is not None and getattr(actor, "key_id", None) == key_id:
            raise ActionError("you cannot revoke the key you are signed in with")
        if r["revoked_at"]:
            return {"key_id": key_id, "revoked": True, "already": True}
        self.svc.keys.revoke(key_id)
        self.audit.record("key.revoke", key_id, actor, source, label=r["label"])
        return {"key_id": key_id, "revoked": True}

    # ---- policy shortcuts -----------------------------------------------------------------------------
    def set_budget(self, policy_id: str, monthly_usd: float | None = None, monthly_tokens: int | None = None, actor=None,
                   source: str = "dashboard") -> dict:
        pe = self.svc.policies
        if policy_id not in pe._current:
            raise ActionError(f"unknown policy {policy_id}")
        body = pe.get(policy_id).body()
        if monthly_usd is not None:
            if monthly_usd < 0:
                raise ActionError("budget must be positive")
            body["budget"]["monthly_usd"] = float(monthly_usd)
        if monthly_tokens is not None:
            body["budget"]["monthly_tokens"] = int(monthly_tokens)
        p = pe.publish(policy_id, body, actor=f"key:{getattr(actor, 'key_id', 'system')}")
        self.audit.record("policy.budget", policy_id, actor, source, version=p.version_tag, monthly_usd=monthly_usd,
                          monthly_tokens=monthly_tokens)
        return {"policy_id": policy_id, "published": p.version_tag, "budget": body["budget"]}

    def set_remote_mode(self, mode: str, actor=None, source: str = "dashboard") -> dict:
        if mode not in REMOTE_MODES:
            raise ActionError(f"mode must be one of {', '.join(REMOTE_MODES)}")
        self.svc.remote.set_mode(mode)
        self.audit.record("remote.mode", mode, actor, source)
        return {"mode": mode}

    # ---- alerts ---------------------------------------------------------------------------------------
    def add_alert_channel(self, name: str, kind: str, url: str, actor=None, source: str = "dashboard") -> dict:
        from .ops import _validate_webhook
        import uuid
        if kind not in ("slack", "teams", "webhook"):
            raise ActionError("kind must be slack, teams or webhook")
        try:
            _validate_webhook(url)
        except ValueError as e:
            raise ActionError(str(e))
        chans = self.svc.ops_settings.get("alerts.channels")
        ch = {"id": "ch_" + uuid.uuid4().hex[:8], "name": name.strip()[:60] or kind, "kind": kind, "url": url, "enabled": True}
        self.svc.ops_settings.set("alerts.channels", chans + [ch], getattr(actor, "key_id", None))
        self.audit.record("alerts.channel.add", ch["id"], actor, source, name=ch["name"], kind=kind)
        return _public_channel(ch)

    def remove_alert_channel(self, channel_id: str, actor=None, source: str = "dashboard") -> dict:
        chans = self.svc.ops_settings.get("alerts.channels")
        rest = [c for c in chans if c["id"] != channel_id]
        if len(rest) == len(chans):
            raise ActionError(f"unknown channel {channel_id}")
        self.svc.ops_settings.set("alerts.channels", rest, getattr(actor, "key_id", None))
        self.audit.record("alerts.channel.remove", channel_id, actor, source)
        return {"removed": channel_id}

    def set_alert_rule(self, rule: str, enabled: bool, percent: float | None = None, actor=None,
                       source: str = "dashboard") -> dict:
        rules = self.svc.ops_settings.get("alerts.rules")
        if rule not in rules:
            raise ActionError(f"unknown rule {rule} (have: {', '.join(rules)})")
        rules[rule]["enabled"] = bool(enabled)
        if percent is not None:
            if "percent" not in rules[rule] or not (1 <= float(percent) <= 100):
                raise ActionError("this rule has no percentage, or it is outside 1-100")
            rules[rule]["percent"] = float(percent)
        self.svc.ops_settings.set("alerts.rules", rules, getattr(actor, "key_id", None))
        self.audit.record("alerts.rule", rule, actor, source, **rules[rule])
        return {"rule": rule, **rules[rule]}

    # ---- reports --------------------------------------------------------------------------------------
    def chargeback(self, month: str | None = None, tenant: str | None = None) -> dict:
        """Per-department cost for one calendar month (UTC), from measured request rows."""
        month = month or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
        try:
            start = dt.datetime.strptime(month, "%Y-%m").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            raise ActionError("month must look like 2026-09")
        end = (start + dt.timedelta(days=32)).replace(day=1)
        where, args = "ts>=? AND ts<?", [start.timestamp(), end.timestamp()]
        if tenant:
            where += " AND tenant_id=?"
            args.append(tenant)
        rows = self.svc.db.all(
            "SELECT tenant_id, department_id, COUNT(*) requests, SUM(status='ok') answered, SUM(status='denied') denied,"
            " SUM(COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)) tokens, SUM(COALESCE(tokens_avoided,0)) tokens_avoided,"
            " SUM(COALESCE(cost_usd,0)) cost_usd, SUM(COALESCE(counterfactual_usd,0)) counterfactual_usd,"
            " SUM(route IN ('local','local_large','cache')) on_device, SUM(COALESCE(energy_j,0)) energy_j"
            f" FROM requests WHERE {where} GROUP BY tenant_id, department_id ORDER BY cost_usd DESC", tuple(args))
        pe = self.svc.policies
        for r in rows:
            d = pe.tenants.get(r["tenant_id"], {}).get("departments", {}).get(r["department_id"])
            budget = pe.get(d["policy"]).budget.monthly_usd if d else None
            r["display_name"] = d["display_name"] if d else r["department_id"]
            r["budget_usd"] = budget
            r["budget_used"] = (r["cost_usd"] / budget) if budget else None
            r["avoided_usd"] = max(0.0, (r["counterfactual_usd"] or 0) - (r["cost_usd"] or 0))
            r["on_device_share"] = (r["on_device"] / r["answered"]) if r["answered"] else None
        tot = {k: sum((r[k] or 0) for r in rows) for k in ("requests", "answered", "denied", "tokens", "tokens_avoided",
                                                            "cost_usd", "counterfactual_usd", "avoided_usd", "energy_j")}
        return {"month": month, "label": "Measured", "rows": rows, "totals": tot,
                "basis": "cost = configured rates x metered tokens; avoided = reference hosted rate x same tokens - cost"}

    # ---- data -----------------------------------------------------------------------------------------
    def backup(self, actor=None, source: str = "dashboard") -> dict:
        return self.svc.ops.backups.create("manual", actor)

    def erase(self, key_id: str | None = None, tenant_id: str | None = None, department_id: str | None = None, actor=None,
              source: str = "dashboard") -> dict:
        try:
            return self.svc.ops.retention.erase(actor, key_id=key_id, tenant=tenant_id, department=department_id)
        except ValueError as e:
            raise ActionError(str(e))

    def set_source_revoked(self, source_id: str, revoked: bool, actor=None, source: str = "dashboard") -> dict:
        n = 0
        if source_id.startswith("kb_"):
            if not self.svc.kb.source(source_id):
                raise ActionError(f"unknown knowledge source {source_id}")
            self.svc.kb.set_revoked(source_id, revoked)
        if self.svc.cache is not None:
            if revoked:
                n = self.svc.cache.revoke_source(source_id)
            else:
                self.svc.cache.restore_source(source_id)
        self.svc.bus.emit("service_state_changed", component=f"source:{source_id}", state="revoked" if revoked else "restored",
                          cache_entries_revoked=n)
        self.audit.record("knowledge.revoke" if revoked else "knowledge.restore", source_id, actor, source,
                          cache_entries_revoked=n)
        return {"source_id": source_id, "revoked": revoked, "cache_entries_revoked": n}


def _public_channel(ch: dict) -> dict:
    """Webhook URLs contain their own secret token: show only the host."""
    from urllib.parse import urlparse
    return {**{k: v for k, v in ch.items() if k != "url"}, "host": urlparse(ch["url"]).hostname}


def public_channels(chans: list[dict]) -> list[dict[str, Any]]:
    return [_public_channel(c) for c in chans]

"""Operations: audit log, operator settings, alerts, retention, backups, SIEM forwarding.

Everything here acts on real state (database rows, event-bus events, files on disk). Alert and SIEM payloads carry
reason codes, departments, ids and counts, never prompt or answer text. Outbound deliveries (webhooks, syslog) are
recorded in egress_log like any other traffic leaving the device.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import socket
import sqlite3
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .settings import ROOT

log = logging.getLogger("nanogate.ops")

SENSITIVE = ("Confidential", "Restricted", "Secret")


# ---- audit log ----------------------------------------------------------------------------------------
class AuditLog:
    def __init__(self, db, bus):
        self.db, self.bus = db, bus

    def record(self, action: str, target: str | None = None, actor=None, source: str = "dashboard", **detail) -> None:
        self.db.execute("INSERT INTO audit_log(ts, actor_key_id, actor_label, source, action, target, detail_json)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (time.time(), getattr(actor, "key_id", None), getattr(actor, "label", None), source, action, target,
                         json.dumps(detail, default=str)))
        self.bus.emit("audit_recorded", action=action, target=target, source=source,
                      actor=getattr(actor, "key_id", None))

    def list(self, limit: int = 200, action: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM audit_log", []
        if action:
            sql += " WHERE action LIKE ?"
            args.append(action + "%")
        rows = self.db.all(sql + " ORDER BY audit_id DESC LIMIT ?", (*args, int(limit)))
        for r in rows:
            r["detail"] = json.loads(r.pop("detail_json") or "{}")
        return rows


# ---- settings -----------------------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {
    "alerts.channels": [],     # [{id, name, kind: slack|teams|webhook, url, enabled}]
    "alerts.rules": {
        "budget_threshold": {"enabled": True, "percent": 80},
        "model_down": {"enabled": True},
        "secret_blocked": {"enabled": True},
        "spoof_attempt": {"enabled": True},
        "sensitive_egress": {"enabled": True},
        "chain_integrity": {"enabled": True},
        "error_spike": {"enabled": True, "percent": 20, "min_requests": 10},
    },
    "siem": {"enabled": False, "host": "", "port": 514, "protocol": "udp"},
    "backups": {"scheduled": True, "every_hours": 24, "keep": 7},
    "retention": {"enabled": True, "telemetry_days": 30},
}


class OpsSettings:
    def __init__(self, db):
        self.db = db

    def get(self, key: str) -> Any:
        r = self.db.one("SELECT value_json FROM ops_settings WHERE key=?", (key,))
        base = DEFAULTS.get(key)
        if not r:
            return json.loads(json.dumps(base))
        v = json.loads(r["value_json"])
        if isinstance(base, dict) and isinstance(v, dict):
            merged = json.loads(json.dumps(base))
            for k, val in v.items():
                merged[k] = {**merged[k], **val} if isinstance(merged.get(k), dict) and isinstance(val, dict) else val
            return merged
        return v

    def set(self, key: str, value: Any, actor: str | None) -> Any:
        if key not in DEFAULTS:
            raise KeyError(key)
        self.db.execute("INSERT OR REPLACE INTO ops_settings(key, value_json, updated_at, updated_by) VALUES (?,?,?,?)",
                        (key, json.dumps(value), time.time(), actor))
        return self.get(key)

    def all(self) -> dict[str, Any]:
        return {k: self.get(k) for k in DEFAULTS}


# ---- alerts -------------------------------------------------------------------------------------------
RULES = {
    "budget_threshold": ("warning", "A department is close to its monthly AI budget"),
    "model_down": ("critical", "A local model stopped answering"),
    "secret_blocked": ("warning", "A request containing a secret was blocked"),
    "spoof_attempt": ("critical", "Someone tried to impersonate another tenant or department"),
    "sensitive_egress": ("critical", "Sensitive data left the device"),
    "chain_integrity": ("critical", "Receipt chain verification failed"),
    "error_spike": ("warning", "Many requests are failing"),
    "test": ("info", "Test alert from NanoGate"),
}
COOLDOWN_S = 15 * 60


def _validate_webhook(url: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise ValueError("webhook URL must be http(s)://host/…")
    return url


class AlertEngine:
    def __init__(self, svc, settings: OpsSettings):
        self.svc, self.settings = svc, settings
        self._last: dict[str, float] = {}
        self._client = httpx.AsyncClient(timeout=6)

    def _cooling(self, key: str) -> bool:
        now = time.time()
        if now - self._last.get(key, 0) < COOLDOWN_S:
            return True
        self._last[key] = now
        return False

    def enabled(self, rule: str) -> dict | None:
        cfg = self.settings.get("alerts.rules").get(rule, {})
        return cfg if cfg.get("enabled") else None

    async def raise_alert(self, rule: str, subject: str | None, detail: dict, title: str | None = None,
                          dedupe: bool = True) -> dict | None:
        if dedupe and self._cooling(f"{rule}:{subject}"):
            return None
        severity, default_title = RULES[rule]
        alert = {"alert_id": "alrt_" + uuid.uuid4().hex[:16], "ts": time.time(), "rule": rule, "severity": severity,
                 "subject": subject, "title": title or default_title, "detail": detail}
        delivered = await self._deliver(alert)
        self.svc.db.execute("INSERT INTO alerts(alert_id, ts, rule, severity, subject, title, detail_json, delivered_json)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (alert["alert_id"], alert["ts"], rule, severity, subject, alert["title"],
                             json.dumps(detail, default=str), json.dumps(delivered)))
        self.svc.bus.emit("alert_raised", alert_id=alert["alert_id"], rule=rule, severity=severity, subject=subject,
                          title=alert["title"])
        return {**alert, "delivered": delivered}

    def _text(self, a: dict) -> str:
        bits = ", ".join(f"{k}: {v}" for k, v in a["detail"].items() if v is not None)
        return f"[NanoGate · {a['severity'].upper()}] {a['title']}" + (f" ({a['subject']})" if a["subject"] else "") \
            + (f" — {bits}" if bits else "")

    async def _deliver(self, a: dict) -> list[dict]:
        out = []
        for ch in self.settings.get("alerts.channels"):
            if not ch.get("enabled", True):
                continue
            payload = {"text": self._text(a)} if ch.get("kind") in ("slack", "teams") else \
                {"source": "nanogate", **{k: a[k] for k in ("alert_id", "ts", "rule", "severity", "subject", "title", "detail")}}
            body = json.dumps(payload, default=str).encode()
            host = urlparse(ch["url"]).hostname or "?"
            t0 = time.perf_counter()
            try:
                r = await self._client.post(ch["url"], content=body, headers={"content-type": "application/json"})
                ok, outcome = r.status_code < 300, f"http {r.status_code}"
            except Exception as e:
                ok, outcome = False, type(e).__name__
            self.svc.db.execute("INSERT INTO egress_log(ts, request_id, destination, allowed, bytes_out, bytes_in, outcome)"
                                " VALUES (?,?,?,?,?,?,?)", (time.time(), None, f"alert:{host}", 1, len(body), 0, outcome))
            out.append({"channel": ch.get("name") or host, "ok": ok, "outcome": outcome,
                        "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return out

    # event-driven rules
    async def on_event(self, ev: dict) -> None:
        t, d = ev["type"], ev.get("data", {})
        if t == "receipt_sealed":
            if d.get("reason") == "SECRET_BLOCKED" and self.enabled("secret_blocked"):
                await self.raise_alert("secret_blocked", f"{d.get('tenant')}/{d.get('department')}",
                                       {"receipt": d.get("receipt_id"), "reason": d.get("reason")})
            if d.get("reason") in ("TENANT_SPOOF_REJECTED", "DEPARTMENT_SPOOF_REJECTED") and self.enabled("spoof_attempt"):
                await self.raise_alert("spoof_attempt", f"{d.get('tenant')}/{d.get('department')}",
                                       {"receipt": d.get("receipt_id"), "reason": d.get("reason")})
        elif t == "service_state_changed" and str(d.get("component", "")).startswith("model:"):
            if d.get("state") != "ready" and self.enabled("model_down"):
                await self.raise_alert("model_down", d.get("component"), {"state": d.get("state"), "reason": d.get("reason")})

    # periodic rules
    async def check_periodic(self) -> None:
        db = self.svc.db
        cfg = self.enabled("budget_threshold")
        if cfg:
            period = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
            for b in db.all("SELECT * FROM budget_accounts WHERE period=?", (period,)):
                pct = max(b["spent_usd"] / b["limit_usd"] if b["limit_usd"] else 0,
                          b["spent_tokens"] / b["limit_tokens"] if b["limit_tokens"] else 0) * 100
                level = 100 if pct >= 100 else cfg.get("percent", 80) if pct >= cfg.get("percent", 80) else None
                if level and not self._cooling(f"budget:{b['tenant_id']}/{b['department_id']}:{period}:{level}"):
                    await self.raise_alert("budget_threshold", f"{b['tenant_id']}/{b['department_id']}",
                                           {"used_percent": round(pct, 1), "spent_usd": round(b["spent_usd"], 4),
                                            "limit_usd": b["limit_usd"], "period": period}, dedupe=False,
                                           title=f"Budget {round(pct)}% used this month")
        if self.enabled("sensitive_egress"):
            r = db.one("SELECT COALESCE(SUM(remote_bytes),0) b, COUNT(*) n FROM requests WHERE ts>=? AND remote_bytes>0 AND "
                       f"data_class IN ({','.join('?' * len(SENSITIVE))})", (time.time() - 300, *SENSITIVE))
            if r and r["b"]:
                await self.raise_alert("sensitive_egress", "remote connector", {"bytes": r["b"], "requests": r["n"]})
        cfg = self.enabled("error_spike")
        if cfg:
            r = db.one("SELECT COUNT(*) n, SUM(status='error') e FROM requests WHERE ts>=?", (time.time() - 900,))
            if r and r["n"] >= cfg.get("min_requests", 10) and (r["e"] or 0) / r["n"] * 100 >= cfg.get("percent", 20):
                await self.raise_alert("error_spike", "gateway", {"errors": r["e"], "requests": r["n"], "window_min": 15})

    async def check_chain(self) -> None:
        if not self.enabled("chain_integrity"):
            return
        res = await asyncio.to_thread(self.svc.receipts.verify_chain)
        if not res["valid"]:
            await self.raise_alert("chain_integrity", "receipts", {"invalid": len(res["invalid_receipts"]),
                                                                   "first": (res["invalid_receipts"] or [None])[0]})

    async def aclose(self) -> None:
        await self._client.aclose()


# ---- SIEM (syslog) --------------------------------------------------------------------------------------
SIEM_EVENTS = {"receipt_sealed", "policy_published", "service_state_changed", "alert_raised", "audit_recorded",
               "offline_verification"}


class SiemForwarder:
    """RFC 5424 syslog lines with a JSON message, over UDP or TCP. Metadata only."""

    def __init__(self, svc, settings: OpsSettings):
        self.svc, self.settings = svc, settings
        self.sent = 0
        self.last_error: str | None = None

    def _line(self, ev: dict) -> bytes:
        sev = 2 if ev["type"] == "alert_raised" and ev["data"].get("severity") == "critical" else 6
        ts = dt.datetime.fromtimestamp(ev["ts"], dt.timezone.utc).isoformat(timespec="milliseconds")
        msg = json.dumps({"event": ev["type"], "request_id": ev.get("request_id"), **ev.get("data", {})}, default=str)
        return f"<{8 + sev}>1 {ts} {socket.gethostname()} nanogate - {ev['type']} - {msg}\n".encode()

    def send(self, ev: dict) -> None:
        cfg = self.settings.get("siem")
        if not cfg.get("enabled") or not cfg.get("host") or ev["type"] not in SIEM_EVENTS:
            return
        line = self._line(ev)
        try:
            if cfg.get("protocol") == "tcp":
                with socket.create_connection((cfg["host"], int(cfg["port"])), timeout=3) as s:
                    s.sendall(line)
            else:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.sendto(line, (cfg["host"], int(cfg["port"])))
            self.sent += 1
            self.last_error = None
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"[:200]


# ---- retention ----------------------------------------------------------------------------------------
class Retention:
    """Deletes request rows and erases receipt bodies older than each department's policy retention.

    Pruned receipts keep hash, HMAC and chain links, so chain verification still proves nothing was altered or
    removed; only the body (who/what/route detail) is gone."""

    def __init__(self, svc, audit: AuditLog):
        self.svc, self.audit = svc, audit

    def _prune(self, where: str, args: tuple, reason: str) -> dict:
        db = self.svc.db
        with db.tx() as c:
            rc = c.execute(f"UPDATE receipts SET body_json='', pruned_at=?, pruned_reason=? WHERE pruned_at IS NULL AND {where}",
                           (time.time(), reason, *args)).rowcount
            rq = c.execute(f"DELETE FROM requests WHERE {where}", args).rowcount
        return {"receipts_pruned": rc, "requests_deleted": rq}

    def run(self) -> dict:
        pe = self.svc.policies
        total = {"receipts_pruned": 0, "requests_deleted": 0, "departments": []}
        for tid, t in pe.tenants.items():
            for did, d in t["departments"].items():
                days = pe.get(d["policy"]).retention.receipt_days
                cutoff = time.time() - days * 86400
                r = self._prune("tenant_id=? AND department_id=? AND ts<?", (tid, did, cutoff), f"retention:{days}d")
                if r["receipts_pruned"] or r["requests_deleted"]:
                    total["departments"].append({"department": f"{tid}/{did}", "days": days, **r})
                total["receipts_pruned"] += r["receipts_pruned"]
                total["requests_deleted"] += r["requests_deleted"]
        days = self.svc.ops_settings.get("retention").get("telemetry_days", 30)
        self.svc.db.execute("DELETE FROM telemetry_samples WHERE ts<?", (time.time() - days * 86400,))
        if total["receipts_pruned"] or total["requests_deleted"]:
            self.audit.record("retention.prune", None, source="system", **total)
        return total

    def erase(self, actor, key_id: str | None = None, tenant: str | None = None, department: str | None = None) -> dict:
        """Right-to-erasure: everything recorded for one API key (an app or a person) or one department."""
        if key_id:
            rids = [r["request_id"] for r in self.svc.db.all("SELECT request_id FROM requests WHERE key_id=?", (key_id,))]
            target = f"key:{key_id}"
        elif tenant and department:
            rids = [r["request_id"] for r in self.svc.db.all(
                "SELECT request_id FROM requests WHERE tenant_id=? AND department_id=?", (tenant, department))]
            target = f"department:{tenant}/{department}"
        else:
            raise ValueError("key_id, or tenant and department, required")
        res = {"receipts_pruned": 0, "requests_deleted": 0, "cache_entries_deleted": 0}
        for i in range(0, len(rids), 500):
            chunk = rids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            r = self._prune(f"request_id IN ({marks})", tuple(chunk), f"erasure:{target}")
            res["receipts_pruned"] += r["receipts_pruned"]
            res["requests_deleted"] += r["requests_deleted"]
            with self.svc.db.tx() as c:
                res["cache_entries_deleted"] += c.execute(
                    f"DELETE FROM cache_entries WHERE origin_request_id IN ({marks})", tuple(chunk)).rowcount
        self.audit.record("privacy.erase", target, actor, **res)
        return {"target": target, **res}


# ---- backups ------------------------------------------------------------------------------------------
class Backups:
    """Consistent online SQLite backup + the two secrets needed to keep keys and receipts valid after a restore.
    Archives are mode 0600: they contain the key pepper and the receipt HMAC key."""

    def __init__(self, svc, audit: AuditLog):
        self.svc, self.audit = svc, audit
        self.dir = svc.settings.data_dir / "backups"

    def create(self, kind: str = "manual", actor=None) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        ts = time.time()
        bid = "bk_" + dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
        path = self.dir / f"{bid}.tar.gz"
        s = self.svc.settings
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "nanogate.db"
            src = sqlite3.connect(s.db_path)
            dst = sqlite3.connect(snap)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            manifest = {"backup_id": bid, "created_at": ts, "kind": kind, "commit": self.svc.commit,
                        "schema": [r["name"] for r in self.svc.db.all("SELECT name FROM schema_migrations ORDER BY name")]}
            (Path(tmp) / "manifest.json").write_text(json.dumps(manifest, indent=2))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f, tarfile.open(fileobj=f, mode="w:gz") as tar:
                tar.add(snap, "nanogate.db")
                tar.add(Path(tmp) / "manifest.json", "manifest.json")
                for name in ("key_pepper", "receipt_hmac.key"):
                    if (s.data_dir / name).exists():
                        tar.add(s.data_dir / name, name)
                for cfg in (s.policies_file, s.pricing_file):
                    if cfg.exists():
                        tar.add(cfg, f"config/{cfg.name}")
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        size = path.stat().st_size
        self.svc.db.execute("INSERT INTO backups(backup_id, ts, filename, bytes, sha256, kind, created_by) VALUES (?,?,?,?,?,?,?)",
                            (bid, ts, path.name, size, h, kind, getattr(actor, "key_id", None)))
        self.audit.record("backup.create", bid, actor, source="system" if actor is None else "dashboard",
                          kind=kind, bytes=size, sha256=h)
        self._rotate()
        return {"backup_id": bid, "ts": ts, "filename": path.name, "bytes": size, "sha256": h, "kind": kind}

    def _rotate(self) -> None:
        keep = int(self.svc.ops_settings.get("backups").get("keep", 7))
        old = self.svc.db.all("SELECT backup_id, filename FROM backups WHERE kind='scheduled' ORDER BY ts DESC LIMIT -1 OFFSET ?",
                              (keep,))
        for b in old:
            (self.dir / b["filename"]).unlink(missing_ok=True)
            self.svc.db.execute("DELETE FROM backups WHERE backup_id=?", (b["backup_id"],))

    def list(self) -> list[dict]:
        rows = self.svc.db.all("SELECT * FROM backups ORDER BY ts DESC")
        for r in rows:
            r["present"] = (self.dir / r["filename"]).exists()
        return rows

    def path(self, backup_id: str) -> Path | None:
        r = self.svc.db.one("SELECT filename FROM backups WHERE backup_id=?", (backup_id,))
        p = self.dir / r["filename"] if r else None
        return p if p and p.exists() else None

    def due(self) -> bool:
        cfg = self.svc.ops_settings.get("backups")
        if not cfg.get("scheduled"):
            return False
        last = self.svc.db.one("SELECT MAX(ts) t FROM backups WHERE kind='scheduled'")
        return not last or not last["t"] or time.time() - last["t"] >= float(cfg.get("every_hours", 24)) * 3600


# ---- wiring -------------------------------------------------------------------------------------------
class Operations:
    def __init__(self, svc):
        self.svc = svc
        self.audit = AuditLog(svc.db, svc.bus)
        self.settings = svc.ops_settings = OpsSettings(svc.db)
        self.alerts = AlertEngine(svc, self.settings)
        self.siem = SiemForwarder(svc, self.settings)
        self.retention = Retention(svc, self.audit)
        self.backups = Backups(svc, self.audit)
        self.last_retention: dict | None = None

    def tasks(self) -> list:
        return [self._event_loop(), self._periodic_loop()]

    async def _event_loop(self) -> None:
        q = self.svc.bus.subscribe()
        while True:
            ev = await q.get()
            try:
                await asyncio.to_thread(self.siem.send, ev)
                await self.alerts.on_event(ev)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("operations event handling failed")

    async def _periodic_loop(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(60)
            n += 1
            try:
                await self.alerts.check_periodic()
                if n % 10 == 1:
                    await self.alerts.check_chain()
                if n % 60 == 1 and self.settings.get("retention").get("enabled", True):
                    self.last_retention = {"ts": time.time(), **await asyncio.to_thread(self.retention.run)}
                if self.backups.due():
                    await asyncio.to_thread(self.backups.create, "scheduled")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("operations periodic job failed")

    async def aclose(self) -> None:
        await self.alerts.aclose()


def tls_status(settings) -> dict:
    cert, key = os.environ.get("NANOGATE_TLS_CERT"), os.environ.get("NANOGATE_TLS_KEY")
    if not cert or not key:
        return {"enabled": False, "reason": "NANOGATE_TLS_CERT / NANOGATE_TLS_KEY not set (scripts/make_tls_cert.sh)"}
    info: dict[str, Any] = {"enabled": Path(cert).exists() and Path(key).exists(), "cert": Path(cert).name}
    try:
        from cryptography import x509
        c = x509.load_pem_x509_certificate(Path(cert).read_bytes())
        info.update(subject=c.subject.rfc4514_string(), not_after=c.not_valid_after_utc.isoformat(),
                    days_left=(c.not_valid_after_utc - dt.datetime.now(dt.timezone.utc)).days,
                    self_signed=c.issuer == c.subject)
    except Exception as e:
        info["detail"] = f"certificate not parsed: {type(e).__name__}"
    return info


__all__ = ["Operations", "AuditLog", "OpsSettings", "tls_status", "ROOT", "_validate_webhook"]

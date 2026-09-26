"""Operations API: organisation and keys, audit log, alerts, knowledge, backups, retention, exports, chargeback,
models, the NanoGate Assistant, and the employee chat app.

Reads accept an admin or auditor session; changes require admin. Every change goes through actions.Actions,
which validates it and writes the audit log.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .actions import ActionError, public_channels
from .api_admin import admin, issue_session, resolve_session
from .auth import AuthError, Identity, bearer
from .ops import tls_status
from .pipeline import GatewayError
from .reason_codes import Reason

router = APIRouter(prefix="/api", tags=["Operations"])


def reader(request: Request, authorization: str | None = Header(None), token: str | None = Query(None)) -> Identity:
    """Admin or auditor (read-only) dashboard session."""
    svc = request.app.state.svc
    raw = bearer(authorization) or token
    ident = resolve_session(svc, raw) if raw and raw.startswith("ngs_") else None
    if ident is None and raw and not raw.startswith("ngs_"):
        try:
            ident = svc.keys.resolve(raw, svc.policies.policy_for)
        except AuthError:
            ident = None
    if ident is None:
        raise HTTPException(401, detail={"code": Reason.AUTH_INVALID.value, "message": "dashboard session required"})
    if not ({"admin", "audit"} & set(ident.scopes)):
        raise HTTPException(403, detail={"code": Reason.SCOPE_DENIED.value, "message": "admin or auditor scope required"})
    return ident


def _act(fn, *a, **kw) -> Any:
    try:
        return fn(*a, **kw)
    except ActionError as e:
        raise HTTPException(422, detail={"code": "INVALID_ACTION", "message": str(e)})


# ---- organisation & keys ------------------------------------------------------------------------------
class TenantReq(BaseModel):
    tenant_id: str
    display_name: str


class DeptReq(BaseModel):
    tenant_id: str
    department_id: str
    display_name: str
    policy_id: str


class DeptPolicyReq(BaseModel):
    tenant_id: str
    department_id: str
    policy_id: str


class KeyReq(BaseModel):
    tenant_id: str
    department_id: str
    kind: str = "app"
    name: str
    days: int | None = None


@router.get("/org", summary="Tenants and departments with policy, budget, spend and key counts")
async def org(request: Request, ident: Identity = Depends(reader)):
    svc = request.app.state.svc
    return {"tenants": svc.actions.org(), "policies": sorted(svc.policies._current)}


@router.post("/org/tenants")
async def create_tenant(req: TenantReq, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.create_tenant, req.tenant_id, req.display_name, actor=ident)


@router.post("/org/departments")
async def create_department(req: DeptReq, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.create_department, req.tenant_id, req.department_id, req.display_name,
                req.policy_id, actor=ident)


@router.post("/org/departments/policy")
async def move_department(req: DeptPolicyReq, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.move_department, req.tenant_id, req.department_id, req.policy_id, actor=ident)


@router.get("/keys", summary="API keys (never the secret)")
async def keys(request: Request, tenant: str | None = None, department: str | None = None,
               ident: Identity = Depends(reader)):
    return {"keys": request.app.state.svc.actions.keys(tenant, department), "you": ident.key_id}


@router.post("/keys", summary="Create an API key; the secret is returned once")
async def create_key(req: KeyReq, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.create_key, req.tenant_id, req.department_id, req.kind, req.name, req.days,
                actor=ident)


@router.post("/keys/{key_id}/revoke")
async def revoke_key(key_id: str, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.revoke_key, key_id, actor=ident)


@router.get("/audit", summary="Administrative changes, newest first")
async def audit(request: Request, limit: int = 200, action: str | None = None, ident: Identity = Depends(reader)):
    return {"entries": request.app.state.svc.ops.audit.list(min(limit, 1000), action)}


# ---- alerts -------------------------------------------------------------------------------------------
class ChannelReq(BaseModel):
    name: str
    kind: str
    url: str


class RuleReq(BaseModel):
    rule: str
    enabled: bool
    percent: float | None = None


@router.get("/alerts")
async def alerts(request: Request, limit: int = 100, ident: Identity = Depends(reader)):
    svc = request.app.state.svc
    rows = svc.db.all("SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (min(limit, 500),))
    for r in rows:
        r["detail"] = json.loads(r.pop("detail_json") or "{}")
        r["delivered"] = json.loads(r.pop("delivered_json") or "[]")
    from .ops import RULES
    return {"alerts": rows, "rules": svc.ops_settings.get("alerts.rules"),
            "rule_info": {k: {"severity": v[0], "title": v[1]} for k, v in RULES.items() if k != "test"},
            "channels": public_channels(svc.ops_settings.get("alerts.channels")),
            "open": sum(1 for r in rows if not r["acknowledged_at"])}


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    svc.db.execute("UPDATE alerts SET acknowledged_at=?, acknowledged_by=? WHERE alert_id=? AND acknowledged_at IS NULL",
                   (time.time(), ident.key_id, alert_id))
    return {"alert_id": alert_id, "acknowledged": True}


@router.post("/alerts/channels")
async def add_channel(req: ChannelReq, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.add_alert_channel, req.name, req.kind, req.url, actor=ident)


@router.delete("/alerts/channels/{channel_id}")
async def remove_channel(channel_id: str, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.remove_alert_channel, channel_id, actor=ident)


@router.post("/alerts/rules")
async def set_rule(req: RuleReq, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.set_alert_rule, req.rule, req.enabled, req.percent, actor=ident)


@router.post("/alerts/test", summary="Send a test alert to every enabled channel")
async def test_alert(request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    a = await svc.ops.alerts.raise_alert("test", "channels", {"sent_by": ident.key_id}, dedupe=False)
    return {"alert_id": a["alert_id"], "delivered": a["delivered"],
            "note": None if a["delivered"] else "No channel configured: the alert is only in the list."}


# ---- operations: TLS, backups, retention, erasure, SIEM ----------------------------------------------
class EraseReq(BaseModel):
    key_id: str | None = None
    tenant_id: str | None = None
    department_id: str | None = None
    confirm: str = Field(..., description="must be ERASE")


class SiemReq(BaseModel):
    enabled: bool
    host: str = ""
    port: int = 514
    protocol: str = "udp"


class BackupCfgReq(BaseModel):
    scheduled: bool = True
    every_hours: float = 24
    keep: int = 7


class RetentionCfgReq(BaseModel):
    enabled: bool = True
    telemetry_days: int = 30


@router.get("/ops", summary="HTTPS, backups, retention, SIEM and data inventory")
async def ops(request: Request, ident: Identity = Depends(reader)):
    svc = request.app.state.svc
    pe = svc.policies
    retention = [{"department": f"{t}/{d}", "policy": dv["policy"], "receipt_days": pe.get(dv["policy"]).retention.receipt_days,
                  "store_raw_prompts": pe.get(dv["policy"]).retention.store_raw_prompts}
                 for t, tv in pe.tenants.items() for d, dv in tv["departments"].items()]
    counts = svc.db.one("SELECT (SELECT COUNT(*) FROM requests) requests, (SELECT COUNT(*) FROM receipts) receipts,"
                        " (SELECT COUNT(*) FROM receipts WHERE pruned_at IS NOT NULL) receipts_pruned,"
                        " (SELECT COUNT(*) FROM cache_entries) cache_entries, (SELECT MIN(ts) FROM requests) oldest_request,"
                        " (SELECT COUNT(*) FROM requests WHERE raw_prompt IS NOT NULL) raw_prompts_stored")
    return {
        "tls": tls_status(svc.settings),
        "backups": {"config": svc.ops_settings.get("backups"), "items": svc.ops.backups.list(),
                    "directory": str(svc.ops.backups.dir)},
        "retention": {"config": svc.ops_settings.get("retention"), "departments": retention,
                      "last_run": svc.ops.last_retention},
        "siem": {**svc.ops_settings.get("siem"), "sent": svc.ops.siem.sent, "last_error": svc.ops.siem.last_error},
        "data": {**counts, "db_bytes": svc.settings.db_path.stat().st_size if svc.settings.db_path.exists() else None},
    }


@router.post("/ops/backups")
async def backup_now(request: Request, ident: Identity = Depends(admin)):
    import asyncio
    return await asyncio.to_thread(request.app.state.svc.actions.backup, ident)


@router.get("/ops/backups/{backup_id}/download")
async def backup_download(backup_id: str, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    if "admin" not in ident.scopes:   # archives hold the key pepper and receipt signing key
        raise HTTPException(403, detail={"code": Reason.SCOPE_DENIED.value, "message": "admin scope required"})
    p = svc.ops.backups.path(backup_id)
    if not p:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "backup file not found"})
    svc.ops.audit.record("backup.download", backup_id, ident)
    return FileResponse(p, filename=p.name, media_type="application/gzip")


@router.put("/ops/backups/config")
async def backup_config(req: BackupCfgReq, request: Request, ident: Identity = Depends(admin)):
    if not (1 <= req.every_hours <= 24 * 30) or not (1 <= req.keep <= 365):
        raise HTTPException(422, detail={"code": "INVALID", "message": "every 1-720 hours, keep 1-365 backups"})
    svc = request.app.state.svc
    v = svc.ops_settings.set("backups", req.model_dump(), ident.key_id)
    svc.ops.audit.record("backup.config", None, ident, **req.model_dump())
    return v


@router.put("/ops/retention/config")
async def retention_config(req: RetentionCfgReq, request: Request, ident: Identity = Depends(admin)):
    if not (1 <= req.telemetry_days <= 3650):
        raise HTTPException(422, detail={"code": "INVALID", "message": "telemetry_days 1-3650"})
    svc = request.app.state.svc
    v = svc.ops_settings.set("retention", req.model_dump(), ident.key_id)
    svc.ops.audit.record("retention.config", None, ident, **req.model_dump())
    return v


@router.post("/ops/retention/run", summary="Apply every department's retention period now")
async def retention_run(request: Request, ident: Identity = Depends(admin)):
    import asyncio
    svc = request.app.state.svc
    res = await asyncio.to_thread(svc.ops.retention.run)
    svc.ops.last_retention = {"ts": time.time(), **res}
    return res


@router.post("/ops/erase", summary="Right to erasure for one key or one department")
async def erase(req: EraseReq, request: Request, ident: Identity = Depends(admin)):
    if req.confirm != "ERASE":
        raise HTTPException(422, detail={"code": "CONFIRMATION_REQUIRED", "message": "type ERASE to confirm"})
    import asyncio
    return await asyncio.to_thread(lambda: _act(request.app.state.svc.actions.erase, req.key_id, req.tenant_id,
                                                req.department_id, actor=ident))


@router.put("/ops/siem")
async def siem_config(req: SiemReq, request: Request, ident: Identity = Depends(admin)):
    if req.protocol not in ("udp", "tcp") or not (1 <= req.port <= 65535) or (req.enabled and not req.host.strip()):
        raise HTTPException(422, detail={"code": "INVALID", "message": "host required; protocol udp|tcp; port 1-65535"})
    svc = request.app.state.svc
    v = svc.ops_settings.set("siem", req.model_dump(), ident.key_id)
    svc.ops.audit.record("siem.config", req.host or None, ident, enabled=req.enabled, port=req.port, protocol=req.protocol)
    return v


@router.post("/ops/siem/test")
async def siem_test(request: Request, ident: Identity = Depends(admin)):
    import asyncio
    svc = request.app.state.svc
    before = svc.ops.siem.sent
    await asyncio.to_thread(svc.ops.siem.send, {"type": "audit_recorded", "ts": time.time(), "request_id": None,
                                                "data": {"action": "siem.test", "actor": ident.key_id}})
    return {"sent": svc.ops.siem.sent > before, "error": svc.ops.siem.last_error}


# ---- exports & chargeback ------------------------------------------------------------------------------
EXPORT_COLS = ("ts", "request_id", "receipt_id", "tenant_id", "department_id", "key_id", "intent", "data_class", "route",
               "reason", "status", "http_status", "cache_status", "p_error", "latency_ms", "ttft_ms", "prompt_tokens",
               "completion_tokens", "tokens_avoided", "cost_usd", "counterfactual_usd", "energy_j", "remote_bytes",
               "policy_version", "model")


def _range(since: float | None, until: float | None) -> tuple[str, list]:
    where, args = ["1=1"], []
    if since:
        where.append("ts>=?")
        args.append(since)
    if until:
        where.append("ts<?")
        args.append(until)
    return " AND ".join(where), args


@router.get("/export/requests.csv", summary="Request metadata as CSV (no prompt text)")
async def export_requests(request: Request, since: float | None = None, until: float | None = None,
                          department: str | None = None, ident: Identity = Depends(reader)):
    svc = request.app.state.svc
    where, args = _range(since, until)
    if department:
        where += " AND department_id=?"
        args.append(department)
    rows = svc.db.all(f"SELECT {', '.join(EXPORT_COLS)} FROM requests WHERE {where} ORDER BY ts", tuple(args))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(("time_utc",) + EXPORT_COLS)
    for r in rows:
        w.writerow((time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["ts"])),) + tuple(r[c] for c in EXPORT_COLS))
    svc.ops.audit.record("export.requests", department, ident, rows=len(rows))
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="nanogate-requests.csv"'})


@router.get("/export/receipts.jsonl", summary="Sealed receipts with hashes, for independent verification")
async def export_receipts(request: Request, since: float | None = None, until: float | None = None,
                          ident: Identity = Depends(reader)):
    svc = request.app.state.svc
    where, args = _range(since, until)
    rows = svc.db.all(f"SELECT seq, receipt_id, request_id, ts, tenant_id, department_id, reason, body_json, prev_hash, hash,"
                      f" hmac, pruned_at FROM receipts WHERE {where} ORDER BY seq", tuple(args))

    def gen():
        for r in rows:
            body = r.pop("body_json")
            yield json.dumps({**r, "body": json.loads(body) if body else None, "body_canonical": body}) + "\n"
    svc.ops.audit.record("export.receipts", None, ident, rows=len(rows))
    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Content-Disposition": 'attachment; filename="nanogate-receipts.jsonl"'})


@router.get("/finops/chargeback", summary="Measured cost per department for one month")
async def chargeback(request: Request, month: str | None = None, tenant: str | None = None,
                     ident: Identity = Depends(reader)):
    return _act(request.app.state.svc.actions.chargeback, month, tenant)


@router.get("/finops/chargeback.csv")
async def chargeback_csv(request: Request, month: str | None = None, tenant: str | None = None,
                         ident: Identity = Depends(reader)):
    d = _act(request.app.state.svc.actions.chargeback, month, tenant)
    cols = ("tenant_id", "department_id", "display_name", "requests", "answered", "denied", "tokens", "tokens_avoided",
            "cost_usd", "counterfactual_usd", "avoided_usd", "budget_usd", "budget_used", "on_device_share", "energy_j")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(("month",) + cols)
    for r in d["rows"]:
        w.writerow((d["month"],) + tuple(r.get(c) for c in cols))
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="nanogate-chargeback-{d["month"]}.csv"'})


# ---- knowledge ------------------------------------------------------------------------------------------
class SourceReq(BaseModel):
    tenant_id: str
    name: str
    departments: list[str]
    data_class: str = "Internal"
    description: str = ""


class DocReq(BaseModel):
    filename: str
    content_b64: str


def _kb(svc):
    if svc.kb is None:
        raise HTTPException(503, detail={"code": "KNOWLEDGE_UNAVAILABLE", "message": svc.ml_error or "embedding model not loaded"})
    return svc.kb


@router.get("/knowledge")
async def knowledge(request: Request, tenant: str | None = None, ident: Identity = Depends(reader)):
    svc = request.app.state.svc
    return {"sources": svc.kb.list(tenant) if svc.kb else [], "available": svc.kb is not None,
            "reason": None if svc.kb else (svc.ml_error or "embedding model not loaded")}


@router.post("/knowledge")
async def create_source(req: SourceReq, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    kb = _kb(svc)
    depts = svc.policies.tenants.get(req.tenant_id, {}).get("departments", {})
    bad = [d for d in req.departments if d not in depts]
    if not depts or bad or not req.departments or not req.name.strip():
        raise HTTPException(422, detail={"code": "INVALID", "message": f"unknown tenant or departments {bad}; name and at least one department required"})
    try:
        s = kb.create_source(req.tenant_id, req.name.strip()[:80], req.departments, req.data_class, req.description[:400],
                             ident.key_id)
    except ValueError as e:
        raise HTTPException(422, detail={"code": "INVALID", "message": str(e)})
    svc.ops.audit.record("knowledge.create", s["source_id"], ident, name=s["name"], departments=req.departments,
                         data_class=req.data_class)
    return s


@router.post("/knowledge/{source_id}/documents", summary="Upload a document (base64 JSON body)")
async def add_document(source_id: str, req: DocReq, request: Request, ident: Identity = Depends(admin)):
    import asyncio
    svc = request.app.state.svc
    kb = _kb(svc)
    try:
        data = base64.b64decode(req.content_b64, validate=True)
        res = await asyncio.to_thread(kb.add_document, source_id, req.filename, data)
    except KeyError:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "unknown source"})
    except (ValueError, base64.binascii.Error) as e:
        raise HTTPException(422, detail={"code": "INVALID_DOCUMENT", "message": str(e)[:300]})
    if svc.cache is not None:
        svc.cache.set_source_version(source_id, res["version"])
        s = kb.source(source_id)
        res["cache_entries_invalidated"] = svc.cache.invalidate_departments(s["tenant_id"], s["departments"],
                                                                            f"knowledge {source_id} changed")
    svc.ops.audit.record("knowledge.document.add", source_id, ident, filename=req.filename, chunks=res["chunks"],
                         bytes=len(data), cache_entries_invalidated=res.get("cache_entries_invalidated"))
    return res


@router.delete("/knowledge/{source_id}/documents/{doc_id}")
async def remove_document(source_id: str, doc_id: str, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    kb = _kb(svc)
    v = kb.remove_document(source_id, doc_id)
    if svc.cache is not None:
        svc.cache.set_source_version(source_id, v)
        s = kb.source(source_id)
        svc.cache.invalidate_departments(s["tenant_id"], s["departments"], f"knowledge {source_id} changed")
    svc.ops.audit.record("knowledge.document.remove", source_id, ident, doc_id=doc_id)
    return {"source_id": source_id, "version": v}


@router.post("/knowledge/{source_id}/revoke")
async def revoke_kb(source_id: str, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.set_source_revoked, source_id, True, actor=ident)


@router.post("/knowledge/{source_id}/restore")
async def restore_kb(source_id: str, request: Request, ident: Identity = Depends(admin)):
    return _act(request.app.state.svc.actions.set_source_revoked, source_id, False, actor=ident)


@router.delete("/knowledge/{source_id}")
async def delete_kb(source_id: str, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    kb = _kb(svc)
    if not kb.source(source_id):
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "unknown source"})
    if svc.cache is not None:
        svc.cache.revoke_source(source_id)
    kb.delete_source(source_id)
    svc.ops.audit.record("knowledge.delete", source_id, ident)
    return {"deleted": source_id}


# ---- models -------------------------------------------------------------------------------------------
@router.get("/models", summary="Models the runtime is serving and how each tier is doing")
async def models(request: Request, ident: Identity = Depends(reader)):
    svc = request.app.state.svc
    tiers = []
    for a in [svc.local] + ([svc.local_large] if svc.local_large else []):
        tiers.append({"tier": a.tier, "model": a.model, "base_url": a.base_url, "status": a.status,
                      "details": a.details, "tokens_per_s": a.last_tokens_per_s, "queue_depth": a.queue_depth,
                      "in_flight": a.in_flight, "warmup_ms": a.warmup_ms})
    served: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=3) as c:
        for base in sorted({t["base_url"] for t in tiers}):
            try:
                r = await c.get(base.rstrip("/") + "/models")
                served[base] = [m.get("id") for m in r.json().get("data", [])]
            except Exception as e:
                served[base] = {"error": type(e).__name__}
    from .embeddings import device
    return {"tiers": tiers, "served": served,
            "support_models": [{"role": "cache retrieval embeddings", "model": svc.settings.embedding_model},
                               {"role": "cache verifier (NLI)", "model": svc.settings.verifier_model},
                               {"role": "router", "model": svc.router.meta.get("version")}],
            "ml_device": device() if svc.settings.load_ml else "not loaded",
            "commands": {"status": "zrt status", "serve": "zrt serve hf:<org>/<model> --gpu-memory-fraction 0.2",
                         "stop": "zrt stop hf:<org>/<model>", "project": "scripts/runtime.sh status|start|stop"}}


# ---- assistant ----------------------------------------------------------------------------------------
class AssistantReq(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[dict] = []


@router.post("/assistant/chat", summary="Ask the NanoGate Assistant (runs on the local models)")
async def assistant_chat(req: AssistantReq, request: Request, ident: Identity = Depends(reader)):
    svc = request.app.state.svc
    if not any(a and a.status.get("state") == "ready" for a in (svc.local, svc.local_large)):
        raise HTTPException(503, detail={"code": Reason.MODEL_UNAVAILABLE.value, "message": "no local model is ready"})
    res = await svc.assistant.chat(ident, req.history, req.message)
    if "admin" not in ident.scopes:   # auditors can ask, not change
        res["proposals"] = []
    svc.bus.emit("assistant_answered", steps=len(res["steps"]), proposals=len(res["proposals"]), ms=res["elapsed_ms"])
    return res


@router.post("/assistant/proposals/{proposal_id}/confirm")
async def assistant_confirm(proposal_id: str, request: Request, ident: Identity = Depends(admin)):
    import asyncio
    svc = request.app.state.svc
    try:
        return await asyncio.to_thread(svc.assistant.confirm, proposal_id, ident)
    except ActionError as e:
        raise HTTPException(422, detail={"code": "INVALID_ACTION", "message": str(e)})


@router.post("/assistant/proposals/{proposal_id}/dismiss")
async def assistant_dismiss(proposal_id: str, request: Request, ident: Identity = Depends(admin)):
    request.app.state.svc.assistant.dismiss(proposal_id)
    return {"dismissed": proposal_id}


# ---- employee chat app --------------------------------------------------------------------------------
class ChatSessionReq(BaseModel):
    api_key: str


class ChatSendReq(BaseModel):
    messages: list[dict] = Field(..., min_length=1, max_length=40)
    model: str = "nanogate-auto"


def chat_user(request: Request, authorization: str | None = Header(None)) -> Identity:
    svc = request.app.state.svc
    raw = bearer(authorization)
    ident = resolve_session(svc, raw) if raw and raw.startswith("ngs_") else None
    if ident is None:
        raise HTTPException(401, detail={"code": Reason.AUTH_INVALID.value, "message": "chat session required"})
    if not ident.has("chat"):
        raise HTTPException(403, detail={"code": Reason.SCOPE_DENIED.value, "message": "this key cannot chat"})
    return ident


@router.post("/chat/session", summary="Sign in to the chat app with a person or app key")
async def chat_session(req: ChatSessionReq, request: Request):
    svc = request.app.state.svc
    try:
        ident = svc.keys.resolve(req.api_key, svc.policies.policy_for)
    except AuthError as e:
        raise HTTPException(e.status, detail={"code": e.reason.value, "message": e.message})
    if not ident.has("chat"):
        raise HTTPException(403, detail={"code": Reason.SCOPE_DENIED.value, "message": "this key cannot chat"})
    dept = svc.policies.tenants[ident.tenant_id]["departments"][ident.department_id]
    return {"token": issue_session(svc, ident, ttl_s=8 * 3600), "expires_in": 8 * 3600,
            "identity": {"name": (ident.label or "").split(":", 1)[-1], "tenant": svc.policies.tenants[ident.tenant_id]["display_name"],
                         "department": dept["display_name"], "department_id": ident.department_id,
                         "is_admin": "admin" in ident.scopes}}


@router.post("/chat/send", summary="Send a conversation through the full NanoGate pipeline")
async def chat_send(req: ChatSendReq, request: Request, ident: Identity = Depends(chat_user)):
    msgs = [{"role": m.get("role"), "content": str(m.get("content", ""))[:16000]} for m in req.messages
            if m.get("role") in ("user", "assistant")]
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(422, detail={"code": "INVALID_REQUEST", "message": "the last message must be from the user"})
    body = {"model": req.model if req.model in ("nanogate-auto", "nanogate-local", "nanogate-local-large") else "nanogate-auto",
            "messages": msgs, "max_tokens": 768, "temperature": 0.3}
    try:
        resp, headers = await request.app.state.pipeline.complete(ident, body)
        return {"ok": True, "message": resp["choices"][0]["message"]["content"], "usage": resp.get("usage"),
                "headers": headers}
    except GatewayError as e:
        return {"ok": False, "error": e.body()["error"], "headers": e.headers}

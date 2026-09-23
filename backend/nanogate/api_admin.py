"""Dashboard / administration API. Every number returned is computed from the database,
live services, benchmark artifacts on disk, or explicit scenario configuration."""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .auth import AuthError, Identity, bearer
from .cache import conversation_keys, namespace_fields, namespace_id
from .dlp import LayeredDLP
from .pipeline import GatewayError
from .policy import Policy
from .receipts import canonical
from .reason_codes import Reason
from .settings import ROOT

router = APIRouter(prefix="/api", tags=["Dashboard"])
RESULTS = ROOT / "results"
ARTIFACTS = ROOT / "artifacts"


# ---- dashboard sessions ---------------------------------------------------------------------------
def _session_key(svc) -> bytes:
    return hmac.new(svc.keys.pepper, b"dashboard-session", hashlib.sha256).digest()


def issue_session(svc, ident: Identity, ttl_s: int = 12 * 3600) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"k": ident.key_id, "exp": int(time.time()) + ttl_s}).encode()).decode()
    sig = hmac.new(_session_key(svc), payload.encode(), hashlib.sha256).hexdigest()
    return f"ngs_{payload}.{sig}"


def resolve_session(svc, token: str) -> Identity | None:
    if not token or not token.startswith("ngs_") or "." not in token:
        return None
    payload, sig = token[4:].rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_session_key(svc), payload.encode(), hashlib.sha256).hexdigest()):
        return None
    d = json.loads(base64.urlsafe_b64decode(payload.encode()))
    if d["exp"] < time.time():
        return None
    row = svc.db.one("SELECT * FROM api_keys WHERE key_id=?", (d["k"],))
    if not row or row["revoked_at"] or (row["expires_at"] and row["expires_at"] < time.time()):
        return None
    return Identity(row["key_id"], row["key_hash"], row["tenant_id"], row["department_id"], row["role"],
                    json.loads(row["scopes"]), svc.policies.policy_for(row["tenant_id"], row["department_id"]), row["label"])


def admin(request: Request, authorization: str | None = Header(None), token: str | None = Query(None)) -> Identity:
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
    if "admin" not in ident.scopes:
        raise HTTPException(403, detail={"code": Reason.SCOPE_DENIED.value, "message": "admin scope required"})
    return ident


class SessionReq(BaseModel):
    api_key: str


@router.post("/session", summary="Exchange an admin API key for a 12h dashboard session token")
async def create_session(req: SessionReq, request: Request):
    svc = request.app.state.svc
    try:
        ident = svc.keys.resolve(req.api_key, svc.policies.policy_for)
    except AuthError as e:
        raise HTTPException(e.status, detail={"code": e.reason.value, "message": e.message})
    if "admin" not in ident.scopes:
        raise HTTPException(403, detail={"code": Reason.SCOPE_DENIED.value, "message": "admin scope required"})
    return {"token": issue_session(svc, ident), "identity": ident.public(), "expires_in": 12 * 3600}


@router.get("/me")
async def me(ident: Identity = Depends(admin)):
    return ident.public()


# ---- live events ------------------------------------------------------------------------------------
@router.get("/events", summary="Server-sent events emitted by real backend state changes")
async def events(request: Request, since: int = 0, ident: Identity = Depends(admin)):
    bus = request.app.state.svc.bus
    q = bus.subscribe()

    async def gen():
        try:
            for ev in bus.recent(since):
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache",
                                                                             "X-Accel-Buffering": "no"})


# ---- overview ---------------------------------------------------------------------------------------
def _pct(vals: list[float], p: float) -> float | None:
    return float(np.percentile(vals, p)) if vals else None


@router.get("/overview")
async def overview(request: Request, window_h: float = 24, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    since = time.time() - window_h * 3600
    rows = svc.db.all("SELECT route, status, latency_ms, ttft_ms, data_class, remote_bytes, cache_status, cost_usd, "
                      "counterfactual_usd, prompt_tokens, completion_tokens, tokens_avoided FROM requests WHERE ts>=?", (since,))
    ok = [r for r in rows if r["status"] == "ok"]
    local_like = [r for r in ok if r["route"] in ("local", "local_large", "cache")]
    lat = [r["latency_ms"] for r in ok if r["latency_ms"] is not None]
    lookups = [r for r in rows if r["cache_status"] and r["cache_status"] != "CACHE_INELIGIBLE"]
    hits = [r for r in rows if r["cache_status"] == "CACHE_VERIFIED"]
    sensitive = [r for r in rows if r["data_class"] in ("Confidential", "Restricted", "Secret")]
    avoided = sum(max(0.0, (r["counterfactual_usd"] or 0) - (r["cost_usd"] or 0)) for r in ok)
    return {
        "window_hours": window_h, "generated_at": time.time(),
        "requests": len(rows), "answered": len(ok), "denied": sum(1 for r in rows if r["status"] == "denied"),
        "errors": sum(1 for r in rows if r["status"] == "error"),
        "local_coverage": (len(local_like) / len(ok)) if ok else None,
        "cost_actual_usd": sum(r["cost_usd"] or 0 for r in ok),
        "cost_avoided_usd": avoided if ok else None,
        "cost_basis": f"measured tokens x {svc.pricing.reference.provider}/{svc.pricing.reference.model} rate (config/pricing.yaml)",
        "sensitive_requests": len(sensitive),
        "sensitive_egress_bytes": sum(r["remote_bytes"] or 0 for r in sensitive),
        "latency_avg_ms": (sum(lat) / len(lat)) if lat else None,
        "latency_p50_ms": _pct(lat, 50), "latency_p95_ms": _pct(lat, 95), "latency_samples": len(lat),
        "cache_lookups": len(lookups), "cache_hits": len(hits),
        "cache_verification_rate": (len(hits) / len(lookups)) if lookups else None,
        "tokens_total": sum((r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0) for r in ok),
        "tokens_avoided": sum(r["tokens_avoided"] or 0 for r in ok),
        "queue_depth": svc.local.queue_depth, "in_flight": svc.local.in_flight,
        "model": {"name": svc.local.model, "state": svc.local.status.get("state"), "reason": svc.local.status.get("reason"),
                  "tokens_per_s": svc.local.last_tokens_per_s, "revision": svc.local.revision},
        "route_counts": _count(ok, "route"),
    }


def _count(rows: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r[key])] = out.get(str(r[key]), 0) + 1
    return out


@router.get("/status")
async def status(request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    ok, failing = svc.ready()
    return {"ready": ok, "failing": failing, "components": svc.components(), "app_mode": svc.settings.app_mode,
            "telemetry_mode": svc.telemetry.mode, "remote": svc.remote.health_state, "commit": svc.commit,
            "sse_subscribers": svc.bus.subscribers, "host": svc.last_sample and "zgx"}


# ---- requests / receipts ----------------------------------------------------------------------------
@router.get("/requests")
async def list_requests(request: Request, department: str | None = None, data_class: str | None = None,
                        route: str | None = None, reason: str | None = None, status: str | None = None,
                        cache: str | None = None, tenant: str | None = None, since: float | None = None,
                        q: str | None = None, limit: int = 100, offset: int = 0, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    where, args = ["1=1"], []
    for col, val in (("department_id", department), ("data_class", data_class), ("route", route), ("reason", reason),
                     ("status", status), ("cache_status", cache), ("tenant_id", tenant)):
        if val:
            where.append(f"{col}=?")
            args.append(val)
    if since:
        where.append("ts>=?")
        args.append(since)
    if q:
        where.append("(request_id LIKE ? OR receipt_id LIKE ? OR reason_codes LIKE ?)")
        args += [f"%{q}%"] * 3
    total = svc.db.one(f"SELECT COUNT(*) n FROM requests WHERE {' AND '.join(where)}", tuple(args))["n"]
    rows = svc.db.all(f"SELECT request_id, receipt_id, ts, tenant_id, department_id, intent, data_class, route, reason,"
                      f" reason_codes, status, http_status, cache_status, p_error, latency_ms, ttft_ms, prompt_tokens,"
                      f" completion_tokens, tokens_avoided, cost_usd, counterfactual_usd, energy_j, remote_bytes,"
                      f" policy_version, model, stream FROM requests WHERE {' AND '.join(where)}"
                      f" ORDER BY ts DESC LIMIT ? OFFSET ?", tuple(args) + (min(limit, 500), offset))
    for r in rows:
        r["reason_codes"] = json.loads(r["reason_codes"] or "[]")
    facets = {c: [x[c] for x in svc.db.all(f"SELECT DISTINCT {c} FROM requests WHERE {c} IS NOT NULL ORDER BY {c}")]
              for c in ("department_id", "data_class", "route", "reason", "status", "cache_status", "tenant_id")}
    return {"total": total, "rows": rows, "facets": facets}


@router.get("/receipts/{receipt_id}")
async def get_receipt(receipt_id: str, request: Request, ident: Identity = Depends(admin)):
    r = request.app.state.svc.receipts.get(receipt_id)
    if not r:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "receipt not found"})
    return r


@router.post("/receipts/{receipt_id}/verify")
async def verify_receipt(receipt_id: str, request: Request, ident: Identity = Depends(admin)):
    return request.app.state.svc.receipts.verify(receipt_id)


@router.get("/receipts-chain/verify")
async def verify_chain(request: Request, ident: Identity = Depends(admin)):
    return request.app.state.svc.receipts.verify_chain()


class TamperReq(BaseModel):
    field: str = "decision.chosen_route"
    value: Any = "remote"


@router.post("/receipts/{receipt_id}/tamper-demo",
             summary="Real cryptographic tamper check on a detached copy (the audit log itself is not modified)")
async def tamper_demo(receipt_id: str, req: TamperReq, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    row = svc.db.one("SELECT * FROM receipts WHERE receipt_id=?", (receipt_id,))
    if not row:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "receipt not found"})
    original = svc.receipts.verify(receipt_id)
    body = json.loads(row["body_json"])
    cur: Any = body
    parts = req.field.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    before = cur.get(parts[-1])
    cur[parts[-1]] = req.value
    mutated_canon = canonical(body)
    recomputed = hashlib.sha256((row["prev_hash"] + mutated_canon).encode()).hexdigest()
    mac_of_recomputed = hmac.new(svc.receipts.key, recomputed.encode(), hashlib.sha256).hexdigest()
    return {"receipt_id": receipt_id, "field": req.field, "original_value": before, "tampered_value": req.value,
            "original_verification": original, "stored_hash": row["hash"], "tampered_hash": recomputed,
            "hash_matches": recomputed == row["hash"], "hmac_matches": hmac.compare_digest(mac_of_recomputed, row["hmac"]),
            "verdict": "INTEGRITY FAILURE DETECTED" if recomputed != row["hash"] else "UNCHANGED",
            "note": "The mutation is applied to a detached copy; the sealed audit chain is untouched."}


# ---- playground (dashboard-originated real requests) -----------------------------------------------
class PlaygroundReq(BaseModel):
    tenant: str = "acme"
    department: str = "it"
    content: str
    model: str = "nanogate-auto"
    max_tokens: int | None = 256
    temperature: float | None = 0.2
    system: str | None = None
    spoof_tenant_header: str | None = None


@router.post("/playground/chat", summary="Send a real request through the pipeline as a department principal")
async def playground(req: PlaygroundReq, request: Request, ident: Identity = Depends(admin)):
    app = request.app
    principal = app.state.playground_identity(req.tenant, req.department)
    if principal is None:
        raise HTTPException(404, detail={"code": "NO_PRINCIPAL", "message": f"no playground key for {req.tenant}/{req.department}"})
    msgs = ([{"role": "system", "content": req.system}] if req.system else []) + [{"role": "user", "content": req.content}]
    body = {"model": req.model, "messages": msgs, "max_tokens": req.max_tokens, "temperature": req.temperature}
    pipe = app.state.pipeline
    try:
        if req.spoof_tenant_header:
            from .auth import check_spoofing
            try:
                check_spoofing(principal, {"x-tenant": req.spoof_tenant_header})
            except AuthError as e:
                raise pipe.auth_failure(e, body)
        resp, headers = await pipe.complete(principal, body)
        return {"ok": True, "response": resp, "headers": headers}
    except GatewayError as e:
        return JSONResponse({"ok": False, "error": e.body()["error"], "headers": e.headers}, status_code=200)


# ---- policies ---------------------------------------------------------------------------------------
@router.get("/policies")
async def policies(request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    return {"policies": svc.policies.list(), "tenants": svc.policies.tenants}


@router.get("/policies/{policy_id}")
async def policy(policy_id: str, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    for p in svc.policies.list():
        if p["policy_id"] == policy_id:
            return p
    raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "policy not found"})


class PolicyTestReq(BaseModel):
    policy_id: str | None = None
    tenant: str = "acme"
    department: str = "hr"
    prompt: str


@router.post("/policies/test", summary="Run the real DLP + policy engine against a test prompt (no inference)")
async def policy_test(req: PolicyTestReq, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    pid = req.policy_id or svc.policies.policy_for(req.tenant, req.department)
    dres = await asyncio.to_thread(svc.dlp.scan, req.prompt)
    dec = svc.policies.evaluate(pid, dres.data_class, dres.counts, dres.has_secret, dres.has_pii, dres.available,
                                svc.remote.mode)
    transformed = None
    if dec.transform == "redact":
        transformed = LayeredDLP.redact(req.prompt, dres.findings)
    elif dec.transform == "tokenize":
        transformed = LayeredDLP.tokenize(req.prompt, dres.findings)[0]
    # "0 bytes may leave device" is only asserted when the evaluated decision removes every remote path.
    enforced_local = (not dec.egress_permitted) and "remote" not in dec.allowed_routes
    return {"policy_version": dec.policy_version, "classification": dec.data_class, "dlp": dres.public(),
            "findings": dres.counts, "allowed_routes": dec.allowed_routes, "denied_routes": dec.denied_routes,
            "action": dec.action, "reason_codes": dec.reason_codes, "egress": {"permitted": dec.egress_permitted,
                                                                                "reason": dec.egress_reason},
            "remote_path_removed": enforced_local, "transformed_preview": transformed, "cache_eligible": dec.cache_eligible,
            "remote_connector_mode": svc.remote.mode}


class PublishReq(BaseModel):
    policy_id: str
    body: dict


@router.post("/policies/publish")
async def policy_publish(req: PublishReq, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    try:
        p = svc.policies.publish(req.policy_id, req.body, actor=f"key:{ident.key_id}")
    except Exception as e:
        raise HTTPException(422, detail={"code": "POLICY_INVALID", "message": str(e)[:400]})
    return {"published": p.version_tag, "sha256": p.body_sha256()}


# ---- router lab -------------------------------------------------------------------------------------
def _json(p: Path) -> Any:
    return json.loads(p.read_text()) if p.exists() else None


@router.get("/router")
async def router_info(request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    meta = svc.router.meta or None
    evald = _json(ARTIFACTS / "router" / "evaluation.json")
    return {"available": svc.router.available, "error": svc.router.error, "meta": meta, "evaluation": evald,
            "live_counts": _count(svc.db.all("SELECT route FROM requests WHERE status='ok'"), "route")}


class ThresholdReq(BaseModel):
    threshold: float


@router.post("/router/threshold-preview", summary="Recompute coverage/risk at a threshold on the frozen test predictions")
async def threshold_preview(req: ThresholdReq, request: Request, ident: Identity = Depends(admin)):
    preds = _json(ARTIFACTS / "router" / "test_predictions.json")
    if not preds:
        raise HTTPException(404, detail={"code": "ROUTER_UNAVAILABLE", "message": "no test predictions artifact"})
    p = np.asarray(preds["p_error"])
    y = np.asarray(preds["y_error"])
    yl = np.asarray(preds.get("large_correct") or [np.nan] * len(y), dtype=float)
    acc = p < req.threshold
    cov = float(acc.mean())
    risk = float(y[acc].mean()) if acc.any() else None
    out = {"threshold": req.threshold, "n": int(len(y)), "coverage": cov, "selective_risk": risk,
           "selective_accuracy": (1 - risk) if risk is not None else None, "deferred": int((~acc).sum()),
           "kind": "Preview on frozen test set (not a new measurement of live traffic)"}
    if not np.isnan(yl).all():
        final = np.where(acc, 1 - y, yl)
        out["system_accuracy_with_large_escalation"] = float(np.nanmean(final))
    return out


# ---- cache ------------------------------------------------------------------------------------------
@router.get("/cache")
async def cache_info(request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    bench = _latest("cache_metrics.json")
    return {"summary": svc.cache.summary() if svc.cache else None, "error": svc.ml_error,
            "benchmark": bench, "sources": [{"source_id": s, "version": v, "revoked": s in (svc.cache.revoked_sources if svc.cache else set())}
                                            for s, v in (svc.cache.source_versions.items() if svc.cache else [])],
            "entries": svc.db.all("SELECT cache_id, tenant_id, department_id, namespace, policy_version, data_class, "
                                  "created_at, expires_at, validation_state, invalidated_reason, hits, source_id, "
                                  "substr(canonical_query,1,120) AS query_preview FROM cache_entries ORDER BY created_at DESC LIMIT 100")}


class PairReq(BaseModel):
    a: str
    b: str
    tenant_a: str = "acme"
    department_a: str = "it"
    tenant_b: str = "acme"
    department_b: str = "it"
    system_a: str | None = None
    system_b: str | None = None


@router.post("/cache/test-pair", summary="Evaluate two requests with the real embedding + verifier + namespace rules")
async def cache_pair(req: PairReq, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    if svc.cache is None:
        raise HTTPException(503, detail={"code": "CACHE_UNAVAILABLE", "message": svc.ml_error or "cache not loaded"})

    def ns_for(tenant, dept, system, text):
        pid = svc.policies.policy_for(tenant, dept)
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": text}]
        _, sh, cf = conversation_keys(msgs)
        return namespace_fields(tenant, dept, svc.policies.get(pid).version_tag, svc.settings.local_model_family, sh, cf)

    def run():
        na, nb = ns_for(req.tenant_a, req.department_a, req.system_a, req.a), ns_for(req.tenant_b, req.department_b, req.system_b, req.b)
        v = svc.cache.embed([req.a, req.b])
        sim = float(v[0] @ v[1])
        verdict = svc.cache.verifier.verify(req.b, req.a)
        diff = [k for k in na if na[k] != nb[k]]
        th = svc.cache.th
        if "tenant_id" in diff or "department_id" in diff:
            decision = Reason.CACHE_NAMESPACE_MISMATCH.value
        elif "policy_version" in diff:
            decision = Reason.CACHE_STALE.value
        elif diff:
            decision = Reason.CACHE_CONTEXT_MISMATCH.value
        elif sim < th.retrieval:
            decision = Reason.CACHE_MISS.value
        elif verdict.equivalent:
            decision = Reason.CACHE_VERIFIED.value
        else:
            decision = Reason.CACHE_HARD_NEGATIVE.value
        return {"decision": decision, "equivalent": decision == Reason.CACHE_VERIFIED.value, "similarity": sim,
                "retrieval_threshold": th.retrieval, "verifier_threshold": th.verifier, "verifier": verdict.public(),
                "namespace_a": {**na, "id": namespace_id(na)}, "namespace_b": {**nb, "id": namespace_id(nb)},
                "namespace_differences": diff, "threshold_source": th.source}

    return await asyncio.to_thread(run)


@router.post("/cache/sources/{source_id}/revoke")
async def revoke_source(source_id: str, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    n = svc.cache.revoke_source(source_id)
    svc.bus.emit("service_state_changed", component=f"source:{source_id}", state="revoked", cache_entries_revoked=n)
    return {"source_id": source_id, "revoked": True, "entries_revoked": n}


@router.post("/cache/sources/{source_id}/restore")
async def restore_source(source_id: str, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    svc.cache.restore_source(source_id)
    svc.bus.emit("service_state_changed", component=f"source:{source_id}", state="restored")
    return {"source_id": source_id, "revoked": False}


# ---- finops -----------------------------------------------------------------------------------------
@router.get("/finops/measured")
async def finops_measured(request: Request, days: int = 30, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    since = time.time() - days * 86400
    rows = svc.db.all("SELECT ts, department_id, route, status, prompt_tokens, completion_tokens, tokens_avoided, cost_usd, "
                      "counterfactual_usd, energy_j FROM requests WHERE ts>=? AND status='ok' ORDER BY ts", (since,))
    by_route: dict[str, dict] = {}
    by_dept: dict[str, dict] = {}
    series: dict[str, dict] = {}
    for r in rows:
        tok = (r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0)
        for key, bucket in ((r["route"], by_route), (r["department_id"], by_dept)):
            b = bucket.setdefault(key, {"requests": 0, "tokens": 0, "cost_usd": 0.0, "counterfactual_usd": 0.0,
                                        "tokens_avoided": 0, "energy_j": 0.0})
            b["requests"] += 1
            b["tokens"] += tok
            b["cost_usd"] += r["cost_usd"] or 0
            b["counterfactual_usd"] += r["counterfactual_usd"] or 0
            b["tokens_avoided"] += r["tokens_avoided"] or 0
            b["energy_j"] += r["energy_j"] or 0
        hour = dt.datetime.fromtimestamp(r["ts"], dt.timezone.utc).strftime("%Y-%m-%dT%H:00Z")
        s = series.setdefault(hour, {"bucket": hour, "tokens": 0, "tokens_avoided": 0, "requests": 0, "cost_usd": 0.0,
                                     "counterfactual_usd": 0.0})
        s["tokens"] += tok
        s["tokens_avoided"] += r["tokens_avoided"] or 0
        s["requests"] += 1
        s["cost_usd"] += r["cost_usd"] or 0
        s["counterfactual_usd"] += r["counterfactual_usd"] or 0
    cache_rows = [r for r in rows if r["route"] == "cache"]
    return {
        "label": "Measured", "window_days": days, "requests": len(rows),
        "tokens": sum((r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0) for r in rows),
        "tokens_avoided": sum(r["tokens_avoided"] or 0 for r in rows),
        "actual_cost_usd": sum(r["cost_usd"] or 0 for r in rows),
        "counterfactual_usd": sum(r["counterfactual_usd"] or 0 for r in rows),
        "cache_avoided_usd": sum(r["counterfactual_usd"] or 0 for r in cache_rows),
        "energy_j": sum(r["energy_j"] or 0 for r in rows),
        "by_route": by_route, "by_department": by_dept, "series": list(series.values()),
        "budgets": svc.budget.accounts(), "pricing": svc.pricing.public(),
        "attribution_note": "Avoided cost = (reference hosted-API rate x measured tokens) - actual cost. "
                            "Cache avoided cost uses the original answer's measured tokens.",
    }


@router.get("/finops/scenario")
async def finops_scenario(request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    sc = yaml.safe_load(svc.settings.scenario_file.read_text())
    ref = svc.pricing.for_model(sc["comparison"]["model"], sc["comparison"]["provider"])
    w = sc["workload"]
    measured = svc.db.one("SELECT COUNT(*) n, AVG(prompt_tokens) p, AVG(completion_tokens) c, "
                          "SUM(route='cache')*1.0/COUNT(*) hit, SUM(route IN ('local','local_large','cache'))*1.0/COUNT(*) cov, "
                          "AVG(energy_j) e FROM requests WHERE status='ok'") or {}
    hit = w["cache_hit_rate"] if w["cache_hit_rate"] is not None else measured.get("hit")
    cov = w["local_coverage"] if w["local_coverage"] is not None else measured.get("cov")
    n = w["annual_requests"]
    api_cost = ref.cost(w["avg_prompt_tokens"], w["avg_completion_tokens"]) * n if ref else None
    hw = sc["hardware"]
    amort = hw["purchase_price_usd"] / hw["useful_life_years"]
    e_j = measured.get("e")
    energy_cost = (e_j * n * (1 - (hit or 0)) / 3.6e6) * sc["electricity"]["price_usd_per_kwh"] if e_j else None
    residual_remote = api_cost * (1 - (cov or 0)) if (api_cost is not None and cov is not None) else None
    return {"label": "Scenario assumption", "assumptions": sc,
            "inputs_from_measurement": {"cache_hit_rate": measured.get("hit") if w["cache_hit_rate"] is None else None,
                                        "local_coverage": measured.get("cov") if w["local_coverage"] is None else None,
                                        "avg_gpu_energy_j_per_request": e_j, "sample_requests": measured.get("n")},
            "projection": {"all_remote_api_cost_usd": api_cost, "hardware_amortization_usd_per_year": amort,
                           "gpu_energy_cost_usd_per_year": energy_cost, "residual_remote_cost_usd_per_year": residual_remote,
                           "projected_nanogate_cost_usd_per_year": (amort + (energy_cost or 0) + (residual_remote or 0))
                           if residual_remote is not None else None},
            "warning": "Projection built from assumptions in config/scenario.yaml. Not a measurement."}


# ---- infrastructure ---------------------------------------------------------------------------------
@router.get("/infrastructure")
async def infrastructure(request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    snap = await asyncio.to_thread(svc.telemetry.snapshot)
    models = []
    for a in [svc.local] + ([svc.local_large] if svc.local_large else []):
        models.append({"tier": a.tier, "name": a.model, "status": a.status, "details": a.details, "revision": a.revision,
                       "warmup_ms": a.warmup_ms, "tokens_per_s": a.last_tokens_per_s, "queue_depth": a.queue_depth,
                       "in_flight": a.in_flight, "placement": await a.placement()})
    from .services import hf_revision
    aux = [{"role": "embedding", "name": svc.settings.embedding_model, "revision": hf_revision(svc.settings.embedding_model),
            "device": _ml_device(), "loaded": svc.cache is not None},
           {"role": "cache verifier (NLI)", "name": svc.settings.verifier_model, "revision": hf_revision(svc.settings.verifier_model),
            "device": _ml_device(), "loaded": svc.cache is not None},
           {"role": "DLP NER", "name": "spaCy en_core_web_sm via Presidio", "revision": _spacy_version(),
            "device": "cpu", "loaded": svc.dlp.status()["layers"]["presidio"]},
           {"role": "router", "name": "logistic regression + isotonic calibration", "revision": svc.router.meta.get("version"),
            "device": "cpu", "loaded": svc.router.available}]
    return {"telemetry": snap, "latest_sample": svc.last_sample, "models": models, "aux_models": aux,
            "services": svc.components(), "remote": svc.remote.health_state, "egress": svc.egress.summary(),
            "offline": svc.offline_report, "uptime_s": time.time() - svc.started_at,
            "host_uptime_s": _host_uptime()}


def _ml_device() -> str:
    from .embeddings import device
    return device()


def _spacy_version() -> str | None:
    try:
        import spacy
        return f"spacy {spacy.__version__}"
    except Exception:
        return None


def _host_uptime() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return None


@router.get("/telemetry/history")
async def telemetry_history(request: Request, minutes: int = 60, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    return {"mode": svc.telemetry.mode, "rows": svc.db.all(
        "SELECT * FROM telemetry_samples WHERE ts>=? ORDER BY ts", (time.time() - minutes * 60,))}


@router.post("/offline/verify", summary="Run the offline verification (real request with outbound network blocked)")
async def offline_verify(request: Request, ident: Identity = Depends(admin)):
    from .offline import run_offline_verification
    svc = request.app.state.svc
    res = await run_offline_verification(request.app)
    svc.offline_report = res
    svc.settings.offline_report.write_text(json.dumps(res, indent=2, default=str))
    svc.bus.emit("offline_verification", verdict=res.get("verdict"), mode=res.get("isolation_mode"))
    return res


class RemoteModeReq(BaseModel):
    mode: str


@router.post("/remote/mode", summary="Switch the remote connector mode at runtime")
async def remote_mode(req: RemoteModeReq, request: Request, ident: Identity = Depends(admin)):
    svc = request.app.state.svc
    try:
        svc.remote.set_mode(req.mode)
    except ValueError as e:
        raise HTTPException(422, detail={"code": "INVALID_MODE", "message": str(e)})
    st = await svc.remote.health()
    svc.bus.emit("service_state_changed", component="remote", state=st.get("state"), mode=req.mode)
    return st


# ---- datasets / benchmarks / security ----------------------------------------------------------------
@router.get("/datasets")
async def datasets(request: Request, ident: Identity = Depends(admin)):
    reg = _json(ROOT / "datasets" / "registry.json") or {}
    now = time.time()
    out = []
    for name, d in reg.items():
        try:
            age = now - dt.datetime.fromisoformat(d["retrieved_at"]).timestamp()
        except Exception:
            age = None
        stale_after = 7 * 86400 if name == "cisa_kev" else None   # KEV updates frequently; static datasets never go stale
        out.append({"name": name, **d, "age_s": age, "stale": bool(stale_after and age and age > stale_after),
                    "live_source": name == "cisa_kev"})
    return {"datasets": out}


def _runs() -> list[Path]:
    return sorted([p for p in RESULTS.glob("*") if p.is_dir() and (p / "run_manifest.json").exists()], reverse=True)


def _latest(fname: str) -> dict | None:
    for p in _runs():
        if (p / fname).exists():
            d = json.loads((p / fname).read_text())
            d["_run_id"] = p.name
            return d
    return None


@router.get("/benchmarks")
async def benchmarks(request: Request, ident: Identity = Depends(admin)):
    out = []
    for p in _runs():
        m = json.loads((p / "run_manifest.json").read_text())
        s = _json(p / "summary.json")
        out.append({"run_id": p.name, "manifest": m, "summary": s, "files": sorted(f.name for f in p.iterdir())})
    return {"runs": out}


@router.get("/benchmarks/{run_id}/{fname}")
async def benchmark_file(run_id: str, fname: str, request: Request, ident: Identity = Depends(admin)):
    p = (RESULTS / run_id / fname).resolve()
    if not str(p).startswith(str(RESULTS.resolve())) or not p.exists() or p.suffix != ".json":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "file not found"})
    return json.loads(p.read_text())


@router.get("/security")
async def security(request: Request, ident: Identity = Depends(admin)):
    return {"latest": _latest("security_results.json")}

"""The NanoGate decision pipeline.

REQUEST → AUTHENTICATE → IDENTIFY → RATE LIMIT → CLASSIFY (DLP) → POLICY → BUDGET RESERVE
→ VERIFIED CACHE → LOCAL INFERENCE → RISK ESTIMATION (router) → ROUTE → OUTPUT SECURITY
→ COST SETTLEMENT → CACHE WRITE → SEALED RECEIPT → RESPONSE

Every stage appends to the request timeline and emits a live event. Nothing here fabricates
output: if a stage cannot run, the request fails with an explicit reason code and a receipt.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from . import metrics as M
from . import router_features as rf
from .auth import AuthError, Identity
from .budget import BudgetDenied, Reservation
from .cache import CacheLookup, conversation_keys, namespace_fields, namespace_id
from .dlp import SECRET_TYPES, DlpResult, LayeredDLP
from .inference import GenerationResult, ModelUnavailable
from .knowledge import needs_kev, rag_messages
from .logging_setup import log as jlog, request_id_var
from .policy import PolicyDecision
from .reason_codes import Reason, primary
from .remote import ConnectorUnavailable, EgressDenied
from .services import Services

log = logging.getLogger("nanogate.pipeline")

MODEL_ALIASES = {"nanogate-auto": "auto", "nanogate-local": "local", "nanogate-local-large": "local_large",
                 "nanogate-remote": "remote"}


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class GatewayError(Exception):
    def __init__(self, status: int, reason: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.reason, self.message, self.headers = status, reason, message, headers or {}

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": "nanogate_policy_error" if self.status < 500 else "nanogate_error",
                          "code": self.reason, "param": None}}


@dataclass
class Ctx:
    svc: Services
    request_id: str = field(default_factory=lambda: "req_" + uuid.uuid4().hex[:20])
    receipt_id: str = field(default_factory=lambda: "rcpt_" + uuid.uuid4().hex[:20])
    t0: float = field(default_factory=time.perf_counter)
    wall0: float = field(default_factory=time.time)
    identity: Identity | None = None
    timeline: list[dict] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    stream: bool = False
    # evidence
    dlp_in: DlpResult | None = None
    dlp_out: DlpResult | None = None
    decision: PolicyDecision | None = None
    cache: CacheLookup | None = None
    gen: GenerationResult | None = None
    final_gen: GenerationResult | None = None
    router: dict | None = None
    route: str = "none"
    fallback_state: str | None = None
    requested_route: str = "auto"
    reservation: Reservation | None = None
    retrieval: dict | None = None
    energy_j: float | None = None
    cost: dict = field(default_factory=dict)
    output_action: str | None = None
    intent: str | None = None
    prompt_hash: str | None = None
    answer_hash: str | None = None
    params: dict = field(default_factory=dict)
    ns: dict | None = None
    cache_write: str | None = None
    error: str | None = None

    def stage(self, name: str, status: str, **detail) -> None:
        now = time.perf_counter()
        entry = {"stage": name, "status": status, "t_ms": round((now - self.t0) * 1000, 2), "detail": detail}
        self.timeline.append(entry)
        self.svc.bus.emit(f"stage:{name}", self.request_id, status=status, receipt_id=self.receipt_id,
                          department=self.identity.department_id if self.identity else None, detail=detail)

    def code(self, c: Reason | str) -> None:
        v = c.value if isinstance(c, Reason) else c
        if v not in self.codes:
            self.codes.append(v)

    @property
    def reason(self) -> str:
        return primary(self.codes)

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000


# ------------------------------------------------------------------------------------------------
class Pipeline:
    def __init__(self, svc: Services):
        self.svc = svc

    # ---- entry points -------------------------------------------------------------------------
    def auth_failure(self, err: AuthError, body: dict) -> GatewayError:
        """Authentication/spoofing failures still produce a sealed receipt."""
        ctx = Ctx(self.svc, identity=err.identity)
        request_id_var.set(ctx.request_id)
        ctx.code(err.reason)
        ctx.stage("identity", "denied", reason=err.reason.value, message=err.message)
        ctx.route = "denied"
        self._finish(ctx, status="denied", http_status=err.status, messages=body.get("messages") or [])
        return GatewayError(err.status, err.reason.value, err.message, self._headers(ctx))

    async def complete(self, ident: Identity, body: dict) -> tuple[dict, dict]:
        ctx = Ctx(self.svc, identity=ident)
        request_id_var.set(ctx.request_id)
        self.svc.bus.emit("request_started", ctx.request_id, receipt_id=ctx.receipt_id, department=ident.department_id,
                          tenant=ident.tenant_id, stream=False)
        try:
            messages, prep = await self._prepare(ctx, body)
            if ctx.cache and ctx.cache.hit:
                text = ctx.cache.entry["response"]
                return self._finish_ok(ctx, messages, text, body), self._headers(ctx)
            gen = await self._infer(ctx, prep["model_messages"], prep["params"])
            text = await self._route(ctx, prep, gen)
            text = await asyncio.to_thread(self._output_security, ctx, text, prep)
            return self._finish_ok(ctx, messages, text, body, prep), self._headers(ctx)
        except GatewayError as e:
            self._fail(ctx, e, body)
            e.headers = self._headers(ctx)
            raise
        except Exception as e:   # never lose a receipt: unexpected failures are sealed too
            log.exception("unhandled pipeline error")
            ge = GatewayError(500, "INTERNAL_ERROR", f"internal error: {type(e).__name__}")
            self._fail(ctx, ge, body)
            ge.headers = self._headers(ctx)
            raise ge

    async def stream(self, ident: Identity, body: dict) -> tuple[AsyncIterator[bytes], dict]:
        """Real token streaming. Headers carry the pre-generation decision; the final chunk
        carries the sealed decision (route/reason/receipt) in a separate `nanogate` field."""
        ctx = Ctx(self.svc, identity=ident, stream=True)
        request_id_var.set(ctx.request_id)
        self.svc.bus.emit("request_started", ctx.request_id, receipt_id=ctx.receipt_id, department=ident.department_id,
                          tenant=ident.tenant_id, stream=True)
        try:
            messages, prep = await self._prepare(ctx, body)
        except GatewayError as e:
            self._fail(ctx, e, body)
            e.headers = self._headers(ctx)
            raise
        headers = self._headers(ctx, provisional=True)
        created = int(time.time())
        cid = "chatcmpl-" + ctx.request_id

        def chunk(delta: dict, finish: str | None = None, extra: dict | None = None) -> bytes:
            d = {"id": cid, "object": "chat.completion.chunk", "created": created,
                 "model": (ctx.final_gen.model if ctx.final_gen else self.svc.local.model),
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if extra:
                d.update(extra)
            return f"data: {json.dumps(d)}\n\n".encode()

        async def gen_iter() -> AsyncIterator[bytes]:
            try:
                yield chunk({"role": "assistant", "content": ""})
                if ctx.cache and ctx.cache.hit:
                    text = ctx.cache.entry["response"]
                    yield chunk({"content": text})   # cached answer delivered as one chunk (no timed replay)
                    self._finish_ok(ctx, messages, text, body)
                else:
                    res = GenerationResult()
                    ctx.stage("inference", "started", tier="local", model=self.svc.local.model, streaming=True)
                    e0 = self.svc.telemetry.energy_mj()
                    held = ""
                    first = True
                    try:
                        async for delta in self.svc.local.stream(prep["model_messages"], prep["params"], res):
                            if first:
                                self.svc.bus.emit("token_stream_started", ctx.request_id, ttft_ms=round(res.ttft_ms or 0, 1))
                                first = False
                            held += delta
                            safe, held = self._stream_holdback(ctx, held, prep)
                            if safe:
                                yield chunk({"content": safe})
                    except ModelUnavailable as e:
                        raise GatewayError(503, Reason.MODEL_UNAVAILABLE.value, f"local model unavailable: {e}")
                    tail, _ = self._stream_holdback(ctx, held, prep, final=True)
                    if tail:
                        yield chunk({"content": tail})
                    e1 = self.svc.telemetry.energy_mj()
                    ctx.energy_j = (e1 - e0) / 1000 if e0 is not None and e1 is not None else None
                    self._record_gen(ctx, res)
                    ctx.final_gen = res
                    ctx.route = "local"
                    # Router runs post-hoc: tokens were already delivered, so escalation is not possible.
                    sc = self._score(ctx, prep, res)
                    if sc and sc.available and not sc.accept_local:
                        ctx.code(Reason.ROUTER_POST_HOC_STREAM)
                    elif sc and sc.available:
                        ctx.code(Reason.LOCAL_CONFIDENT)
                    ctx.stage("route", "selected", route="local", reason=ctx.reason, post_hoc=True)
                    text = res.text
                    full_out = await asyncio.to_thread(self.svc.dlp.scan, text)
                    ctx.dlp_out = full_out
                    ctx.stage("output_scan", "done", findings=full_out.counts, action=ctx.output_action or "allow",
                              note="streamed with regex/secret hold-back; NER result recorded post-hoc")
                    self._finish_ok(ctx, messages, text, body, prep, already_scanned=True)
                yield chunk({}, finish=(ctx.final_gen.finish_reason if ctx.final_gen and ctx.final_gen.finish_reason else "stop"),
                            extra={"usage": self._usage(ctx),
                                   "nanogate": {"request_id": ctx.request_id, "receipt_id": ctx.receipt_id,
                                                "route": ctx.route, "reason": ctx.reason, "reason_codes": ctx.codes}})
                yield b"data: [DONE]\n\n"
            except GatewayError as e:
                self._fail(ctx, e, body)
                yield f"data: {json.dumps(e.body())}\n\n".encode()
                yield b"data: [DONE]\n\n"
            except BaseException as e:   # client disconnect / cancellation
                if ctx.reservation:
                    self.svc.budget.release(ctx.reservation)
                    ctx.reservation = None
                if not isinstance(e, GeneratorExit):
                    log.warning("stream aborted: %s", type(e).__name__)
                raise

        return gen_iter(), headers

    # ---- stages -------------------------------------------------------------------------------
    async def _prepare(self, ctx: Ctx, body: dict) -> tuple[list[dict], dict]:
        svc, ident = self.svc, ctx.identity
        assert ident is not None
        pol = svc.policies.get(ident.policy_id)
        ctx.stage("identity", "ok", tenant=ident.tenant_id, department=ident.department_id, role=ident.role,
                  key=ident.key_id, policy=pol.version_tag)
        if not svc.rate.allow(ident.key_id, pol.rate_limit.rpm):
            ctx.code(Reason.RATE_LIMITED)
            ctx.stage("rate_limit", "denied", rpm=pol.rate_limit.rpm)
            raise GatewayError(429, Reason.RATE_LIMITED.value, f"rate limit {pol.rate_limit.rpm}/min exceeded")
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise GatewayError(400, "INVALID_REQUEST", "messages must be a non-empty list")
        messages = [{"role": str(m.get("role", "user")), "content": m.get("content") if isinstance(m.get("content"), str)
                     else _flatten_content(m.get("content"))} for m in messages]
        total_chars = sum(len(m["content"] or "") for m in messages)
        if total_chars > svc.settings.max_prompt_chars:
            ctx.code(Reason.INPUT_TOO_LARGE)
            ctx.stage("rate_limit", "denied", chars=total_chars, limit=svc.settings.max_prompt_chars)
            raise GatewayError(413, Reason.INPUT_TOO_LARGE.value, f"prompt of {total_chars} chars exceeds {svc.settings.max_prompt_chars}")
        ctx.stage("rate_limit", "ok", rpm=pol.rate_limit.rpm)
        query, sys_hash, ctx_fp = conversation_keys(messages)
        ctx.intent = rf.intent_of(query)
        ctx.prompt_hash = sha(json.dumps(messages, sort_keys=True))
        ctx.requested_route = MODEL_ALIASES.get(str(body.get("model", "")), "auto")

        # --- DLP (input) ---
        scan_text = "\n␞\n".join(m["content"] or "" for m in messages)
        dres = await asyncio.to_thread(svc.dlp.scan, scan_text)
        ctx.dlp_in = dres
        for t, n in dres.counts.items():
            M.DLP.labels(t, "input").inc(n)
        ctx.stage("dlp", "ok" if dres.available else "unavailable", data_class=dres.data_class if dres.available else "Unknown",
                  entities=dres.counts, layers=dres.layers_used, elapsed_ms=round(dres.elapsed_ms, 1),
                  reason=dres.unavailable_reason)

        # --- policy ---
        dec = svc.policies.evaluate(ident.policy_id, dres.data_class, dres.counts, dres.has_secret, dres.has_pii,
                                    dres.available, svc.remote.mode)
        ctx.decision = dec
        for c in dec.reason_codes:
            ctx.code(c)
        ctx.stage("policy", "denied" if dec.denied else "ok", policy_version=dec.policy_version, action=dec.action,
                  data_class=dec.data_class, allowed_routes=dec.allowed_routes, denied_routes=dec.denied_routes,
                  egress=dec.egress_permitted)
        self.svc.bus.emit("request_classified", ctx.request_id, data_class=dec.data_class, intent=ctx.intent,
                          entities=dres.counts)
        if dec.denied:
            status = 503 if Reason.DLP_UNAVAILABLE_FAIL_CLOSED.value in dec.reason_codes else 403
            if not ctx.codes:
                ctx.code(Reason.POLICY_BLOCK)
            raise GatewayError(status, primary(dec.reason_codes) or Reason.POLICY_BLOCK.value,
                               f"request blocked by policy {dec.policy_version}: {dec.egress_reason}")

        # --- requested route must be permitted (no escalation by model name) ---
        if ctx.requested_route in ("local_large", "remote") and ctx.requested_route not in dec.allowed_routes:
            ctx.code(Reason.ROUTE_ESCALATION_DENIED)
            ctx.stage("route", "denied", requested=ctx.requested_route, why=dec.denied_routes.get(ctx.requested_route))
            raise GatewayError(403, Reason.ROUTE_ESCALATION_DENIED.value,
                               f"route '{ctx.requested_route}' not permitted: {dec.denied_routes.get(ctx.requested_route)}")

        # --- transformation (redact / tokenize) ---
        tok_map: dict[str, str] = {}
        model_messages = messages
        if dec.transform in ("redact", "tokenize"):
            model_messages = []
            for m in messages:
                r = await asyncio.to_thread(svc.dlp.scan, m["content"] or "")
                if dec.transform == "tokenize":
                    txt, mp = LayeredDLP.tokenize(m["content"] or "", r.findings)
                    tok_map.update(mp)
                else:
                    txt = LayeredDLP.redact(m["content"] or "", r.findings)
                model_messages.append({**m, "content": txt})
            ctx.stage("transform", "applied", action=dec.transform, entities=dres.counts)

        # --- knowledge source (RAG) ---
        source_id = source_hash = None
        if "cisa_kev" in pol.knowledge_sources and needs_kev(query) and svc.kev.state and svc.cache is not None:
            ret = await asyncio.to_thread(svc.kev.retrieve, query)
            if ret.get("available"):
                ctx.retrieval = ret
                model_messages = rag_messages(model_messages, ret)
                source_id, source_hash = ret["source_id"], ret["source_version"]
                ctx.stage("retrieval", "ok", source=source_id, version=source_hash, records=[r["cveID"] for r in ret["records"]],
                          top_sim=round(ret["top_sim"], 4), exact_match=ret["exact_match"])

        # --- params ---
        max_req = pol.budget.max_tokens_per_request
        mt = body.get("max_completion_tokens") or body.get("max_tokens") or max_req
        params = {k: body.get(k) for k in ("temperature", "top_p", "stop", "seed", "response_format") if body.get(k) is not None}
        params["max_tokens"] = int(min(int(mt), max_req))
        ctx.params = {**params}

        # --- budget reservation (conservative maximum) ---
        est_prompt = max(1, total_chars // 3)
        est_tokens = est_prompt + params["max_tokens"]
        usd = 0.0
        if "remote" in dec.allowed_routes and svc.remote.mode == "live":
            rate = svc.pricing.for_model(svc.remote.model or "")
            usd = rate.cost(est_prompt, params["max_tokens"]) if rate else 0.0
        try:
            ctx.reservation = svc.budget.reserve(ctx.request_id, ident.tenant_id, ident.department_id, usd, est_tokens,
                                                 pol.budget.monthly_usd, pol.budget.monthly_tokens)
            self.svc.bus.emit("budget_reserved", ctx.request_id, usd=usd, tokens=est_tokens)
            ctx.stage("budget", "reserved", usd=round(usd, 6), tokens=est_tokens)
        except BudgetDenied as e:
            ctx.code(Reason.BUDGET_DENY)
            ctx.stage("budget", "denied", **e.detail)
            raise GatewayError(429, Reason.BUDGET_DENY.value, f"budget denied: {e.detail}")

        # --- verified cache ---
        ns = namespace_fields(ident.tenant_id, ident.department_id, dec.policy_version,
                              svc.settings.local_model_family, sys_hash, ctx_fp)
        ctx.ns = ns
        if dec.cache_eligible and svc.cache is not None and ctx.requested_route == "auto":
            lk = await asyncio.to_thread(svc.cache.lookup, query, ns)
            ctx.cache = lk
            if lk.decision not in (Reason.CACHE_MISS.value,):
                ctx.code(lk.decision)
            M.CACHE.labels(lk.decision).inc()
            ev = "cache_verified" if lk.hit else ("cache_candidate_found" if lk.candidates else "cache_miss")
            self.svc.bus.emit(ev, ctx.request_id, decision=lk.decision, similarity=lk.similarity)
            ctx.stage("cache", "hit" if lk.hit else "miss", decision=lk.decision, similarity=lk.similarity,
                      verifier=(lk.verifier or {}).get("score"), candidates=len(lk.candidates), evidence=lk.evidence,
                      namespace_id=lk.namespace_id)
            if lk.hit:
                ctx.route = "cache"
        else:
            why = "cache unavailable" if svc.cache is None else ("explicit route requested" if ctx.requested_route != "auto"
                                                                 else dec.denied_routes.get("cache", "not eligible"))
            ctx.code(Reason.CACHE_INELIGIBLE)
            ctx.stage("cache", "skipped", reason=why)
        return messages, {"model_messages": model_messages, "params": params, "query": query, "tok_map": tok_map,
                          "source_id": source_id, "source_hash": source_hash, "policy": pol}

    async def _infer(self, ctx: Ctx, messages: list[dict], params: dict, force: bool = False) -> GenerationResult | None:
        svc = self.svc
        if ctx.requested_route in ("local_large", "remote") and not force:
            return None
        if svc.local.queue_depth >= svc.settings.max_queue_depth:
            ctx.code(Reason.QUEUE_FULL)
            ctx.stage("inference", "rejected", queue_depth=svc.local.queue_depth)
            raise GatewayError(503, Reason.QUEUE_FULL.value, "local inference queue is full")
        ctx.stage("inference", "started", tier="local", model=svc.local.model, queue_depth=svc.local.queue_depth)
        svc.bus.emit("local_inference_started", ctx.request_id, model=svc.local.model)
        e0 = svc.telemetry.energy_mj()
        try:
            res = await svc.local.generate(messages, params)
        except ModelUnavailable as e:
            ctx.code(Reason.MODEL_UNAVAILABLE)
            ctx.stage("inference", "failed", error=str(e)[:200])
            raise GatewayError(503, Reason.MODEL_UNAVAILABLE.value, f"local model unavailable: {e}")
        e1 = svc.telemetry.energy_mj()
        ctx.energy_j = (e1 - e0) / 1000 if e0 is not None and e1 is not None else None
        self._record_gen(ctx, res)
        return res

    def _record_gen(self, ctx: Ctx, res: GenerationResult) -> None:
        ctx.gen = res
        M.TTFT.labels(res.tier).observe((res.ttft_ms or 0) / 1000)
        M.TOKENS.labels("prompt", res.tier).inc(res.prompt_tokens or 0)
        M.TOKENS.labels("completion", res.tier).inc(res.completion_tokens or 0)
        ctx.stage("inference", "completed", tier=res.tier, model=res.model, ttft_ms=_r(res.ttft_ms), total_ms=_r(res.total_ms),
                  queue_ms=_r(res.queue_ms), prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                  tokens_per_s=_r(res.tokens_per_s), finish_reason=res.finish_reason, energy_j=_r(ctx.energy_j))
        self.svc.bus.emit("local_inference_completed", ctx.request_id, tier=res.tier, ttft_ms=_r(res.ttft_ms),
                          total_ms=_r(res.total_ms), completion_tokens=res.completion_tokens)

    def _score(self, ctx: Ctx, prep: dict, res: GenerationResult):
        svc = self.svc
        ret = ctx.retrieval
        feats = rf.extract(prep["model_messages"], prep["params"], res,
                           retrieval={k: ret.get(k) for k in ("top_sim", "margin", "coverage", "verifier", "sources", "age_days", "mismatch")} if ret else None,
                           system={"queue_depth": float(svc.local.queue_depth),
                                   "mem_pressure": (svc.last_sample.get("mem_used_bytes") or 0) / svc.last_sample["mem_total_bytes"]
                                   if svc.last_sample.get("mem_total_bytes") else None},
                           data_class=ctx.decision.data_class if ctx.decision else None)
        th = prep["policy"].router.threshold
        sc = svc.router.score(feats, th)
        ctx.router = {**sc.public(), "features_missing": [k for k, v in feats.items() if v is None]}
        if not sc.available:
            ctx.code(Reason.ROUTER_UNAVAILABLE)
            ctx.stage("router", "unavailable", reason=sc.reason)
        else:
            ctx.stage("router", "scored", raw_score=_r(sc.raw_score, 4), p_error=_r(sc.p_error, 4), threshold=_r(sc.threshold, 4),
                      accept_local=sc.accept_local, factors=sc.factors[:3])
            self.svc.bus.emit("router_scored", ctx.request_id, p_error=sc.p_error, threshold=sc.threshold)
        return sc

    async def _route(self, ctx: Ctx, prep: dict, gen: GenerationResult | None) -> str:
        svc, dec = self.svc, ctx.decision
        assert dec is not None
        params, mm = prep["params"], prep["model_messages"]

        if gen is not None:
            sc = self._score(ctx, prep, gen)
            if not sc.available or sc.accept_local:
                ctx.route, ctx.final_gen = "local", gen
                if sc.available:
                    ctx.code(Reason.LOCAL_CONFIDENT)
                ctx.stage("route", "selected", route="local", reason=ctx.reason)
                svc.bus.emit("route_selected", ctx.request_id, route="local", reason=ctx.reason)
                return gen.text
        # Escalation (router rejected local answer) or explicit tier request.
        order = ["local_large", "remote"] if ctx.requested_route == "auto" else [ctx.requested_route]
        for tier in order:
            if tier not in dec.allowed_routes:
                continue
            if tier == "local_large":
                if not svc.local_large or svc.local_large.status.get("state") != "ready":
                    ctx.stage("route", "skipped", route="local_large", why="local-large tier not ready")
                    continue
                try:
                    ctx.stage("inference", "started", tier="local_large", model=svc.local_large.model)
                    e0 = svc.telemetry.energy_mj()
                    res = await svc.local_large.generate(mm, params)
                    e1 = svc.telemetry.energy_mj()
                    if e0 is not None and e1 is not None:
                        ctx.energy_j = (ctx.energy_j or 0) + (e1 - e0) / 1000
                    self._record_gen(ctx, res)
                    ctx.route, ctx.final_gen = "local_large", res
                    ctx.code(Reason.LOCAL_LARGE_SELECTED)
                    ctx.stage("route", "selected", route="local_large", reason=Reason.LOCAL_LARGE_SELECTED.value)
                    svc.bus.emit("route_selected", ctx.request_id, route="local_large", reason=ctx.reason)
                    return res.text
                except ModelUnavailable as e:
                    ctx.stage("route", "failed", route="local_large", error=str(e)[:160])
                    continue
            if tier == "remote":
                if svc.remote.mode == "disabled":
                    ctx.code(Reason.REMOTE_DISABLED)
                    ctx.stage("route", "skipped", route="remote", why="REMOTE_MODE=disabled")
                    continue
                try:
                    ticket = svc.egress.mint(ctx.request_id, dec)
                    ctx.stage("egress", "permitted", destination=svc.remote.endpoint, mode=svc.remote.mode,
                              data_class=dec.data_class)
                    res = await svc.remote.generate(ticket, mm, params)
                    res.model = svc.remote.model_name
                    ctx.gen = ctx.gen or res
                    ctx.route, ctx.final_gen = ("remote_simulated" if svc.remote.mode == "mock" else "remote"), res
                    ctx.code(Reason.REMOTE_ALLOWED)
                    M.TOKENS.labels("prompt", res.tier).inc(res.prompt_tokens or 0)
                    M.TOKENS.labels("completion", res.tier).inc(res.completion_tokens or 0)
                    ctx.stage("route", "selected", route=ctx.route, reason=Reason.REMOTE_ALLOWED.value,
                              label=svc.remote.label, egress=svc.egress.for_request(ctx.request_id))
                    svc.bus.emit("route_selected", ctx.request_id, route=ctx.route, reason=ctx.reason)
                    return res.text
                except EgressDenied as e:
                    ctx.stage("egress", "blocked", why=str(e))
                except ConnectorUnavailable as e:
                    ctx.code(Reason.CONNECTOR_UNAVAILABLE)
                    ctx.code(Reason.REMOTE_UNAVAILABLE)
                    ctx.stage("route", "failed", route="remote", error=str(e)[:200], label=svc.remote.label)
                    svc.bus.emit("service_state_changed", component="remote", state="unavailable", reason=str(e)[:120])
        # No escalation succeeded: local fallback or abstain.
        if gen is None:
            # explicit tier request failed; run local so the request still completes
            ctx.fallback_state = "explicit_tier_failed_local_fallback"
            gen = await self._infer(ctx, mm, params, force=True)
            self._score(ctx, prep, gen)
        pol = prep["policy"]
        if ctx.requested_route == "auto":
            ctx.code(Reason.ROUTER_ABSTAIN)
        if pol.router.abstain_action == "refuse" and ctx.requested_route == "auto":
            ctx.fallback_state = "abstained_refused"
            ctx.stage("route", "abstained", why="router rejected local answer and no permitted tier succeeded")
            raise GatewayError(422, Reason.ROUTER_ABSTAIN.value, "router abstained: no tier met the risk threshold")
        ctx.fallback_state = ctx.fallback_state or "local_answer_flagged"
        ctx.route, ctx.final_gen = "local", gen
        ctx.stage("route", "fallback", route="local", reason=ctx.reason, fallback=ctx.fallback_state)
        svc.bus.emit("route_selected", ctx.request_id, route="local", reason=ctx.reason, fallback=ctx.fallback_state)
        return gen.text

    def _output_security(self, ctx: Ctx, text: str, prep: dict) -> str:
        pol = prep["policy"]
        if not pol.dlp.output_scan:
            ctx.stage("output_scan", "skipped")
            return LayeredDLP.detokenize(text, prep["tok_map"])
        res = self.svc.dlp.scan(text)
        ctx.dlp_out = res
        for t, n in res.counts.items():
            M.DLP.labels(t, "output").inc(n)
        if not res.available:
            ctx.code(Reason.OUTPUT_BLOCKED)
            ctx.stage("output_scan", "failed", reason=res.unavailable_reason)
            raise GatewayError(503, Reason.OUTPUT_BLOCKED.value, "output scanner unavailable (fail closed)")
        in_fps = {f.fingerprint for f in (ctx.dlp_in.findings if ctx.dlp_in else [])}
        secrets = [f for f in res.findings if f.type in SECRET_TYPES]
        mode = pol.dlp.output_pii_action
        if mode == "allow":
            flagged = []
        elif mode == "allow_if_in_input":
            flagged = [f for f in res.findings if f.type != "PERSON" and f.fingerprint not in in_fps]
        else:
            flagged = [f for f in res.findings if f.type != "PERSON"]
        flagged = list({id(f): f for f in flagged + secrets}.values())
        action = "allow"
        if flagged:
            if mode == "block" or (secrets and any(f.fingerprint not in in_fps for f in secrets) and mode == "block"):
                ctx.code(Reason.OUTPUT_BLOCKED)
                ctx.output_action = "block"
                ctx.stage("output_scan", "blocked", findings=res.counts)
                raise GatewayError(451, Reason.OUTPUT_BLOCKED.value, "model output blocked by output DLP policy")
            text = LayeredDLP.redact(text, flagged)
            ctx.code(Reason.OUTPUT_REDACTED)
            action = "redact"
        ctx.output_action = action
        text = LayeredDLP.detokenize(text, prep["tok_map"])
        ctx.stage("output_scan", "done", findings=res.counts, action=action, redacted=len(flagged))
        self.svc.bus.emit("output_scanned", ctx.request_id, action=action, findings=res.counts)
        return text

    def _stream_holdback(self, ctx: Ctx, held: str, prep: dict, final: bool = False) -> tuple[str, str]:
        """Scan the not-yet-emitted buffer with fast recognizers; emit all but a 48-char tail."""
        pol = prep["policy"]
        if not pol.dlp.output_scan:
            return (held, "") if final else (held[:-48] if len(held) > 48 else "", held[-48:] if len(held) > 48 else held)
        res = self.svc.dlp_fast.scan(held)
        in_fps = {f.fingerprint for f in (ctx.dlp_in.findings if ctx.dlp_in else [])}
        mode = pol.dlp.output_pii_action
        flagged = [f for f in res.findings if f.type in SECRET_TYPES or
                   (mode in ("redact", "block") or (mode == "allow_if_in_input" and f.fingerprint not in in_fps))]
        if mode == "allow":
            flagged = [f for f in res.findings if f.type in SECRET_TYPES]
        if flagged:
            held = LayeredDLP.redact(held, flagged)
            ctx.code(Reason.OUTPUT_REDACTED)
            ctx.output_action = "redact"
        held = LayeredDLP.detokenize(held, prep["tok_map"])
        if final or len(held) <= 48:
            return (held, "") if final else ("", held)
        return held[:-48], held[-48:]

    # ---- completion ---------------------------------------------------------------------------
    def _usage(self, ctx: Ctx) -> dict:
        g = ctx.final_gen
        if ctx.cache and ctx.cache.hit:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        p = sum(x for x in [ctx.gen.prompt_tokens if ctx.gen else 0,
                            g.prompt_tokens if g is not None and g is not ctx.gen else 0] if x)
        c = sum(x for x in [ctx.gen.completion_tokens if ctx.gen else 0,
                            g.completion_tokens if g is not None and g is not ctx.gen else 0] if x)
        return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}

    def _economics(self, ctx: Ctx) -> dict:
        svc = self.svc
        ref = svc.pricing.reference
        tiers = []
        for g in {id(x): x for x in [ctx.gen, ctx.final_gen] if x is not None}.values():
            name = g.model.replace(" (simulated remote)", "")
            rate = svc.pricing.for_model(name)
            if g.tier == "remote_simulated":
                rate = svc.pricing.for_model(name) or rate
            pt, ct = g.prompt_tokens or 0, g.completion_tokens or 0
            cost = rate.cost(pt, ct) if (rate and g.tier != "remote_simulated") else 0.0
            tiers.append({"tier": g.tier, "model": g.model, "prompt_tokens": pt, "completion_tokens": ct,
                          "rate": {"provider": rate.provider, "model": rate.model, "input_per_mtok": rate.input_per_mtok,
                                   "output_per_mtok": rate.output_per_mtok, "effective_date": rate.effective_date,
                                   "source": rate.source} if rate else None,
                          "cost_usd": round(cost, 8), "rate_missing": rate is None})
        actual = sum(t["cost_usd"] for t in tiers)
        avoided_tokens = 0
        if ctx.cache and ctx.cache.hit:
            e = ctx.cache.entry
            avoided_tokens = int((e.get("prompt_tokens") or 0) + (e.get("completion_tokens") or 0))
            cf = ref.cost(int(e.get("prompt_tokens") or 0), int(e.get("completion_tokens") or 0))
        else:
            fg = ctx.final_gen
            cf = ref.cost(fg.prompt_tokens or 0, fg.completion_tokens or 0) if fg else 0.0
        return {"pricing_version": svc.pricing.version, "pricing_sha256": svc.pricing.sha256[:16],
                "tiers": tiers, "actual_cost_usd": round(actual, 8),
                "counterfactual": {"provider": ref.provider, "model": ref.model, "input_per_mtok": ref.input_per_mtok,
                                   "output_per_mtok": ref.output_per_mtok, "effective_date": ref.effective_date,
                                   "source": ref.source, "cost_usd": round(cf, 8),
                                   "basis": "same measured tokens at reference hosted-API rate"},
                "avoided_cost_usd": round(max(0.0, cf - actual), 8), "tokens_avoided_by_cache": avoided_tokens,
                "energy_j": _r(ctx.energy_j, 3),
                "energy_note": "GPU energy delta over the request window (NVML); shared with concurrent requests"}

    def _finish_ok(self, ctx: Ctx, messages: list[dict], text: str, body: dict, prep: dict | None = None,
                   already_scanned: bool = False) -> dict:
        svc = self.svc
        if ctx.cache and ctx.cache.hit:
            ctx.stage("output_scan", "skipped", reason="cached answer passed output scan when stored")
            if not ctx.final_gen:
                ctx.final_gen = None
        econ = self._economics(ctx)
        ctx.cost = econ
        usage = self._usage(ctx)
        if ctx.reservation:
            svc.budget.settle(ctx.reservation, econ["actual_cost_usd"], usage["total_tokens"])
            svc.bus.emit("budget_settled", ctx.request_id, usd=econ["actual_cost_usd"], tokens=usage["total_tokens"])
            ctx.stage("settle", "ok", usd=econ["actual_cost_usd"], tokens=usage["total_tokens"],
                      released_tokens=ctx.reservation.tokens - usage["total_tokens"])
            ctx.reservation = None
        ctx.answer_hash = sha(text)
        # cache write: only verified-safe, router-accepted, untransformed answers
        if (prep and svc.cache is not None and ctx.decision and ctx.decision.cache_eligible and not (ctx.cache and ctx.cache.hit)
                and ctx.requested_route == "auto" and ctx.output_action in (None, "allow") and not prep["tok_map"]
                and Reason.ROUTER_ABSTAIN.value not in ctx.codes and Reason.ROUTER_POST_HOC_STREAM.value not in ctx.codes
                and ctx.route in ("local", "local_large") and ctx.final_gen and ctx.final_gen.finish_reason == "stop"
                and Reason.ROUTER_UNAVAILABLE.value not in ctx.codes):
            pol = prep["policy"]
            ctx.cache_write = svc.cache.store(prep["query"], text, ctx.ns, ctx.decision.data_class, pol.cache.ttl_s,
                                              ctx.final_gen.model, ctx.final_gen.prompt_tokens, ctx.final_gen.completion_tokens,
                                              prep["source_id"], prep["source_hash"], ctx.request_id)
            ctx.stage("cache_write", "stored", cache_id=ctx.cache_write)
        self._finish(ctx, status="ok", http_status=200, messages=messages)
        g = ctx.final_gen
        return {"id": "chatcmpl-" + ctx.request_id, "object": "chat.completion", "created": int(ctx.wall0),
                "model": g.model if g else (ctx.cache.entry.get("model_name") if ctx.cache and ctx.cache.hit else svc.local.model),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "finish_reason": (g.finish_reason if g and g.finish_reason else "stop"), "logprobs": None}],
                "usage": usage, "system_fingerprint": f"nanogate-{svc.commit or 'dev'}"}

    def _fail(self, ctx: Ctx, e: GatewayError, body: dict) -> None:
        if ctx.reservation:
            self.svc.budget.release(ctx.reservation)
            ctx.stage("settle", "released", tokens=ctx.reservation.tokens)
            ctx.reservation = None
        if e.reason not in ctx.codes:
            ctx.code(e.reason)
        ctx.error = e.message
        if ctx.route in ("none",):
            ctx.route = "denied" if e.status < 500 else "error"
        M.POLICY_BLOCKS.labels(ctx.reason).inc()
        self._finish(ctx, status="denied" if e.status < 500 else "error", http_status=e.status,
                     messages=body.get("messages") or [])

    def _finish(self, ctx: Ctx, status: str, http_status: int, messages: list[dict]) -> None:
        svc, ident = self.svc, ctx.identity
        # The receipt stage is part of the sealed body; its hash is emitted right after sealing.
        ctx.timeline.append({"stage": "receipt", "status": "sealed", "t_ms": round(ctx.elapsed_ms, 2),
                             "detail": {"algorithm": "sha256-chain+hmac-sha256"}})
        body = self._receipt_body(ctx, status, http_status)
        tenant = ident.tenant_id if ident else "unauthenticated"
        dept = ident.department_id if ident else "unknown"
        seal = svc.receipts.seal(ctx.receipt_id, ctx.request_id, tenant, dept, ctx.reason, body)
        svc.bus.emit("stage:receipt", ctx.request_id, status="sealed", receipt_id=ctx.receipt_id, detail={"hash": seal["hash"][:16]})
        usage = self._usage(ctx) if status == "ok" else {"prompt_tokens": (ctx.gen.prompt_tokens if ctx.gen else 0) or 0,
                                                        "completion_tokens": (ctx.gen.completion_tokens if ctx.gen else 0) or 0}
        econ = ctx.cost or {}
        pol = ident and svc.policies._current.get(ident.policy_id)
        store_raw = svc.settings.store_raw_prompts and pol and pol.retention.store_raw_prompts
        svc.db.execute(
            "INSERT INTO requests(request_id, receipt_id, ts, tenant_id, department_id, key_id, intent, data_class, route, reason,"
            " reason_codes, status, http_status, cache_status, p_error, latency_ms, ttft_ms, prompt_tokens, completion_tokens,"
            " tokens_avoided, cost_usd, counterfactual_usd, energy_j, remote_bytes, policy_version, model, stream, prompt_sha256,"
            " raw_prompt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ctx.request_id, ctx.receipt_id, ctx.wall0, tenant, dept, ident.key_id if ident else None, ctx.intent,
             ctx.decision.data_class if ctx.decision else (ctx.dlp_in.data_class if ctx.dlp_in else None), ctx.route, ctx.reason,
             json.dumps(ctx.codes), status, http_status, ctx.cache.decision if ctx.cache else None,
             (ctx.router or {}).get("p_error"), ctx.elapsed_ms, ctx.gen.ttft_ms if ctx.gen else None,
             usage.get("prompt_tokens"), usage.get("completion_tokens"), econ.get("tokens_avoided_by_cache", 0),
             econ.get("actual_cost_usd"), (econ.get("counterfactual") or {}).get("cost_usd"), ctx.energy_j,
             svc.egress.for_request(ctx.request_id)["bytes_out"], ctx.decision.policy_version if ctx.decision else None,
             ctx.final_gen.model if ctx.final_gen else None, int(ctx.stream), ctx.prompt_hash,
             json.dumps(messages) if store_raw else None))
        M.REQUESTS.labels(dept, ctx.route, ctx.reason, status).inc()
        M.LATENCY.labels(ctx.route).observe(ctx.elapsed_ms / 1000)
        if econ.get("actual_cost_usd"):
            M.COST.labels(ctx.route).inc(econ["actual_cost_usd"])
        svc.bus.emit("receipt_sealed", ctx.request_id, receipt_id=ctx.receipt_id, route=ctx.route, reason=ctx.reason,
                     status=status, department=dept, tenant=tenant, intent=ctx.intent,
                     data_class=ctx.decision.data_class if ctx.decision else None,
                     p_error=(ctx.router or {}).get("p_error"), latency_ms=round(ctx.elapsed_ms, 1),
                     tokens=(usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0),
                     cost_usd=econ.get("actual_cost_usd"), avoided_usd=econ.get("avoided_cost_usd"))
        jlog(log, logging.INFO, "request finished", request_id=ctx.request_id, receipt_id=ctx.receipt_id, route=ctx.route,
             reason=ctx.reason, status=status, http_status=http_status, latency_ms=round(ctx.elapsed_ms, 1),
             tenant=tenant, department=dept)

    def _receipt_body(self, ctx: Ctx, status: str, http_status: int) -> dict:
        svc, ident = self.svc, ctx.identity
        s = svc.last_sample
        dres, dec, lk = ctx.dlp_in, ctx.decision, ctx.cache
        g = ctx.final_gen or ctx.gen
        eg = svc.egress.for_request(ctx.request_id)
        return {
            "schema": "nanogate.receipt/1",
            "status": status, "http_status": http_status, "error": ctx.error,
            "identity": {"request_id": ctx.request_id, "receipt_id": ctx.receipt_id, "timestamp": ctx.wall0,
                         "tenant_hash": sha(ident.tenant_id)[:16] if ident else None,
                         "tenant_id": ident.tenant_id if ident else None,
                         "department": ident.department_id if ident else None, "role": ident.role if ident else None,
                         "key_id": ident.key_id if ident else None, "key_hash": ident.key_hash[:16] if ident else None,
                         "policy_version": dec.policy_version if dec else None},
            "classification": {"data_class": dec.data_class if dec else (dres.data_class if dres and dres.available else None),
                               "intent": ctx.intent, "entity_counts": dres.counts if dres else {},
                               "entities": [f.public() for f in dres.findings] if dres else [],
                               "secret_detected": dres.has_secret if dres else None,
                               "dlp_available": dres.available if dres else None, "dlp_layers": dres.layers_used if dres else [],
                               "transformation": dec.transform if dec else None, "prompt_sha256": ctx.prompt_hash,
                               "raw_prompt_stored": False},
            "policy": dec.as_dict() if dec else None,
            "cache": ({**lk.public(), "tokens_avoided": (ctx.cost or {}).get("tokens_avoided_by_cache", 0),
                       "written_cache_id": ctx.cache_write} if lk else
                      {"decision": Reason.CACHE_INELIGIBLE.value if Reason.CACHE_INELIGIBLE.value in ctx.codes else None,
                       "written_cache_id": ctx.cache_write}),
            "retrieval": ({"source_id": ctx.retrieval["source_id"], "source_version": ctx.retrieval["source_version"],
                           "records": [r["cveID"] for r in ctx.retrieval["records"]], "top_sim": ctx.retrieval["top_sim"],
                           "coverage": ctx.retrieval["coverage"], "exact_match": ctx.retrieval["exact_match"]}
                          if ctx.retrieval else None),
            "model": ({"tier": g.tier, "model": g.model, "revision": (svc.local.revision if g.tier == "local" else
                                                                      svc.local_large.revision if g.tier == "local_large" and svc.local_large else None),
                       "runtime": svc.local.details.get("runtime"), "runtime_version": svc.local.details.get("runtime_version"),
                       "quantization": (svc.local.details if g.tier == "local" else (svc.local_large.details if svc.local_large else {})).get("quantization"),
                       "generation_params": ctx.params, "prompt_tokens": g.prompt_tokens,
                       "completion_tokens": g.completion_tokens, "ttft_ms": _r(g.ttft_ms), "total_ms": _r(g.total_ms),
                       "queue_ms": _r(g.queue_ms), "tokens_per_s": _r(g.tokens_per_s), "finish_reason": g.finish_reason,
                       "first_pass": ({"tier": ctx.gen.tier, "model": ctx.gen.model, "prompt_tokens": ctx.gen.prompt_tokens,
                                       "completion_tokens": ctx.gen.completion_tokens, "total_ms": _r(ctx.gen.total_ms)}
                                      if ctx.gen is not None and ctx.gen is not g else None),
                       "answer_sha256": ctx.answer_hash} if g else None),
            "router": ({**ctx.router, "schema_version": rf.SCHEMA_VERSION,
                        "dataset_hash": svc.router.meta.get("dataset_sha256"),
                        "calibration": svc.router.meta.get("calibration_method")} if ctx.router else None),
            "decision": {"allowed_routes": dec.allowed_routes if dec else [], "denied_routes": dec.denied_routes if dec else {},
                         "requested_route": ctx.requested_route, "chosen_route": ctx.route, "reason_code": ctx.reason,
                         "reason_codes": ctx.codes, "fallback_state": ctx.fallback_state,
                         "connector": {"mode": svc.remote.mode, "label": svc.remote.label,
                                       "health": svc.remote.health_state.get("state")},
                         "egress": {"remote_requests": eg["requests"], "remote_bytes_out": eg["bytes_out"],
                                    "remote_bytes_in": eg["bytes_in"], "permitted": dec.egress_permitted if dec else False,
                                    "instrumentation": "application-layer egress guard"},
                         "output_action": ctx.output_action,
                         "output_entities": ctx.dlp_out.counts if ctx.dlp_out else {}},
            "economics": ctx.cost or None,
            "device": {"telemetry_mode": svc.telemetry.mode, "sampled_at": s.get("ts"),
                       "gpu_util_pct": s.get("gpu_util"), "gpu_power_w": s.get("gpu_power_w"), "gpu_temp_c": s.get("gpu_temp_c"),
                       "mem_used_bytes": s.get("mem_used_bytes"), "mem_total_bytes": s.get("mem_total_bytes"),
                       "tokens_per_s": _r(g.tokens_per_s) if g else None, "model_health": svc.local.status.get("state"),
                       "queue_depth": svc.local.queue_depth},
            "timeline": ctx.timeline,
            "integrity": {"git_commit": svc.commit, "router_version": svc.router.meta.get("version"),
                          "router_run_id": svc.router.meta.get("run_id"), "latency_ms": round(ctx.elapsed_ms, 2)},
        }

    def _headers(self, ctx: Ctx, provisional: bool = False) -> dict:
        dec = ctx.decision
        h = {"x-nanogate-request-id": ctx.request_id, "x-nanogate-receipt-id": ctx.receipt_id,
             "x-nanogate-route": ctx.route if not provisional else ("cache" if ctx.cache and ctx.cache.hit else "local"),
             "x-nanogate-reason": ctx.reason or "",
             "x-nanogate-reason-codes": ",".join(ctx.codes),
             "x-nanogate-policy-version": dec.policy_version if dec else "",
             "x-nanogate-data-class": dec.data_class if dec else "",
             "x-nanogate-estimated-cost-usd": f"{(ctx.cost or {}).get('actual_cost_usd', 0.0):.8f}"}
        if provisional:
            h["x-nanogate-decision"] = "provisional; final decision in last stream chunk and receipt"
        return h


def _r(v, n: int = 2):
    return None if v is None else round(float(v), n)


def _flatten_content(c: Any) -> str:
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return "" if c is None else str(c)

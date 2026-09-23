"""Remote connector behind an application-layer egress guard.

Modes (REMOTE_MODE):
  disabled     default. The connector refuses every call.
  mock         "Simulated larger tier": served by the LOCAL large model, labelled as simulated in
               every receipt/UI surface. Zero bytes leave the device.
  live         Real OpenAI-compatible provider (REMOTE_BASE_URL / REMOTE_API_KEY / REMOTE_MODEL).
  outage-test  Real adapter pointed at an endpoint that refuses connections, to exercise genuine
               connector failure handling (no hang, local fallback, receipts).

EgressGuard: every remote call needs an EgressTicket, which the pipeline can only mint from a
PolicyDecision with egress_permitted=True. Attempts without a ticket are refused and counted.
This is application-layer instrumentation; it is not OS-level network isolation.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .db import Database
from .inference import GenerationResult, LocalModelAdapter, ModelUnavailable


class ConnectorUnavailable(Exception):
    pass


class EgressDenied(Exception):
    pass


@dataclass(frozen=True)
class EgressTicket:
    request_id: str
    data_class: str
    policy_version: str


class EgressGuard:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self.remote_requests = 0
        self.remote_bytes_out = 0
        self.remote_bytes_in = 0
        self.blocked_attempts = 0
        self.per_request: dict[str, dict[str, int]] = {}

    def mint(self, request_id: str, decision) -> EgressTicket:
        if not decision.egress_permitted:
            with self._lock:
                self.blocked_attempts += 1
            self._log(request_id, "remote", False, 0, 0, "blocked: policy")
            raise EgressDenied(decision.egress_reason)
        return EgressTicket(request_id, decision.data_class, decision.policy_version)

    def record(self, ticket: EgressTicket, destination: str, bytes_out: int, bytes_in: int, outcome: str) -> None:
        with self._lock:
            self.remote_requests += 1
            self.remote_bytes_out += bytes_out
            self.remote_bytes_in += bytes_in
            pr = self.per_request.setdefault(ticket.request_id, {"requests": 0, "bytes_out": 0, "bytes_in": 0})
            pr["requests"] += 1
            pr["bytes_out"] += bytes_out
            pr["bytes_in"] += bytes_in
        self._log(ticket.request_id, destination, True, bytes_out, bytes_in, outcome)

    def for_request(self, request_id: str) -> dict[str, int]:
        return dict(self.per_request.get(request_id, {"requests": 0, "bytes_out": 0, "bytes_in": 0}))

    def _log(self, rid, dest, allowed, bo, bi, outcome):
        try:
            self.db.execute("INSERT INTO egress_log(ts, request_id, destination, allowed, bytes_out, bytes_in, outcome)"
                            " VALUES (?,?,?,?,?,?,?)", (time.time(), rid, dest, int(allowed), bo, bi, outcome))
        except Exception:
            pass

    def summary(self) -> dict:
        return {"remote_requests": self.remote_requests, "remote_bytes_out": self.remote_bytes_out,
                "remote_bytes_in": self.remote_bytes_in, "blocked_attempts": self.blocked_attempts,
                "instrumentation": "application-layer (all remote traffic must pass EgressGuard); not OS-level isolation"}


class RemoteConnector:
    def __init__(self, mode: str, guard: EgressGuard, base_url: str | None, api_key: str | None, model: str | None,
                 provider: str | None, timeout_s: float, simulated_backend: LocalModelAdapter | None):
        self.guard = guard
        self.base_url, self.api_key, self.model, self.provider = base_url, api_key, model, provider
        self.timeout_s = timeout_s
        self.simulated_backend = simulated_backend
        self.mode = "disabled"
        self.health_state: dict[str, Any] = {}
        self.set_mode(mode)

    def set_mode(self, mode: str) -> None:
        if mode not in ("disabled", "mock", "live", "outage-test"):
            raise ValueError(f"unknown REMOTE_MODE {mode}")
        if mode == "live" and not (self.base_url and self.api_key and self.model):
            self.health_state = {"state": "unavailable", "reason": "REMOTE_MODE=live but REMOTE_BASE_URL/API_KEY/MODEL not configured"}
        self.mode = mode

    @property
    def label(self) -> str:
        return {"disabled": "Remote disabled", "mock": "Simulated larger tier", "live": f"Live remote ({self.provider})",
                "outage-test": "Outage test adapter"}[self.mode]

    @property
    def endpoint(self) -> str:
        if self.mode == "outage-test":
            return "http://127.0.0.1:9/v1"   # discard port: connection refused
        if self.mode == "mock":
            return "local://" + (self.simulated_backend.model if self.simulated_backend else "none")
        return self.base_url or ""

    @property
    def model_name(self) -> str:
        if self.mode == "mock":
            return (self.simulated_backend.model if self.simulated_backend else "unavailable") + " (simulated remote)"
        return self.model or ""

    async def health(self) -> dict:
        t0 = time.perf_counter()
        if self.mode == "disabled":
            self.health_state = {"state": "disabled", "reason": "REMOTE_MODE=disabled"}
        elif self.mode == "mock":
            st = await self.simulated_backend.health() if self.simulated_backend else {"state": "unavailable"}
            self.health_state = {"state": "healthy" if st.get("state") == "ready" else "unavailable",
                                 "reason": None if st.get("state") == "ready" else st.get("reason"), "simulated": True}
        else:
            if self.mode == "live" and not (self.base_url and self.api_key and self.model):
                self.health_state = {"state": "unavailable", "reason": "live remote not configured"}
                return self.health_state
            try:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    r = await c.get(f"{self.endpoint}/models", headers={"Authorization": f"Bearer {self.api_key or ''}"})
                    r.raise_for_status()
                self.health_state = {"state": "healthy", "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
            except Exception as e:
                self.health_state = {"state": "unavailable", "reason": f"{type(e).__name__}: {e}"[:200]}
        self.health_state["mode"] = self.mode
        self.health_state["label"] = self.label
        self.health_state["checked_at"] = time.time()
        return self.health_state

    async def generate(self, ticket: EgressTicket, messages: list[dict], params: dict) -> GenerationResult:
        if not isinstance(ticket, EgressTicket):
            self.guard.blocked_attempts += 1
            raise EgressDenied("no egress ticket")
        if self.mode == "disabled":
            raise ConnectorUnavailable("remote connector disabled")
        if self.mode == "mock":
            if not self.simulated_backend:
                raise ConnectorUnavailable("no simulated backend")
            try:
                res = await self.simulated_backend.generate(messages, params, logprobs=False)
            except ModelUnavailable as e:
                raise ConnectorUnavailable(str(e)) from e
            res.tier = "remote_simulated"
            self.guard.record(ticket, "simulated:local", 0, 0, "simulated")
            return res
        payload = {"model": self.model or "unknown", "messages": messages, "stream": False}
        for k in ("temperature", "top_p", "stop", "max_tokens"):
            if params.get(k) is not None:
                payload[k] = params[k]
        body = json.dumps(payload).encode()
        res = GenerationResult(model=self.model or "", tier="remote")
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=3.0)) as c:
                r = await c.post(f"{self.endpoint}/chat/completions", content=body,
                                 headers={"Authorization": f"Bearer {self.api_key or ''}", "Content-Type": "application/json"})
            self.guard.record(ticket, self.endpoint, len(body), len(r.content), f"http {r.status_code}")
            r.raise_for_status()
            d = r.json()
            res.text = d["choices"][0]["message"]["content"] or ""
            res.finish_reason = d["choices"][0].get("finish_reason")
            u = d.get("usage") or {}
            res.prompt_tokens, res.completion_tokens = u.get("prompt_tokens"), u.get("completion_tokens")
        except (httpx.HTTPError, KeyError, ValueError) as e:
            if not isinstance(e, httpx.HTTPStatusError):
                self.guard.record(ticket, self.endpoint, 0, 0, f"failed: {type(e).__name__}")
            raise ConnectorUnavailable(f"{type(e).__name__}: {e}"[:200]) from e
        finally:
            res.total_ms = (time.perf_counter() - t0) * 1000
            res.ttft_ms = res.total_ms
        return res

"""Offline verification: prove the core product works with no outbound network.

Isolation modes (reported honestly in the result):
  os_disconnected       the host had no default route when the test ran (e.g. cable unplugged)
  process_socket_guard  the host was online, so every non-loopback socket connect/DNS lookup made
                        by the gateway process was blocked for the duration of the test
The test issues a real request through the full pipeline (DLP, policy, cache, local inference,
router, receipt) and records any outbound attempts that the guard blocked.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from contextlib import contextmanager
from typing import Any

_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "ip6-localhost"}


def _is_loopback(host: Any) -> bool:
    if host is None:
        return True
    h = str(host)
    if h in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(h.split("%")[0]).is_loopback
    except ValueError:
        return False


@contextmanager
def socket_guard(blocked: list[dict]):
    orig_connect, orig_connect_ex = socket.socket.connect, socket.socket.connect_ex
    orig_gai = socket.getaddrinfo
    lock = threading.Lock()

    def record(kind: str, target: Any):
        with lock:
            blocked.append({"kind": kind, "target": str(target)[:120], "ts": time.time()})

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            record("connect", address)
            raise OSError(101, "Network is unreachable (NanoGate offline verification guard)")
        return orig_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            record("connect_ex", address)
            return 101
        return orig_connect_ex(self, address)

    def getaddrinfo(host, *a, **k):
        if not _is_loopback(host):
            record("dns", host)
            raise socket.gaierror(socket.EAI_NONAME, "DNS blocked (NanoGate offline verification guard)")
        return orig_gai(host, *a, **k)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = orig_connect, orig_connect_ex, orig_gai


def dependency_risks(svc) -> list[dict]:
    from urllib.parse import urlparse
    risks = []
    host = urlparse(svc.local.base_url).hostname
    risks.append({"check": "local model endpoint is loopback", "ok": _is_loopback(host), "detail": svc.local.base_url})
    risks.append({"check": "HF hub offline mode (embeddings/verifier load from disk)",
                  "ok": os.environ.get("HF_HUB_OFFLINE") == "1", "detail": os.environ.get("HF_HOME")})
    risks.append({"check": "remote connector not required", "ok": True,
                  "detail": f"REMOTE_MODE={svc.remote.mode}; core path never depends on it"})
    risks.append({"check": "public data served from local snapshot", "ok": svc.kev.state is not None,
                  "detail": svc.kev.state.version if svc.kev.state else svc.kev.error})
    return risks


def has_default_route() -> bool:
    try:
        with open("/proc/net/route") as f:
            return any(l.split()[1] == "00000000" for l in f.readlines()[1:] if len(l.split()) > 1)
    except Exception:
        return True


async def run_offline_verification(app) -> dict:
    svc = app.state.svc
    pipe = app.state.pipeline
    started = time.time()
    online = has_default_route()
    mode = "process_socket_guard" if online else "os_disconnected"
    blocked: list[dict] = []
    checks: dict[str, Any] = {}
    result: dict[str, Any] = {"executed": True, "executed_at": started, "isolation_mode": mode,
                              "os_default_route_present": online, "dependency_risks": dependency_risks(svc)}
    ident = app.state.playground_identity("acme", "it")
    if ident is None:
        result.update(passed=False, error="no playground key for acme/it (run scripts/bootstrap_keys.py)")
        return result
    q = f"Offline check {int(started)}: in one sentence, what does a VPN client do?"
    with socket_guard(blocked):
        try:
            health = await svc.local.health()
            checks["local_model_ready"] = health.get("state") == "ready"
            resp, headers = await pipe.complete(ident, {"model": "nanogate-auto", "messages": [{"role": "user", "content": q}],
                                                        "max_tokens": 64, "temperature": 0})
            checks["request_completed"] = True
            checks["real_local_inference"] = headers.get("x-nanogate-route") in ("local", "local_large") and \
                (resp.get("usage", {}).get("completion_tokens") or 0) > 0
            checks["dlp_policy_executed"] = bool(headers.get("x-nanogate-policy-version"))
            rid = headers.get("x-nanogate-receipt-id")
            ver = svc.receipts.verify(rid) if rid else {"valid": False}
            checks["receipt_sealed_and_valid"] = bool(ver.get("valid"))
            checks["router_scored"] = "ROUTER_UNAVAILABLE" not in headers.get("x-nanogate-reason-codes", "")
            smp = svc.telemetry.sample()
            checks["local_telemetry_collected"] = smp.get("gpu_power_w") is not None or smp.get("mem_used_bytes") is not None
            result.update(request_id=headers.get("x-nanogate-request-id"), receipt_id=rid,
                          route=headers.get("x-nanogate-route"), reason=headers.get("x-nanogate-reason"),
                          completion_tokens=resp.get("usage", {}).get("completion_tokens"),
                          answer_preview=resp["choices"][0]["message"]["content"][:160])
        except Exception as e:
            checks["request_completed"] = False
            result["error"] = f"{type(e).__name__}: {getattr(e, 'message', e)}"[:300]
    result["blocked_outbound_attempts"] = blocked
    result["checks"] = checks
    result["duration_s"] = round(time.time() - started, 2)
    result["passed"] = bool(checks) and all(checks.values())
    result["verdict"] = "PASS" if result["passed"] else "FAIL"
    return result

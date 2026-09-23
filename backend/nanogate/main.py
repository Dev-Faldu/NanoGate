"""NanoGate FastAPI application."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import generate_latest

from . import api_admin, api_openai
from .auth import Identity
from .logging_setup import setup_logging
from .metrics import REGISTRY
from .pipeline import Pipeline
from .services import Services
from .settings import ROOT, Settings, get_settings

log = logging.getLogger("nanogate")

DESCRIPTION = """
**NanoGate** is an OpenAI-compatible, local-first AI decision control plane running on the HP ZGX Nano.

### Adopting NanoGate
Change only `base_url` and `api_key` in any OpenAI SDK:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="ng_live_...")
r = client.chat.completions.create(model="nanogate-auto",
        messages=[{"role": "user", "content": "How do I reset the VPN client?"}])
print(r.choices[0].message.content)
```

### Authentication
`Authorization: Bearer <api key>`. Identity (tenant, department, role, scopes, policy) is resolved
server-side from the key. Identity headers (`X-Tenant`, `X-Department`, `X-Role`) are never trusted;
if they disagree with the key the request is rejected (`TENANT_SPOOF_REJECTED`, `DEPARTMENT_SPOOF_REJECTED`).

### Receipts
Every answered **or denied** request produces a sealed decision receipt (SHA-256 hash chain + HMAC).
Its id is returned in `x-nanogate-receipt-id`. Dashboard endpoints under `/api` require an admin session.

### Streaming
`stream: true` relays the local model's real token stream. Headers are provisional; the final chunk
contains a `nanogate` object with the sealed route, reason codes and receipt id. Router escalation is
not possible after tokens are delivered, so streamed answers are scored post-hoc (`ROUTER_POST_HOC_STREAM`).
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(path=str(settings.data_dir / "nanogate.log"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = Services(settings)
        app.state.svc = svc
        app.state.pipeline = Pipeline(svc)
        app.state.playground_identity = lambda t, d: _playground_identity(svc, t, d)
        await svc.start()
        ok, failing = svc.ready()
        log.info("NanoGate started", extra={"fields": {"ready": ok, "failing": failing}})
        yield
        await svc.stop()

    app = FastAPI(title="NanoGate", version="1.0.0", description=DESCRIPTION, lifespan=lifespan,
                  openapi_tags=[{"name": "OpenAI-compatible"}, {"name": "Dashboard"}, {"name": "Health"}])
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["x-nanogate-request-id", "x-nanogate-receipt-id", "x-nanogate-route",
                                       "x-nanogate-reason", "x-nanogate-reason-codes", "x-nanogate-policy-version",
                                       "x-nanogate-data-class", "x-nanogate-estimated-cost-usd"])
    app.include_router(api_openai.router)
    app.include_router(api_admin.router)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": {"message": str(exc.errors())[:600], "type": "invalid_request_error",
                                       "code": "INVALID_REQUEST", "param": None}}, status_code=400)

    @app.get("/healthz", tags=["Health"], summary="Liveness + per-component health")
    async def healthz(request: Request):
        svc = request.app.state.svc
        return {"alive": True, "components": svc.components()}

    @app.get("/readyz", tags=["Health"], summary="503 unless every essential component can provide real behavior")
    async def readyz(request: Request):
        svc = request.app.state.svc
        ok, failing = svc.ready()
        return JSONResponse({"ready": ok, "failing": failing,
                             "components": {k: {"ok": v["ok"], "reason": v.get("reason")} for k, v in svc.components().items()}},
                            status_code=200 if ok else 503)

    @app.get("/metrics", tags=["Health"], summary="Prometheus metrics")
    async def metrics():
        return PlainTextResponse(generate_latest(REGISTRY).decode(), media_type="text/plain; version=0.0.4")

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = (dist / path).resolve()
            if path and str(f).startswith(str(dist.resolve())) and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    return app


def _playground_identity(svc: Services, tenant: str, dept: str) -> Identity | None:
    row = svc.db.one("SELECT * FROM api_keys WHERE tenant_id=? AND department_id=? AND label LIKE 'playground%' "
                     "AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1", (tenant, dept))
    if not row:
        return None
    return Identity(row["key_id"], row["key_hash"], row["tenant_id"], row["department_id"], row["role"],
                    json.loads(row["scopes"]), svc.policies.policy_for(tenant, dept), row["label"])




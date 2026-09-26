"""OpenAI-compatible API: POST /v1/chat/completions, GET /v1/models.

Adoption = change base_url + api_key in any OpenAI SDK. NanoGate metadata travels in
x-nanogate-* response headers (and, for streams, a separate `nanogate` field on the final
chunk), never inside assistant message text.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthError, Identity, bearer, check_spoofing
from .pipeline import GatewayError, Pipeline
from .reason_codes import Reason

router = APIRouter()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = None


class ChatCompletionRequest(BaseModel):
    """Subset of the OpenAI Chat Completions schema supported by NanoGate."""
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {
        "model": "nanogate-auto", "messages": [{"role": "user", "content": "How do I reset the VPN client?"}]}})
    model: str = Field("nanogate-auto", description="nanogate-auto (policy + router decide), nanogate-local, "
                       "nanogate-local-large, nanogate-remote. Any other value is treated as nanogate-auto.")
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stream: bool = False
    stop: str | list[str] | None = None
    seed: int | None = None
    response_format: dict | None = None
    metadata: dict | None = None


def get_identity(request: Request, authorization: str | None, x_api_key: str | None, body: dict,
                 required_scope: str = "chat") -> Identity:
    svc = request.app.state.svc
    pipe: Pipeline = request.app.state.pipeline
    try:
        ident = svc.keys.resolve(bearer(authorization, x_api_key), svc.policies.policy_for)
        check_spoofing(ident, dict(request.headers))
        if not ident.has(required_scope):
            raise AuthError(Reason.SCOPE_DENIED, 403, f"key lacks scope '{required_scope}'", ident)
        return ident
    except AuthError as e:
        raise pipe.auth_failure(e, body)


RESPONSES = {
    401: {"description": "AUTH_INVALID | AUTH_REVOKED | AUTH_EXPIRED"},
    403: {"description": "TENANT_SPOOF_REJECTED | DEPARTMENT_SPOOF_REJECTED | SECRET_BLOCKED | POLICY_BLOCK | "
                         "ROUTE_ESCALATION_DENIED | SCOPE_DENIED"},
    413: {"description": "INPUT_TOO_LARGE"}, 422: {"description": "ROUTER_ABSTAIN (only when policy abstain_action=refuse)"},
    429: {"description": "RATE_LIMITED | BUDGET_DENY"}, 451: {"description": "OUTPUT_BLOCKED"},
    503: {"description": "MODEL_UNAVAILABLE | QUEUE_FULL | DLP_UNAVAILABLE_FAIL_CLOSED"},
}


@router.post("/v1/chat/completions", tags=["OpenAI-compatible"], responses=RESPONSES,
             summary="Create a chat completion (policy-enforced, receipted)")
async def chat_completions(req: ChatCompletionRequest, request: Request, authorization: str | None = Header(None),
                           x_api_key: str | None = Header(None)):
    """Every call (answered or denied) produces a sealed decision receipt. Response headers:
    `x-nanogate-request-id`, `x-nanogate-receipt-id`, `x-nanogate-route`, `x-nanogate-reason`,
    `x-nanogate-reason-codes`, `x-nanogate-policy-version`, `x-nanogate-data-class`,
    `x-nanogate-estimated-cost-usd`. With `stream: true` tokens are the model's real token stream;
    headers are provisional and the final chunk carries a `nanogate` object with the sealed decision."""
    body = req.model_dump(exclude_none=True)
    body["messages"] = [m.model_dump() for m in req.messages]
    pipe: Pipeline = request.app.state.pipeline
    try:
        ident = get_identity(request, authorization, x_api_key, body)
        if req.stream:
            it, headers = await pipe.stream(ident, body)
            return StreamingResponse(it, media_type="text/event-stream", headers={**headers, "Cache-Control": "no-cache"})
        resp, headers = await pipe.complete(ident, body)
        return JSONResponse(resp, headers=headers)
    except GatewayError as e:
        return JSONResponse(e.body(), status_code=e.status, headers=e.headers)


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {"model": "nanogate-embed", "input": ["reset VPN"]}})
    input: str | list[str]
    model: str = "nanogate-embed"


@router.post("/v1/embeddings", tags=["OpenAI-compatible"], responses=RESPONSES,
             summary="Create embeddings on-device (384-dim bge-small; DLP-checked, receipted)")
async def embeddings(req: EmbeddingRequest, request: Request, authorization: str | None = Header(None),
                     x_api_key: str | None = Header(None)):
    body = req.model_dump()
    pipe: Pipeline = request.app.state.pipeline
    try:
        ident = get_identity(request, authorization, x_api_key, {"messages": []})
        resp, headers = await pipe.embed(ident, body)
        return JSONResponse(resp, headers=headers)
    except GatewayError as e:
        return JSONResponse(e.body(), status_code=e.status, headers=e.headers)


@router.get("/v1/models", tags=["OpenAI-compatible"], summary="List routable models")
async def list_models(request: Request, authorization: str | None = Header(None), x_api_key: str | None = Header(None)):
    svc = request.app.state.svc
    try:
        get_identity(request, authorization, x_api_key, {})
    except GatewayError as e:
        return JSONResponse(e.body(), status_code=e.status, headers=e.headers)
    created = int(svc.started_at)
    data = [
        {"id": "nanogate-auto", "object": "model", "created": created, "owned_by": "nanogate",
         "description": "Policy + calibrated router choose cache / local / local-large / remote"},
        {"id": "nanogate-local", "object": "model", "created": created, "owned_by": "nanogate",
         "backing_model": svc.local.model, "state": svc.local.status.get("state")},
    ]
    if svc.local_large:
        data.append({"id": "nanogate-local-large", "object": "model", "created": created, "owned_by": "nanogate",
                     "backing_model": svc.local_large.model, "state": svc.local_large.status.get("state")})
    data.append({"id": "nanogate-embed", "object": "model", "created": created, "owned_by": "nanogate",
                 "backing_model": svc.settings.embedding_model, "endpoint": "/v1/embeddings"})
    data.append({"id": "nanogate-remote", "object": "model", "created": created, "owned_by": "nanogate",
                 "mode": svc.remote.mode, "label": svc.remote.label})
    return {"object": "list", "data": data}

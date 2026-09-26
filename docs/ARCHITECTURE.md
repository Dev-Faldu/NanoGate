# Architecture

NanoGate is a single FastAPI process (`backend/nanogate`) in front of a local OpenAI-compatible model runtime
(vLLM on the GB10 GPU, one server per model tier), plus a React dashboard served by the same process. Everything runs on the HP ZGX Nano.

```
 Application (OpenAI SDK: base_url + api_key)
            │  POST /v1/chat/completions
            ▼
 ┌──────────────────────── NanoGate gateway (FastAPI :8080) ────────────────────────┐
 │ auth.py        API key → HMAC hash lookup → Identity (tenant, dept, role, scopes)   │
 │                spoofed X-Tenant / X-Department / X-Role → 403 + receipt              │
 │ RateLimiter    token bucket per key (policy rpm)                                    │
 │ dlp.py         secrets → regex+checksums → de-obfuscation → Presidio/spaCy NER      │
 │ policy.py      ONE engine: data class × department policy → allowed routes, action   │
 │ budget.py      reserve (BEGIN IMMEDIATE) … settle / release                          │
 │ knowledge.py   CISA KEV hybrid retrieval (Security dept), source version             │
 │ cache.py       namespace(tenant,dept,policy,model family,system hash,context hash)   │
 │                → bge-small retrieval → verifier.py (NLI + slot checks) → hit / miss   │
 │ inference.py   streaming adapter: TTFT, tokens, per-token logprobs, queue, energy    │
 │ router_model   features → logistic regression → isotonic → p(error) vs threshold      │
 │ remote.py      EgressGuard ticket required; disabled | mock | live | outage-test      │
 │ dlp (output)   output scan: redact / block; detokenize                                │
 │ receipts.py    canonical JSON → SHA-256 chain → HMAC seal                            │
 │ events.py      in-process bus → SSE /api/events → dashboard                          │
 │ telemetry.py   NVML + /proc + /sys, per-field availability                            │
 └──────────────────────────────────────────────────────────────────────────────────────┘
            │ HTTP (loopback)                          ┆ only with an EgressTicket
            ▼                                          ┆
   vLLM (Qwen2.5 3B / 14B) on NVIDIA GB10              ┆  Remote provider (outside trusted boundary)
```

## Request lifecycle (`pipeline.py`)

| # | Stage | Module | Failure → reason code |
|---|---|---|---|
| 1 | Authenticate + identify | `auth.KeyStore.resolve`, `check_spoofing` | AUTH_INVALID / AUTH_REVOKED / AUTH_EXPIRED / SCOPE_DENIED / TENANT_SPOOF_REJECTED / DEPARTMENT_SPOOF_REJECTED |
| 2 | Rate limit, size limit | `RateLimiter`, `MAX_PROMPT_CHARS` | RATE_LIMITED / INPUT_TOO_LARGE |
| 3 | Classify (input DLP) | `LayeredDLP.scan` | DLP_UNAVAILABLE_FAIL_CLOSED |
| 4 | Policy | `PolicyEngine.evaluate` | SECRET_BLOCKED / POLICY_BLOCK / SENSITIVE_LOCAL_ONLY / ROUTE_ESCALATION_DENIED |
| 5 | Transform | redact / tokenize (reversible, in-memory only) | PII_REDACTED |
| 6 | Retrieval | `KEVSource.retrieve` (Security policy only) | — |
| 7 | Budget reserve | `BudgetEngine.reserve` | BUDGET_DENY |
| 8 | Verified cache | `SemanticCache.lookup` | CACHE_VERIFIED / CACHE_HARD_NEGATIVE / CACHE_NAMESPACE_MISMATCH / CACHE_STALE / CACHE_CONTEXT_MISMATCH / CACHE_SOURCE_REVOKED |
| 9 | Local inference | `LocalModelAdapter.stream` (always streams internally) | MODEL_UNAVAILABLE / QUEUE_FULL |
| 10 | Risk estimation | `Router.score` | ROUTER_UNAVAILABLE |
| 11 | Route | local → local-large → remote (policy-permitted only) | LOCAL_CONFIDENT / LOCAL_LARGE_SELECTED / REMOTE_ALLOWED / REMOTE_DISABLED / CONNECTOR_UNAVAILABLE / ROUTER_ABSTAIN |
| 12 | Output security | output DLP | OUTPUT_REDACTED / OUTPUT_BLOCKED |
| 13 | Settle | `BudgetEngine.settle` (actual tokens × configured rate) | — |
| 14 | Cache write | only router-accepted, clean, untransformed, cache-eligible answers | — |
| 15 | Receipt | `ReceiptStore.seal` | — |

Every stage appends to the request timeline (stored in the receipt) and emits an event on the bus.
Headers: `x-nanogate-request-id`, `-receipt-id`, `-route`, `-reason`, `-reason-codes`, `-policy-version`, `-data-class`,
`-estimated-cost-usd`. The primary reason is chosen by a fixed precedence list (`reason_codes.py`).

### Streaming
Tokens are relayed from the model as they are generated. A 48-character hold-back buffer is scanned with the fast
(regex + secret) recognizers so secrets/PII are redacted before they reach the client; full NER output scanning is
recorded post-hoc. The router cannot escalate an answer that was already streamed, so streamed answers are scored
post-hoc and flagged `ROUTER_POST_HOC_STREAM` when p(error) exceeds the threshold.

## Data model (SQLite, `migrations/001_init.sql`)
`tenants, departments, api_keys (hash only), policies (immutable versions), requests (no raw prompt by default),
receipts (hash chain), cache_entries, budget_accounts, budget_ledger, model_registry, benchmark_runs,
telemetry_samples, security_tests, dataset_registry, source_state, egress_log`. Indexed by request/receipt id, tenant,
department, timestamp, route, reason and policy version.

## Dashboard
React 18 + TypeScript + Vite + Tailwind + TanStack Query + React Router + Recharts. All numbers come from `/api/*`,
the SSE stream, benchmark artifacts or `config/scenario.yaml`. Routes: `/overview`, `/requests`, `/requests/:receiptId`,
`/policies`, `/router-lab`, `/cache`, `/finops`, `/infrastructure`. Browser auth exchanges an admin API key for a
12-hour HMAC-signed session token; the key itself is never stored client-side.

## Deployment on the ZGX Nano
No root, no Docker socket was available on the event device, so everything is user-space: `.venv` (torch cu130),
`.runtime/node`, `.runtime/vllm` (own venv, CUDA 13 wheels for the GB10, compute capability 12.1), model weights under
`.runtime/hf`. `docker-compose.yml` is provided for hosts where containers are allowed; the model runtime stays native.

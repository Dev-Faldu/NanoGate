# NanoGate

**Every AI request takes the cheapest safe route — with proof.**

NanoGate is an OpenAI-compatible, local-first **AI decision control plane** that runs on the HP ZGX Nano. Applications keep
their OpenAI SDK and change only `base_url` and `api_key`. For every request NanoGate decides — and proves afterwards:

> Who are you? What data are you sending? Which policy applies? Can a previous answer be *safely* reused? Is the local answer
> likely good enough? Is a larger or remote tier allowed? What did it cost? What actually happened?

```
REQUEST → AUTHENTICATE → IDENTIFY → CLASSIFY (DLP) → POLICY → BUDGET → VERIFIED CACHE → LOCAL INFERENCE (GB10)
        → RISK ESTIMATION (calibrated router) → ROUTE → OUTPUT SECURITY → COST SETTLEMENT → SEALED RECEIPT → RESPONSE
```

## Why it matters
Enterprise AI trades privacy, quality, cost, latency and auditability against each other, usually implicitly. NanoGate
makes the trade-off an explicit, per-request, policy-driven decision executed on hardware the enterprise controls, and seals
it into a tamper-evident receipt that a CISO, CFO or auditor can verify.

## What runs where (all on the ZGX Nano)
| Component | Implementation |
|---|---|
| Local inference | Ollama 0.34 (CUDA 13 backend) on the NVIDIA GB10 — `qwen2.5:3b-instruct` (default tier), `qwen2.5:14b-instruct` (escalation tier) |
| Gateway | FastAPI (`backend/nanogate`), SQLite, JSON logs, Prometheus `/metrics`, SSE `/api/events` |
| Verified cache | `BAAI/bge-small-en-v1.5` retrieval + `cross-encoder/nli-deberta-v3-base` NLI verifier + slot checks, tenant-safe namespaces |
| Router | Logistic regression + isotonic calibration over request/retrieval/generation/system features (trained on graded local answers) |
| DLP | Secret patterns, regex + Luhn/SSN checks, de-obfuscation, Presidio + spaCy NER; input and output |
| Receipts | Canonical JSON, SHA-256 hash chain, HMAC-SHA256 seal, verification + tamper test |
| Telemetry | NVML (GPU util/power/energy/temp/clock), `/proc/meminfo` (unified memory), `/sys` thermal, DMI identity |
| Dashboard | React + TypeScript + Vite + Tailwind + TanStack Query + Recharts, served by the gateway |

## Quick start
```bash
make setup        # user-space: venv, Node, Ollama, models, UI deps, API keys (no root)
make seed-data    # public datasets with provenance + synthetic security data
make doctor       # hardware, CUDA, model, artifacts, ports, telemetry
make router-data  # real local inference over public data (GPU)
make train-router # grouped split, isotonic calibration, validation threshold
make start        # Ollama + gateway on http://127.0.0.1:8080 (API + dashboard)
make demo         # validate → start → wait ready → six real demo beats
```
Admin key for the dashboard: `var/dev_keys.json` → `keys.admin.key` (mode 0600, never printed by any command).

### Use it from any OpenAI SDK
```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="<keys['app:acme/it'].key>")
r = client.chat.completions.create(model="nanogate-auto",
                                   messages=[{"role": "user", "content": "How do I reset the VPN client?"}])
```
See `examples/openai_client.py` (sync + streaming, reading `x-nanogate-*` headers). OpenAPI: `http://127.0.0.1:8080/docs`.

Models: `nanogate-auto` (policy + router decide), `nanogate-local`, `nanogate-local-large`, `nanogate-remote` (checked against
policy; `ROUTE_ESCALATION_DENIED` when not permitted).

## ZGX / model setup notes
- GB10 is compute capability 12.1; Ollama's `cuda_v13` backend is used automatically (`scripts/runtime.sh`).
- `nvidia-smi` reports GPU memory as *Not Supported* on the unified-memory GB10; NanoGate reads unified memory from
  `/proc/meminfo` and says so in the UI.
- Any OpenAI-compatible local endpoint works: set `LOCAL_MODEL_BASE_URL`, `LOCAL_MODEL_NAME`, `LOCAL_MODEL_FAMILY` (`.env.example`).

## Datasets
MMLU, GSM8K, PAWS and the live CISA KEV catalog (with retrieval date, version, SHA-256), an authored enterprise cache set,
and synthetic security evaluation data — see [`datasets/DATA_SOURCES.md`](datasets/DATA_SOURCES.md).

## APIs
| Endpoint | Purpose |
|---|---|
| `POST /v1/chat/completions`, `GET /v1/models` | OpenAI-compatible (sync + real token streaming) |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | health (per component), readiness (503 unless essential components work), Prometheus |
| `/api/*` | dashboard: overview, requests, receipts (+verify, tamper test), policies (+test, publish), router (+threshold preview), cache (+pair test, source revoke), finops (measured / scenario), infrastructure, offline verification, remote mode, SSE events |

## Benchmarks and measured results
`make benchmark` runs DLP, cache, router, security suite (101 cases), load test (concurrency 1/2/4/8) and end-to-end, each into
a new `results/<run_id>/` with a manifest, then regenerates [`RESULTS.md`](RESULTS.md). RESULTS.md is the only place numbers
are reported, each with sample size, run id and status (Measured / Target / Unavailable). Weak results are reported as measured.

## Security
Threat → control → implementation → test mapping in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md). `make test-security`,
`make redact-check`, `make lint-metrics` ([`NO_FAKE_METRICS.md`](NO_FAKE_METRICS.md)).

## Documentation
[Architecture](docs/ARCHITECTURE.md) · [Threat model](docs/THREAT_MODEL.md) · [AI evaluation](docs/AI_EVALUATION.md) ·
[Model card](docs/MODEL_CARD.md) · [Results](RESULTS.md) · [Demo runbook](docs/DEMO_RUNBOOK.md) ·
[Two-minute video](docs/TWO_MINUTE_VIDEO.md) · [Judge Q&A](docs/JUDGE_QA.md) · [Data sources](datasets/DATA_SOURCES.md) ·
[Limitations](docs/LIMITATIONS.md) · [Implementation plan](IMPLEMENTATION_PLAN.md)

## Limitations (short)
Egress control is application-layer; the router is trained on public benchmark tasks and its discrimination is modest;
the cache verifier is not perfect (97.4% hard-negative rejection on the authored test split); DLP is pattern + NER based;
single node. Full list: [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

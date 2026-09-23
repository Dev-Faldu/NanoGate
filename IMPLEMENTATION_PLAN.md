# NanoGate — Implementation Plan

## 1. Current state (inspected 2026-09-23)

| Item | Finding |
|---|---|
| Repository | `Dev's_Product/` was empty. No prior code, mockups, or tests to preserve. `git init` performed. |
| Host | `zgx-ae3c`, **aarch64**, Ubuntu 24.04.5, kernel 7.0.0-1019-nvidia, 20 CPU cores |
| GPU | **NVIDIA GB10** (compute capability 12.1), driver 580.178.04, CUDA 13.0, **121.6 GiB unified memory** |
| `nvidia-smi` memory | Reports `Not Supported` for GPU memory (unified memory); memory must come from `/proc/meminfo` |
| Privileges | No sudo, no Docker socket access, unprivileged user namespaces blocked (AppArmor) |
| Python | 3.12.3 system; project venv `.venv` (torch 2.14 cu130: CUDA works on GB10) |
| Node | Not installed system-wide → Node v24.21.0 arm64 in `.runtime/node` |
| Local model runtime | None present (port 11000 = NVIDIA DGX Dashboard, not an LLM) → Ollama v0.34.3 arm64 in `.runtime/ollama`, CUDA v13 backend detects GB10 |
| Models | `qwen2.5:3b-instruct` (local tier), `qwen2.5:14b-instruct` (local-large tier) via Ollama; `BAAI/bge-small-en-v1.5` embeddings; `cross-encoder/nli-deberta-v3-base` verifier |
| Logprobs | Ollama OpenAI endpoint returns `logprobs` + `top_logprobs`: real generation-confidence features are available |
| Internet | Available (PyPI, HF, npm, GitHub, cisa.gov) |

## 2. Architecture

```
OpenAI SDK app ──(base_url, api_key)──► NanoGate gateway (FastAPI, :8080)
   auth → identity → rate limit → DLP(in) → policy → budget reserve → verified cache
   → local inference (Ollama, GB10) → router (calibrated p_error) → escalate? (local-large | remote)
   → DLP(out) → budget settle → cache write → sealed receipt (hash chain + HMAC) → response
Each stage emits an event on an in-process bus → SSE /api/events → React dashboard (:5173 dev / served static)
Telemetry: NVML + /proc + /sys + Ollama /api/ps → /api/telemetry (+ sampled into SQLite)
```

Backend package `backend/nanogate/`: `settings, db, auth, policy, dlp, embeddings, verifier, cache, inference, remote, egress, router_model, budget, pricing, receipts, events, telemetry, metrics, pipeline, api/*`.
Single authoritative policy engine (`policy.py`); all modules consume its `PolicyDecision`.

## 3. Missing components → build order

1. Runtime bootstrap: `scripts/runtime.sh`, `scripts/fetch_datasets.py` (done), `Makefile`, `.env.example`
2. Core: settings, SQLite + migrations, JSON logging w/ redaction, reason codes, event bus
3. Auth + identity (hashed API keys, scopes, expiry, revocation, spoof rejection)
4. Policy engine (versioned YAML → pydantic), policy test/publish APIs, cache invalidation on publish
5. Layered DLP (regex + checksums + secret patterns + Presidio/spaCy NER), input+output scan
6. Embeddings + verifier + tenant-safe semantic cache
7. Local inference adapter (sync, streaming, cancellation, timeouts, TTFT, logprobs), local-large tier
8. Remote connector interface (disabled | mock="Simulated larger tier" | live | outage-test) + egress meter
9. Budget reserve/settle (SQLite `BEGIN IMMEDIATE`), pricing, cost accounting (measured vs scenario)
10. Router: data generation over public datasets → features → grouped split → LR + isotonic → artifacts
11. Receipts (hash chain, HMAC, verify endpoint)
12. Telemetry providers (RealZGX, explicit Demo)
13. Pipeline + OpenAI-compatible API + admin/dashboard APIs + SSE + /metrics + /healthz /readyz
14. Benchmarks + report generation + plots
15. Frontend (React/TS/Vite/Tailwind/TanStack Query/Router/Recharts), Playwright E2E
16. Docs, doctor, offline test, demo runner

## 4. Verification strategy
- pytest unit/integration/security suites against the real app (integration tests require the real model; they skip with an explicit reason if it is down, never fake).
- OpenAI Python SDK integration test changing only `base_url` + `api_key`.
- Attack suite ≥100 cases against a live gateway.
- Playwright E2E against a live stack with screenshots.

## 5. Benchmark plan
Router (local-only, large-only, raw-logprob, LR uncal, LR cal, HGB cal); cache (none/exact/cosine/verified);
DLP (regex/Presidio/layered); end-to-end; attack suite; load (1/2/4/8). Each run → `results/<run_id>/` with manifest; never overwritten.

## 6. Hardware detection strategy
Capability probes per field: NVML (util, temp, power, clocks, name, driver), `nvidia-smi` fallback, `/proc/meminfo` (unified memory),
`/proc/stat`/psutil (CPU), `/sys/class/thermal`, disk via `shutil.disk_usage`, network via psutil + default route, Ollama `/api/ps` + `/api/version`.
Every field returns `{status, value, unit, source, reason}`; unavailable → `value=null` + reason. Demo telemetry only with `TELEMETRY_MODE=demo` (badged in UI).

## 7. Data provenance strategy
`datasets/registry.json` (machine) + `datasets/DATA_SOURCES.md` (human): source, publisher, license, retrieval time, version, SHA-256, rows, fields, preprocessing.
Categories: A live operational, B public (MMLU, GSM8K, PAWS, CISA KEV), C synthetic security evaluation data (deterministic seed), D scenario assumptions (`config/scenario.yaml`).
Authored evaluation data (enterprise cache pairs) is labelled "Authored evaluation set".

## 8. UI implementation plan
Light editorial design system (ivory ground, glass panels, navy text, restrained accents), 8px grid, 16px radii, 150–220ms transitions, reduced-motion.
Pages: Overview (Decision Stream + Decision Path), Requests (+ receipt drawer), Receipt page, Policy Studio, Router Lab, Verified Cache, FinOps (Measured | Scenario), Infrastructure.
Every panel has loading/empty/degraded/unavailable/error/normal states. All numbers from APIs/SSE/artifacts/scenario config.

## 9. Acceptance checklist
- [ ] OpenAI SDK works by changing only base_url + api_key (sync + stream)
- [ ] Spoofed tenant/department headers rejected with reason codes
- [ ] VPN paraphrase → CACHE_VERIFIED; other-employee → CACHE_HARD_NEGATIVE; cross-tenant → namespace miss
- [ ] HR synthetic PII → SENSITIVE_LOCAL_ONLY, remote connector never invoked (egress meter = 0)
- [ ] Router artifacts persisted with metrics, calibration, hashes; routes by calibrated p_error
- [ ] Remote outage → local fallback, no hang, receipt
- [ ] Receipts hash-chained, tamper detected
- [ ] Budget race tests pass (no overspend)
- [ ] ≥100 attack cases recorded with pass/fail
- [ ] Load test at 1/2/4/8 measured
- [ ] Offline test executed and its mode recorded honestly
- [ ] Dashboard: no hardcoded metrics; SSE-driven live stream
- [ ] Docs complete; RESULTS.md statuses honest

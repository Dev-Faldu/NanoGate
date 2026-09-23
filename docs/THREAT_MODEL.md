# Threat model

Scope: the NanoGate gateway on a single ZGX Nano serving multiple tenants/departments over its OpenAI-compatible API.
Trusted: the device, the gateway process, the local model runtime on loopback. Untrusted: API clients, their prompts
and headers, model output, remote providers. Results reference `RESULTS.md` (attack suite run ids in `results/`).

| Threat | Control | Implementation | Test | Result |
|---|---|---|---|---|
| Tenant spoofing | Identity only from the authenticated key; conflicting identity headers rejected | `auth.check_spoofing` | `test_auth.py::test_tenant_spoof_rejected`, attack suite `cross_tenant` (9 cases) | Pass (see RESULTS.md) |
| Department / role spoofing | Same, for department and role headers | `auth.check_spoofing` | `test_department_spoof_rejected`, `test_role_spoof_rejected`, attack suite (8 cases) | Pass |
| Stolen / invalid / revoked / expired keys | HMAC-peppered key hashes, revocation + expiry checks, scopes | `auth.KeyStore` | `test_auth.py` (6 cases), attack suite `authentication` (9) | Pass |
| Cache leakage across tenants | Namespace = H(tenant, dept, policy version, model family, system hash, context hash); retrieval is namespace-scoped | `cache.namespace_fields`, `SemanticCache.lookup` | `test_cache.py` cross-tenant/department; cache benchmark isolation probes (90) | 0 leaks measured |
| Cache poisoning | No client write path; only gateway writes router-accepted, output-scanned, untransformed answers | `pipeline._finish_ok` cache-write guard | `test_poisoning_clients_cannot_write`; attack suite (3 + 1 model-dependent) | Pass (model-dependent case pending) |
| Semantic collision (similar but unsafe) | NLI verifier + slot conflicts (ownership, scope, polarity, action, timeframe, entities, output type) | `verifier.py` | frozen pairs in `test_cache.py`; cache benchmark hard negatives; attack suite (6) | Hard-negative rejection reported in RESULTS.md (not 100% — see LIMITATIONS) |
| Prompt injection to change routing | Routing decided by policy on classified input, never by prompt text; explicit tier requests checked against policy | `policy.evaluate`, `ROUTE_ESCALATION_DENIED` | attack suite `route_escalation` (4), `prompt_injection` (4 + 1 model-dependent) | Pass |
| System-prompt / context extraction | RAG context is public data; receipts never store prompts | `knowledge.rag_messages` | attack suite `prompt_extraction` (model-dependent) | Pending model |
| Secret leakage (input) | Secret recognizers; policy denies secrets (never sent anywhere, never stored) | `dlp.SECRET_RULES`, `policy` | `test_policy_dlp.py`; DLP benchmark; attack suite `secret_leakage` (20) | Pass |
| PII leakage to remote | Confidential+ removes remote routes; Sales tokenizes before any remote tier | `policy.evaluate`, `LayeredDLP.tokenize` | `test_policy_hr_pii_local_only`, attack suite `obfuscated_pii` (11) | Pass |
| Output leakage | Output DLP: redact/block new identifiers and secrets; streaming hold-back scanning | `pipeline._output_security`, `_stream_holdback` | attack suite `output_leakage` (model-dependent) | Pending model |
| Obfuscated identifiers | De-obfuscation layer ([at]/[dot], spaced digits, spelled digits) | `dlp._deobfuscated` | DLP benchmark obfuscated templates; attack suite | Pass on synthetic set |
| Budget races | Reserve/settle inside `BEGIN IMMEDIATE`; limit − spent − reserved checked atomically | `budget.BudgetEngine` | `test_budget_race_never_overspends` (60 threads), attack suite `budget_race` | Exactly 10/10 reservations; no overspend |
| Remote timeout / outage | Connect timeout 3 s, total 20 s; explicit fallback to local; receipt either way | `remote.RemoteConnector`, `pipeline._route` | attack suite `remote_outage` (outage-test adapter) | Pending model (fallback path needs inference) |
| Queue overload | Bounded concurrency + queue depth → `QUEUE_FULL` 503 | `LocalModelAdapter`, `MAX_QUEUE_DEPTH` | load test | See RESULTS.md |
| Oversized input | Character limit before any processing → 413 | `pipeline._prepare` | `test_oversized_prompt`, attack suite (4) | Pass |
| Sensitive data in logs | Callers never log prompts; JSON log formatter redacts keys, bearer tokens, emails, SSNs, card numbers, JWTs | `logging_setup.redact` | `test_log_redaction`, `make redact-check` | No findings |
| Receipt tampering | SHA-256 hash chain over canonical JSON + HMAC with a device key (0600) | `receipts.ReceiptStore` | `test_receipt_chain_and_tamper_detection`, `test_receipt_deletion_breaks_chain`, attack suite (3), UI tamper test | Mutation and deletion detected |
| Stale policy | Immutable policy versions; version in cache namespace; publish invalidates old entries | `PolicyEngine.publish`, `cache.invalidate_policy` | `test_stale_policy_invalidation`, attack suite `stale_policy` | Pass |
| Stale / revoked source | Cache entries bound to source version; revocation marks entries revoked | `cache._freshness`, `revoke_source` | `test_source_version_change_is_stale`, `test_revoked_source`, cache benchmark | Pass |
| Context mismatch | Conversation context and system prompt hashed into the namespace | `cache.conversation_keys` | `test_context_mismatch`, attack suite | Pass |
| DLP outage | `fail_mode: closed` → deny with `DLP_UNAVAILABLE_FAIL_CLOSED` | `policy.evaluate` | `test_dlp_unavailable_end_to_end` | Pass |
| Dashboard access | Admin scope required; HMAC-signed, expiring session tokens | `api_admin.admin`, `issue_session` | `test_dashboard_requires_admin`, attack suite (forged session) | Pass |

## Residual risks (not mitigated here)
- Egress control is **application-layer**: code paths that bypass `EgressGuard` (e.g. a malicious dependency) are not
  blocked by the OS. No firewall rule was installed (no root on the event device).
- The DLP is pattern + NER based; novel secret formats and free-text sensitive facts ("my diagnosis is…") can pass.
- The HMAC key and API-key pepper live on the same device as the database; an attacker with filesystem access to the
  device can re-seal a modified chain. External anchoring (e.g. periodic head-hash publication) is not implemented.
- A single-process SQLite deployment is not highly available.

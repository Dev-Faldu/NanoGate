# Judge Q&A

**Why not LiteLLM?**
LiteLLM is a provider proxy: it forwards a request to the model you name. NanoGate decides *whether and where* a request may
go, based on authenticated identity, detected data class and a versioned policy, then proves the decision with a sealed
receipt. It verifies cache reuse semantically instead of by string/cosine match, estimates the risk of the local answer with
a calibrated model, meters budget atomically, and runs entirely on the ZGX Nano. The OpenAI-compatible surface is the only
thing it shares with a proxy.

**What actual AI did you build?**
Three learned components run on the device: (1) a trained, isotonic-calibrated logistic router that predicts the
probability the local answer is wrong, from 50+ features including answer-token log-probs and retrieval evidence,
trained on graded outputs of the local model on public datasets; (2) a cache verifier combining a local NLI cross-encoder
with structured slot checks; (3) layered DLP including spaCy NER. The answers themselves come from local Qwen 2.5 models.

**Why does the router need calibration?**
A routing threshold is a statement like "escalate when more than 30% of answers like this are wrong". Raw scores —
especially token confidence — are not probabilities. In our run the raw log-prob baseline had ECE 0.25; isotonic
calibration on a held-out split turns scores into observed error frequencies, so the threshold means what it says.

**Why not always use the largest model?**
It costs latency and energy on every request, and it is still wrong sometimes. The cost–quality frontier in Router Lab
shows measured accuracy and on-device seconds for local-only, larger-only and routed configurations; the router spends the
larger model only where the local answer is predicted to fail.

**Can PII still leak?**
Not to a remote provider through NanoGate: Confidential-or-higher data removes every remote route in the policy decision,
no egress ticket can be minted, and the connector refuses calls without one — receipts record the connector's byte count.
But the DLP can miss identifiers it does not recognise, and egress control is application-layer (not an OS firewall). We do
not claim perfect privacy.

**How is the cache isolated?**
Every entry lives in a namespace hashed from tenant, department, policy version, model family, system-prompt hash and
conversation-context hash. Retrieval only searches the request's own namespace. Benchmarks probe other tenants,
departments, policy versions, contexts and system prompts: 0 leaks in 90 probes (RESULTS.md).

**How are cache hard negatives handled?**
Similarity only nominates candidates. A local NLI model must judge them equivalent, and slot checks veto differences in
ownership ("another employee's"), scope ("for all users"), polarity, destructive actions, timeframe, entities/CVE ids and
requested output format. Rejections return `CACHE_HARD_NEGATIVE` with the evidence. Measured rejection is 97.4% on the
authored test split — not perfect, and the known miss is documented.

**Are savings real?**
The token counts are real (metered by the runtime) and the rates are published prices with source and date. "Cost avoided"
is a calculated counterfactual: the same measured tokens at a reference hosted rate minus actual cost. Annual projections
live on a separate Scenario tab built from stated assumptions. We make no guaranteed-savings claim.

**What is actually running on the ZGX Nano?**
Ollama with Qwen 2.5 3B and 14B on the GB10 GPU (CUDA 13), the gateway (FastAPI), embeddings + NLI verifier (PyTorch,
CUDA-capable), Presidio/spaCy, SQLite, and the dashboard. The Infrastructure page shows the DMI product name, GPU, driver,
CUDA, unified memory, live NVML power/utilisation/temperature, model digests and placement — each with its source.

**What happens with no internet?**
Nothing in the core path needs it: model weights, embedding/verifier weights and public-data snapshots are on disk.
"Run Offline Verification" blocks outbound sockets/DNS for the gateway process, sends a real request through the whole
pipeline and reports PASS/FAIL with the isolation mode used. Public-data freshness is shown so a stale snapshot is never
presented as live.

**What happens when the model is down?**
`/readyz` returns 503 with the failing component; requests get `503 MODEL_UNAVAILABLE` with a sealed receipt and their budget
reservation released; the dashboard shows "Model unavailable" with the actual connection error. Nothing is fabricated.

**What happens if DLP is unavailable?**
Policies are `fail_mode: closed`: requests are denied with `DLP_UNAVAILABLE_FAIL_CLOSED` (tested end-to-end).

**What is the largest limitation?**
The router is trained on public benchmark tasks with automatic rubrics; its calibration on free-form enterprise questions
is unverified, and its discrimination is modest. Second: egress enforcement is in-application, not at the OS layer.

**How does this become an enterprise product?**
SSO/OIDC identities instead of static keys; Postgres + a vector index for multi-node; OS-level egress policy (nftables/eBPF)
bound to the same policy engine; per-tenant router fine-tuning from graded production feedback; external anchoring of the
receipt chain head; policy-as-code review workflow; connectors for internal knowledge sources with source versioning.

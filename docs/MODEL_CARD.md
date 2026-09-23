# Model card

## Models running on the device

| Role | Model | Source | License | Runtime / placement | Revision |
|---|---|---|---|---|---|
| Local tier (default route) | `qwen2.5:3b-instruct` (Q4_K_M) | Ollama library (Qwen team, Alibaba) | Qwen Research License (see `ollama show`) | Ollama 0.34.3, CUDA 13 backend, NVIDIA GB10 | runtime digest, shown in Infrastructure and receipts |
| Local-large tier (escalation) | `qwen2.5:14b-instruct` (Q4_K_M) | Ollama library | Apache-2.0 | same | runtime digest |
| Cache retrieval embeddings | `BAAI/bge-small-en-v1.5` | Hugging Face | MIT | sentence-transformers, CUDA or CPU | HF snapshot hash (`hf_revision`) |
| Cache verifier | `cross-encoder/nli-deberta-v3-base` | Hugging Face | Apache-2.0 | sentence-transformers CrossEncoder | HF snapshot hash |
| DLP NER | spaCy `en_core_web_sm` via Presidio | spaCy / Microsoft | MIT | CPU | spaCy version |
| Router | Logistic regression + isotonic calibration (scikit-learn) | trained here | project | CPU | `artifacts/router/meta.json` version |

Licenses for third-party weights are as published by their authors; verify before commercial redistribution.

## NanoGate router

- **Intended use:** decide whether a local answer is acceptable or whether a permitted larger tier should answer.
- **Output:** calibrated `p(error)` ∈ [0,1] + top contributing features; compared with a validation-selected threshold.
- **Training data:** local-model answers on public MMLU / GSM8K / CISA-KEV lookups, graded automatically (see AI_EVALUATION).
- **Out of scope:** it does not judge factuality of open-ended enterprise answers; free-form helpdesk questions are outside
  the training distribution (features still compute; calibration is unverified there).
- **Known weaknesses:** trained on one local model; features include system load (TTFT, queue) that may shift with hardware;
  department is not represented in training data (weight ≈ 0); calibration quality depends on the calibration split size.
- **Artifacts:** `artifacts/router/{model.joblib, meta.json, feature_schema.json, evaluation.json, test_predictions.json}`,
  `artifacts/calibration/{isotonic.joblib, meta.json}` — with dataset hash, split hash, code commit, threshold rule, metrics.

## Cache verifier

- Score = max(entail(a→b), entail(b→a)) × (1 − max contradiction), plus slot-conflict veto.
- Thresholds are validation-selected (`artifacts/cache/thresholds.json`).
- Weaknesses: NLI on short questions is asymmetric; jurisdiction/qualifier changes ("in the EU") can pass; rules are English-only.

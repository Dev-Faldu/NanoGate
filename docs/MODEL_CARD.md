# Model card

## Models running on the device

| Role | Model | Source | License | Runtime / placement | Revision |
|---|---|---|---|---|---|
| Local tier (default route) | `Qwen/Qwen2.5-3B-Instruct` (BF16) | Hugging Face (Qwen team, Alibaba) | Qwen Research License | vLLM (CUDA 13) via HP Z Runtime, NVIDIA GB10, :8000 | HF commit sha, or ZRT manifest sha256 |
| Local-large tier (escalation) | `Qwen/Qwen2.5-14B-Instruct` (BF16) | Hugging Face | Apache-2.0 | vLLM via ZRT, :8000 (project vLLM: :8001) | HF commit sha, or ZRT manifest sha256 |
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
- **Known weaknesses:** trained on one local model's answers (Qwen2.5-3B, Q4 build) while the device now serves the BF16
  weights through ZRT — retrain on ZRT outputs for matched calibration; system-load signals (queue, TTFT, memory) are
  recorded in receipts but excluded from the model since rf-1.2 (they leaked run conditions);
  department is not represented in training data (weight ≈ 0); calibration quality depends on the calibration split size.
- **Artifacts:** `artifacts/router/{model.joblib, meta.json, feature_schema.json, evaluation.json, test_predictions.json}`,
  `artifacts/calibration/{isotonic.joblib, meta.json}` — with dataset hash, split hash, code commit, threshold rule, metrics.

## Cache verifier

- Score = max(entail(a→b), entail(b→a)) × (1 − max contradiction), plus slot-conflict veto.
- Thresholds are validation-selected (`artifacts/cache/thresholds.json`).
- Weaknesses: NLI on short questions is asymmetric; jurisdiction/qualifier changes ("in the EU") can pass; rules are English-only.

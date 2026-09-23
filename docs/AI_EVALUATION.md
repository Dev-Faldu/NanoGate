# AI evaluation

All numbers live in [`RESULTS.md`](../RESULTS.md) with their run ids; this document explains how they are produced and
what they do and do not show. Every run writes to a new `results/<run_id>/` directory with a manifest (git commit, dataset
hashes, split hash, model identities, hardware, thresholds, policy/pricing hashes, CLI args); nothing is overwritten.

## 1. Router (risk of an unacceptable local answer)

**Target.** `y_error = 1` when the local model's answer fails the task rubric, graded automatically against public ground
truth: MMLU (final letter), GSM8K (final number), CISA KEV lookups (normalised string / exact date).

**Data generation.** `bench/generate_router_data.py` runs the *same* `LocalModelAdapter` the gateway uses
(`qwen2.5:3b-instruct`, temperature 0, 512 max tokens, top-5 logprobs), and the local-large tier (`qwen2.5:14b-instruct`)
on the same items for the larger-tier baseline and the cost–quality frontier. Raw tokens and log-probs are stored so
features can be recomputed without re-running the GPU.

**Features** (`router_features.py`, schema `rf-1.1`): request (length, structure, code, intent, requested length), retrieval
(top similarity, margin, entity coverage, source count/age, CVE mismatch), generation (mean/min/p10 log-prob, entropy,
top-1/top-2 margin, **log-prob and margin of the final answer token**, stop reason, length, refusal, hedging, schema
validity, self-contradiction) and system (queue depth, TTFT, memory pressure). Every nullable feature has a `__missing`
indicator. Token confidence is one feature among many — never "correctness".

**Protocol.** Grouped split by canonical-intent id (SHA-256 bucket): 60% train / 20% calibration / 20% test. Logistic
regression (standardised, C=0.5) is fitted on train; isotonic calibration on the calibration split; the deployment
threshold is chosen **on the calibration split** as the largest coverage whose selective risk ≤ 10%; the test split is
scored once. Baselines: raw log-prob threshold (`p_error = 1 − exp(mean logprob)`, i.e. "token confidence as
correctness"), uncalibrated logistic, calibrated gradient boosting. Metrics: AUROC, ECE (15 bins), Brier, NLL, AURC,
coverage and selective accuracy at the threshold, risk–coverage curve, reliability diagram, cost–quality frontier
(measured on-device seconds; remote dollars only as a labelled scenario).

**History (reported, not hidden).**
- `20260923T024343Z-router-40ed7e` — pipeline validation on a **partial** 870-item run (power interruptions stopped
  generation). Local accuracy 48.9%, calibrated-LR AUROC 0.574, raw log-prob AUROC 0.506 / ECE 0.250 (badly miscalibrated,
  as expected). The audit showed (a) MMLU sampling was unstratified (professional_law = 53% of MMLU items),
  (b) the grader missed `Answer: <D>`, (c) mean log-prob over reasoning text is a weak signal. Fixes: stratified sampling,
  grader fix, answer-token features. That partial data was archived (`datasets/processed/router_runs/archive/`) and is not
  mixed into later runs.
- The complete run is produced by `make router-data && make train-router`; RESULTS.md shows whichever run is latest and
  labels partial data explicitly.

## 2. Verified semantic cache

**Data.** Authored enterprise groups (30 canonical requests, 61 paraphrases, 63 typed hard negatives, 8 unrelated) split by
group into validation / test, plus 1,000 PAWS pairs as an independent public adversarial test.

**Strategies.** No cache, exact match, cosine-only semantic cache, verified semantic cache (bge-small retrieval + NLI
verifier + slot checks). Thresholds are selected on validation (maximise correct hits subject to **zero** validation false
hits) and written to `artifacts/cache/thresholds.json`, which the gateway loads. The verified strategy on the test split
runs through the real `SemanticCache` class on a temporary database, including isolation probes (other tenant, other
department, other policy version, other context, other system prompt), source staleness, revocation and TTL.

**History.**
- `20260923T023338Z-cache-688607`: first run. Validation forced a 0.95 retrieval threshold; test hit rate 3.75% at 100%
  precision. Inspection of **validation** failures only showed four rule gaps (scope phrases like "the whole marketing
  team", group possessives like "the finance team's drive", over-broad action conflicts, and "email" as a topic being read
  as an output format). Rules were generalised, and the test split was not used for this analysis.
- `20260923T023708Z-cache-0dcb26`: current thresholds (retrieval 0.60, verifier 0.30). Test: 43.8% hit rate, 97.1%
  precision, 2.3% false-hit rate, 97.4% hard-negative rejection, 0 isolation leaks in 90 probes. The one test false hit
  ("…notify a customer **in the EU**?" served the generic answer) is a known failure mode (jurisdiction qualifiers), recorded
  in LIMITATIONS rather than tuned away. PAWS: verified false-accept 40.9% vs cosine 100% at the same retrieval threshold.
- Cache lookup latency in that run was measured on **CPU** (the GPU was kept idle during power investigation).

## 3. DLP

Synthetic security evaluation data (300 positives with spans, 120 hard negatives, 200 MMLU negatives). Compared: regex
baseline, Presidio (spaCy `en_core_web_sm`) baseline, layered NanoGate detector. Span recall per category, finding
precision, F1, document false-positive rate, secret recall, per-document latency. **Caveat:** the same team wrote the
detectors and the templates, so this is a regression benchmark, not an estimate of real-world recall.

## 4. Security suite
`bench/attack_suite.py`: 101 adversarial cases in 20 categories against the live gateway. Cases needing real inference are
recorded as *skipped* (never as pass) when the model is unavailable.

## 5. Load and end-to-end
`bench/load_test.py` (concurrency 1/2/4/8, distinct MMLU prompts so the cache cannot help, streaming TTFT, NVML sampling,
cold vs warm) and `bench/evaluate_end_to_end.py` (six demo beats + mixed workload with receipts, cost and egress).
Both refuse to run without the real model.

## Integrity rules followed
No test-set tuning (the cache revision used validation failures only; the one test false hit was left as is); no removal of
hard examples; no label edits; no cherry-picked runs (every run stays in `results/`); partial data is labelled.

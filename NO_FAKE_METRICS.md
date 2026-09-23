# NO_FAKE_METRICS — repository rule

Every number NanoGate shows must be traceable to one of:

1. real computation by the running system,
2. real telemetry read from this device (NVML, `/proc`, `/sys`, runtime APIs),
3. a documented public dataset or public API (with retrieval time and hash),
4. real output of the configured local model,
5. an executed benchmark run (with a run manifest in `results/<run_id>/`),
6. a clearly labelled configuration or **Scenario** assumption.

## Never

- never fabricate numbers, latency, throughput, token counts, costs, savings, accuracy, confidence, calibration, cache precision, DLP recall, GPU utilisation, memory, power, request counts or benchmark results;
- never hardcode real-looking telemetry or headline metrics into UI components;
- never fake model output or call a deterministic script "AI";
- never fake benchmark output, or replace a failed benchmark with invented numbers;
- never present a scenario assumption as a measurement;
- never present a cached public-data snapshot as current live data (freshness is always shown);
- never present synthetic security data as real enterprise data;
- never present simulated remote inference as real remote inference (`REMOTE_MODE=mock` is labelled **Simulated larger tier** everywhere);
- never generate "live" events with timers — events come only from backend state changes.

## When something cannot be measured

Show **Unavailable** and record the reason (`{status: "unavailable", value: null, reason: "..."}`).
Targets are labelled **Target**, assumptions **Scenario**, benchmark snapshots **Benchmark**,
public data **Public dataset**, device readings **Live device telemetry**.

## Enforcement

- `make lint-metrics` (`scripts/check_no_fake_metrics.py`) scans `frontend/src` and `backend/nanogate` for random values,
  timer-driven events, marketing claims ("500 users", "99.99%", "guaranteed"…), hardcoded money/percent/token literals and
  placeholder-data identifiers. Allowed exceptions are listed in the script with a justification.
- Formatters render `null`/`undefined`/`NaN` as "Unavailable" (unit-tested in `frontend/src/lib/format.test.ts`).
- Telemetry fields are unit-tested to be either live or unavailable-with-reason (`tests/test_budget_receipts.py`).
- Benchmarks write to new run directories and refuse to overwrite (`bench/common.py`).

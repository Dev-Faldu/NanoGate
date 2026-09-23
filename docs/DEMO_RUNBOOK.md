# Demo runbook (5 minutes)

## Before the judges arrive (10 min)
1. Power: ZGX Nano on its own wall socket with the original HP adapter (see LIMITATIONS: power interruptions under GPU load).
2. `make doctor` — every required line OK.
3. `make demo` — starts Ollama + gateway, waits for `/readyz`, warms the model, runs the six beats once, prints URLs.
4. Open `http://127.0.0.1:8080`, sign in with `keys.admin.key` from `var/dev_keys.json` (do not show the file on screen).
5. `make offline-test` once so Infrastructure shows a real, timestamped offline verdict.
6. Keep a terminal ready with `examples/openai_client.py` (`NANOGATE_API_KEY` exported from `app:acme/it`).

## The six beats
Use **Overview → Try it** (or `python scripts/demo_beats.py`). Every outcome is whatever the system actually decides; say
what the receipt says, not what you expected.

| Beat | Action | Point to |
|---|---|---|
| 1 Local intelligence | "Local intelligence" preset (IT) | Decision path lights up; tokens, TTFT and route in the receipt; Infrastructure GPU power moves |
| 2 Verified reuse | "Verified reuse" preset | `CACHE_VERIFIED` if verified: similarity, verifier score, cache id, tokens avoided, latency |
| 3 Cache security | "Cache hard negative" preset | `CACHE_HARD_NEGATIVE` with ownership/authorization evidence; fresh local answer |
| 4 Privacy | "Privacy (synthetic PII)" preset (HR) | `SENSITIVE_LOCAL_ONLY`, entity types (no values), remote routes removed, **Remote bytes out: 0** from the egress meter |
| 5 Routing | "Hard public question" preset or `demo_beats.py` beat 5 | Router Lab: raw score, calibrated p(error), threshold, top factors; the route actually taken |
| 6 Resilience | Infrastructure → Remote connector → `outage-test`, then send with model `nanogate-remote` (or beat 6 of the script) | `CONNECTOR_UNAVAILABLE`, local fallback, no hang, receipt. Switch back to `disabled` |

## Closing (30 s)
Open the receipt → **Verify integrity** (SEALED) → **Run tamper test** (INTEGRITY FAILURE DETECTED, hashes differ).
Then RESULTS.md: every number with its run id, including the weak ones.

## If something fails
- Model unavailable → the UI says so; `scripts/runtime.sh start`, then `make doctor`.
- Router artifacts missing → Router Lab shows "not trained"; requests still work with `ROUTER_UNAVAILABLE`.
- Never re-run a beat to get a "better" outcome on stage; explain the receipt instead.

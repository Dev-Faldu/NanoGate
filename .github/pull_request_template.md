## What changed

<!-- One or two sentences. Link the issue if there is one. -->

## How I tested it

- [ ] `make test` (backend pytest + frontend vitest)
- [ ] Ran it against the gateway if the change touches the pipeline or UI
- [ ] `make lint-metrics` passes (no hardcoded or fabricated numbers — see NO_FAKE_METRICS.md)

## Checklist

- [ ] No secrets, API keys, `var/` files or model weights committed
- [ ] New numbers shown in the UI come from the backend, a benchmark artifact or `config/scenario.yaml`
- [ ] Docs / RESULTS.md updated if behaviour or measurements changed

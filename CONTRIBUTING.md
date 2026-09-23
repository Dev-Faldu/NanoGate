# Contributing to NanoGate

`main` is protected: nobody pushes to it directly. Every change goes through a pull request that
must pass CI and be approved by a code owner.

## Workflow

```bash
git clone https://github.com/Dev-Faldu/NanoGate.git && cd NanoGate
make setup && make seed-data && make doctor      # one-time (ZGX / arm64 + NVIDIA)

git switch main && git pull                      # start from the latest main
git switch -c feat/short-description             # your own branch (feat/, fix/, docs/, bench/)
# ... edit, then:
make test
git add -p && git commit -m "feat: what you did"
git push -u origin feat/short-description        # pushes your branch, never main
```

Then open a pull request on GitHub (`base: main` ← `compare: your branch`). Fill in the template.

- CI runs backend tests (without GPU/model — model-dependent tests skip with a reason), the
  no-fake-metrics check, and the frontend type check, build and unit tests.
- A code owner reviews and approves; then merge with **Squash and merge**.
- If `main` moved on meanwhile: `git switch main && git pull && git switch - && git rebase main`, then
  `git push --force-with-lease` (only ever force-push **your own** branch).

## Rules

- Never commit `var/` (keys, database, HMAC key), `.env`, `.runtime/`, `.venv/`, model weights or raw datasets
  — `.gitignore` covers them; run `make redact-check` if unsure.
- No hardcoded metrics in the UI or backend (see `NO_FAKE_METRICS.md`). Unmeasured values show "Unavailable".
- Benchmarks write new `results/<run_id>/` directories; never edit or delete earlier runs to improve numbers.
- Keep one topic per pull request; small PRs get reviewed faster.

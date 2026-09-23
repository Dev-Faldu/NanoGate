# NanoGate — local-first AI decision control plane on the HP ZGX Nano.
# All tooling is user-space (no root): Python venv, Node and Ollama live under .runtime/.
SHELL := /bin/bash
PY := .venv/bin/python
NODE_BIN := $(CURDIR)/.runtime/node/bin
NPM := PATH="$(NODE_BIN):$$PATH" npm
HF := HF_HOME="$(CURDIR)/.runtime/hf"
LOCAL_MODEL ?= qwen2.5:3b-instruct
LARGE_MODEL ?= qwen2.5:14b-instruct
OLLAMA_VERSION ?= v0.34.3
NODE_VERSION ?= v24.21.0

.PHONY: help doctor setup dev start stop seed-data seed-demo test test-security train-router router-data benchmark \
        demo clean-demo redact-check offline-test build-ui e2e lint-metrics report

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-16s\033[0m %s\n",$$1,$$2}'

doctor: ## Check hardware, runtimes, models, artifacts, ports, telemetry
	@$(PY) scripts/doctor.py

setup: ## Install everything in user space (venv, node, ollama, models, UI deps, keys)
	@test -x .venv/bin/python || python3 -m venv .venv
	@.venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt
	@.venv/bin/python -m spacy download en_core_web_sm -q >/dev/null || true
	@mkdir -p .runtime/node .runtime/ollama .runtime/logs
	@test -x .runtime/node/bin/node || curl -sL https://nodejs.org/dist/$(NODE_VERSION)/node-$(NODE_VERSION)-linux-arm64.tar.xz | tar -xJ -C .runtime/node --strip-components=1
	@test -x .runtime/ollama/bin/ollama || (curl -sL -o .runtime/ollama.tar.zst https://github.com/ollama/ollama/releases/download/$(OLLAMA_VERSION)/ollama-linux-arm64.tar.zst && tar -I zstd -xf .runtime/ollama.tar.zst -C .runtime/ollama && rm .runtime/ollama.tar.zst)
	@scripts/runtime.sh start
	@OLLAMA_HOST=127.0.0.1:11434 .runtime/ollama/bin/ollama pull $(LOCAL_MODEL)
	@OLLAMA_HOST=127.0.0.1:11434 .runtime/ollama/bin/ollama pull $(LARGE_MODEL)
	@$(HF) $(PY) -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('BAAI/bge-small-en-v1.5'); CrossEncoder('cross-encoder/nli-deberta-v3-base')"
	@cd frontend && $(NPM) install --silent
	@$(PY) scripts/bootstrap_keys.py
	@echo "setup complete — run: make doctor"

seed-data: ## Download public datasets (MMLU, GSM8K, PAWS, CISA KEV) with provenance + synthetic security data
	@$(HF) $(PY) scripts/fetch_datasets.py
	@$(PY) scripts/generate_synthetic_security.py

router-data: ## Generate router training data with REAL local inference (local + local-large tiers)
	@$(PY) bench/generate_router_data.py --tier local --n-mmlu 900 --n-gsm8k 450 --n-kev 450
	@$(PY) bench/generate_router_data.py --tier local_large --n-mmlu 900 --n-gsm8k 450 --n-kev 450

train-router: ## Train + calibrate the router; select threshold on validation; score test once
	@cd bench && ../$(PY) train_router.py

build-ui: ## Production build of the dashboard (served by the gateway)
	@cd frontend && $(NPM) run build

start: ## Start model runtime + gateway (serves API and built dashboard on :8080)
	@scripts/runtime.sh start
	@test -d frontend/dist || $(MAKE) build-ui
	@scripts/gateway.sh start

stop: ## Stop gateway and model runtime
	@scripts/gateway.sh stop || true
	@scripts/runtime.sh stop || true

dev: ## Gateway + Vite dev server with hot reload (UI on :5173)
	@scripts/runtime.sh start && scripts/gateway.sh start
	@cd frontend && $(NPM) run dev

test: ## Backend (pytest) + frontend (vitest) tests
	@$(PY) -m pytest
	@cd frontend && $(NPM) test --silent

test-security: ## Security unit tests + live adversarial attack suite (>=100 cases)
	@$(PY) -m pytest tests/test_auth.py tests/test_policy_dlp.py tests/test_cache.py tests/test_budget_receipts.py -q
	@cd bench && ../$(PY) attack_suite.py

benchmark: ## Run every benchmark and regenerate RESULTS.md (new run ids; nothing is overwritten)
	@cd bench && ../$(PY) evaluate_dlp.py
	@cd bench && ../$(PY) evaluate_cache.py --write-thresholds
	@cd bench && ../$(PY) train_router.py
	@cd bench && ../$(PY) attack_suite.py
	@cd bench && ../$(PY) load_test.py
	@cd bench && ../$(PY) evaluate_end_to_end.py
	@cd bench && ../$(PY) generate_report.py

report: ## Regenerate RESULTS.md from existing artifacts
	@cd bench && ../$(PY) generate_report.py

demo: ## Validate, start, warm the real model, verify telemetry, run the six demo beats
	@$(PY) scripts/doctor.py || (echo "doctor failed — fix the FAIL lines above before the demo"; exit 1)
	@test -f artifacts/router/meta.json || (echo "router artifacts missing — run make router-data && make train-router"; exit 1)
	@$(MAKE) -s start
	@$(PY) scripts/wait_ready.py
	@$(PY) scripts/demo_beats.py
	@echo ""; echo "Dashboard: http://127.0.0.1:8080  ·  API docs: http://127.0.0.1:8080/docs"
	@echo "Admin key: var/dev_keys.json → keys.admin.key (not printed)"

seed-demo: ## Run the demo beats once against a running gateway (real requests; nothing fabricated)
	@$(PY) scripts/demo_beats.py

clean-demo: ## Stop services and remove local state (DB, receipts, cache) — keeps models, data, artifacts
	@scripts/gateway.sh stop || true
	@rm -f var/nanogate.db var/nanogate.db-wal var/nanogate.db-shm var/offline_test.json var/startup.json
	@echo "local state cleared (keys and HMAC key kept; delete var/ entirely for a full reset)"

redact-check: ## Scan logs, receipts and tracked files for secrets / raw identifiers
	@$(PY) scripts/redact_check.py

offline-test: ## Real request with outbound network blocked; persists the verdict
	@$(PY) scripts/offline_test.py

e2e: ## Playwright browser tests against the running stack
	@cd frontend && PATH="$(NODE_BIN):$$PATH" CHROME_PATH="$(CURDIR)/.runtime/pw-browsers/chromium-1243/chrome-linux-arm64/chrome" npx playwright test

lint-metrics: ## Static check for fabricated / hardcoded metrics (NO_FAKE_METRICS.md)
	@$(PY) scripts/check_no_fake_metrics.py

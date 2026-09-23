-- NanoGate schema v1 (SQLite). Raw prompts are NOT stored by default.
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS departments (
  tenant_id TEXT NOT NULL,
  department_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  policy_id TEXT NOT NULL,
  PRIMARY KEY (tenant_id, department_id)
);

CREATE TABLE IF NOT EXISTS api_keys (
  key_id TEXT PRIMARY KEY,
  key_hash TEXT NOT NULL UNIQUE,          -- HMAC-SHA256(pepper, secret); raw secret never stored
  prefix TEXT NOT NULL,                   -- first chars for identification in UI
  tenant_id TEXT NOT NULL,
  department_id TEXT NOT NULL,
  role TEXT NOT NULL,
  scopes TEXT NOT NULL,                   -- JSON list
  label TEXT,
  created_at REAL NOT NULL,
  expires_at REAL,
  revoked_at REAL
);

CREATE TABLE IF NOT EXISTS policies (
  policy_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  version_tag TEXT NOT NULL,              -- e.g. "hr-v3"
  status TEXT NOT NULL,                   -- published | superseded | draft
  body_json TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  published_at REAL,
  published_by TEXT,
  PRIMARY KEY (policy_id, version)
);

CREATE TABLE IF NOT EXISTS requests (
  request_id TEXT PRIMARY KEY,
  receipt_id TEXT,
  ts REAL NOT NULL,
  tenant_id TEXT NOT NULL,
  department_id TEXT NOT NULL,
  key_id TEXT,
  intent TEXT,
  data_class TEXT,
  route TEXT,
  reason TEXT,
  reason_codes TEXT,
  status TEXT NOT NULL,                   -- ok | denied | error
  http_status INTEGER,
  cache_status TEXT,
  p_error REAL,
  latency_ms REAL,
  ttft_ms REAL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  tokens_avoided INTEGER DEFAULT 0,
  cost_usd REAL,
  counterfactual_usd REAL,
  energy_j REAL,
  remote_bytes INTEGER DEFAULT 0,
  policy_version TEXT,
  model TEXT,
  stream INTEGER DEFAULT 0,
  prompt_sha256 TEXT,
  raw_prompt TEXT                          -- only if STORE_RAW_PROMPTS=true
);
CREATE INDEX IF NOT EXISTS ix_requests_ts ON requests(ts);
CREATE INDEX IF NOT EXISTS ix_requests_tenant ON requests(tenant_id, department_id);
CREATE INDEX IF NOT EXISTS ix_requests_route ON requests(route);
CREATE INDEX IF NOT EXISTS ix_requests_reason ON requests(reason);
CREATE INDEX IF NOT EXISTS ix_requests_receipt ON requests(receipt_id);
CREATE INDEX IF NOT EXISTS ix_requests_policy ON requests(policy_version);

CREATE TABLE IF NOT EXISTS receipts (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id TEXT NOT NULL UNIQUE,
  request_id TEXT NOT NULL,
  ts REAL NOT NULL,
  tenant_id TEXT NOT NULL,
  department_id TEXT NOT NULL,
  reason TEXT,
  body_json TEXT NOT NULL,                -- canonical JSON (sorted keys)
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL,                     -- sha256(prev_hash || body)
  hmac TEXT NOT NULL                      -- HMAC-SHA256(server key, hash)
);
CREATE INDEX IF NOT EXISTS ix_receipts_request ON receipts(request_id);
CREATE INDEX IF NOT EXISTS ix_receipts_ts ON receipts(ts);

CREATE TABLE IF NOT EXISTS cache_entries (
  cache_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  department_id TEXT NOT NULL,
  namespace TEXT NOT NULL,
  canonical_query TEXT NOT NULL,
  response TEXT NOT NULL,
  embedding BLOB NOT NULL,
  embedding_model_version TEXT NOT NULL,
  verifier_model_version TEXT NOT NULL,
  model_family TEXT NOT NULL,
  model_name TEXT,
  policy_version TEXT NOT NULL,
  system_prompt_hash TEXT NOT NULL,
  context_fingerprint TEXT NOT NULL,
  source_id TEXT,
  source_hash TEXT,
  data_class TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  validation_state TEXT NOT NULL,         -- valid | invalidated | revoked
  invalidated_reason TEXT,
  response_hash TEXT NOT NULL,
  completion_tokens INTEGER,
  prompt_tokens INTEGER,
  hits INTEGER DEFAULT 0,
  origin_request_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_cache_ns ON cache_entries(namespace, validation_state);
CREATE INDEX IF NOT EXISTS ix_cache_tenant ON cache_entries(tenant_id, department_id);

CREATE TABLE IF NOT EXISTS budget_accounts (
  tenant_id TEXT NOT NULL,
  department_id TEXT NOT NULL,
  period TEXT NOT NULL,                   -- YYYY-MM
  limit_usd REAL NOT NULL,
  limit_tokens INTEGER NOT NULL,
  spent_usd REAL NOT NULL DEFAULT 0,
  spent_tokens INTEGER NOT NULL DEFAULT 0,
  reserved_usd REAL NOT NULL DEFAULT 0,
  reserved_tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, department_id, period)
);

CREATE TABLE IF NOT EXISTS budget_ledger (
  entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
  reservation_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  department_id TEXT NOT NULL,
  period TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- reserve | settle | release | deny
  usd REAL NOT NULL,
  tokens INTEGER NOT NULL,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ledger_req ON budget_ledger(request_id);

CREATE TABLE IF NOT EXISTS model_registry (
  model_key TEXT PRIMARY KEY,
  tier TEXT NOT NULL,
  info_json TEXT NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_runs (
  run_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  ts REAL NOT NULL,
  manifest_json TEXT NOT NULL,
  summary_json TEXT
);

CREATE TABLE IF NOT EXISTS telemetry_samples (
  ts REAL NOT NULL,
  gpu_util REAL, gpu_temp_c REAL, gpu_power_w REAL, gpu_clock_mhz REAL,
  mem_used_bytes INTEGER, mem_total_bytes INTEGER, cpu_util REAL,
  queue_depth INTEGER, in_flight INTEGER, tokens_per_s REAL
);
CREATE INDEX IF NOT EXISTS ix_tel_ts ON telemetry_samples(ts);

CREATE TABLE IF NOT EXISTS security_tests (
  run_id TEXT NOT NULL,
  attack_id TEXT NOT NULL,
  category TEXT NOT NULL,
  expected TEXT NOT NULL,
  actual TEXT,
  passed INTEGER NOT NULL,
  reason_code TEXT,
  receipt_id TEXT,
  PRIMARY KEY (run_id, attack_id)
);

CREATE TABLE IF NOT EXISTS dataset_registry (
  name TEXT PRIMARY KEY,
  info_json TEXT NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS source_state (
  source_id TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS egress_log (
  ts REAL NOT NULL,
  request_id TEXT,
  destination TEXT NOT NULL,
  allowed INTEGER NOT NULL,
  bytes_out INTEGER NOT NULL,
  bytes_in INTEGER NOT NULL,
  outcome TEXT NOT NULL
);

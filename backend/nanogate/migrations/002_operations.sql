-- NanoGate schema v2: operations (audit log, settings, alerts, backups), company knowledge, retention pruning.
-- Additive only: no existing column changes meaning.

-- Every administrative change, whoever made it (dashboard, assistant after confirmation, or a scheduled job).
CREATE TABLE IF NOT EXISTS audit_log (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  actor_key_id TEXT,                      -- NULL for system jobs
  actor_label TEXT,
  source TEXT NOT NULL,                   -- dashboard | assistant | system
  action TEXT NOT NULL,                   -- e.g. key.create, key.revoke, department.create, retention.prune
  target TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}'  -- never contains secrets or prompt text
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(ts);

-- Operator settings edited from the dashboard (alert channels, SIEM, backups). JSON values, no secrets in exports.
CREATE TABLE IF NOT EXISTS ops_settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at REAL NOT NULL,
  updated_by TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
  alert_id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  rule TEXT NOT NULL,
  severity TEXT NOT NULL,                 -- info | warning | critical
  subject TEXT,                           -- what it is about (department, component, receipt)
  title TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  delivered_json TEXT NOT NULL DEFAULT '[]',
  acknowledged_at REAL,
  acknowledged_by TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_ts ON alerts(ts);

CREATE TABLE IF NOT EXISTS backups (
  backup_id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  filename TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- manual | scheduled | pre-upgrade
  created_by TEXT
);

-- Company documents for retrieval, scoped to a tenant and a set of its departments.
CREATE TABLE IF NOT EXISTS knowledge_sources (
  source_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  departments_json TEXT NOT NULL,         -- JSON list of department ids that may retrieve from it
  data_class TEXT NOT NULL,               -- Public | Internal | Confidential | Restricted
  version TEXT NOT NULL,                  -- changes whenever documents change: cache entries bind to it
  created_at REAL NOT NULL,
  created_by TEXT,
  revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS knowledge_docs (
  doc_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  chunks INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_kdocs_source ON knowledge_docs(source_id);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  ord INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_kchunks_source ON knowledge_chunks(source_id);

-- Retention: a pruned receipt keeps its hash, HMAC and chain links (so the chain still verifies) but its body is erased.
ALTER TABLE receipts ADD COLUMN pruned_at REAL;
ALTER TABLE receipts ADD COLUMN pruned_reason TEXT;

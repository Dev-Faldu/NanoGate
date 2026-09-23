/** API contracts mirrored from backend/nanogate/api_admin.py */

export interface Overview {
  window_hours: number;
  generated_at: number;
  requests: number;
  answered: number;
  denied: number;
  errors: number;
  local_coverage: number | null;
  cost_actual_usd: number;
  cost_avoided_usd: number | null;
  cost_basis: string;
  sensitive_requests: number;
  sensitive_egress_bytes: number;
  latency_avg_ms: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_samples: number;
  cache_lookups: number;
  cache_hits: number;
  cache_verification_rate: number | null;
  tokens_total: number;
  tokens_avoided: number;
  queue_depth: number;
  in_flight: number;
  model: { name: string; state: string; reason?: string | null; tokens_per_s: number | null; revision: string };
  route_counts: Record<string, number>;
}

export interface RequestRow {
  request_id: string;
  receipt_id: string;
  ts: number;
  tenant_id: string;
  department_id: string;
  intent: string | null;
  data_class: string | null;
  route: string;
  reason: string;
  reason_codes: string[];
  status: "ok" | "denied" | "error";
  http_status: number;
  cache_status: string | null;
  p_error: number | null;
  latency_ms: number | null;
  ttft_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  tokens_avoided: number;
  cost_usd: number | null;
  counterfactual_usd: number | null;
  energy_j: number | null;
  remote_bytes: number;
  policy_version: string | null;
  model: string | null;
  stream: number;
}

export interface RequestsResp {
  total: number;
  rows: RequestRow[];
  facets: Record<string, string[]>;
}

export interface TimelineEntry {
  stage: string;
  status: string;
  t_ms: number;
  detail: Record<string, unknown>;
}

export interface Receipt {
  receipt_id: string;
  request_id: string;
  seq: number;
  ts: number;
  reason: string;
  hash: string;
  prev_hash: string;
  hmac: string;
  body: ReceiptBody;
}

export interface ReceiptBody {
  status: string;
  http_status: number;
  error: string | null;
  identity: Record<string, any>;
  classification: Record<string, any>;
  policy: Record<string, any> | null;
  cache: Record<string, any> | null;
  retrieval: Record<string, any> | null;
  model: Record<string, any> | null;
  router: Record<string, any> | null;
  decision: Record<string, any>;
  economics: Record<string, any> | null;
  device: Record<string, any>;
  timeline: TimelineEntry[];
  integrity: Record<string, any>;
}

export interface Verification {
  receipt_id: string;
  valid: boolean;
  checks: Record<string, boolean>;
  hash?: string;
  recomputed_hash?: string;
  prev_hash?: string;
  verified_at?: number;
}

export interface Field {
  status: "live" | "unavailable" | "demo";
  value: any;
  unit: string | null;
  source: string | null;
  reason: string | null;
}

export interface GatewayEvent {
  seq: number;
  type: string;
  ts: number;
  request_id: string | null;
  data: Record<string, any>;
}

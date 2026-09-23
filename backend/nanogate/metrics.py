"""Prometheus metrics (GET /metrics). Values come only from real request processing."""
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

REQUESTS = Counter("nanogate_requests_total", "Requests processed", ["department", "route", "reason", "status"], registry=REGISTRY)
LATENCY = Histogram("nanogate_request_latency_seconds", "End-to-end request latency", ["route"], registry=REGISTRY,
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64))
TTFT = Histogram("nanogate_ttft_seconds", "Time to first token", ["tier"], registry=REGISTRY,
                 buckets=(0.02, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8))
TOKENS = Counter("nanogate_tokens_total", "Tokens processed", ["kind", "tier"], registry=REGISTRY)
CACHE = Counter("nanogate_cache_decisions_total", "Cache decisions", ["decision"], registry=REGISTRY)
DLP = Counter("nanogate_dlp_findings_total", "DLP findings", ["entity", "direction"], registry=REGISTRY)
POLICY_BLOCKS = Counter("nanogate_policy_blocks_total", "Denied requests", ["reason"], registry=REGISTRY)
COST = Counter("nanogate_cost_usd_total", "Measured cost (configured rates x measured tokens)", ["route"], registry=REGISTRY)
QUEUE = Gauge("nanogate_queue_depth", "Requests waiting for a local inference slot", registry=REGISTRY)
IN_FLIGHT = Gauge("nanogate_in_flight", "Requests currently generating", registry=REGISTRY)
MODEL_UP = Gauge("nanogate_model_ready", "1 if the local model endpoint is ready", ["tier"], registry=REGISTRY)
ERRORS = Counter("nanogate_errors_total", "Errors", ["kind"], registry=REGISTRY)

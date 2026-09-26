"""Runtime configuration from environment (and optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    app_mode: str = field(default_factory=lambda: _env("APP_MODE", "production"))  # production | demo (reproducible)
    host: str = field(default_factory=lambda: _env("NANOGATE_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("NANOGATE_PORT", "8080")))
    data_dir: Path = field(default_factory=lambda: Path(_env("NANOGATE_DATA_DIR", str(ROOT / "var"))))
    db_path: Path | None = None
    receipt_hmac_key_file: Path | None = None

    local_model_base_url: str = field(default_factory=lambda: _env("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:8000/v1"))
    local_model_name: str = field(default_factory=lambda: _env("LOCAL_MODEL_NAME", "Qwen/Qwen2.5-3B-Instruct"))
    local_model_api_key: str = field(default_factory=lambda: _env("LOCAL_MODEL_API_KEY", ""))
    local_model_family: str = field(default_factory=lambda: _env("LOCAL_MODEL_FAMILY", "qwen2.5"))
    local_model_revision: str = field(default_factory=lambda: _env("LOCAL_MODEL_REVISION", "auto"))
    # vLLM serves one model per process: the escalation tier has its own server
    local_large_model_base_url: str = field(default_factory=lambda: _env("LOCAL_LARGE_MODEL_BASE_URL", "http://127.0.0.1:8001/v1"))
    local_large_model_name: str | None = field(default_factory=lambda: _env("LOCAL_LARGE_MODEL_NAME", "Qwen/Qwen2.5-14B-Instruct"))
    local_timeout_s: float = field(default_factory=lambda: float(_env("LOCAL_MODEL_TIMEOUT_S", "120")))
    max_concurrent_inference: int = field(default_factory=lambda: int(_env("MAX_CONCURRENT_INFERENCE", "4")))
    max_queue_depth: int = field(default_factory=lambda: int(_env("MAX_QUEUE_DEPTH", "32")))

    remote_mode: str = field(default_factory=lambda: _env("REMOTE_MODE", "disabled"))  # disabled|mock|live|outage-test
    remote_base_url: str | None = field(default_factory=lambda: _env("REMOTE_BASE_URL"))
    remote_api_key: str | None = field(default_factory=lambda: _env("REMOTE_API_KEY"))
    remote_model: str | None = field(default_factory=lambda: _env("REMOTE_MODEL"))
    remote_provider: str | None = field(default_factory=lambda: _env("REMOTE_PROVIDER", "openai"))
    remote_timeout_s: float = field(default_factory=lambda: float(_env("REMOTE_TIMEOUT_S", "20")))

    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    verifier_model: str = field(default_factory=lambda: _env("VERIFIER_MODEL", "cross-encoder/nli-deberta-v3-base"))
    ml_device: str = field(default_factory=lambda: _env("ML_DEVICE", "auto"))

    telemetry_mode: str = field(default_factory=lambda: _env("TELEMETRY_MODE", "real"))  # real | demo
    telemetry_interval_s: float = field(default_factory=lambda: float(_env("TELEMETRY_INTERVAL_S", "2")))

    policies_file: Path = field(default_factory=lambda: Path(_env("POLICIES_FILE", str(ROOT / "config" / "policies.yaml"))))
    pricing_file: Path = field(default_factory=lambda: Path(_env("PRICING_FILE", str(ROOT / "config" / "pricing.yaml"))))
    scenario_file: Path = field(default_factory=lambda: Path(_env("SCENARIO_FILE", str(ROOT / "config" / "scenario.yaml"))))
    router_dir: Path = field(default_factory=lambda: Path(_env("ROUTER_ARTIFACT_DIR", str(ROOT / "artifacts" / "router"))))
    cache_cfg_file: Path = field(default_factory=lambda: Path(_env("CACHE_CONFIG_FILE", str(ROOT / "artifacts" / "cache" / "thresholds.json"))))
    kev_file: Path = field(default_factory=lambda: ROOT / "datasets" / "raw" / "cisa_kev" / "known_exploited_vulnerabilities.json")
    offline_report: Path = field(default_factory=lambda: ROOT / "var" / "offline_test.json")

    store_raw_prompts: bool = field(default_factory=lambda: _bool("STORE_RAW_PROMPTS", False))
    max_prompt_chars: int = field(default_factory=lambda: int(_env("MAX_PROMPT_CHARS", "32000")))
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"))
    load_ml: bool = field(default_factory=lambda: _bool("NANOGATE_LOAD_ML", True))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.db_path is None:
            self.db_path = Path(_env("NANOGATE_DB", str(self.data_dir / "nanogate.db")))
        if self.receipt_hmac_key_file is None:
            self.receipt_hmac_key_file = self.data_dir / "receipt_hmac.key"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(**overrides) -> Settings:
    """Used by tests to build an isolated configuration."""
    global _settings
    _settings = Settings(**overrides)
    return _settings

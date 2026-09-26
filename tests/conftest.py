"""Shared fixtures. Each test session gets an isolated data dir (DB, keys, HMAC key).

Tests that need the real local model are marked `needs_model` and skip with an explicit
reason when the endpoint is unreachable — they never fall back to fake output.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ML_DEVICE", "cpu")
os.environ.setdefault("HF_HOME", str(ROOT / ".runtime" / "hf"))


def model_up() -> bool:
    base = os.environ.get("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:8000/v1")
    try:
        return httpx.get(f"{base}/models", timeout=2).status_code == 200
    except Exception:
        return False


MODEL_UP = model_up()
needs_model = pytest.mark.skipif(not MODEL_UP, reason="local model endpoint unreachable — real inference required, no fake fallback")


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    from nanogate.settings import reset_settings
    d = tmp_path_factory.mktemp("nanogate")
    return reset_settings(data_dir=d, telemetry_interval_s=5.0)


@pytest.fixture(scope="session")
def app(settings):
    from fastapi.testclient import TestClient
    from nanogate.main import create_app
    a = create_app(settings)
    with TestClient(a) as client:
        a.state.client = client
        yield a


@pytest.fixture(scope="session")
def client(app):
    return app.state.client


@pytest.fixture(scope="session")
def svc(app):
    return app.state.svc


@pytest.fixture(scope="session")
def keys(svc):
    """tenant/department -> raw key (chat scope); plus 'admin'."""
    out = {}
    for tenant, t in svc.policies.tenants.items():
        for dept in t["departments"]:
            _, raw = svc.keys.create(tenant, dept, "member", ["chat"], label=f"test:{tenant}/{dept}")
            out[f"{tenant}/{dept}"] = raw
            svc.keys.create(tenant, dept, "member", ["chat"], label=f"playground:{tenant}/{dept}")
    _, out["admin"] = svc.keys.create("acme", "it", "admin", ["admin", "chat"], label="admin")
    return out


def auth(k: str) -> dict:
    return {"Authorization": f"Bearer {k}"}

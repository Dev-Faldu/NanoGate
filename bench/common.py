"""Benchmark harness: unique run ids, run manifests, append-only result directories."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("HF_HOME", str(ROOT / ".runtime" / "hf"))
RESULTS = ROOT / "results"


def sha256_file(p: Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str | None:
    try:
        c = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=3)
        if c.returncode != 0:
            return None
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return c.stdout.strip()[:12] + ("-dirty" if dirty else "")
    except Exception:
        return None


def hardware_summary() -> dict:
    out: dict[str, Any] = {"machine": platform.machine(), "python": platform.python_version()}
    try:
        out["product"] = Path("/sys/class/dmi/id/product_name").read_text().strip()
    except Exception:
        out["product"] = None
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        n = pynvml.nvmlDeviceGetName(h)
        out["gpu"] = n.decode() if isinstance(n, bytes) else n
        out["driver"] = pynvml.nvmlSystemGetDriverVersion()
    except Exception as e:
        out["gpu"] = f"unavailable: {type(e).__name__}"
    try:
        out["mem_total_bytes"] = int(open("/proc/meminfo").readline().split()[1]) * 1024
    except Exception:
        pass
    return out


def new_run(kind: str, args: dict | None = None, extra: dict | None = None) -> tuple[str, Path]:
    ts = dt.datetime.now(dt.timezone.utc)
    run_id = f"{ts.strftime('%Y%m%dT%H%M%SZ')}-{kind}-{uuid.uuid4().hex[:6]}"
    d = RESULTS / run_id
    d.mkdir(parents=True, exist_ok=False)
    reg = json.loads((ROOT / "datasets" / "registry.json").read_text()) if (ROOT / "datasets" / "registry.json").exists() else {}
    manifest = {
        "run_id": run_id, "kind": kind, "timestamp": ts.isoformat(timespec="seconds"), "git_commit": git_commit(),
        "cli_args": args or {}, "argv": sys.argv, "hardware": hardware_summary(),
        "datasets": {k: {"sha256": v.get("sha256"), "rows": v.get("rows"), "retrieved_at": v.get("retrieved_at"),
                         "version": v.get("version")} for k, v in reg.items()},
        "policy_sha256": sha256_file(ROOT / "config" / "policies.yaml"),
        "pricing_sha256": sha256_file(ROOT / "config" / "pricing.yaml"),
        "router_meta": json.loads((ROOT / "artifacts/router/meta.json").read_text()) if (ROOT / "artifacts/router/meta.json").exists() else None,
        **(extra or {}),
    }
    (d / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    return run_id, d


def write(d: Path, name: str, obj: Any) -> Path:
    p = d / name
    if p.exists():
        raise FileExistsError(f"refusing to overwrite {p}")
    p.write_text(json.dumps(obj, indent=2, default=str))
    return p


def percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    import numpy as np
    return float(np.percentile(xs, q))

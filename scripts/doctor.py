"""`make doctor` — verify the machine can run NanoGate for real. Never prints secrets.

Exit code 1 if any REQUIRED check fails.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
results: list[tuple[str, str, str, bool]] = []   # (check, state, detail, required)


def check(name: str, ok: bool | None, detail: str, required: bool = True, warn: bool = False):
    state = "OK" if ok else ("WARN" if (warn or not required) else "FAIL")
    if ok is None:
        state = "SKIP"
    results.append((name, state, detail, required))


def run(cmd: list[str], timeout=5) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return None


def main() -> int:
    check("architecture", True, f"{platform.machine()} ({'Arm64 native' if platform.machine() == 'aarch64' else 'non-Arm host'})")
    osr = Path("/etc/os-release").read_text() if Path("/etc/os-release").exists() else ""
    check("os", "Ubuntu" in osr or bool(osr), next((l.split("=", 1)[1].strip('"') for l in osr.splitlines() if l.startswith("PRETTY_NAME")), platform.platform()))
    product = Path("/sys/class/dmi/id/product_name").read_text().strip() if Path("/sys/class/dmi/id/product_name").exists() else "unknown"
    check("device", True, product, required=False)
    check("python", sys.version_info >= (3, 11), platform.python_version())
    venv = ROOT / ".venv" / "bin" / "python"
    check("python venv", venv.exists(), str(venv.relative_to(ROOT)) if venv.exists() else "missing: run make setup")
    node = ROOT / ".runtime" / "node" / "bin" / "node"
    check("node", node.exists() or shutil.which("node") is not None, run([str(node) if node.exists() else "node", "-v"]) or "missing")
    check("package manager (npm)", (ROOT / ".runtime/node/bin/npm").exists() or shutil.which("npm") is not None, "npm")
    docker = shutil.which("docker")
    dinfo = run(["docker", "info", "--format", "{{.ServerVersion}}"]) if docker else None
    check("container runtime", bool(dinfo), f"docker {dinfo}" if dinfo else ("docker present but socket not accessible (not required)" if docker else "none (not required)"), required=False)
    smi = run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    check("gpu / driver", bool(smi), smi or "nvidia-smi unavailable")
    cuda = run(["nvidia-smi"]) or ""
    cv = next((l.split("CUDA Version:")[1].split("|")[0].strip() for l in cuda.splitlines() if "CUDA Version" in l), None)
    check("cuda", bool(cv), f"CUDA {cv}" if cv else "not detected")
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        p = pynvml.nvmlDeviceGetPowerUsage(h) / 1000
        check("telemetry (NVML)", True, f"power {p:.1f} W readable")
    except Exception as e:
        check("telemetry (NVML)", False, f"{type(e).__name__}", required=False)
    import httpx
    base = os.environ.get("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
    model = os.environ.get("LOCAL_MODEL_NAME", "qwen2.5:3b-instruct")
    try:
        ids = [m["id"] for m in httpx.get(f"{base}/models", timeout=3).json()["data"]]
        check("local model endpoint", True, base)
        check("local model ready", model in ids, f"{model} {'listed' if model in ids else 'NOT listed: run make setup'}")
        large = os.environ.get("LOCAL_LARGE_MODEL_NAME", "qwen2.5:14b-instruct")
        check("local-large model", large in ids, large, required=False)
    except Exception as e:
        check("local model endpoint", False, f"{base} unreachable ({type(e).__name__}); start with scripts/runtime.sh start")
        check("local model ready", False, "endpoint down")
    du = shutil.disk_usage(ROOT)
    check("disk", du.free > 20e9, f"{du.free / 1e9:.0f} GB free")
    db = ROOT / "var" / "nanogate.db"
    check("database", db.exists() or os.access(ROOT, os.W_OK), "var/nanogate.db" + (" present" if db.exists() else " (created on first start)"))
    try:
        import yaml
        pol = yaml.safe_load((ROOT / "config/policies.yaml").read_text())
        check("policy", bool(pol.get("policies")), f"{len(pol['policies'])} policies in config/policies.yaml")
    except Exception as e:
        check("policy", False, f"{type(e).__name__}: {e}")
    check("pricing config", (ROOT / "config/pricing.yaml").exists(), "config/pricing.yaml")
    hf = ROOT / ".runtime" / "hf" / "hub"
    for repo in ("BAAI/bge-small-en-v1.5", "cross-encoder/nli-deberta-v3-base"):
        check(f"model asset {repo.split('/')[1]}", (hf / ("models--" + repo.replace("/", "--"))).exists(), "cached locally (offline-capable)" if (hf / ("models--" + repo.replace("/", "--"))).exists() else "missing: run make setup")
    reg = ROOT / "datasets" / "registry.json"
    check("public datasets", reg.exists(), f"{len(json.loads(reg.read_text()))} registered" if reg.exists() else "run make seed-data")
    check("router artifact", (ROOT / "artifacts/router/meta.json").exists(), "artifacts/router/meta.json" if (ROOT / "artifacts/router/meta.json").exists() else "not trained: make train-router", required=False)
    check("calibration artifact", (ROOT / "artifacts/calibration/isotonic.joblib").exists(), "artifacts/calibration/isotonic.joblib", required=False)
    check("cache thresholds", (ROOT / "artifacts/cache/thresholds.json").exists(), "validation-selected" if (ROOT / "artifacts/cache/thresholds.json").exists() else "defaults (run make benchmark)", required=False)
    keys = ROOT / "var" / "dev_keys.json"
    mode = oct(keys.stat().st_mode & 0o777) if keys.exists() else None
    check("api keys provisioned", keys.exists() and mode == "0o600", f"var/dev_keys.json mode {mode}" if keys.exists() else "run make setup")
    for port, name in ((8080, "gateway port 8080"), (5173, "dev ui port 5173")):
        s = socket.socket()
        busy = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        check(name, True, "in use (service running?)" if busy else "free", required=False)
    w = max(len(r[0]) for r in results)
    for name, state, detail, _ in results:
        print(f"  {state:4s}  {name:<{w}}  {detail}")
    fails = [r for r in results if r[1] == "FAIL"]
    print(f"\n{len(results)} checks · {len(fails)} failing" + (": " + ", ".join(r[0] for r in fails) if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

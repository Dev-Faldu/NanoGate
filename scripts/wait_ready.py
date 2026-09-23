"""Wait for /readyz, record measured gateway startup time, print health (never secrets)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8080"


def main() -> int:
    t0 = time.time()
    last = None
    while time.time() - t0 < 300:
        try:
            r = httpx.get(f"{BASE}/readyz", timeout=5)
            last = r.json()
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    waited = time.time() - t0
    if not last or not last.get("ready"):
        print("NOT READY:", json.dumps((last or {}).get("failing")), file=sys.stderr)
        for k, v in (last or {}).get("components", {}).items():
            if not v["ok"]:
                print(f"  {k}: {v.get('reason')}", file=sys.stderr)
        return 1
    h = httpx.get(f"{BASE}/healthz", timeout=10).json()["components"]
    (ROOT / "var" / "startup.json").write_text(json.dumps({"ready_after_s": waited, "measured_at": time.time(),
                                                           "model_warmup_ms": h["model"].get("latency_ms")}))
    print(f"ready (waited {waited:.1f}s)")
    for k, v in h.items():
        print(f"  {'✓' if v['ok'] else '·'} {k:14s} {v.get('model') or v.get('version') or v.get('state') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""`make offline-test` — run the gateway's offline verification and print the verdict.

The gateway blocks all non-loopback sockets/DNS in its own process (or, if the host has no
default route, reports that the OS itself was offline) and issues a real request through the
full pipeline. The report is persisted to var/offline_test.json and shown in the dashboard.
For an OS-level test: unplug the network, then run this command.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8080"


def main() -> int:
    keys = json.loads((ROOT / "var/dev_keys.json").read_text())["keys"]
    tok = httpx.post(f"{BASE}/api/session", json={"api_key": keys["admin"]["key"]}, timeout=30).json()["token"]
    r = httpx.post(f"{BASE}/api/offline/verify", headers={"Authorization": f"Bearer {tok}"}, timeout=600).json()
    print(f"OFFLINE CORE: {r.get('verdict')}   (isolation: {r.get('isolation_mode')})")
    for k, v in (r.get("checks") or {}).items():
        print(f"  {'✓' if v else '✕'} {k}")
    for d in r.get("dependency_risks", []):
        print(f"  {'ok ' if d['ok'] else 'RISK'} {d['check']}: {d['detail']}")
    print(f"  blocked outbound attempts during test: {len(r.get('blocked_outbound_attempts', []))}")
    if r.get("error"):
        print("  error:", r["error"])
    return 0 if r.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())

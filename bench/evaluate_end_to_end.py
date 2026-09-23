"""End-to-end evaluation against a LIVE gateway with REAL local inference.

1. Runs the six demo beats (scripts/demo_beats.py).
2. Runs a mixed workload: KEV lookups (Security), MMLU (IT), authored cache paraphrases (IT),
   synthetic-PII HR requests, and secret-bearing requests. Measures route distribution, reason
   codes, latency, tokens, measured cost/counterfactual, cache tokens avoided, receipt validity,
   and egress bytes on sensitive requests.

Usage: python bench/evaluate_end_to_end.py [--n 60]
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time

import httpx
import yaml

from common import ROOT, new_run, percentile, write

GW = "http://127.0.0.1:8080"


def workload(n: int) -> list[tuple[str, str]]:
    rng = random.Random(42)
    kev = json.loads((ROOT / "datasets/raw/cisa_kev/known_exploited_vulnerabilities.json").read_text())["vulnerabilities"]
    mmlu = [json.loads(l) for l in (ROOT / "datasets/raw/mmlu/mmlu_subset_test.jsonl").read_text().split("\n") if l.strip()]
    groups = yaml.safe_load((ROOT / "datasets/authored/cache_groups.yaml").read_text())["groups"]
    rng.shuffle(kev)
    rng.shuffle(mmlu)
    items: list[tuple[str, str]] = []
    k = n // 5
    items += [("app:acme/security", f"Which vendor's product is affected by {r['cveID']} according to CISA KEV?") for r in kev[:k]]
    items += [("app:acme/it", f"In at most three sentences: {r['question']}") for r in mmlu[:k]]
    for g in groups[:k]:
        items.append(("app:acme/it", g["canonical"]))
        items.append(("app:acme/it", g["paraphrases"][0]))
    items += [("app:acme/hr", f"Summarize the leave request of employee EMP-{rng.randint(100000, 999999)}, "
                              f"contact {rng.choice(['a', 'b', 'c'])}.{rng.randint(10, 99)}@acme-corp.example.") for _ in range(k // 2)]
    items += [("app:acme/it", f"Why does this fail? password: Zq{rng.randint(1000, 9999)}!x") for _ in range(k // 2)]
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    args = ap.parse_args()
    st = httpx.get(f"{GW}/healthz", timeout=10).json()["components"]
    if not st["model"]["ok"]:
        raise SystemExit("model unavailable; end-to-end evaluation requires real inference")
    run_id, d = new_run("e2e", vars(args))
    beats_path = d / "demo_beats.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/demo_beats.py"), "--json", str(beats_path)], check=False)
    keys = json.loads((ROOT / "var/dev_keys.json").read_text())["keys"]
    admin = httpx.post(f"{GW}/api/session", json={"api_key": keys["admin"]["key"]}).json()["token"]
    rows = []
    with httpx.Client(base_url=GW, timeout=600) as c:
        for keyname, prompt in workload(args.n):
            t0 = time.perf_counter()
            r = c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {keys[keyname]['key']}"},
                       json={"model": "nanogate-auto", "max_tokens": 256, "temperature": 0, "messages": [{"role": "user", "content": prompt}]})
            rid = r.headers.get("x-nanogate-receipt-id")
            rec = c.get(f"/api/receipts/{rid}", headers={"Authorization": f"Bearer {admin}"}).json()
            v = c.post(f"/api/receipts/{rid}/verify", headers={"Authorization": f"Bearer {admin}"}).json()
            b = rec["body"]
            rows.append({"department": keyname.split("/")[1], "status": r.status_code, "route": b["decision"]["chosen_route"],
                         "reason": rec["reason"], "data_class": (b.get("classification") or {}).get("data_class"),
                         "latency_ms": (time.perf_counter() - t0) * 1000, "prompt_tokens": (b.get("model") or {}).get("prompt_tokens"),
                         "completion_tokens": (b.get("model") or {}).get("completion_tokens"),
                         "cost_usd": (b.get("economics") or {}).get("actual_cost_usd"),
                         "counterfactual_usd": ((b.get("economics") or {}).get("counterfactual") or {}).get("cost_usd"),
                         "tokens_avoided": (b.get("economics") or {}).get("tokens_avoided_by_cache"),
                         "remote_bytes_out": b["decision"]["egress"]["remote_bytes_out"], "receipt_valid": v["valid"],
                         "p_error": (b.get("router") or {}).get("p_error")})
            print(f"{rows[-1]['department']:9s} {rows[-1]['route']:12s} {rows[-1]['reason']:24s} {rows[-1]['latency_ms']:8.0f} ms", flush=True)
    ok = [r for r in rows if r["status"] == 200]
    sens = [r for r in rows if r["data_class"] in ("Confidential", "Restricted", "Secret")]
    lat = [r["latency_ms"] for r in ok]
    out = {"run_id": run_id, "n": len(rows), "answered": len(ok), "denied": len(rows) - len(ok),
           "routes": {k: sum(1 for r in rows if r["route"] == k) for k in {r["route"] for r in rows}},
           "reasons": {k: sum(1 for r in rows if r["reason"] == k) for k in {r["reason"] for r in rows}},
           "local_coverage": sum(r["route"] in ("local", "local_large", "cache") for r in ok) / len(ok) if ok else None,
           "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95)},
           "latency_by_route": {k: {"p50": percentile([r["latency_ms"] for r in ok if r["route"] == k], 50), "n": sum(1 for r in ok if r["route"] == k)}
                                for k in {r["route"] for r in ok}},
           "receipts_valid": sum(r["receipt_valid"] for r in rows), "sensitive_requests": len(sens),
           "sensitive_remote_bytes": sum(r["remote_bytes_out"] or 0 for r in sens), "rows": rows,
           "demo_beats": json.loads(beats_path.read_text()) if beats_path.exists() else None}
    cost = {"run_id": run_id, "label": "Measured", "requests": len(ok),
            "prompt_tokens": sum(r["prompt_tokens"] or 0 for r in ok), "completion_tokens": sum(r["completion_tokens"] or 0 for r in ok),
            "actual_cost_usd": sum(r["cost_usd"] or 0 for r in ok), "counterfactual_usd": sum(r["counterfactual_usd"] or 0 for r in ok),
            "tokens_avoided_by_cache": sum(r["tokens_avoided"] or 0 for r in ok)}
    cost["avoided_usd"] = cost["counterfactual_usd"] - cost["actual_cost_usd"]
    write(d, "e2e_metrics.json", out)
    write(d, "cost_metrics.json", cost)
    print(json.dumps({k: out[k] for k in ("run_id", "n", "routes", "local_coverage", "latency_ms", "receipts_valid", "sensitive_remote_bytes")}, indent=1))


if __name__ == "__main__":
    main()

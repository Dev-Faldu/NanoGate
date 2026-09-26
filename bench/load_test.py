"""Load / performance test against a LIVE gateway with REAL local inference.

Concurrency levels (default 1,2,4,8). Each request is a distinct public-data question (MMLU),
so the verified cache cannot serve it. Streaming is used so TTFT is measured client-side.
NVML is sampled during every level (GPU util, power, temperature) and unified memory from
/proc/meminfo. Cold start = restart of the local vLLM server (server_start_ms) + first request.

Usage: python bench/load_test.py [--levels 1,2,4,8] [--per-level 16] [--max-tokens 128]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import threading
import time

import httpx

from common import ROOT, new_run, percentile, write

GW = "http://127.0.0.1:8080"


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_ev = threading.Event()
        self.rows: list[dict] = []

    def run(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            h = None
        while not self.stop_ev.is_set():
            r = {"ts": time.time()}
            if h is not None:
                import pynvml
                for k, fn in (("util", lambda: pynvml.nvmlDeviceGetUtilizationRates(h).gpu),
                              ("power_w", lambda: pynvml.nvmlDeviceGetPowerUsage(h) / 1000),
                              ("temp_c", lambda: pynvml.nvmlDeviceGetTemperature(h, 0))):
                    try:
                        r[k] = fn()
                    except Exception:
                        r[k] = None
            info = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
            r["mem_used_bytes"] = (info["MemTotal"] - info["MemAvailable"]) * 1024
            self.rows.append(r)
            self.stop_ev.wait(0.5)

    def summary(self) -> dict:
        def agg(k):
            v = [r[k] for r in self.rows if r.get(k) is not None]
            return {"mean": sum(v) / len(v), "max": max(v)} if v else None
        return {"samples": len(self.rows), "gpu_util_pct": agg("util"), "gpu_power_w": agg("power_w"),
                "gpu_temp_c": agg("temp_c"), "mem_used_bytes": agg("mem_used_bytes")}


def prompts(n: int, seed: int) -> list[str]:
    rows = [json.loads(l) for l in (ROOT / "datasets/raw/mmlu/mmlu_subset_test.jsonl").read_text().split("\n") if l.strip()]
    random.Random(seed).shuffle(rows)
    return [f"In at most three sentences: {r['question']}" for r in rows[:n]]


async def one(c: httpx.AsyncClient, key: str, prompt: str, max_tokens: int) -> dict:
    t0 = time.perf_counter()
    ttft = None
    usage, route, status = None, None, None
    try:
        async with c.stream("POST", "/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                            json={"model": "nanogate-auto", "stream": True, "max_tokens": max_tokens, "temperature": 0.2,
                                  "messages": [{"role": "user", "content": prompt}]}) as r:
            status = r.status_code
            async for line in r.aiter_lines():
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                d = json.loads(line[5:])
                if "error" in d:
                    status = d["error"]["code"]
                    continue
                if ttft is None and d.get("choices") and d["choices"][0]["delta"].get("content"):
                    ttft = (time.perf_counter() - t0) * 1000
                if d.get("usage"):
                    usage = d["usage"]
                    route = (d.get("nanogate") or {}).get("route")
    except Exception as e:
        status = f"exception:{type(e).__name__}"
    return {"ttft_ms": ttft, "latency_ms": (time.perf_counter() - t0) * 1000, "status": status, "route": route,
            "completion_tokens": (usage or {}).get("completion_tokens"), "prompt_tokens": (usage or {}).get("prompt_tokens")}


async def level(key: str, conc: int, ps: list[str], max_tokens: int) -> dict:
    sem = asyncio.Semaphore(conc)
    async with httpx.AsyncClient(base_url=GW, timeout=600) as c:
        async def guarded(p):
            async with sem:
                return await one(c, key, p, max_tokens)
        s = Sampler()
        s.start()
        t0 = time.perf_counter()
        res = await asyncio.gather(*(guarded(p) for p in ps))
        wall = time.perf_counter() - t0
        s.stop_ev.set()
        s.join()
    # success = answered (HTTP 200). A verified cache hit is a correct answer with no generation, so it counts as
    # success; generation metrics (TTFT, tokens/s, generated latency) come only from requests that generated tokens.
    ok = [r for r in res if r["status"] == 200]
    gen = [r for r in ok if r["completion_tokens"]]
    lat = [r["latency_ms"] for r in gen]
    ttft = [r["ttft_ms"] for r in gen if r["ttft_ms"] is not None]
    toks = sum(r["completion_tokens"] for r in gen)
    per_req_tps = [r["completion_tokens"] / ((r["latency_ms"] - (r["ttft_ms"] or 0)) / 1000) for r in gen
                   if r["latency_ms"] and r["ttft_ms"] and r["latency_ms"] > r["ttft_ms"]]
    return {"concurrency": conc, "requests": len(res), "ok": len(ok), "generated": len(gen), "cache_hits": len(ok) - len(gen),
            "error_rate": 1 - len(ok) / len(res),
            "errors": sorted({str(r["status"]) for r in res if r not in ok}),
            "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95), "mean": sum(lat) / len(lat) if lat else None,
                           "basis": "requests that generated tokens"},
            "ttft_ms": {"p50": percentile(ttft, 50), "p95": percentile(ttft, 95)},
            "throughput_rps": len(ok) / wall, "aggregate_tokens_per_s": toks / wall,
            "per_request_decode_tokens_per_s": {"p50": percentile(per_req_tps, 50)}, "wall_s": wall,
            "routes": {k: sum(1 for r in ok if r["route"] == k) for k in {r["route"] for r in ok}},
            "device": s.summary(), "raw": res}


async def cold_warm(key: str, model: str, max_tokens: int) -> dict:
    # vLLM cannot unload a model on request: cold start = restart the local-tier server, then the first request
    t0 = time.perf_counter()
    rs = subprocess.run([str(ROOT / "scripts/runtime.sh"), "restart", "local"], capture_output=True, text=True)
    if rs.returncode != 0:
        return {"available": False, "reason": f"cannot restart vLLM: {(rs.stderr or rs.stdout).strip()[-200:]}"}
    server_start_ms = (time.perf_counter() - t0) * 1000
    async with httpx.AsyncClient(base_url=GW, timeout=600) as c:
        for _ in range(120):   # wait for the gateway's health loop to see the model again
            if (await c.get("/healthz")).json()["components"]["model"]["ok"]:
                break
            await asyncio.sleep(1)
        cold = await one(c, key, "In one sentence, what is DNS? (cold)", max_tokens)
        warm = await one(c, key, "In one sentence, what is DHCP? (warm)", max_tokens)
    return {"available": True, "server_start_ms": server_start_ms, "cold": cold, "warm": warm}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--per-level", type=int, default=16)
    ap.add_argument("--max-tokens", type=int, default=128)
    args = ap.parse_args()
    keys = json.loads((ROOT / "var/dev_keys.json").read_text())["keys"]
    key = keys["app:acme/it"]["key"]
    st = httpx.get(f"{GW}/healthz", timeout=10).json()["components"]
    if not st["model"]["ok"]:
        raise SystemExit(f"model unavailable ({st['model'].get('reason')}); load test requires real inference")
    model = st["model"]["model"]
    run_id, d = new_run("load", vars(args), {"model": model})
    levels = [int(x) for x in args.levels.split(",")]
    ps = prompts(args.per_level * len(levels) + 10, seed=int(time.time()))
    cw = asyncio.run(cold_warm(key, model, args.max_tokens))
    out = {"run_id": run_id, "model": model, "cold_warm": cw, "levels": []}
    for i, conc in enumerate(levels):
        chunk = ps[i * args.per_level:(i + 1) * args.per_level]
        r = asyncio.run(level(key, conc, chunk, args.max_tokens))
        out["levels"].append(r)
        print(json.dumps({k: r[k] for k in ("concurrency", "ok", "error_rate", "latency_ms", "ttft_ms", "aggregate_tokens_per_s")}), flush=True)
    startup = ROOT / "var" / "startup.json"
    out["gateway_startup"] = json.loads(startup.read_text()) if startup.exists() else None
    reliable = [l["concurrency"] for l in out["levels"] if l["error_rate"] == 0]
    out["max_reliable_concurrency_tested"] = max(reliable) if reliable else None
    write(d, "load_metrics.json", out)
    print(json.dumps({"run_id": run_id, "max_reliable_concurrency_tested": out["max_reliable_concurrency_tested"]}))


if __name__ == "__main__":
    main()

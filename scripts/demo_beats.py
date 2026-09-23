"""The five-minute demo, executed for real through the OpenAI Python SDK.

Only base_url and api_key differ from a stock OpenAI client. Every beat prints what the system
actually decided (route, reason codes, tokens, latency, receipt) — outcomes are reported as
measured, whatever they are. Returns a JSON summary (also used by bench/evaluate_end_to_end.py).

Usage: python scripts/demo_beats.py [--base http://127.0.0.1:8080] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]

BEATS = [
    ("1 · Local intelligence", "app:acme/it", "nanogate-auto", "How do I reset the VPN client?", True),
    ("2 · Verified reuse", "app:acme/it", "nanogate-auto", "What steps restore my VPN connection?", False),
    ("3 · Cache security (hard negative)", "app:acme/it", "nanogate-auto", "How do I reset another employee's VPN password?", False),
    ("4 · Privacy (synthetic PII)", "app:acme/hr", "nanogate-auto",
     "Draft a two-sentence note confirming the leave balance for employee Maria Lopez (EMP-204981, "
     "maria.lopez@acme-corp.example, SSN 219-09-9999).", False),
]


def hard_public_question() -> str:
    rows = [json.loads(l) for l in (ROOT / "datasets/raw/mmlu/mmlu_subset_test.jsonl").read_text().split("\n") if l.strip()]
    q = next(r for r in rows if r["subject"] == "professional_law")   # first item, fixed (not cherry-picked by outcome)
    opts = "\n".join(f"{'ABCD'[i]}. {c}" for i, c in enumerate(q["choices"]))
    return (f"Answer the following multiple-choice question. Think briefly, then finish with a line 'Answer: <letter>'.\n\n"
            f"{q['question']}\n{opts}"), "ABCD"[q["answer"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    keys = json.loads((ROOT / "var/dev_keys.json").read_text())["keys"]
    admin = httpx.post(f"{args.base}/api/session", json={"api_key": keys["admin"]["key"]}, timeout=30).json()["token"]
    adm = lambda m, p, **kw: httpx.request(m, f"{args.base}{p}", headers={"Authorization": f"Bearer {admin}"}, timeout=120, **kw)
    results = []

    def show(title, raw, t0, streamed_text=None, extra=None):
        h = raw.headers if hasattr(raw, "headers") else raw
        rid = h.get("x-nanogate-receipt-id")
        rec = adm("GET", f"/api/receipts/{rid}").json() if rid else {}
        b = rec.get("body", {})
        ver = adm("POST", f"/api/receipts/{rid}/verify").json() if rid else {}
        row = {"beat": title, "route": b.get("decision", {}).get("chosen_route"), "reason": rec.get("reason"),
               "reason_codes": b.get("decision", {}).get("reason_codes"), "receipt_id": rid, "receipt_valid": ver.get("valid"),
               "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "ttft_ms": (b.get("model") or {}).get("ttft_ms"),
               "prompt_tokens": (b.get("model") or {}).get("prompt_tokens"), "completion_tokens": (b.get("model") or {}).get("completion_tokens"),
               "cache": {k: (b.get("cache") or {}).get(k) for k in ("decision", "similarity")} | {"verifier": ((b.get("cache") or {}).get("verifier") or {}).get("score"),
                                                                                                    "conflicts": ((b.get("cache") or {}).get("verifier") or {}).get("conflicts")},
               "router": {k: (b.get("router") or {}).get(k) for k in ("raw_score", "p_error", "threshold", "accept_local")},
               "data_class": (b.get("classification") or {}).get("data_class"),
               "remote_bytes_out": (b.get("decision", {}).get("egress") or {}).get("remote_bytes_out"),
               "tokens_avoided": (b.get("cache") or {}).get("tokens_avoided"), **(extra or {})}
        results.append(row)
        print(f"\n━━ {title}")
        for k in ("route", "reason", "reason_codes", "data_class", "latency_ms", "ttft_ms", "completion_tokens", "cache", "router",
                  "remote_bytes_out", "receipt_id", "receipt_valid"):
            print(f"   {k:18s} {row[k]}")
        if streamed_text is not None:
            print(f"   answer             {streamed_text[:160].strip()!r}")
        return row

    for title, keyname, model, prompt, stream in BEATS:
        client = OpenAI(base_url=f"{args.base}/v1", api_key=keys[keyname]["key"])
        t0 = time.perf_counter()
        if stream:
            text = ""
            with client.chat.completions.with_streaming_response.create(model=model, stream=True, temperature=0,
                                                                         messages=[{"role": "user", "content": prompt}]) as resp:
                for ch in resp.parse():
                    if ch.choices and ch.choices[0].delta.content:
                        text += ch.choices[0].delta.content
                headers = resp.headers
            show(title, headers, t0, text, {"streamed": True})
        else:
            raw = client.chat.completions.with_raw_response.create(model=model, temperature=0, messages=[{"role": "user", "content": prompt}])
            show(title, raw, t0, raw.parse().choices[0].message.content)

    q, gold = hard_public_question()
    client = OpenAI(base_url=f"{args.base}/v1", api_key=keys["app:acme/it"]["key"])
    t0 = time.perf_counter()
    raw = client.chat.completions.with_raw_response.create(model="nanogate-auto", temperature=0, messages=[{"role": "user", "content": q}])
    ans = raw.parse().choices[0].message.content
    import re
    m = re.findall(r"answer\s*[:：]?\s*[\*\(<\[]*([A-D])", ans, re.I)
    show("5 · Routing (hard public question, MMLU professional_law)", raw, t0, ans,
         {"gold": gold, "model_answer": m[-1].upper() if m else None, "correct": bool(m) and m[-1].upper() == gold})

    adm("POST", "/api/remote/mode", json={"mode": "outage-test"})
    try:
        t0 = time.perf_counter()
        raw = client.chat.completions.with_raw_response.create(model="nanogate-remote", temperature=0,
                                                               messages=[{"role": "user", "content": "In one sentence, what does a VPN do?"}])
        show("6 · Resilience (remote connector outage)", raw, t0, raw.parse().choices[0].message.content)
    finally:
        adm("POST", "/api/remote/mode", json={"mode": "disabled"})

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
    ok = all(r["receipt_valid"] for r in results)
    print(f"\nAll receipts verified: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

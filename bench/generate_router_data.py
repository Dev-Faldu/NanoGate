"""Generate router training/evaluation data by running REAL local inference on public datasets.

For each item: run the local tier (and optionally the local-large tier) through the same adapter
the gateway uses, extract router features, grade the answer against the dataset ground truth
(task rubric), and append to datasets/processed/router_runs/<tier>.jsonl. Resumable.

Sources: MMLU subset (MIT), GSM8K test (MIT), CISA KEV-derived lookup QA (public domain).
Usage: python bench/generate_router_data.py --tier local --n-mmlu 700 --n-gsm8k 400 --n-kev 400
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from nanogate import router_features as rf  # noqa: E402
from nanogate.inference import LocalModelAdapter  # noqa: E402
from nanogate.knowledge import KEVSource, rag_messages  # noqa: E402
from nanogate.settings import get_settings  # noqa: E402

RAW = ROOT / "datasets" / "raw"
OUT = ROOT / "datasets" / "processed" / "router_runs"
SEED = 20260923
LETTERS = "ABCD"


def mem_pressure() -> float | None:
    try:
        info = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        return 1.0 - info["MemAvailable"] / info["MemTotal"]
    except Exception:
        return None


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().split("\n") if l.strip()]


def build_items(n_mmlu: int, n_gsm: int, n_kev: int) -> list[dict]:
    rng = random.Random(SEED)
    items: list[dict] = []
    mmlu = load_jsonl(RAW / "mmlu" / "mmlu_subset_test.jsonl")
    rng.shuffle(mmlu)
    by_subj: dict[str, list] = {}
    for r in mmlu:
        by_subj.setdefault(r["subject"], []).append(r)
    per = max(1, n_mmlu // len(by_subj))          # stratified: equal items per subject
    picked = [r for subj in sorted(by_subj) for r in by_subj[subj][:per]]
    for r in picked:
        opts = "\n".join(f"{LETTERS[i]}. {c}" for i, c in enumerate(r["choices"]))
        prompt = (f"Answer the following multiple-choice question. Think briefly, then finish with a line "
                  f"'Answer: <letter>'.\n\n{r['question']}\n{opts}")
        items.append({"id": r["id"], "group": r["id"], "source": "mmlu", "subject": r["subject"],
                      "prompt": prompt, "gold": LETTERS[r["answer"]], "kind": "mcq"})
    gsm = load_jsonl(RAW / "gsm8k" / "gsm8k_test.jsonl")
    rng.shuffle(gsm)
    for r in gsm[:n_gsm]:
        prompt = (f"Solve the problem. Show brief reasoning, then finish with a line 'Answer: <number>'.\n\n"
                  f"{r['question']}")
        items.append({"id": r["id"], "group": r["id"], "source": "gsm8k", "prompt": prompt,
                      "gold": r["answer"], "kind": "number"})
    kev = json.loads((RAW / "cisa_kev" / "known_exploited_vulnerabilities.json").read_text())["vulnerabilities"]
    rng.shuffle(kev)
    templates = [
        ("vendor", "Which vendor or project makes the product affected by {cveID}, according to CISA KEV?", "vendorProject"),
        ("product", "What product is affected by {cveID} in the CISA Known Exploited Vulnerabilities catalog?", "product"),
        ("date", "On what date was {cveID} added to the CISA KEV catalog? Give the date as YYYY-MM-DD.", "dateAdded"),
        ("cve_from_desc", "Which CVE ID in CISA KEV corresponds to this vulnerability: \"{vulnerabilityName}\"?", "cveID"),
    ]
    for i, r in enumerate(kev[:n_kev]):
        tname, tpl, field = templates[i % len(templates)]
        items.append({"id": f"kev/{r['cveID']}/{tname}", "group": f"kev/{r['cveID']}", "source": "cisa_kev",
                      "prompt": tpl.format(**r), "gold": r[field], "kind": "kev_" + tname})
    return items


def grade(item: dict, text: str) -> bool:
    kind, gold = item["kind"], str(item["gold"])
    if kind == "mcq":
        m = re.findall(r"answer\s*(?:is)?\s*[:：]?\s*[\*\(<\[]*([A-D])[\)>\]\*]*(?![A-Za-z])", text, re.I)
        if not m:
            m = re.findall(r"\b([A-D])[\).]", text)
        return bool(m) and m[-1].upper() == gold
    if kind == "number":
        m = re.findall(r"answer\s*[:：]?\s*\$?\s*(-?[\d,]*\.?\d+)", text, re.I) or re.findall(r"-?[\d,]*\.?\d+", text)
        if not m:
            return False
        try:
            return abs(float(m[-1].replace(",", "")) - float(gold)) < 1e-6
        except ValueError:
            return False
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    if kind == "kev_date":
        return gold in text
    return norm(gold) in norm(text)


async def run(args) -> None:
    s = get_settings()
    model = s.local_model_name if args.tier == "local" else s.local_large_model_name
    base = s.local_model_base_url if args.tier == "local" else s.local_large_model_base_url
    adapter = LocalModelAdapter(base, model, s.local_model_api_key, s.local_model_family,
                                tier=args.tier, timeout_s=300, max_concurrency=args.concurrency)
    kev = KEVSource()
    kev.load()
    items = build_items(args.n_mmlu, args.n_gsm8k, args.n_kev)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{args.tier}.jsonl"
    done: set[str] = set()
    if out.exists():
        for l in out.read_text().split("\n"):
            try:
                row = json.loads(l)
            except ValueError:
                continue  # tolerate a line truncated by an interrupted run
            if row.get("model") != model:
                # resuming would mix two models' answers (and logprob features) in one training set
                raise SystemExit(f"{out} holds rows from model '{row.get('model')}', not '{model}': "
                                 f"move it to {OUT / 'archive'} and rerun")
            done.add(row["id"])
    if args.only_split:
        # same SHA-256 grouping as train_router.py; used for the local-large baseline on the test split only
        import hashlib as _h

        def _split(g):
            h = int(_h.sha256(g.encode()).hexdigest(), 16) % 100
            return "train" if h < 60 else "calibration" if h < 80 else "test"
        items = [it for it in items if _split(it["group"]) == args.only_split]
    todo = [it for it in items if it["id"] not in done]
    print(f"tier={args.tier} model={model} items={len(items)} todo={len(todo)}", flush=True)
    lock = asyncio.Lock()
    t_start = time.time()
    n_done = 0
    n_err = 0

    async def one(it: dict) -> None:
        nonlocal n_done, n_err
        messages = [{"role": "user", "content": it["prompt"]}]
        retrieval = None
        if it["source"] == "cisa_kev":
            retrieval = kev.retrieve(it["prompt"])
            messages = rag_messages(messages, retrieval)
        params = {"temperature": 0.0, "max_tokens": 512, "seed": 7}
        try:
            res = await adapter.generate(messages, params)
        except Exception as e:
            print(f"  error {it['id']}: {e}", flush=True)
            n_err += 1
            return
        feats = rf.extract(messages, params, res,
                           retrieval={k: retrieval.get(k) for k in ("top_sim", "margin", "coverage", "verifier", "sources", "age_days", "mismatch")} if retrieval else None,
                           system={"queue_depth": float(adapter.queue_depth), "mem_pressure": mem_pressure()},
                           data_class="Public")
        ok = grade(it, res.text)
        row = {"id": it["id"], "group": it["group"], "source": it["source"], "kind": it["kind"],
               "tier": args.tier, "model": model, "correct": ok, "y_error": 0 if ok else 1, "features": feats,
               "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
               "ttft_ms": res.ttft_ms, "total_ms": res.total_ms, "finish_reason": res.finish_reason,
               "output": res.text, "output_sha256": hashlib.sha256(res.text.encode()).hexdigest(),
               "gold": it["gold"], "ts": time.time(),
               "raw": {"tokens": res.tokens, "token_logprobs": res.token_logprobs, "top_logprobs": res.top_logprobs}}
        async with lock:
            with out.open("a") as f:
                f.write(json.dumps(row) + "\n")
            n_done += 1
            if n_done % 25 == 0:
                rate = n_done / (time.time() - t_start)
                print(f"  {n_done}/{len(todo)}  {rate:.2f} items/s", flush=True)

    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(it):
        async with sem:
            await one(it)

    await asyncio.gather(*(guarded(it) for it in todo))
    await adapter.aclose()
    print(f"done ({n_done} written, {n_err} errors)", flush=True)
    if n_err > 0.02 * max(1, len(todo)):
        # missing rows would silently shrink and bias the training set: fail so the pipeline step is retried
        raise SystemExit(f"{n_err} of {len(todo)} generations failed; rerun to resume")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["local", "local_large"], default="local")
    ap.add_argument("--n-mmlu", type=int, default=700)
    ap.add_argument("--n-gsm8k", type=int, default=400)
    ap.add_argument("--n-kev", type=int, default=400)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--only-split", choices=["train", "calibration", "test"], default=None,
                    help="restrict to one split (same SHA-256 grouping as train_router.py); used for the large-tier test baseline")
    asyncio.run(run(ap.parse_args()))

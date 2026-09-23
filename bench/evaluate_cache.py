"""Cache benchmark: no cache vs exact vs cosine semantic vs verified semantic.

Data: authored enterprise groups (datasets/authored/cache_groups.yaml) split BY GROUP into
validation (threshold selection) and test (reported once). PAWS (public) is a pair-level
adversarial stress test. The verified strategy on the test split runs through the real
SemanticCache class against a temporary database, so it exercises serving code.

Usage: python bench/evaluate_cache.py [--write-thresholds] [--paws-n 1000]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import tempfile
import time
from pathlib import Path

import numpy as np
import yaml

from common import ROOT, new_run, percentile, write

from nanogate.cache import SemanticCache, Thresholds, namespace_fields  # noqa: E402
from nanogate.db import Database  # noqa: E402
from nanogate.embeddings import embed  # noqa: E402
from nanogate.services import hf_revision  # noqa: E402
from nanogate.settings import get_settings  # noqa: E402
from nanogate.verifier import Verifier, slot_conflicts, slots  # noqa: E402

GROUPS = ROOT / "datasets" / "authored" / "cache_groups.yaml"


def split_of(gid: str) -> str:
    return "validation" if int(hashlib.sha256(gid.encode()).hexdigest(), 16) % 2 == 0 else "test"


def build(groups: list[dict], unrelated: list[str], split: str):
    canon = [g for g in groups if split_of(g["id"]) == split]
    queries = []
    for g in canon:
        for p in g["paraphrases"]:
            queries.append({"text": p, "target": g["id"], "kind": "paraphrase", "group": g["id"]})
        for h in g["hard_negatives"]:
            queries.append({"text": h["text"], "target": None, "kind": f"hard_negative:{h['type']}", "group": g["id"]})
    for i, u in enumerate(unrelated):
        if (i % 2 == 0) == (split == "validation"):
            queries.append({"text": u, "target": None, "kind": "unrelated", "group": None})
    return canon, queries


def score_queries(canon, queries, verifier: Verifier, k: int = 3):
    """Per query: top-k canonical candidates with cosine, NLI score and slot conflicts (threshold-free)."""
    cv = embed([c["canonical"] for c in canon])
    qv = embed([q["text"] for q in queries])
    sims = qv @ cv.T
    pairs, index = [], []
    for qi, q in enumerate(queries):
        order = np.argsort(-sims[qi])[:k]
        for ci in order:
            pairs.append((q["text"], canon[ci]["canonical"]))
            index.append((qi, int(ci), float(sims[qi, ci])))
    verdicts = verifier.score_many(pairs)
    for q in queries:
        q["cands"] = []
    for (qi, ci, s), v in zip(index, verdicts):
        queries[qi]["cands"].append({"cid": canon[ci]["id"], "sim": s, "vscore": v.score, "conflicts": v.conflicts})
    return queries


def simulate(queries, tr: float, tv: float | None) -> dict:
    """tv=None -> cosine-only strategy."""
    hits = correct = false_hits = 0
    hn = hn_rej = 0
    should_hit = sum(1 for q in queries if q["target"])
    should_miss = len(queries) - should_hit
    for q in queries:
        served = None
        for c in q["cands"]:
            if c["sim"] < tr:
                continue
            if tv is None or (c["vscore"] >= tv and not c["conflicts"]):
                served = c["cid"]
                break
        if q["kind"].startswith("hard_negative"):
            hn += 1
            hn_rej += served is None
        if served is not None:
            hits += 1
            if served == q["target"]:
                correct += 1
            else:
                false_hits += 1
    return {"queries": len(queries), "hits": hits, "hit_rate": hits / len(queries), "correct_hits": correct,
            "precision": (correct / hits) if hits else None, "recall_of_equivalent": correct / should_hit if should_hit else None,
            "false_hits": false_hits, "false_hit_rate": false_hits / max(1, should_miss),
            "hard_negatives": hn, "hard_negative_rejection": hn_rej / hn if hn else None}


def select_thresholds(val):
    best_cos, best_ver = None, None
    for tr in np.arange(0.60, 0.97, 0.01):
        m = simulate(val, float(tr), None)
        if m["false_hits"] == 0 and (best_cos is None or m["correct_hits"] > best_cos[1]["correct_hits"]):
            best_cos = (float(tr), m)
        for tv in np.arange(0.05, 0.96, 0.05):
            mv = simulate(val, float(tr), float(tv))
            key = (mv["correct_hits"], -abs(tv - 0.5), -tr)
            if mv["false_hits"] == 0 and (best_ver is None or key > best_ver[2]):
                best_ver = (float(tr), float(tv), key, mv)
    return best_cos, best_ver


def real_cache_eval(canon, queries, th: Thresholds, verifier: Verifier) -> tuple[dict, list[float], dict]:
    """Run the actual SemanticCache on a temp DB: store canonicals, look up every query, probe isolation."""
    tmp = Path(tempfile.mkdtemp(prefix="ng-cachebench-"))
    db = Database(tmp / "bench.db")
    db.migrate()
    s = get_settings()
    cache = SemanticCache(db, verifier, th, embed, hf_revision(s.embedding_model), hf_revision(s.verifier_model))
    ns_a = namespace_fields("acme", "it", "it-v1", "qwen2.5", "sys0", "ctx0")
    id_map = {}
    for c in canon:
        cid = cache.store(c["canonical"], f"ANSWER::{c['id']}", ns_a, "Internal", 3600, "bench", 20, 40, None, None, "bench")
        id_map[cid] = c["id"]
    lat, hits, correct, false_hits = [], 0, 0, 0
    decisions: dict[str, int] = {}
    for q in queries:
        t0 = time.perf_counter()
        r = cache.lookup(q["text"], ns_a)
        lat.append((time.perf_counter() - t0) * 1000)
        decisions[r.decision] = decisions.get(r.decision, 0) + 1
        q["served_real"] = id_map.get(r.entry["cache_id"]) if r.hit else None
        q["decision_real"] = r.decision
        if r.hit:
            hits += 1
            correct += q["served_real"] == q["target"]
            false_hits += q["served_real"] != q["target"]
    # isolation probes: identical canonical text from other tenants/departments/policies/contexts
    probes = {"cross_tenant": namespace_fields("globex", "it", "it-v1", "qwen2.5", "sys0", "ctx0"),
              "cross_department": namespace_fields("acme", "sales", "sales-v1", "qwen2.5", "sys0", "ctx0"),
              "stale_policy": namespace_fields("acme", "it", "it-v2", "qwen2.5", "sys0", "ctx0"),
              "context_mismatch": namespace_fields("acme", "it", "it-v1", "qwen2.5", "sys0", "ctx-other"),
              "system_prompt_mismatch": namespace_fields("acme", "it", "it-v1", "qwen2.5", "sys-other", "ctx0")}
    iso = {}
    for name, ns in probes.items():
        leaks = sum(cache.lookup(c["canonical"], ns).hit for c in canon)
        iso[name] = {"probes": len(canon), "leaks": leaks}
    # source revocation + staleness
    cache.set_source_version("kb", "v1")
    cid = cache.store("How do I rotate the VPN certificate?", "ANSWER::src", ns_a, "Internal", 3600, "bench", 1, 1, "kb", "v1", "bench")
    src_ok = cache.lookup("How do I rotate the VPN certificate?", ns_a).hit
    cache.set_source_version("kb", "v2")
    stale_rejected = not cache.lookup("How do I rotate the VPN certificate?", ns_a).hit
    cache.set_source_version("kb", "v1")
    cache.revoke_source("kb")
    revoked_rejected = not cache.lookup("How do I rotate the VPN certificate?", ns_a).hit
    ttl_rejected = not cache.lookup(canon[0]["canonical"], ns_a, now=time.time() + 7200).hit
    n = len(queries)
    should_miss = sum(1 for q in queries if not q["target"])
    hn = [q for q in queries if q["kind"].startswith("hard_negative")]
    metrics = {"queries": n, "hits": hits, "hit_rate": hits / n, "precision": correct / hits if hits else None,
               "false_hit_rate": false_hits / max(1, should_miss), "correct_hits": correct, "false_hits": false_hits,
               "hard_negatives": len(hn), "hard_negative_rejection": sum(q["served_real"] is None for q in hn) / len(hn) if hn else None,
               "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95), "decisions": decisions}
    checks = {"cross_tenant_leaks": iso["cross_tenant"]["leaks"], "probes": sum(v["probes"] for v in iso.values()),
              "detail": iso, "source_hit_before_change": src_ok, "stale_source_rejected": stale_rejected,
              "revoked_source_rejected": revoked_rejected, "ttl_expired_rejected": ttl_rejected}
    return metrics, lat, checks


def paws_stress(n: int, tr: float, tv: float, verifier: Verifier) -> dict:
    rows = [json.loads(l) for l in (ROOT / "datasets/raw/paws/paws_labeled_final_test.jsonl").read_text().split("\n") if l.strip()]
    random.Random(7).shuffle(rows)
    rows = rows[:n]
    a = embed([r["a"] for r in rows])
    b = embed([r["b"] for r in rows])
    sims = (a * b).sum(1)
    v = verifier.score_many([(r["a"], r["b"]) for r in rows])
    y = np.array([r["label"] for r in rows])
    cos_acc = sims >= tr
    ver_acc = cos_acc & np.array([x.score >= tv and not x.conflicts for x in v])
    nli_only = cos_acc & np.array([x.score >= tv for x in v])

    def rates(acc):
        return {"false_accept_rate": float(acc[y == 0].mean()), "true_accept_rate": float(acc[y == 1].mean()),
                "precision": float(y[acc].mean()) if acc.any() else None}
    return {"n": n, "positives": int(y.sum()), "negatives": int((y == 0).sum()), "thresholds": {"retrieval": tr, "verifier": tv},
            "cosine_only": rates(cos_acc), "verified_nli_only": rates(nli_only), "verified_nli_plus_slots": rates(ver_acc),
            "note": "PAWS pairs are adversarial word-order/word-swap paraphrases of Wikipedia sentences (not enterprise questions)."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-thresholds", action="store_true")
    ap.add_argument("--paws-n", type=int, default=1000)
    args = ap.parse_args()
    doc = yaml.safe_load(GROUPS.read_text())
    groups, unrelated = doc["groups"], doc["unrelated"]
    run_id, d = new_run("cache", vars(args), {"authored_set_sha256": hashlib.sha256(GROUPS.read_bytes()).hexdigest()})
    verifier = Verifier()
    cv, qv = build(groups, unrelated, "validation")
    ct, qt = build(groups, unrelated, "test")
    score_queries(cv, qv, verifier)
    score_queries(ct, qt, verifier)
    best_cos, best_ver = select_thresholds(qv)
    tr_c = best_cos[0] if best_cos else 0.95
    tr_v, tv_v = (best_ver[0], best_ver[1]) if best_ver else (0.8, 0.5)
    th = Thresholds(tr_v, tv_v, f"validation-selected (run {run_id})", run_id)
    verifier.threshold = tv_v
    norm = lambda s: re.sub(r"\W+", " ", s.lower()).strip()
    exact_hits = sum(1 for q in qt if any(norm(q["text"]) == norm(c["canonical"]) for c in ct))
    real, lat, checks = real_cache_eval(ct, qt, th, verifier)
    methods = {
        "no_cache": {"hit_rate": 0.0, "precision": None, "false_hit_rate": 0.0, "hard_negative_rejection": 1.0, "p50_ms": None, "p95_ms": None},
        "exact": {"hit_rate": exact_hits / len(qt), "precision": 1.0 if exact_hits else None, "false_hit_rate": 0.0,
                  "hard_negative_rejection": 1.0, "p50_ms": None, "p95_ms": None},
        "cosine_semantic": {**simulate(qt, tr_c, None), "threshold": tr_c, "p50_ms": None, "p95_ms": None},
        "verified_semantic": {**real, "thresholds": {"retrieval": tr_v, "verifier": tv_v}},
    }
    # scatter data: best candidate per test query
    pts = []
    for q in qt:
        c = q["cands"][0]
        region = "accepted" if q["served_real"] else ("hard_negative" if c["sim"] >= tr_v else "rejected")
        pts.append({"a": q["text"], "b": next(x["canonical"] for x in ct if x["id"] == c["cid"]), "similarity": round(c["sim"], 4),
                    "verifier": round(c["vscore"], 4), "region": region, "kind": q["kind"], "conflicts": c["conflicts"],
                    "correct": (q["served_real"] == q["target"]) if q["served_real"] else (q["target"] is None)})
    by_type = {}
    for q in qt:
        if q["kind"].startswith("hard_negative"):
            t = q["kind"].split(":")[1]
            b = by_type.setdefault(t, [0, 0])
            b[0] += 1
            b[1] += q["served_real"] is None
    paws = paws_stress(args.paws_n, tr_v, tv_v, verifier) if args.paws_n else None
    out = {"run_id": run_id, "n_groups": {"validation": len(cv), "test": len(ct)}, "n_queries": len(qt),
           "n_validation_queries": len(qv), "validation": {"cosine": best_cos[1] if best_cos else None, "verified": best_ver[3] if best_ver else None,
                                                           "selection_rule": "maximize correct hits subject to zero false hits on validation"},
           "methods": methods, "pairs": pts, "hard_negative_rejection_by_type": {k: {"n": v[0], "rejected": v[1]} for k, v in by_type.items()},
           "isolation": checks, "paws": paws, "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95), "n": len(lat)},
           "embedding_model": hf_revision(get_settings().embedding_model), "verifier_model": hf_revision(get_settings().verifier_model),
           "device": __import__("nanogate.embeddings", fromlist=["device"]).device(),
           "data_label": "Authored evaluation set (enterprise cache groups) + Public dataset (PAWS)"}
    write(d, "cache_metrics.json", out)
    if args.write_thresholds:
        p = ROOT / "artifacts" / "cache" / "thresholds.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"retrieval": tr_v, "verifier": tv_v, "source": f"validation-selected (run {run_id})", "run_id": run_id,
                                 "selection_rule": out["validation"]["selection_rule"]}, indent=2))
    v = methods["verified_semantic"]
    print(json.dumps({"run_id": run_id, "thresholds": {"retrieval": tr_v, "verifier": tv_v, "cosine_only": tr_c},
                      "verified_test": {k: v[k] for k in ("hit_rate", "precision", "false_hit_rate", "hard_negative_rejection", "p50_ms", "p95_ms")},
                      "cosine_test": {k: methods["cosine_semantic"][k] for k in ("hit_rate", "precision", "false_hit_rate", "hard_negative_rejection")},
                      "isolation": {k: checks[k] for k in ("cross_tenant_leaks", "stale_source_rejected", "revoked_source_rejected", "ttl_expired_rejected")},
                      "paws": paws and {k: paws[k] for k in ("cosine_only", "verified_nli_plus_slots")}}, indent=1, default=str))


if __name__ == "__main__":
    main()

"""DLP benchmark: regex baseline vs Presidio baseline vs layered NanoGate detector.

Data: datasets/synthetic/dlp_eval.jsonl (Synthetic security evaluation data + public MMLU negatives).
Span-level recall per category; precision over non-PERSON findings; document-level sensitive/secret
detection; per-document latency. The detector rules and the synthetic templates were written by the
same team: treat results as a regression benchmark, not an estimate of real-world recall.
"""
from __future__ import annotations

import json
import time

from common import ROOT, new_run, percentile, write

from nanogate.dlp import SECRET_TYPES, LayeredDLP  # noqa: E402

DATA = ROOT / "datasets" / "synthetic" / "dlp_eval.jsonl"
CAT = {"EMAIL_ADDRESS": "EMAIL", "PHONE_NUMBER": "PHONE", "US_SSN": "SSN", "CREDIT_CARD": "CARD", "EMPLOYEE_ID": "EMPLOYEE_ID",
       "IP_ADDRESS": "IP", **{t: "SECRET" for t in SECRET_TYPES}}


def overlaps(a, b) -> bool:
    return not (a["end"] <= b["start"] or a["start"] >= b["end"])


def evaluate(det: LayeredDLP, rows: list[dict]) -> dict:
    per_cat: dict[str, dict] = {}
    tp_find = fp_find = 0
    lat = []
    doc = {"sens_tp": 0, "sens_fn": 0, "sens_fp": 0, "sens_tn": 0, "sec_tp": 0, "sec_fn": 0, "sec_fp": 0, "sec_tn": 0}
    fp_examples = []
    for r in rows:
        t0 = time.perf_counter()
        res = det.scan(r["text"])
        lat.append((time.perf_counter() - t0) * 1000)
        finds = [{"start": f.start, "end": f.end, "category": CAT.get(f.type)} for f in res.findings if f.type != "PERSON"]
        for s in r["spans"]:
            c = per_cat.setdefault(s["category"], {"n": 0, "detected": 0})
            c["n"] += 1
            c["detected"] += any(f["category"] == s["category"] and overlaps(f, s) for f in finds)
        for f in finds:
            if any(f["category"] == s["category"] and overlaps(f, s) for s in r["spans"]):
                tp_find += 1
            else:
                fp_find += 1
                if len(fp_examples) < 8:
                    fp_examples.append({"id": r["id"], "category": f["category"], "kind": r["kind"]})
        truth_sens = bool(r["spans"])
        pred_sens = bool(finds)
        doc["sens_tp" if truth_sens and pred_sens else "sens_fn" if truth_sens else "sens_fp" if pred_sens else "sens_tn"] += 1
        truth_sec = any(s["category"] == "SECRET" for s in r["spans"])
        pred_sec = res.has_secret
        doc["sec_tp" if truth_sec and pred_sec else "sec_fn" if truth_sec else "sec_fp" if pred_sec else "sec_tn"] += 1
    n_spans = sum(c["n"] for c in per_cat.values())
    det_spans = sum(c["detected"] for c in per_cat.values())
    recall = det_spans / n_spans if n_spans else None
    precision = tp_find / (tp_find + fp_find) if (tp_find + fp_find) else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    negs = doc["sens_fp"] + doc["sens_tn"]
    return {
        "span_recall": recall, "finding_precision": precision, "f1": f1,
        "per_category": {k: {**v, "recall": v["detected"] / v["n"]} for k, v in sorted(per_cat.items())},
        "doc_sensitive_recall": doc["sens_tp"] / max(1, doc["sens_tp"] + doc["sens_fn"]),
        "doc_false_positive_rate": doc["sens_fp"] / max(1, negs),
        "secret_recall": doc["sec_tp"] / max(1, doc["sec_tp"] + doc["sec_fn"]),
        "secret_false_positive_rate": doc["sec_fp"] / max(1, doc["sec_fp"] + doc["sec_tn"]),
        "confusion": doc, "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95)},
        "false_positive_examples": fp_examples,
    }


def main():
    if not DATA.exists():
        raise SystemExit("run scripts/generate_synthetic_security.py first")
    rows = [json.loads(l) for l in DATA.read_text().split("\n") if l.strip()]
    run_id, d = new_run("dlp", {}, {"dlp_eval_rows": len(rows)})
    detectors = {
        "regex_baseline": LayeredDLP(use_presidio=False, use_secrets=False, use_deobfuscation=False),
        "presidio_baseline": LayeredDLP(use_presidio=True, use_regex=False, use_secrets=False, use_deobfuscation=False),
        "layered_nanogate": LayeredDLP(),
    }
    out = {"run_id": run_id, "n_rows": len(rows), "n_positive": sum(bool(r["spans"]) for r in rows),
           "n_spans": sum(len(r["spans"]) for r in rows), "data_label": "Synthetic security evaluation data + Public dataset negatives (MMLU)",
           "caveat": "Detector rules and synthetic templates were written by the same team; this is a regression benchmark.",
           "detectors": {}}
    for name, det in detectors.items():
        det.load()
        out["detectors"][name] = evaluate(det, rows)
    write(d, "dlp_metrics.json", out)
    print(json.dumps({"run_id": run_id, **{k: {m: v[m] for m in ("span_recall", "finding_precision", "f1", "doc_false_positive_rate",
                                                                   "secret_recall", "secret_false_positive_rate")} | {"p50_ms": v["latency_ms"]["p50"]}
                                             for k, v in out["detectors"].items()}}, indent=1))


if __name__ == "__main__":
    main()

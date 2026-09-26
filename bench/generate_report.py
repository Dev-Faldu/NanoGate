"""Assemble RESULTS.md + summary.json from the latest benchmark artifacts.

Only numbers present in results/<run>/ artifacts are reported as Measured. Missing benchmarks are
listed as Unavailable with the reason. Targets are listed separately and never promoted to results.
"""
from __future__ import annotations

import json
from pathlib import Path

from common import RESULTS, ROOT, new_run, write


def latest(fname: str) -> tuple[dict | None, str | None]:
    for p in sorted(RESULTS.glob("*"), reverse=True):
        f = p / fname
        if f.exists() and not (p / "INVALID.json").exists():   # see validate_run.py
            return json.loads(f.read_text()), p.name
    return None, None


def pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def num(v, d=3):
    return "—" if v is None else f"{v:.{d}f}"


def ms(v):
    return "—" if v is None else (f"{v / 1000:.2f} s" if v >= 1000 else f"{v:.0f} ms")


def main():
    run_id, d = new_run("report", {})
    rows: list[tuple[str, str, str, str, str]] = []
    missing: list[str] = []

    def add(metric, value, n, rid, status="Measured"):
        rows.append((metric, value, str(n), rid or "—", status))

    router, rr = latest("router_metrics.json")
    if router:
        m = router["methods"]["calibrated_lr"]
        base = router["baselines"]
        tag = "" if router.get("data_status", "complete") == "complete" else f" ({router['data_status']})"
        add("Local-only accuracy (task rubric)" + tag, pct(base["local_only_accuracy"]), router["n_test"], rr)
        add("Larger-tier-only accuracy" + tag, pct(base["large_only_accuracy"]) if base.get("large_only_accuracy") is not None else "Unavailable",
            router["n_test"], rr, "Measured" if base.get("large_only_accuracy") is not None else "Unavailable")
        for name, label in (("calibrated_lr", "Calibrated logistic router"), ("uncalibrated_lr", "Uncalibrated logistic"),
                            ("raw_logprob", "Raw log-prob threshold"), ("calibrated_hgb", "Calibrated gradient boosting")):
            mm = router["methods"][name]
            add(f"{label}: AUROC / ECE / Brier / AURC" + tag, f"{num(mm['auroc'])} / {num(mm['ece'])} / {num(mm['brier'])} / {num(mm['aurc'])}", router["n_test"], rr)
        add("Router local coverage at validation threshold" + tag, pct(m["coverage"]), router["n_test"], rr)
        add("Router selective accuracy at threshold" + tag, pct(m["selective_accuracy"]), router["n_test"], rr)
        for p in router.get("cost_quality", []):
            add(f"Cost–quality: {p['label']}" + tag, f"acc {pct(p['quality'])}, {num(p['compute_s'], 2)} s/request", p.get("n", router["n_test"]), rr,
                "Measured" if p["kind"] == "measured" else "Oracle bound")
    else:
        missing.append("Router benchmark (needs router data from real local inference)")

    cache, cr = latest("cache_metrics.json")
    if cache:
        for k, label in (("cosine_semantic", "Cosine-only semantic cache"), ("verified_semantic", "Verified semantic cache")):
            mm = cache["methods"][k]
            add(f"{label}: hit rate / precision / false-hit rate", f"{pct(mm['hit_rate'])} / {pct(mm['precision'])} / {pct(mm['false_hit_rate'])}", cache["n_queries"], cr)
            add(f"{label}: hard-negative rejection", pct(mm["hard_negative_rejection"]), mm.get("hard_negatives", "—"), cr)
        add("Verified cache lookup latency P50 / P95 (" + cache.get("device", "?") + ")", f"{ms(cache['latency_ms']['p50'])} / {ms(cache['latency_ms']['p95'])}", cache["latency_ms"]["n"], cr)
        iso = cache["isolation"]
        add("Cross-tenant cache leaks", str(iso["cross_tenant_leaks"]), iso["detail"]["cross_tenant"]["probes"], cr)
        add("Namespace isolation leaks (tenant/dept/policy/context/system)", str(sum(v["leaks"] for v in iso["detail"].values())), iso["probes"], cr)
        add("Stale source / revoked source / TTL rejected", f"{iso['stale_source_rejected']} / {iso['revoked_source_rejected']} / {iso['ttl_expired_rejected']}", 3, cr)
        if cache.get("paws"):
            pw = cache["paws"]
            add("PAWS false-accept rate: cosine-only vs verified", f"{pct(pw['cosine_only']['false_accept_rate'])} vs {pct(pw['verified_nli_plus_slots']['false_accept_rate'])}", pw["negatives"], cr)
            add("PAWS true-accept rate: cosine-only vs verified", f"{pct(pw['cosine_only']['true_accept_rate'])} vs {pct(pw['verified_nli_plus_slots']['true_accept_rate'])}", pw["positives"], cr)
    else:
        missing.append("Cache benchmark")

    dlp, dr = latest("dlp_metrics.json")
    if dlp:
        for k, label in (("regex_baseline", "Regex baseline"), ("presidio_baseline", "Presidio baseline"), ("layered_nanogate", "Layered NanoGate DLP")):
            mm = dlp["detectors"][k]
            add(f"DLP {label}: span recall / precision / F1", f"{pct(mm['span_recall'])} / {pct(mm['finding_precision'])} / {num(mm['f1'])}", dlp["n_spans"], dr)
        mm = dlp["detectors"]["layered_nanogate"]
        add("DLP layered: secret recall / document false-positive rate", f"{pct(mm['secret_recall'])} / {pct(mm['doc_false_positive_rate'])}", dlp["n_rows"], dr)
    else:
        missing.append("DLP benchmark")

    sec, sr = latest("security_results.json")
    if sec:
        add("Security attack suite: pass / fail / skipped", f"{sec['pass']} / {sec['fail']} / {sec['skipped']}", sec["total"], sr)
    else:
        missing.append("Security attack suite")

    load, lr_ = latest("load_metrics.json")
    if load:
        for l in load["levels"]:
            add(f"Load c={l['concurrency']}: latency P50/P95, TTFT P50, agg tok/s, errors",
                f"{ms(l['latency_ms']['p50'])} / {ms(l['latency_ms']['p95'])}, {ms(l['ttft_ms']['p50'])}, {num(l['aggregate_tokens_per_s'], 1)}, {pct(l['error_rate'])}",
                l["requests"], lr_)
        add("Max reliable concurrency tested (0 errors)", str(load["max_reliable_concurrency_tested"]), len(load["levels"]), lr_)
        cw = load.get("cold_warm") or {}
        if cw.get("available"):
            add("Cold vs warm TTFT", f"{ms(cw['cold']['ttft_ms'])} vs {ms(cw['warm']['ttft_ms'])}", 2, lr_)
            if cw.get("server_start_ms") is not None:
                add("Model server cold start (vLLM restart until serving)", ms(cw["server_start_ms"]), 1, lr_)
    else:
        missing.append("Load test (needs real local inference)")

    e2e, er = latest("e2e_metrics.json")
    if e2e:
        add("E2E local coverage", pct(e2e["local_coverage"]), e2e["answered"], er)
        add("E2E latency P50 / P95", f"{ms(e2e['latency_ms']['p50'])} / {ms(e2e['latency_ms']['p95'])}", e2e["answered"], er)
        add("E2E receipts verified", f"{e2e['receipts_valid']}/{e2e['n']}", e2e["n"], er)
        add("E2E remote bytes on sensitive requests (app-layer meter)", str(e2e["sensitive_remote_bytes"]), e2e["sensitive_requests"], er)
    else:
        missing.append("End-to-end evaluation (needs real local inference)")
    cost, cor = latest("cost_metrics.json")
    if cost:
        add("Measured cost (configured rates × metered tokens)", f"${cost['actual_cost_usd']:.6f}", cost["requests"], cor)
        add("Same tokens at reference hosted rate (counterfactual)", f"${cost['counterfactual_usd']:.6f}", cost["requests"], cor)
        add("Tokens avoided by verified cache", str(cost["tokens_avoided_by_cache"]), cost["requests"], cor)
    offline = ROOT / "var" / "offline_test.json"
    if offline.exists():
        o = json.loads(offline.read_text())
        add(f"Offline core verification ({o.get('isolation_mode')})", o.get("verdict", "?"), 1, "var/offline_test.json")
    else:
        missing.append("Offline verification")

    targets = [("Cross-tenant cache leaks", "0"), ("Verified cache false-hit rate", "≤ 1%"), ("Sensitive remote egress bytes", "0"),
               ("Receipts verifying", "100%"), ("Security suite failures", "0")]
    lines = ["# NanoGate — Results", "",
             "Generated by `bench/generate_report.py` from artifacts in `results/`. Every row links to the run that produced it.",
             "**Measured** = produced by an executed benchmark on this ZGX Nano. **Target** rows are goals, not results. "
             "Rows marked *partial* were computed on incomplete data and will be superseded.", "",
             "| Metric | Result | Sample size | Run ID | Status |", "|---|---|---|---|---|"]
    lines += [f"| {a} | {b} | {c} | `{d_}` | {e} |" for a, b, c, d_, e in rows]
    lines += ["", "## Targets (not results)", "", "| Metric | Target | Status |", "|---|---|---|"]
    lines += [f"| {a} | {b} | Target |" for a, b in targets]
    if missing:
        lines += ["", "## Unavailable", ""] + [f"- {m}: **Unavailable** (not yet executed)" for m in missing]
    lines += ["", "## Data labels", "", "- Public dataset: MMLU (MIT), GSM8K (MIT), PAWS, CISA KEV (public domain) — see `datasets/DATA_SOURCES.md`.",
              "- Authored evaluation set: enterprise cache groups (`datasets/authored/cache_groups.yaml`).",
              "- Synthetic security evaluation data: `datasets/synthetic/dlp_eval.jsonl`, attack-suite secrets.",
              "- Scenario assumptions (FinOps projections) are never reported here."]
    (ROOT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    write(d, "summary.json", {"run_id": run_id, "rows": [dict(zip(("metric", "result", "n", "run_id", "status"), r)) for r in rows],
                              "missing": missing, "targets": targets})
    (d / "RESULTS.md").write_text("\n".join(lines) + "\n")
    _plots(d, cache, load, e2e)
    print(f"RESULTS.md written ({len(rows)} measured rows, {len(missing)} unavailable) · run {run_id}")


def _plots(d: Path, cache, load, e2e):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    if cache:
        fig, ax = plt.subplots(figsize=(6, 4))
        colors = {"accepted": "#2a78d6", "hard_negative": "#1baf7a", "rejected": "#eb6834"}
        names = {"accepted": "served (verified)", "hard_negative": "rejected by verifier", "rejected": "below retrieval threshold"}
        for reg, c in colors.items():
            pts = [p for p in cache["pairs"] if p["region"] == reg]
            ax.scatter([p["similarity"] for p in pts], [p["verifier"] for p in pts], s=22, color=c, label=f"{names[reg]} (n={len(pts)})", edgecolor="#fcfcfb", lw=1)
        th = cache["methods"]["verified_semantic"]["thresholds"]
        ax.axvline(th["retrieval"], color="#0b0b0b", ls=":", lw=1); ax.axhline(th["verifier"], color="#0b0b0b", ls=":", lw=1)
        ax.set_xlabel("retrieval cosine similarity"); ax.set_ylabel("verifier score"); ax.legend(frameon=False)
        ax.set_title("Cache: similarity vs verifier (test split)", loc="left")
        fig.tight_layout(); fig.savefig(d / "cache_precision_curve.png", dpi=150); plt.close(fig)
    lat = []
    if e2e:
        lat = [r["latency_ms"] for r in e2e["rows"] if r["status"] == 200]
    elif load:
        lat = [r["latency_ms"] for l in load["levels"] for r in l["raw"] if r["status"] == 200]
    if lat:
        fig, ax = plt.subplots(figsize=(6, 3.5))
        ax.hist(lat, bins=30, color="#2a78d6", edgecolor="#fcfcfb")
        ax.set_xlabel("end-to-end latency (ms)"); ax.set_ylabel("requests"); ax.set_title(f"Latency distribution (n={len(lat)})", loc="left")
        fig.tight_layout(); fig.savefig(d / "latency_distribution.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    main()

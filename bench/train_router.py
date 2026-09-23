"""Train + evaluate the NanoGate router (also serves as bench/evaluate_router.py).

Target: y_error = 1 when the local answer fails the task rubric (graded vs dataset ground truth).
Split: grouped by canonical_intent_id (item/CVE group) into train 60% / calibration 20% / test 20%.
Models: calibrated logistic regression (deployed), uncalibrated logistic, raw log-prob threshold,
calibrated gradient boosting. Calibration: isotonic on the calibration split. Threshold: chosen on
the calibration split (max coverage s.t. selective risk <= --target-risk); test set scored once.

Usage: python bench/train_router.py [--target-risk 0.10] [--no-deploy]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from common import ROOT, git_commit, new_run, write

from nanogate import router_features as rf  # noqa: E402

RUNS = ROOT / "datasets" / "processed" / "router_runs"
ART = ROOT / "artifacts"
TARGET_TOTAL = 1800


def load(tier: str) -> dict[str, dict]:
    p = RUNS / f"{tier}.jsonl"
    rows = {}
    if p.exists():
        for l in p.read_text().split("\n"):
            try:
                r = json.loads(l)
                rows[r["id"]] = r
            except (ValueError, KeyError):
                pass
    return rows


def split_of(group: str) -> str:
    h = int(hashlib.sha256(group.encode()).hexdigest(), 16) % 100
    return "train" if h < 60 else "calibration" if h < 80 else "test"


def ece(p, y, bins=15):
    edges = np.linspace(0, 1, bins + 1)
    e, out = 0.0, []
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if m.sum():
            e += m.mean() * abs(p[m].mean() - y[m].mean())
            out.append({"bin_mid": float((edges[i] + edges[i + 1]) / 2), "mean_pred": float(p[m].mean()),
                        "frac_pos": float(y[m].mean()), "count": int(m.sum())})
    return float(e), out


def risk_coverage(p, y):
    order = np.argsort(p)            # accept lowest predicted risk first
    ys = y[order]
    n = len(y)
    cum = np.cumsum(ys)
    cov = np.arange(1, n + 1) / n
    risk = cum / np.arange(1, n + 1)
    aurc = float(np.mean(risk))
    step = max(1, n // 60)
    pts = [{"coverage": float(cov[i]), "risk": float(risk[i])} for i in range(0, n, step)] + [{"coverage": 1.0, "risk": float(risk[-1])}]
    return pts, aurc


def pick_threshold(p, y, target_risk):
    best = None
    for t in np.unique(np.concatenate([p, [0.0, 1.0]])):
        acc = p < t + 1e-12
        if not acc.any():
            continue
        risk = y[acc].mean()
        if risk <= target_risk and (best is None or acc.mean() > best[1]):
            best = (float(t) + 1e-9, float(acc.mean()), float(risk))
    return best


def metrics(p, y, threshold):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    e, rel = ece(p, y)
    pts, aurc = risk_coverage(p, y)
    acc = p < threshold
    return {"auroc": float(roc_auc_score(y, p)) if len(set(y)) > 1 else None, "ece": e, "brier": float(np.mean((p - y) ** 2)),
            "nll": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), "aurc": aurc, "threshold": float(threshold),
            "coverage": float(acc.mean()), "selective_risk": float(y[acc].mean()) if acc.any() else None,
            "selective_accuracy": float(1 - y[acc].mean()) if acc.any() else None}, rel, pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-risk", type=float, default=0.10)
    ap.add_argument("--no-deploy", action="store_true")
    args = ap.parse_args()
    local, large = load("local"), load("local_large")
    ids = sorted(local)
    if len(ids) < 100:
        raise SystemExit(f"only {len(ids)} router rows; generate more with bench/generate_router_data.py")
    X = np.asarray([rf.vectorize(local[i]["features"]) for i in ids], dtype="float64")
    y = np.asarray([local[i]["y_error"] for i in ids], dtype=int)
    groups = [local[i]["group"] for i in ids]
    split = np.asarray([split_of(g) for g in groups])
    tr, ca, te = split == "train", split == "calibration", split == "test"
    ds_hash = hashlib.sha256("\n".join(f"{i}:{local[i]['output_sha256']}:{local[i]['y_error']}" for i in ids).encode()).hexdigest()
    split_hash = hashlib.sha256("\n".join(f"{i}:{s}" for i, s in zip(ids, split)).encode()).hexdigest()
    status = "complete" if len(ids) >= TARGET_TOTAL * 0.98 else f"partial ({len(ids)}/{TARGET_TOTAL} items)"
    run_id, d = new_run("router", vars(args), {"router_dataset_sha256": ds_hash, "split_sha256": split_hash, "data_status": status})

    lr = Pipeline([("scaler", StandardScaler()), ("lr", LogisticRegression(C=0.5, max_iter=2000))]).fit(X[tr], y[tr])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(lr.predict_proba(X[ca])[:, 1], y[ca])
    hgb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, random_state=0).fit(X[tr], y[tr])
    iso_h = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(hgb.predict_proba(X[ca])[:, 1], y[ca])
    names = rf.feature_names()
    mlp_i = names.index("gen_mean_logprob")

    def raw_lp(Xs):  # naive "token confidence = correctness": p_error = 1 - exp(mean logprob)
        return 1 - np.exp(np.clip(Xs[:, mlp_i], -50, 0))

    scorers = {
        "calibrated_lr": lambda Xs: iso.predict(lr.predict_proba(Xs)[:, 1]),
        "uncalibrated_lr": lambda Xs: lr.predict_proba(Xs)[:, 1],
        "raw_logprob": raw_lp,
        "calibrated_hgb": lambda Xs: iso_h.predict(hgb.predict_proba(Xs)[:, 1]),
    }
    methods, reliability, rc, thresholds = {}, {}, {}, {}
    for name, fn in scorers.items():
        pc = fn(X[ca])
        pick = pick_threshold(pc, y[ca], args.target_risk)
        th = pick[0] if pick else float(np.quantile(pc, 0.5))
        thresholds[name] = {"threshold": th, "validation_coverage": pick[1] if pick else None,
                            "validation_risk": pick[2] if pick else None, "met_target": pick is not None}
        m, rel, pts = metrics(fn(X[te]), y[te], th)
        methods[name], reliability[name], rc[name] = m, rel, pts

    p_test = scorers["calibrated_lr"](X[te])
    th = thresholds["calibrated_lr"]["threshold"]
    test_ids = [i for i, s in zip(ids, split) if s == "test"]
    large_correct = [(large[i]["correct"] if i in large else None) for i in test_ids]
    local_ok = 1 - y[te]
    loc_ms = np.asarray([local[i]["total_ms"] or 0 for i in test_ids]) / 1000
    large_ms = np.asarray([large[i]["total_ms"] if i in large else np.nan for i in test_ids], dtype=float) / 1000
    have_large = ~np.isnan(large_ms)
    baselines = {"local_only_accuracy": float(local_ok.mean()), "local_model": next(iter(local.values()))["model"],
                 "large_model": next(iter(large.values()))["model"] if large else None,
                 "large_only_accuracy": float(np.mean([c for c in large_correct if c is not None])) if any(c is not None for c in large_correct) else None,
                 "large_coverage_of_test": float(have_large.mean())}
    accept = p_test < th
    cq = [{"label": f"Local only ({baselines['local_model']})", "quality": float(local_ok.mean()), "compute_s": float(loc_ms.mean()), "kind": "measured"}]
    if have_large.any():
        lc = np.asarray([c if c is not None else np.nan for c in large_correct], dtype=float)
        sub = have_large
        cq.append({"label": f"Larger tier only ({baselines['large_model']})", "quality": float(np.nanmean(lc[sub])), "compute_s": float(np.nanmean(large_ms[sub])), "kind": "measured", "n": int(sub.sum())})
        routed_q = np.where(accept, local_ok, lc)[sub]
        routed_t = (loc_ms + np.where(accept, 0, large_ms))[sub]
        cq.append({"label": "NanoGate router (local → local-large)", "quality": float(np.nanmean(routed_q)), "compute_s": float(np.nanmean(routed_t)),
                   "kind": "measured", "n": int(sub.sum()), "escalation_rate": float((~accept)[sub].mean())})
        oracle = np.where(local_ok == 1, 1, lc)[sub]
        cq.append({"label": "Oracle escalation (upper bound)", "quality": float(np.nanmean(oracle)), "compute_s": float(np.nanmean((loc_ms + np.where(local_ok == 1, 0, large_ms))[sub])), "kind": "oracle"})
    ev = {"run_id": run_id, "data_status": status, "n_test": int(te.sum()), "methods": methods, "thresholds": thresholds,
          "risk_coverage": rc, "reliability": reliability, "baselines": baselines, "cost_quality": cq,
          "route_distribution_test": {"local": int(accept.sum()), "escalated": int((~accept).sum())},
          "feature_importance": sorted([{"feature": n, "coef": float(c)} for n, c in zip(names, lr.named_steps["lr"].coef_[0])],
                                       key=lambda x: -abs(x["coef"]))[:20],
          "per_source_test": {src: {"n": int(sum(1 for i in test_ids if local[i]["source"] == src)),
                                    "local_accuracy": float(np.mean([local[i]["correct"] for i in test_ids if local[i]["source"] == src]))}
                              for src in sorted({local[i]["source"] for i in test_ids})},
          "target_risk": args.target_risk}
    write(d, "router_metrics.json", ev)
    _plots(d, ev)
    version = f"router-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')}-{run_id[-6:]}"
    meta = {"version": version, "run_id": run_id, "schema_version": rf.SCHEMA_VERSION, "features": names,
            "threshold": th, "threshold_rule": f"max coverage s.t. selective risk <= {args.target_risk} on calibration split",
            "calibration_method": "isotonic", "dataset_sha256": ds_hash, "split_sha256": split_hash,
            "split_rule": "grouped by canonical_intent_id: 60/20/20 train/calibration/test (sha256 bucket)",
            "n_train": int(tr.sum()), "n_calibration": int(ca.sum()), "n_test": int(te.sum()), "base_error_rate_train": float(y[tr].mean()),
            "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "code_commit": git_commit(),
            "local_model": baselines["local_model"], "data_status": status,
            "dataset_sources": sorted({r["source"] for r in local.values()}),
            "metrics_test": methods["calibrated_lr"]}
    if not args.no_deploy:
        (ART / "router").mkdir(parents=True, exist_ok=True)
        (ART / "calibration").mkdir(parents=True, exist_ok=True)
        joblib.dump(lr, ART / "router" / "model.joblib")
        joblib.dump(iso, ART / "calibration" / "isotonic.joblib")
        (ART / "router" / "meta.json").write_text(json.dumps(meta, indent=2))
        (ART / "router" / "feature_schema.json").write_text(json.dumps(rf.schema(), indent=2))
        (ART / "router" / "evaluation.json").write_text(json.dumps(ev, indent=2))
        (ART / "router" / "test_predictions.json").write_text(json.dumps(
            {"run_id": run_id, "ids": test_ids, "p_error": p_test.tolist(), "y_error": y[te].tolist(), "large_correct": large_correct}))
        (ART / "calibration" / "meta.json").write_text(json.dumps({"method": "isotonic", "run_id": run_id, "n": int(ca.sum()),
                                                                    "router_version": version}, indent=2))
    write(d, "router_meta.json", meta)
    print(json.dumps({"run_id": run_id, "status": status, "n": {"train": int(tr.sum()), "cal": int(ca.sum()), "test": int(te.sum())},
                      "threshold": th, "baselines": baselines,
                      "test": {k: {m: (round(v[m], 4) if isinstance(v[m], float) else v[m]) for m in ("auroc", "ece", "brier", "aurc", "coverage", "selective_accuracy")}
                               for k, v in methods.items()}, "cost_quality": cq}, indent=1))


def _plots(d: Path, ev: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    series = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#c3c2b7",
                         "axes.labelcolor": "#52514e", "xtick.color": "#898781", "ytick.color": "#898781"})
    fig, ax = plt.subplots(figsize=(6, 4))
    for (name, pts), c in zip(ev["risk_coverage"].items(), series):
        ax.plot([p["coverage"] for p in pts], [p["risk"] for p in pts], color=c, lw=2, ls="--" if name == "raw_logprob" else "-", label=name)
    ax.set_xlabel("coverage (answers kept local)"); ax.set_ylabel("selective risk"); ax.grid(color="#e1e0d9", lw=0.6)
    ax.legend(frameon=False); ax.set_title(f"Risk–coverage (test n={ev['n_test']})", loc="left")
    fig.tight_layout(); fig.savefig(d / "risk_coverage_curve.png", dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], color="#c3c2b7", ls="--", lw=1)
    for name, c in (("calibrated_lr", series[0]), ("uncalibrated_lr", series[1]), ("raw_logprob", series[2])):
        b = ev["reliability"][name]
        ax.plot([x["mean_pred"] for x in b], [x["frac_pos"] for x in b], "-o", color=c, lw=2, ms=4, label=f"{name} (ECE {ev['methods'][name]['ece']:.3f})")
    ax.set_xlabel("mean predicted p(error)"); ax.set_ylabel("observed error rate"); ax.grid(color="#e1e0d9", lw=0.6)
    ax.legend(frameon=False); ax.set_title("Reliability diagram", loc="left")
    fig.tight_layout(); fig.savefig(d / "reliability_diagram.png", dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(6, 4))
    for p, c in zip(ev["cost_quality"], series):
        ax.scatter(p["compute_s"], p["quality"], color=c, s=50, label=p["label"], edgecolor="#fcfcfb", lw=1.5, zorder=3)
    ax.set_xlabel("mean on-device compute seconds / request"); ax.set_ylabel("accuracy"); ax.grid(color="#e1e0d9", lw=0.6)
    ax.legend(frameon=False, fontsize=8); ax.set_title("Cost–quality frontier (measured)", loc="left")
    fig.tight_layout(); fig.savefig(d / "cost_quality_frontier.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    main()

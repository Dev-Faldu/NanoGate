"""Validity gate for benchmark runs: a run that measured nothing (model down, every request errored) is not a result.

Checks the newest results/<run>/ holding the given metrics file. On failure writes INVALID.json (with the reasons)
into that run, which generate_report.py skips, and exits 1 so the pipeline step is not marked done.
Invalid runs are kept, not deleted: they document what happened.
Usage: python bench/validate_run.py load|e2e|security [--run <run_id>]
"""
from __future__ import annotations

import argparse
import json
import sys

from common import RESULTS

MAX_ERROR_RATE = 0.05   # a few timeouts under load are a result; a dead model is not


def check_load(m: dict) -> list[str]:
    bad = []
    for lv in m.get("levels", []):
        if not lv.get("ok"):
            bad.append(f"concurrency {lv['concurrency']}: 0 successful requests ({lv.get('errors')})")
        elif lv.get("error_rate", 0) > MAX_ERROR_RATE:
            bad.append(f"concurrency {lv['concurrency']}: error rate {lv['error_rate']:.0%} > {MAX_ERROR_RATE:.0%}")
    return bad or ([] if m.get("levels") else ["no concurrency levels recorded"])


def check_e2e(m: dict) -> list[str]:
    bad = []
    if not m.get("answered"):
        bad.append(f"0 of {m.get('n')} requests answered")
    unavailable = m.get("reasons", {}).get("MODEL_UNAVAILABLE", 0)
    if unavailable > MAX_ERROR_RATE * (m.get("n") or 1):
        bad.append(f"MODEL_UNAVAILABLE on {unavailable} of {m.get('n')} requests")
    return bad


def check_security(m: dict) -> list[str]:
    return [] if m.get("total") else ["no cases executed"]


KINDS = {"load": ("load_metrics.json", check_load), "e2e": ("e2e_metrics.json", check_e2e),
         "security": ("security_results.json", check_security)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=sorted(KINDS))
    ap.add_argument("--run", default=None)
    a = ap.parse_args()
    fname, check = KINDS[a.kind]
    runs = [RESULTS / a.run] if a.run else sorted(RESULTS.glob("*"), reverse=True)
    run = next((p for p in runs if (p / fname).exists()), None)
    if run is None:
        print(f"validate {a.kind}: no run with {fname}", file=sys.stderr)
        return 1
    reasons = check(json.loads((run / fname).read_text()))
    if reasons:
        (run / "INVALID.json").write_text(json.dumps({"run_id": run.name, "reasons": reasons}, indent=2))
        print(f"validate {a.kind}: {run.name} INVALID: " + "; ".join(reasons), file=sys.stderr)
        return 1
    print(f"validate {a.kind}: {run.name} valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())

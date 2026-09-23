"""Download public datasets used by NanoGate evaluation, with provenance.

Every dataset snapshot is written to datasets/raw/<name>/ with a provenance record in
datasets/registry.json: source URL, publisher, license, retrieval timestamp, SHA-256, rows.
Re-running refreshes snapshots; offline runs keep the last verified snapshot.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datasets" / "raw"
REGISTRY = ROOT / "datasets" / "registry.json"

MMLU_SUBJECTS = [
    "computer_security", "college_computer_science", "high_school_computer_science",
    "business_ethics", "management", "marketing", "professional_accounting",
    "econometrics", "global_facts", "professional_law", "electrical_engineering",
]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}


def record(reg: dict, name: str, path: Path, rows: int, **meta) -> None:
    reg[name] = {
        **meta,
        "file": str(path.relative_to(ROOT)),
        "rows": rows,
        "sha256": sha256_file(path),
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    print(f"  {name}: {rows} rows sha256={reg[name]['sha256'][:12]}")


def fetch_mmlu(reg: dict) -> None:
    from datasets import load_dataset
    rows = []
    for subj in MMLU_SUBJECTS:
        ds = load_dataset("cais/mmlu", subj, split="test")
        for i, r in enumerate(ds):
            rows.append({"id": f"mmlu/{subj}/{i}", "subject": subj, "question": r["question"],
                         "choices": list(r["choices"]), "answer": int(r["answer"])})
    p = RAW / "mmlu" / "mmlu_subset_test.jsonl"
    write_jsonl(p, rows)
    record(reg, "mmlu", p, len(rows), source="https://huggingface.co/datasets/cais/mmlu",
           publisher="Hendrycks et al. (Center for AI Safety)", license="MIT",
           version="HF cais/mmlu (test split)", subset=MMLU_SUBJECTS,
           fields_used=["question", "choices", "answer"])


def fetch_gsm8k(reg: dict) -> None:
    from datasets import load_dataset
    ds = load_dataset("openai/gsm8k", "main", split="test")
    rows = [{"id": f"gsm8k/test/{i}", "question": r["question"],
             "answer": r["answer"].split("####")[-1].strip().replace(",", "")} for i, r in enumerate(ds)]
    p = RAW / "gsm8k" / "gsm8k_test.jsonl"
    write_jsonl(p, rows)
    record(reg, "gsm8k", p, len(rows), source="https://huggingface.co/datasets/openai/gsm8k",
           publisher="OpenAI (Cobbe et al. 2021)", license="MIT", version="HF openai/gsm8k main/test",
           fields_used=["question", "answer (final number after ####)"])


def fetch_paws(reg: dict) -> None:
    from datasets import load_dataset
    ds = load_dataset("google-research-datasets/paws", "labeled_final", split="test")
    rows = [{"id": f"paws/test/{r['id']}", "a": r["sentence1"], "b": r["sentence2"], "label": int(r["label"])}
            for r in ds]
    p = RAW / "paws" / "paws_labeled_final_test.jsonl"
    write_jsonl(p, rows)
    record(reg, "paws", p, len(rows), source="https://huggingface.co/datasets/google-research-datasets/paws",
           publisher="Google Research (Zhang et al. 2019)",
           license="Free to use for any purpose (per dataset card; original PAWS-Wiki terms)",
           version="labeled_final/test", fields_used=["sentence1", "sentence2", "label"])


def fetch_kev(reg: dict) -> None:
    url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    r = httpx.get(url, timeout=60, follow_redirects=True, headers={"User-Agent": "NanoGate-dataset-fetch/1.0"})
    r.raise_for_status()
    data = r.json()
    p = RAW / "cisa_kev" / "known_exploited_vulnerabilities.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(r.content)
    record(reg, "cisa_kev", p, len(data.get("vulnerabilities", [])), source=url,
           publisher="Cybersecurity and Infrastructure Security Agency (CISA)",
           license="Public domain (U.S. Government work); CISA KEV terms of use",
           version=data.get("catalogVersion"), catalog_released=data.get("dateReleased"),
           fields_used=["cveID", "vendorProject", "product", "vulnerabilityName", "dateAdded",
                        "shortDescription", "requiredAction", "dueDate", "knownRansomwareCampaignUse"])


def main(names: list[str]) -> int:
    reg = load_registry()
    fns = {"mmlu": fetch_mmlu, "gsm8k": fetch_gsm8k, "paws": fetch_paws, "cisa_kev": fetch_kev}
    failed = []
    for n in names or list(fns):
        print(f"fetching {n} ...")
        try:
            fns[n](reg)
        except Exception as e:  # keep last verified snapshot on failure
            failed.append(n)
            prev = reg.get(n)
            print(f"  FAILED: {type(e).__name__}: {e}. "
                  + (f"Keeping previous snapshot from {prev['retrieved_at']}" if prev else "No previous snapshot."))
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

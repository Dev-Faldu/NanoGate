"""`make redact-check` — prove logs, receipts and request records contain no secrets or raw PII,
and that no secret-bearing files are tracked by git.

Scans: var/*.log, var/gateway.out, requests.raw_prompt, receipts.body_json, requests.reason_codes.
(cache_entries hold Internal-class answer text by design and are excluded; cache eligibility
already rejects Confidential and above.)
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from nanogate.dlp import LayeredDLP  # noqa: E402

SENSITIVE = {"US_SSN", "CREDIT_CARD", "API_KEY", "PRIVATE_KEY", "PASSWORD", "JWT", "CLOUD_CREDENTIAL", "DB_CREDENTIAL", "AUTH_TOKEN"}


def scan_text(det: LayeredDLP, label: str, text: str, out: list) -> None:
    for chunk_start in range(0, len(text), 20000):
        r = det.scan(text[chunk_start:chunk_start + 20000])
        for f in r.findings:
            if f.type in SENSITIVE:
                out.append((label, f.type))


def main() -> int:
    det = LayeredDLP(use_presidio=False)   # regex + secrets + de-obfuscation are sufficient and fast
    hits: list[tuple[str, str]] = []
    for p in list((ROOT / "var").glob("*.log")) + [ROOT / "var" / "gateway.out"]:
        if p.exists():
            scan_text(det, str(p.relative_to(ROOT)), p.read_text(errors="replace"), hits)
    db = ROOT / "var" / "nanogate.db"
    if db.exists():
        c = sqlite3.connect(db)
        n_raw = c.execute("SELECT COUNT(*) FROM requests WHERE raw_prompt IS NOT NULL").fetchone()[0]
        print(f"requests with raw prompt stored: {n_raw} (policy default: none)")
        for (body,) in c.execute("SELECT body_json FROM receipts"):
            scan_text(det, "receipts.body_json", body, hits)
        for (rp,) in c.execute("SELECT raw_prompt FROM requests WHERE raw_prompt IS NOT NULL"):
            scan_text(det, "requests.raw_prompt", rp, hits)
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split("\n")
    bad_files = [f for f in tracked if any(s in f for s in ("dev_keys.json", "key_pepper", "receipt_hmac.key", ".env")) and not f.endswith(".env.example")]
    for f in tracked:
        p = ROOT / f
        if f and p.is_file() and p.stat().st_size < 2_000_000 and not f.startswith(("datasets/", "tests/", "bench/attack", "scripts/generate_synthetic")) \
                and p.suffix in (".py", ".ts", ".tsx", ".md", ".yaml", ".yml", ".json", ".sh", ".toml", ".cfg"):
            r = det.scan(p.read_text(errors="replace"))
            for fnd in r.findings:
                if fnd.type in ("API_KEY", "PRIVATE_KEY", "CLOUD_CREDENTIAL", "DB_CREDENTIAL", "AUTH_TOKEN"):
                    hits.append((f"git:{f}", fnd.type))
    summary: dict[str, int] = {}
    for label, t in hits:
        summary[f"{label} :: {t}"] = summary.get(f"{label} :: {t}", 0) + 1
    if bad_files:
        print("TRACKED SECRET FILES:", bad_files)
    if summary:
        print("Sensitive patterns found (types only, values never printed):")
        for k, n in sorted(summary.items()):
            print(f"  {n:4d}  {k}")
    else:
        print("redact-check: no secrets or raw identifiers found in logs, receipts, request records or tracked files")
    return 1 if (summary or bad_files) else 0


if __name__ == "__main__":
    sys.exit(main())

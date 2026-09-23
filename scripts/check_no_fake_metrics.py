"""Static check for NO_FAKE_METRICS.md: flag patterns that usually mean invented numbers in the UI
or backend (random telemetry, timer-driven fake events, hardcoded headline metrics).

Every finding must be fixed or explicitly allow-listed below with a justification.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "frontend" / "src", ROOT / "backend" / "nanogate"]
RULES = [
    ("random-values", re.compile(r"Math\.random\(|random\.(uniform|random|randint)\(")),
    ("timer-events", re.compile(r"\bsetInterval\(")),
    ("marketing-claims", re.compile(r"500\s*(concurrent\s*)?users|99\.99|100%\s*(private|secure|safe|accurate)|zero hallucinations|guaranteed", re.I)),
    ("hardcoded-money", re.compile(r"""["'>]\s*\$\d[\d,]*(\.\d+)?\s*[<"']""")),
    ("hardcoded-percent", re.compile(r"""["'>]\s*\d{1,3}(\.\d+)?%\s*[<"']""")),
    ("hardcoded-tokens", re.compile(r"""["'>]\s*\d[\d,]{3,}\s*tokens\b""", re.I)),
    ("placeholder-data", re.compile(r"\b(fakeData|mockData|dummyData|placeholderData|sampleMetrics)\b")),
]
# path-substring, rule, justification
ALLOW = [
    ("telemetry.py", "random-values", "DevelopmentTelemetryProvider: only with TELEMETRY_MODE=demo; every value marked status='demo' and badged in UI"),
    ("attack_suite", "random-values", "synthetic attack payloads"),
    (".test.", "hardcoded-percent", "unit tests assert formatter output"),
    (".test.", "hardcoded-money", "unit tests assert formatter output"),
]


def main() -> int:
    findings = []
    for base in TARGETS:
        for p in base.rglob("*"):
            if p.suffix not in (".ts", ".tsx", ".py") or "node_modules" in p.parts:
                continue
            rel = str(p.relative_to(ROOT))
            for i, line in enumerate(p.read_text(errors="replace").split("\n"), 1):
                for name, rx in RULES:
                    if rx.search(line) and not any(a in rel and r == name for a, r, _ in ALLOW):
                        findings.append((rel, i, name, line.strip()[:110]))
    for f in findings:
        print(f"{f[0]}:{f[1]}  [{f[2]}]  {f[3]}")
    print(f"\nno-fake-metrics: {len(findings)} finding(s); {len(ALLOW)} allow-listed pattern(s) with justification")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

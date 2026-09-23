"""Knowledge sources for retrieval-augmented answers.

Currently: CISA Known Exploited Vulnerabilities (public domain). Retrieval is hybrid:
exact CVE-ID lookup first, then dense retrieval (local bge embeddings). Each source has a
version (KEV catalogVersion + file hash) that semantic-cache entries are bound to.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .settings import get_settings

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.I)
ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9\-]{2,}\b")
SECURITY_TRIGGER = re.compile(r"CVE-\d{4}-\d{4,}|\bKEV\b|known exploited|vulnerab|exploit|ransomware", re.I)


@dataclass
class SourceState:
    source_id: str
    version: str
    content_hash: str
    records: int
    retrieved_at: str | None
    revoked: bool = False


class KEVSource:
    source_id = "cisa_kev"

    def __init__(self, path: Path | None = None):
        self.path = path or get_settings().kev_file
        self._lock = threading.Lock()
        self.records: list[dict[str, Any]] = []
        self.by_cve: dict[str, dict[str, Any]] = {}
        self.matrix: np.ndarray | None = None
        self.state: SourceState | None = None
        self.error: str | None = None

    def load(self, with_index: bool = True) -> None:
        try:
            raw = self.path.read_bytes()
            data = json.loads(raw)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            return
        h = hashlib.sha256(raw).hexdigest()
        self.records = data.get("vulnerabilities", [])
        self.by_cve = {r["cveID"].upper(): r for r in self.records}
        reg_path = self.path.parents[2] / "registry.json"
        retrieved = None
        if reg_path.exists():
            retrieved = json.loads(reg_path.read_text()).get("cisa_kev", {}).get("retrieved_at")
        self.state = SourceState(self.source_id, f"{data.get('catalogVersion')}:{h[:12]}", h, len(self.records), retrieved)
        if with_index:
            self._build_index(h)

    def _build_index(self, h: str) -> None:
        from .embeddings import embed
        cache = get_settings().data_dir / f"kev_index_{h[:16]}.npy"
        if cache.exists():
            self.matrix = np.load(cache)
            return
        texts = [self.doc_text(r) for r in self.records]
        self.matrix = embed(texts)
        np.save(cache, self.matrix)

    @staticmethod
    def doc_text(r: dict[str, Any]) -> str:
        return (f"{r['cveID']}: {r.get('vendorProject')} {r.get('product')} — {r.get('vulnerabilityName')}. "
                f"{r.get('shortDescription', '')}")

    @staticmethod
    def context_text(r: dict[str, Any]) -> str:
        return (f"[{r['cveID']}] vendor={r.get('vendorProject')}; product={r.get('product')}; "
                f"name={r.get('vulnerabilityName')}; dateAdded={r.get('dateAdded')}; dueDate={r.get('dueDate')}; "
                f"knownRansomwareCampaignUse={r.get('knownRansomwareCampaignUse')}; "
                f"description={r.get('shortDescription')}; requiredAction={r.get('requiredAction')}")

    def retrieve(self, query: str, k: int = 3) -> dict[str, Any]:
        """Returns records + router retrieval features (all measured)."""
        if not self.records or self.matrix is None:
            return {"available": False, "reason": self.error or "KEV index not loaded"}
        from .embeddings import embed
        qv = embed([query])[0]
        sims = self.matrix @ qv
        order = list(np.argsort(-sims)[: k + 1])
        exact = [self.by_cve[c.upper()] for c in CVE_RE.findall(query) if c.upper() in self.by_cve]
        chosen: list[dict[str, Any]] = list(exact)
        for i in order:
            if len(chosen) >= k:
                break
            if self.records[i] not in chosen:
                chosen.append(self.records[i])
        top = float(sims[order[0]])
        second = float(sims[order[1]]) if len(order) > 1 else 0.0
        ctx = "\n".join(self.context_text(r) for r in chosen)
        wanted = set(c.upper() for c in CVE_RE.findall(query)) | set(ENTITY_RE.findall(query)) - {"What", "Which", "When", "CISA", "KEV", "Give", "According"}
        covered = sum(1 for w in wanted if w.lower() in ctx.lower())
        today = dt.date.today()
        ages = []
        for r in chosen:
            try:
                ages.append((today - dt.date.fromisoformat(r["dateAdded"])).days)
            except Exception:
                pass
        mismatch = 1.0 if CVE_RE.findall(query) and not exact else 0.0
        return {
            "available": True, "records": chosen, "context": ctx,
            "top_sim": 1.0 if exact else top, "margin": 1.0 if exact else top - second,
            "coverage": covered / len(wanted) if wanted else None, "verifier": None,
            "sources": float(len(chosen)), "age_days": float(min(ages)) if ages else None,
            "mismatch": mismatch, "exact_match": bool(exact),
            "source_id": self.source_id, "source_version": self.state.version if self.state else None,
        }


_kev: KEVSource | None = None


def kev() -> KEVSource:
    global _kev
    if _kev is None:
        _kev = KEVSource()
    return _kev


def needs_kev(text: str) -> bool:
    return bool(SECURITY_TRIGGER.search(text))


def rag_messages(messages: list[dict], retrieval: dict[str, Any]) -> list[dict]:
    """Inject retrieved public-source context as a system message (source-attributed)."""
    ctx = ("You are answering using the CISA Known Exploited Vulnerabilities catalog. "
           "Use only these records when they are relevant; say so if they do not contain the answer.\n"
           + retrieval["context"])
    return [{"role": "system", "content": ctx}] + messages

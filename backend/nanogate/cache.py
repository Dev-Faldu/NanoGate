"""Tenant-safe verified semantic cache.

Stage 1: dense candidate retrieval (local bge embeddings) strictly inside the request namespace
         namespace = H(tenant | department | policy_version | model_family | system_prompt_hash | context_fingerprint)
Stage 2: verification (NLI + slot checks, verifier.py)
Acceptance requires: same namespace AND sim >= tau_r AND verifier >= tau_v AND no slot conflict
AND policy version current AND source version current AND not revoked AND TTL valid AND
output previously passed the output scan. Everything else is a miss with an explicit reason.

Clients can never write to the cache: entries are created only by the gateway from answers
that passed output scanning and the router (cache-poisoning defense).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .db import Database
from .reason_codes import Reason
from .verifier import Verifier


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def namespace_fields(tenant_id: str, department_id: str, policy_version: str, model_family: str,
                     system_prompt_hash: str, context_fingerprint: str) -> dict[str, str]:
    return {"tenant_id": tenant_id, "department_id": department_id, "policy_version": policy_version,
            "model_family": model_family, "system_prompt_hash": system_prompt_hash,
            "context_fingerprint": context_fingerprint}


def namespace_id(f: dict[str, str]) -> str:
    return sha("|".join(f[k] for k in ("tenant_id", "department_id", "policy_version", "model_family",
                                        "system_prompt_hash", "context_fingerprint")))[:24]


def conversation_keys(messages: list[dict]) -> tuple[str, str, str]:
    """(query, system_prompt_hash, context_fingerprint). Context = all turns before the last user turn."""
    sys_txt = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    last_idx = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    query = str(messages[last_idx].get("content", "")) if last_idx >= 0 else ""
    ctx = [{"r": m.get("role"), "c": str(m.get("content", ""))} for i, m in enumerate(messages)
           if i < last_idx and m.get("role") != "system"]
    return query, sha(sys_txt)[:16], sha(json.dumps(ctx, sort_keys=True))[:16]


@dataclass
class Thresholds:
    retrieval: float = 0.80
    verifier: float = 0.50
    source: str = "default (not yet calibrated)"
    run_id: str | None = None

    @classmethod
    def load(cls, path: Path) -> "Thresholds":
        if path.exists():
            d = json.loads(path.read_text())
            return cls(d["retrieval"], d["verifier"], d.get("source", str(path)), d.get("run_id"))
        return cls()


@dataclass
class CacheLookup:
    decision: str                       # Reason value
    hit: bool = False
    entry: dict | None = None
    similarity: float | None = None
    verifier: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    namespace: dict | None = None
    namespace_id: str | None = None
    evidence: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    thresholds: dict | None = None

    def public(self) -> dict:
        e = self.entry or {}
        return {
            "decision": self.decision, "hit": self.hit, "similarity": None if self.similarity is None else round(self.similarity, 4),
            "verifier": self.verifier, "candidates": self.candidates, "namespace": self.namespace,
            "namespace_id": self.namespace_id, "evidence": self.evidence, "elapsed_ms": round(self.elapsed_ms, 2),
            "thresholds": self.thresholds,
            "entry": {k: e.get(k) for k in ("cache_id", "created_at", "expires_at", "source_id", "source_hash",
                                             "response_hash", "model_name", "policy_version", "completion_tokens",
                                             "prompt_tokens", "hits", "origin_request_id")} if e else None,
        }


class SemanticCache:
    def __init__(self, db: Database, verifier: Verifier, thresholds: Thresholds, embed_fn, embedding_version: str,
                 verifier_version: str):
        self.db = db
        self.verifier = verifier
        self.th = thresholds
        self.verifier.threshold = thresholds.verifier
        self.embed = embed_fn
        self.embedding_version = embedding_version
        self.verifier_version = verifier_version
        self._lock = threading.Lock()
        self._index: dict[str, tuple[list[str], np.ndarray]] | None = None
        self.source_versions: dict[str, str] = {}     # current version per knowledge source
        self.revoked_sources: set[str] = set()
        self.stats = {"lookups": 0, "hits": 0, "hard_negatives": 0, "namespace_misses": 0, "stale": 0,
                      "context_mismatch": 0, "revoked": 0, "writes": 0, "tokens_avoided": 0}

    # ---- index -------------------------------------------------------------------------------
    def _load_index(self) -> None:
        idx: dict[str, tuple[list[str], list[np.ndarray]]] = {}
        for r in self.db.all("SELECT cache_id, namespace, embedding FROM cache_entries WHERE validation_state='valid'"):
            ids, vecs = idx.setdefault(r["namespace"], ([], []))
            ids.append(r["cache_id"])
            vecs.append(np.frombuffer(r["embedding"], dtype="float32"))
        self._index = {ns: (ids, np.vstack(v)) for ns, (ids, v) in idx.items()}

    def _ns_index(self) -> dict[str, tuple[list[str], np.ndarray]]:
        if self._index is None:
            with self._lock:
                if self._index is None:
                    self._load_index()
        return self._index  # type: ignore[return-value]

    def _invalidate_index(self) -> None:
        with self._lock:
            self._index = None

    # ---- lookup ------------------------------------------------------------------------------
    def lookup(self, query: str, ns: dict[str, str], now: float | None = None) -> CacheLookup:
        t0 = time.perf_counter()
        now = now or time.time()
        self.stats["lookups"] += 1
        nsid = namespace_id(ns)
        th = {"retrieval": self.th.retrieval, "verifier": self.th.verifier, "source": self.th.source}
        qv = self.embed([query])[0]
        index = self._ns_index()
        res = CacheLookup(decision=Reason.CACHE_MISS.value, namespace=ns, namespace_id=nsid, thresholds=th)

        # Stage 1: candidates strictly within the namespace
        cands: list[tuple[str, float]] = []
        if nsid in index:
            ids, mat = index[nsid]
            sims = mat @ qv
            order = np.argsort(-sims)[:5]
            cands = [(ids[i], float(sims[i])) for i in order]
        above = [(cid, s) for cid, s in cands if s >= self.th.retrieval]
        res.candidates = [{"cache_id": cid, "similarity": round(s, 4)} for cid, s in cands[:3]]

        if not above:
            # Explain the miss: is there a similar entry in a *different* namespace? (no content is exposed)
            res.decision, res.evidence = self._explain_miss(qv, ns, nsid, index)
            if res.decision == Reason.CACHE_NAMESPACE_MISMATCH.value:
                self.stats["namespace_misses"] += 1
            elif res.decision == Reason.CACHE_STALE.value:
                self.stats["stale"] += 1
            elif res.decision == Reason.CACHE_CONTEXT_MISMATCH.value:
                self.stats["context_mismatch"] += 1
            res.similarity = cands[0][1] if cands else None
            res.elapsed_ms = (time.perf_counter() - t0) * 1000
            return res

        # Stage 2: freshness gates then verification (best candidate first)
        rows = {r["cache_id"]: r for r in self.db.all(
            f"SELECT * FROM cache_entries WHERE cache_id IN ({','.join('?' * len(above))})", tuple(c for c, _ in above))}
        live: list[tuple[dict, float]] = []
        for cid, s in above:
            r = rows.get(cid)
            if not r:
                continue
            gate = self._freshness(r, now)
            if gate:
                res.evidence.append(f"{cid}: {gate[1]}")
                if res.decision == Reason.CACHE_MISS.value:
                    res.decision = gate[0]
                continue
            live.append((r, s))
        if not live:
            res.similarity = above[0][1]
            if res.decision == Reason.CACHE_STALE.value:
                self.stats["stale"] += 1
            elif res.decision == Reason.CACHE_SOURCE_REVOKED.value:
                self.stats["revoked"] += 1
            res.elapsed_ms = (time.perf_counter() - t0) * 1000
            return res

        verdicts = self.verifier.score_many([(query, r["canonical_query"]) for r, _ in live])
        best = None
        for (r, s), v in zip(live, verdicts):
            if v.equivalent:
                best = (r, s, v)
                break
        if best:
            r, s, v = best
            res.decision, res.hit, res.entry, res.similarity, res.verifier = Reason.CACHE_VERIFIED.value, True, r, s, v.public()
            res.evidence.append(f"retrieval {s:.3f} >= {self.th.retrieval}; verifier {v.score:.3f} >= {self.th.verifier}; no slot conflicts")
            self.stats["hits"] += 1
            self.stats["tokens_avoided"] += int((r.get("completion_tokens") or 0) + (r.get("prompt_tokens") or 0))
            self.db.execute("UPDATE cache_entries SET hits=hits+1 WHERE cache_id=?", (r["cache_id"],))
        else:
            (r, s), v = live[0], verdicts[0]
            res.decision, res.similarity, res.verifier = Reason.CACHE_HARD_NEGATIVE.value, s, v.public()
            res.entry = r
            why = v.conflicts or [f"verifier score {v.score:.3f} < {self.th.verifier}"]
            res.evidence.append(f"candidate {r['cache_id']} similar ({s:.3f}) but rejected: " + "; ".join(why))
            self.stats["hard_negatives"] += 1
        res.elapsed_ms = (time.perf_counter() - t0) * 1000
        return res

    def _freshness(self, r: dict, now: float) -> tuple[str, str] | None:
        if r["validation_state"] == "revoked" or (r["source_id"] and r["source_id"] in self.revoked_sources):
            return Reason.CACHE_SOURCE_REVOKED.value, f"source {r['source_id']} revoked"
        if r["validation_state"] != "valid":
            return Reason.CACHE_STALE.value, f"entry {r['validation_state']}: {r['invalidated_reason']}"
        if r["expires_at"] < now:
            return Reason.CACHE_STALE.value, "TTL expired"
        if r["source_id"]:
            cur = self.source_versions.get(r["source_id"])
            if cur is None or cur != r["source_hash"]:
                return Reason.CACHE_STALE.value, f"source {r['source_id']} version changed ({r['source_hash']} -> {cur})"
        if r["embedding_model_version"] != self.embedding_version:
            return Reason.CACHE_STALE.value, "embedding model changed"
        return None

    def _explain_miss(self, qv: np.ndarray, ns: dict[str, str], nsid: str, index) -> tuple[str, list[str]]:
        best_ns, best_s = None, -1.0
        for other, (ids, mat) in index.items():
            if other == nsid:
                continue
            s = float((mat @ qv).max())
            if s > best_s:
                best_ns, best_s = other, s
        if best_ns is None or best_s < self.th.retrieval:
            # check invalidated entries (policy change) within same tenant/department
            return Reason.CACHE_MISS.value, ["no candidate above retrieval threshold"]
        r = self.db.one("SELECT tenant_id, department_id, policy_version, model_family, system_prompt_hash, "
                        "context_fingerprint FROM cache_entries WHERE namespace=? LIMIT 1", (best_ns,))
        if not r:
            return Reason.CACHE_MISS.value, []
        diff = [k for k in ns if r[k] != ns[k]]
        if "tenant_id" in diff or "department_id" in diff:
            return Reason.CACHE_NAMESPACE_MISMATCH.value, [
                f"similar entry exists (sim {best_s:.3f}) in another namespace; differing fields: "
                f"{', '.join(d for d in diff if d in ('tenant_id', 'department_id'))}; never shared across boundaries"]
        if diff == ["context_fingerprint"] or diff == ["system_prompt_hash"] or set(diff) <= {"context_fingerprint", "system_prompt_hash"}:
            return Reason.CACHE_CONTEXT_MISMATCH.value, [f"similar entry (sim {best_s:.3f}) has different conversation/system context"]
        if "policy_version" in diff:
            return Reason.CACHE_STALE.value, [f"similar entry (sim {best_s:.3f}) created under policy {r['policy_version']}; current {ns['policy_version']}"]
        return Reason.CACHE_NAMESPACE_MISMATCH.value, [f"similar entry differs in: {', '.join(diff)}"]

    # ---- writes ------------------------------------------------------------------------------
    def store(self, query: str, response: str, ns: dict[str, str], data_class: str, ttl_s: int, model_name: str,
              prompt_tokens: int | None, completion_tokens: int | None, source_id: str | None,
              source_hash: str | None, request_id: str) -> str:
        cid = "c_" + uuid.uuid4().hex[:16]
        emb = self.embed([query])[0].astype("float32")
        now = time.time()
        self.db.execute(
            "INSERT INTO cache_entries(cache_id, tenant_id, department_id, namespace, canonical_query, response, embedding,"
            " embedding_model_version, verifier_model_version, model_family, model_name, policy_version, system_prompt_hash,"
            " context_fingerprint, source_id, source_hash, data_class, created_at, expires_at, validation_state, response_hash,"
            " completion_tokens, prompt_tokens, origin_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, ns["tenant_id"], ns["department_id"], namespace_id(ns), query, response, emb.tobytes(),
             self.embedding_version, self.verifier_version, ns["model_family"], model_name, ns["policy_version"],
             ns["system_prompt_hash"], ns["context_fingerprint"], source_id, source_hash, data_class, now, now + ttl_s,
             "valid", sha(response), completion_tokens, prompt_tokens, request_id))
        self.stats["writes"] += 1
        self._invalidate_index()
        return cid

    def invalidate_policy(self, policy_id: str, old_version: str | None) -> int:
        if not old_version:
            return 0
        with self.db.tx() as c:
            n = c.execute("UPDATE cache_entries SET validation_state='invalidated', invalidated_reason=? "
                          "WHERE policy_version=? AND validation_state='valid'",
                          (f"policy {old_version} superseded", old_version)).rowcount
        self._invalidate_index()
        return n

    def invalidate_departments(self, tenant_id: str, departments: list[str], reason: str) -> int:
        """Answers cached before a department's knowledge changed were built without it: never reuse them."""
        if not departments:
            return 0
        marks = ",".join("?" * len(departments))
        with self.db.tx() as c:
            n = c.execute(f"UPDATE cache_entries SET validation_state='invalidated', invalidated_reason=? WHERE tenant_id=? "
                          f"AND department_id IN ({marks}) AND validation_state='valid'",
                          (reason, tenant_id, *departments)).rowcount
        self._invalidate_index()
        return n

    def revoke_source(self, source_id: str) -> int:
        self.revoked_sources.add(source_id)
        with self.db.tx() as c:
            n = c.execute("UPDATE cache_entries SET validation_state='revoked', invalidated_reason=? WHERE source_id=? "
                          "AND validation_state='valid'", (f"source {source_id} revoked", source_id)).rowcount
            c.execute("INSERT OR REPLACE INTO source_state(source_id, version, revoked, updated_at) VALUES (?,?,1,?)",
                      (source_id, self.source_versions.get(source_id, "unknown"), time.time()))
        self._invalidate_index()
        return n

    def restore_source(self, source_id: str) -> None:
        self.revoked_sources.discard(source_id)
        self.db.execute("UPDATE source_state SET revoked=0, updated_at=? WHERE source_id=?", (time.time(), source_id))

    def set_source_version(self, source_id: str, version: str) -> None:
        self.source_versions[source_id] = version

    def summary(self) -> dict[str, Any]:
        agg = self.db.one("SELECT COUNT(*) n, SUM(validation_state='valid') valid, SUM(hits) hits, "
                          "SUM(validation_state='invalidated') invalidated, SUM(validation_state='revoked') revoked "
                          "FROM cache_entries") or {}
        ns = self.db.one("SELECT COUNT(DISTINCT namespace) n FROM cache_entries WHERE validation_state='valid'") or {}
        return {"entries": agg.get("n") or 0, "valid": agg.get("valid") or 0, "invalidated": agg.get("invalidated") or 0,
                "revoked": agg.get("revoked") or 0, "total_hits": agg.get("hits") or 0, "namespaces": ns.get("n") or 0,
                "session_stats": dict(self.stats), "embedding_model": self.embedding_version,
                "verifier_model": self.verifier_version,
                "thresholds": {"retrieval": self.th.retrieval, "verifier": self.th.verifier, "source": self.th.source,
                               "run_id": self.th.run_id}}

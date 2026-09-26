"""Company knowledge: documents a tenant uploads (text, Markdown, PDF) for retrieval-augmented answers.

Each source belongs to one tenant, is visible to a chosen set of its departments, and carries a data class. Text is
split into overlapping chunks and embedded on-device with the same bge model as the cache; nothing leaves the device.
A source's version changes whenever its documents change, and cache entries are bound to it, so revoking or editing
a source invalidates every cached answer built from it (same mechanism as the CISA KEV source).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import time
import uuid
from typing import Any

import numpy as np

CHUNK_CHARS = 900
OVERLAP = 150
MIN_SIM = 0.55           # below this a chunk is not considered relevant
MAX_BYTES = 15 * 1024 * 1024
CLASSES = ("Public", "Internal", "Confidential", "Restricted")


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    if name.endswith((".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".html", ".htm")):
        text = data.decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", text) if name.endswith((".html", ".htm")) else text
    raise ValueError("supported files: .pdf, .txt, .md, .csv, .json, .yaml, .html")


def chunk(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    out, i = [], 0
    while i < len(text):
        end = min(len(text), i + CHUNK_CHARS)
        if end < len(text):   # prefer to break at a paragraph or sentence boundary
            cut = max(text.rfind("\n\n", i + CHUNK_CHARS // 2, end), text.rfind(". ", i + CHUNK_CHARS // 2, end))
            if cut > i:
                end = cut + 1
        piece = text[i:end].strip()
        if len(piece) > 40:
            out.append(piece)
        if end >= len(text):
            break
        i = max(end - OVERLAP, i + 1)
    return out


class CompanyKnowledge:
    def __init__(self, db, embed_fn):
        self.db, self.embed = db, embed_fn
        self._lock = threading.Lock()
        self._index: dict[str, tuple[np.ndarray, list[dict]]] = {}   # source_id -> (matrix, chunk rows)

    # ---- sources --------------------------------------------------------------------------------------
    def create_source(self, tenant: str, name: str, departments: list[str], data_class: str, description: str = "",
                      actor: str | None = None) -> dict:
        if data_class not in CLASSES:
            raise ValueError(f"data_class must be one of {CLASSES}")
        sid = "kb_" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] + "_" + uuid.uuid4().hex[:6]
        self.db.execute("INSERT INTO knowledge_sources(source_id, tenant_id, name, description, departments_json, data_class,"
                        " version, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, tenant, name, description, json.dumps(sorted(set(departments))), data_class, "v0",
                         time.time(), actor))
        return self.source(sid)

    def source(self, sid: str) -> dict | None:
        r = self.db.one("SELECT * FROM knowledge_sources WHERE source_id=?", (sid,))
        if not r:
            return None
        r["departments"] = json.loads(r.pop("departments_json"))
        r["documents"] = self.db.all("SELECT doc_id, filename, sha256, bytes, chunks, created_at FROM knowledge_docs "
                                     "WHERE source_id=? ORDER BY created_at", (sid,))
        r["chunks"] = sum(d["chunks"] for d in r["documents"])
        r["revoked"] = bool(r["revoked"])
        return r

    def list(self, tenant: str | None = None) -> list[dict]:
        rows = self.db.all("SELECT source_id FROM knowledge_sources" + (" WHERE tenant_id=?" if tenant else "")
                           + " ORDER BY created_at", (tenant,) if tenant else ())
        return [self.source(r["source_id"]) for r in rows]

    def _bump(self, sid: str) -> str:
        docs = self.db.all("SELECT sha256 FROM knowledge_docs WHERE source_id=? ORDER BY doc_id", (sid,))
        v = "v" + hashlib.sha256("".join(d["sha256"] for d in docs).encode()).hexdigest()[:12]
        self.db.execute("UPDATE knowledge_sources SET version=? WHERE source_id=?", (v, sid))
        with self._lock:
            self._index.pop(sid, None)
        return v

    def add_document(self, sid: str, filename: str, data: bytes) -> dict:
        if len(data) > MAX_BYTES:
            raise ValueError("file larger than 15 MB")
        if not self.source(sid):
            raise KeyError(sid)
        text = extract_text(filename, data)
        pieces = chunk(text)
        if not pieces:
            raise ValueError("no readable text found in this file (scanned PDFs need OCR first)")
        h = hashlib.sha256(data).hexdigest()
        if self.db.one("SELECT 1 FROM knowledge_docs WHERE source_id=? AND sha256=?", (sid, h)):
            raise ValueError("this file is already in the source")
        vecs = self.embed(pieces)
        did = "doc_" + uuid.uuid4().hex[:12]
        with self.db.tx() as c:
            c.execute("INSERT INTO knowledge_docs(doc_id, source_id, filename, sha256, bytes, chunks, created_at)"
                      " VALUES (?,?,?,?,?,?,?)", (did, sid, filename[:200], h, len(data), len(pieces), time.time()))
            c.executemany("INSERT INTO knowledge_chunks(source_id, doc_id, ord, text, embedding) VALUES (?,?,?,?,?)",
                          [(sid, did, i, p, np.asarray(v, dtype="float32").tobytes()) for i, (p, v) in enumerate(zip(pieces, vecs))])
        version = self._bump(sid)
        return {"doc_id": did, "filename": filename, "chunks": len(pieces), "characters": len(text), "version": version}

    def remove_document(self, sid: str, doc_id: str) -> str:
        with self.db.tx() as c:
            c.execute("DELETE FROM knowledge_chunks WHERE source_id=? AND doc_id=?", (sid, doc_id))
            c.execute("DELETE FROM knowledge_docs WHERE source_id=? AND doc_id=?", (sid, doc_id))
        return self._bump(sid)

    def set_revoked(self, sid: str, revoked: bool) -> None:
        self.db.execute("UPDATE knowledge_sources SET revoked=? WHERE source_id=?", (int(revoked), sid))

    def delete_source(self, sid: str) -> None:
        with self.db.tx() as c:
            for t in ("knowledge_chunks", "knowledge_docs", "knowledge_sources"):
                c.execute(f"DELETE FROM {t} WHERE source_id=?", (sid,))
        with self._lock:
            self._index.pop(sid, None)

    # ---- retrieval ------------------------------------------------------------------------------------
    def visible(self, tenant: str, department: str) -> list[dict]:
        rows = self.db.all("SELECT source_id, name, departments_json, data_class, version FROM knowledge_sources "
                           "WHERE tenant_id=? AND revoked=0", (tenant,))
        return [r for r in rows if department in json.loads(r["departments_json"])]

    def _matrix(self, sid: str) -> tuple[np.ndarray, list[dict]]:
        with self._lock:
            if sid in self._index:
                return self._index[sid]
        rows = self.db.all("SELECT chunk_id, doc_id, ord, text, embedding FROM knowledge_chunks WHERE source_id=? ORDER BY chunk_id",
                           (sid,))
        m = np.stack([np.frombuffer(r["embedding"], dtype="float32") for r in rows]) if rows else np.zeros((0, 384), "float32")
        meta = [{k: r[k] for k in ("chunk_id", "doc_id", "ord", "text")} for r in rows]
        with self._lock:
            self._index[sid] = (m, meta)
        return m, meta

    def retrieve(self, tenant: str, department: str, query: str, k: int = 4) -> dict[str, Any] | None:
        """Best chunks across the sources this department may use; None when nothing is relevant."""
        sources = self.visible(tenant, department)
        if not sources:
            return None
        qv = self.embed([query])[0]
        hits = []
        for s in sources:
            m, meta = self._matrix(s["source_id"])
            if not len(meta):
                continue
            sims = m @ qv
            for i in np.argsort(-sims)[:k]:
                hits.append((float(sims[i]), s, meta[i]))
        hits.sort(key=lambda h: -h[0])
        hits = [h for h in hits if h[0] >= MIN_SIM][:k]
        if not hits:
            return None
        names = {d["doc_id"]: d["filename"] for d in self.db.all(
            f"SELECT doc_id, filename FROM knowledge_docs WHERE doc_id IN ({','.join('?' * len(hits))})",
            tuple(h[2]["doc_id"] for h in hits))}
        top = hits[0][1]
        order = {c: i for i, c in enumerate(CLASSES)}
        data_class = max((h[1]["data_class"] for h in hits), key=lambda c: order[c])
        ctx = "\n\n".join(f"[{names.get(h[2]['doc_id'], h[2]['doc_id'])} · {h[1]['name']}]\n{h[2]['text']}" for h in hits)
        return {
            "available": True, "context": ctx, "data_class": data_class,
            "records": [{"source": h[1]["name"], "document": names.get(h[2]["doc_id"]), "similarity": round(h[0], 3)} for h in hits],
            "top_sim": hits[0][0], "margin": hits[0][0] - (hits[1][0] if len(hits) > 1 else 0.0), "coverage": None,
            "verifier": None, "sources": float(len({h[1]['source_id'] for h in hits})), "age_days": None, "mismatch": 0.0,
            "exact_match": False, "source_id": top["source_id"], "source_version": top["version"],
        }


def kb_messages(messages: list[dict], retrieval: dict[str, Any]) -> list[dict]:
    ctx = ("Answer using the company documents below when they are relevant, and name the document you used. "
           "If they do not contain the answer, say so instead of guessing.\n\n" + retrieval["context"])
    return [{"role": "system", "content": ctx}] + messages

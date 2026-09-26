"""Service container: builds every component once and exposes health/readiness."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from .auth import KeyStore, RateLimiter, load_pepper
from .budget import BudgetEngine
from .cache import SemanticCache, Thresholds
from .db import Database
from .dlp import LayeredDLP
from .events import bus
from .inference import LocalModelAdapter
from .knowledge import KEVSource
from .metrics import IN_FLIGHT, MODEL_UP, QUEUE
from .policy import PolicyEngine
from .pricing import Pricing
from .receipts import ReceiptStore
from .remote import EgressGuard, RemoteConnector
from .router_model import Router
from .settings import ROOT, Settings
from .telemetry import provider as telemetry_provider
from .verifier import Verifier

log = logging.getLogger("nanogate.services")


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=3)
        if out.returncode != 0:
            return None
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout
        return out.stdout.strip()[:12] + ("-dirty" if dirty.strip() else "")
    except Exception:
        return None


def hf_revision(repo: str) -> str:
    """Resolved snapshot hash of a locally cached Hugging Face model."""
    import os
    base = Path(os.environ.get("HF_HOME", ROOT / ".runtime" / "hf")) / "hub" / ("models--" + repo.replace("/", "--"))
    ref = base / "refs" / "main"
    return f"{repo}@{ref.read_text().strip()[:12]}" if ref.exists() else f"{repo}@unknown"


class Services:
    def __init__(self, settings: Settings):
        s = self.settings = settings
        self.started_at = time.time()
        self.bus = bus
        self.db = Database(s.db_path)
        self.db.migrate()
        self.keys = KeyStore(self.db, load_pepper(s.data_dir / "key_pepper"))
        self.rate = RateLimiter()
        self.policies = PolicyEngine(self.db, s.policies_file)
        self.policies.load()
        self.pricing = Pricing(s.pricing_file)
        self.budget = BudgetEngine(self.db)
        self.receipts = ReceiptStore(self.db, s.receipt_hmac_key_file)
        self.egress = EgressGuard(self.db)
        self.telemetry = telemetry_provider(s.telemetry_mode)
        self.local = LocalModelAdapter(s.local_model_base_url, s.local_model_name, s.local_model_api_key,
                                       s.local_model_family, s.local_model_revision, "local", s.local_timeout_s,
                                       s.max_concurrent_inference)
        self.local_large = LocalModelAdapter(s.local_large_model_base_url, s.local_large_model_name, s.local_model_api_key,
                                             s.local_model_family, "auto", "local_large", s.local_timeout_s, 2) \
            if s.local_large_model_name else None
        self.remote = RemoteConnector(s.remote_mode, self.egress, s.remote_base_url, s.remote_api_key, s.remote_model,
                                      s.remote_provider, s.remote_timeout_s, self.local_large)
        self.router = Router(s.router_dir)
        self.router.load()
        self.dlp = LayeredDLP()
        self.dlp_fast = LayeredDLP(use_presidio=False)   # streaming hold-back scanner (regex + secrets)
        self.kev = KEVSource(s.kev_file)
        self.cache: SemanticCache | None = None
        self.kb = None               # company knowledge (needs the embedding model)
        self.ml_error: str | None = None
        self.commit = git_commit()
        self.policies.on_publish.append(self._on_policy_publish)
        self._tasks: list[asyncio.Task] = []
        self.last_sample: dict[str, Any] = {}
        self.offline_report: dict | None = None
        from .actions import Actions
        from .assistant import Assistant
        from .ops import Operations
        self.ops = Operations(self)   # audit log, settings, alerts, retention, backups, SIEM
        self.actions = Actions(self)
        self.assistant = Assistant(self)

    # ---- lifecycle ----------------------------------------------------------------------------
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.dlp.load)
        if self.settings.load_ml:
            try:
                await loop.run_in_executor(None, self._load_ml)
            except Exception as e:  # cache becomes unavailable; never silently faked
                self.ml_error = f"{type(e).__name__}: {e}"[:300]
                log.exception("ML components failed to load")
        await self.refresh_models(warm=True)
        await self.remote.health()
        if self.settings.offline_report.exists():
            try:
                self.offline_report = json.loads(self.settings.offline_report.read_text())
            except Exception:
                pass
        self._tasks.append(asyncio.create_task(self._telemetry_loop()))
        self._tasks.append(asyncio.create_task(self._health_loop()))
        self._tasks += [asyncio.create_task(t) for t in self.ops.tasks()]

    def _load_ml(self) -> None:
        from .embeddings import embed, embedder, nli_verifier
        embedder()
        nli_verifier()
        self.kev.load(with_index=True)
        th = Thresholds.load(self.settings.cache_cfg_file)
        self.cache = SemanticCache(self.db, Verifier(th.verifier), th, embed, hf_revision(self.settings.embedding_model),
                                   hf_revision(self.settings.verifier_model))
        if self.kev.state:
            self.cache.set_source_version(self.kev.source_id, self.kev.state.version)
        from .company_kb import CompanyKnowledge
        self.kb = CompanyKnowledge(self.db, embed)
        for s in self.db.all("SELECT source_id, version, revoked FROM knowledge_sources"):
            self.cache.set_source_version(s["source_id"], s["version"])
            if s["revoked"]:
                self.cache.revoked_sources.add(s["source_id"])
        for r in self.db.all("SELECT source_id FROM source_state WHERE revoked=1"):
            self.cache.revoked_sources.add(r["source_id"])

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await self.local.aclose()
        await self.ops.aclose()
        if self.local_large:
            await self.local_large.aclose()

    async def refresh_models(self, warm: bool = False) -> None:
        for a in [self.local] + ([self.local_large] if self.local_large else []):
            prev = a.status.get("state")
            st = await a.health()
            if st["state"] == "ready":
                if not a.details:
                    await a.identify()
                    self.db.execute("INSERT OR REPLACE INTO model_registry(model_key, tier, info_json, updated_at) VALUES (?,?,?,?)",
                                    (a.model, a.tier, json.dumps(a.details), time.time()))
                if warm and a.tier == "local" and a.warmup_ms is None:
                    await a.warmup()
            MODEL_UP.labels(a.tier).set(1 if st["state"] == "ready" else 0)
            if prev != st["state"]:
                self.bus.emit("service_state_changed", component=f"model:{a.tier}", state=st["state"], reason=st.get("reason"))

    def _on_policy_publish(self, policy_id: str, old: str | None, new: str) -> None:
        n = self.cache.invalidate_policy(policy_id, old) if self.cache else 0
        self.bus.emit("policy_published", policy_id=policy_id, old_version=old, new_version=new, cache_invalidated=n)

    async def _telemetry_loop(self) -> None:
        loop = asyncio.get_running_loop()
        last_persist = 0.0
        while True:
            try:
                smp = await loop.run_in_executor(None, self.telemetry.sample)
                smp["queue_depth"] = self.local.queue_depth
                smp["in_flight"] = self.local.in_flight
                smp["tokens_per_s"] = self.local.last_tokens_per_s
                smp["mode"] = self.telemetry.mode
                self.last_sample = smp
                QUEUE.set(self.local.queue_depth)
                IN_FLIGHT.set(self.local.in_flight)
                self.bus.emit("telemetry_updated", **smp)
                if smp["ts"] - last_persist >= 10:
                    last_persist = smp["ts"]
                    self.db.execute("INSERT INTO telemetry_samples(ts, gpu_util, gpu_temp_c, gpu_power_w, gpu_clock_mhz, "
                                    "mem_used_bytes, mem_total_bytes, cpu_util, queue_depth, in_flight, tokens_per_s)"
                                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                    (smp["ts"], smp["gpu_util"], smp["gpu_temp_c"], smp["gpu_power_w"], smp["gpu_clock_mhz"],
                                     smp["mem_used_bytes"], smp["mem_total_bytes"], smp["cpu_util"], smp["queue_depth"],
                                     smp["in_flight"], smp["tokens_per_s"]))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telemetry sample failed")
            await asyncio.sleep(self.settings.telemetry_interval_s)

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                await self.refresh_models()
                prev = self.remote.health_state.get("state")
                st = await self.remote.health()
                if st.get("state") != prev:
                    self.bus.emit("service_state_changed", component="remote", state=st.get("state"), reason=st.get("reason"))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("health loop failed")

    # ---- health -------------------------------------------------------------------------------
    def components(self) -> dict[str, dict]:
        db_ok, db_err = self.db.healthy()
        dlp = self.dlp.status()
        return {
            "process": {"ok": True, "uptime_s": round(time.time() - self.started_at, 1)},
            "database": {"ok": db_ok, "reason": db_err, "path": str(self.settings.db_path.name)},
            "policy": {"ok": bool(self.policies._current), "policies": len(self.policies._current)},
            "model": {"ok": self.local.status.get("state") == "ready", **self.local.status, "model": self.local.model},
            "model_large": {"ok": bool(self.local_large and self.local_large.status.get("state") == "ready"),
                            **(self.local_large.status if self.local_large else {"reason": "not configured"}),
                            "model": self.local_large.model if self.local_large else None},
            "dlp": {"ok": dlp["available"], **dlp},
            "cache": {"ok": self.cache is not None, "reason": None if self.cache else (self.ml_error or "not loaded")},
            "router": {"ok": self.router.available, "reason": self.router.error, "version": self.router.meta.get("version")},
            "calibration": {"ok": self.router.calibrator is not None, "reason": self.router.error},
            "telemetry": {"ok": bool(self.last_sample), "mode": self.telemetry.mode,
                          "nvml": self.telemetry.nvml_error is None, "reason": self.telemetry.nvml_error},
            "knowledge_kev": {"ok": self.kev.state is not None, "reason": self.kev.error,
                              "version": self.kev.state.version if self.kev.state else None},
            "remote": {"ok": True, **self.remote.health_state},
            "knowledge": {"ok": self.kb is not None, "reason": None if self.kb else (self.ml_error or "embedding model not loaded"),
                          "sources": self.db.one("SELECT COUNT(*) n FROM knowledge_sources WHERE revoked=0")["n"]},
        }

    def ready(self) -> tuple[bool, list[str]]:
        c = self.components()
        essential = ["database", "policy", "model", "dlp", "cache", "router", "calibration"]
        failing = [k for k in essential if not c[k]["ok"]]
        return (not failing), failing

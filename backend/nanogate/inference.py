"""Local model adapters (OpenAI-compatible endpoints: vLLM on the ZGX Nano, organizer servers).

Generation always streams internally so time-to-first-token, real token counts and
per-token logprobs are measured on every request. There is no synthetic fallback: if the
endpoint is down, callers get ModelUnavailable.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from .settings import ROOT


class ModelUnavailable(Exception):
    pass


@dataclass
class GenerationResult:
    text: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ttft_ms: float | None = None
    total_ms: float | None = None
    queue_ms: float = 0.0
    model: str = ""
    tier: str = ""
    token_logprobs: list[float] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    top_logprobs: list[list[float]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def tokens_per_s(self) -> float | None:
        if not self.completion_tokens or not self.total_ms or self.ttft_ms is None:
            return None
        gen_ms = max(self.total_ms - self.ttft_ms, 1.0)
        return (self.completion_tokens - 1) / (gen_ms / 1000) if self.completion_tokens > 1 else None


def logprob_stats(res: GenerationResult) -> dict[str, float | None]:
    """Confidence statistics over generated tokens. These are features, not 'correctness'."""
    lps = res.token_logprobs
    if not lps:
        return {"mean_logprob": None, "min_logprob": None, "p10_logprob": None,
                "mean_entropy": None, "mean_margin": None, "frac_low_conf": None}
    ents, margins = [], []
    for tops in res.top_logprobs:
        if not tops:
            continue
        ps = [math.exp(x) for x in tops]
        rest = max(0.0, 1.0 - sum(ps))
        ent = -sum(p * math.log(p) for p in ps if p > 0)
        if rest > 1e-9:
            ent += -rest * math.log(rest)  # lumped tail mass: lower bound on true entropy
        ents.append(ent)
        s = sorted(tops, reverse=True)
        margins.append(s[0] - s[1] if len(s) > 1 else 10.0)
    srt = sorted(lps)
    return {
        "mean_logprob": sum(lps) / len(lps),
        "min_logprob": srt[0],
        "p10_logprob": srt[max(0, int(0.1 * len(srt)) - 1)] if len(srt) >= 10 else srt[0],
        "mean_entropy": sum(ents) / len(ents) if ents else None,
        "mean_margin": sum(margins) / len(margins) if margins else None,
        "frac_low_conf": sum(1 for x in lps if x < math.log(0.5)) / len(lps),
    }


def parse_prometheus(text: str, model: str) -> dict[str, float]:
    """Unlabelled values of Prometheus text-format samples, restricted to `model` when a model_name label exists."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name_labels, _, value = line.rpartition(" ")
        name, _, labels = name_labels.partition("{")
        if 'model_name="' in labels and f'model_name="{model}"' not in labels:
            continue
        try:
            out[name] = float(value)
        except ValueError:
            continue
    return out


def hf_snapshot_info(repo: str) -> dict[str, Any]:
    """Identity of the weights vLLM loaded, read from the shared HF cache (HF_HOME): commit sha, dtype,
    quantization method, parameter count (from the safetensors index) and size on disk."""
    repo_dir = Path(os.environ.get("HF_HOME", ROOT / ".runtime" / "hf")) / "hub" / \
        ("models--" + repo.replace("/", "--"))
    ref = repo_dir / "refs" / "main"
    if not ref.exists():
        return {}
    sha = ref.read_text().strip()
    snap = repo_dir / "snapshots" / sha
    info: dict[str, Any] = {"digest": sha, "modified_at": datetime.fromtimestamp(ref.stat().st_mtime, timezone.utc).isoformat()}
    try:
        cfg = json.loads((snap / "config.json").read_text())
        dtype = cfg.get("torch_dtype") or cfg.get("dtype")
        info["format"] = "safetensors"
        info["quantization"] = (cfg.get("quantization_config") or {}).get("quant_method") or dtype
        idx = snap / "model.safetensors.index.json"
        total = json.loads(idx.read_text()).get("metadata", {}).get("total_size") if idx.exists() else None
        width = {"bfloat16": 2, "float16": 2, "float32": 4}.get(dtype or "")
        if total and width and not cfg.get("quantization_config"):
            info["parameter_size"] = f"{total / width / 1e9:.1f}B"
        info["size_bytes"] = sum(f.resolve().stat().st_size for f in snap.iterdir() if f.is_file())
    except Exception as e:
        info["identity_error"] = f"{type(e).__name__}: {e}"[:200]
    return info


class LocalModelAdapter:
    def __init__(self, base_url: str, model: str, api_key: str = "", family: str = "", revision: str = "auto",
                 tier: str = "local", timeout_s: float = 120.0, max_concurrency: int = 4):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.family = family
        self.revision = revision
        self.tier = tier
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(max_concurrency)
        self.queue_depth = 0
        self.in_flight = 0
        self.status: dict[str, Any] = {"state": "unknown", "reason": "not yet checked"}
        self.warmup_ms: float | None = None
        self.last_tokens_per_s: float | None = None
        self.details: dict[str, Any] = {}
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=5.0))

    @property
    def native_root(self) -> str:
        return self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def health(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            r = await self._client.get(f"{self.base_url}/models", headers=self._headers(), timeout=5.0)
            r.raise_for_status()
            ids = [m.get("id") for m in r.json().get("data", [])]
            ok = self.model in ids
            self.status = {
                "state": "ready" if ok else "model_missing",
                "reason": None if ok else f"endpoint up but model '{self.model}' not listed",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "available_models": ids, "checked_at": time.time(),
            }
        except Exception as e:
            self.status = {"state": "unavailable", "reason": f"{type(e).__name__}: {e}"[:300], "checked_at": time.time()}
        return self.status

    async def identify(self) -> dict[str, Any]:
        """Model identity from the vLLM server (/v1/models, /version) and the shared Hugging Face cache
        (commit sha, dtype, quantization, size). Nothing is inferred when a source is unavailable."""
        info: dict[str, Any] = {"name": self.model, "family": self.family, "runtime": "openai-compatible",
                                "tier": self.tier, "endpoint": self.base_url}
        try:
            r = await self._client.get(f"{self.base_url}/models", headers=self._headers(), timeout=5.0)
            if r.status_code == 200:
                m = next((m for m in r.json().get("data", []) if m.get("id") == self.model), None)
                if m:
                    info.update(runtime="vllm" if m.get("owned_by") == "vllm" else info["runtime"],
                                context_length=m.get("max_model_len"), source=m.get("root"))
            v = await self._client.get(f"{self.native_root}/version", timeout=5.0)
            if v.status_code == 200:
                info["runtime_version"] = v.json().get("version")
        except Exception as e:
            info["identity_error"] = f"{type(e).__name__}: {e}"[:200]
        info.update(hf_snapshot_info(info.get("source") or self.model))
        if self.revision == "auto" and info.get("digest"):
            self.revision = info["digest"][:12]
        info["revision"] = self.revision
        self.details = info
        return info

    async def placement(self) -> dict[str, Any]:
        """Serving state of the model from vLLM's Prometheus /metrics (KV-cache use, running/waiting requests)."""
        try:
            r = await self._client.get(f"{self.native_root}/metrics", timeout=3.0)
            r.raise_for_status()
            mets = parse_prometheus(r.text, self.model)
            kv = mets.get("vllm:kv_cache_usage_perc", mets.get("vllm:gpu_cache_usage_perc"))
            return {"loaded": True, "kv_cache_usage": kv,
                    "requests_running": mets.get("vllm:num_requests_running"),
                    "requests_waiting": mets.get("vllm:num_requests_waiting")}
        except Exception as e:
            return {"loaded": None, "reason": f"{type(e).__name__}"}

    async def warmup(self) -> float | None:
        t0 = time.perf_counter()
        try:
            res = await self.generate([{"role": "user", "content": "Reply with the word ready."}], {"max_tokens": 4})
            self.warmup_ms = round((time.perf_counter() - t0) * 1000, 1)
            self.last_tokens_per_s = res.tokens_per_s or self.last_tokens_per_s
        except ModelUnavailable:
            self.warmup_ms = None
        return self.warmup_ms

    def _payload(self, messages: list[dict], params: dict[str, Any], logprobs: bool) -> dict[str, Any]:
        p: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True,
                             "stream_options": {"include_usage": True}}
        for k in ("temperature", "top_p", "stop", "seed", "presence_penalty", "frequency_penalty", "response_format"):
            if params.get(k) is not None:
                p[k] = params[k]
        mt = params.get("max_completion_tokens") or params.get("max_tokens")
        if mt:
            p["max_tokens"] = int(mt)
        if logprobs:
            p["logprobs"] = True
            p["top_logprobs"] = 5
        return p

    async def stream(self, messages: list[dict], params: dict[str, Any], result: GenerationResult,
                     logprobs: bool = True) -> AsyncIterator[str]:
        """Yield real content deltas from the model; fills `result` as it goes."""
        self.queue_depth += 1
        tq = time.perf_counter()
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            self.queue_depth -= 1
            raise ModelUnavailable("queue wait timeout")
        self.queue_depth -= 1
        self.in_flight += 1
        result.queue_ms = (time.perf_counter() - tq) * 1000
        result.model, result.tier = self.model, self.tier
        t0 = time.perf_counter()
        try:
            async with self._client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(),
                                           json=self._payload(messages, params, logprobs)) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")[:300]
                    raise ModelUnavailable(f"model endpoint HTTP {r.status_code}: {body}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if chunk.get("usage"):
                        result.prompt_tokens = chunk["usage"].get("prompt_tokens")
                        result.completion_tokens = chunk["usage"].get("completion_tokens")
                    for ch in chunk.get("choices", []):
                        delta = (ch.get("delta") or {}).get("content") or ""
                        lp = (ch.get("logprobs") or {}).get("content") or []
                        for t in lp:
                            result.token_logprobs.append(float(t["logprob"]))
                            result.tokens.append(str(t.get("token", "")))
                            result.top_logprobs.append([float(x["logprob"]) for x in t.get("top_logprobs", [])])
                        if ch.get("finish_reason"):
                            result.finish_reason = ch["finish_reason"]
                        if delta:
                            if result.ttft_ms is None:
                                result.ttft_ms = (time.perf_counter() - t0) * 1000
                            result.text += delta
                            yield delta
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            raise ModelUnavailable(f"{type(e).__name__}: {e}"[:300]) from e
        except asyncio.CancelledError:
            result.cancelled = True
            raise
        finally:
            self.in_flight -= 1
            self._sem.release()
            result.total_ms = (time.perf_counter() - t0) * 1000
            if result.tokens_per_s:
                self.last_tokens_per_s = result.tokens_per_s

    async def generate(self, messages: list[dict], params: dict[str, Any], logprobs: bool = True) -> GenerationResult:
        res = GenerationResult()
        async for _ in self.stream(messages, params, res, logprobs=logprobs):
            pass
        return res

    async def aclose(self) -> None:
        await self._client.aclose()

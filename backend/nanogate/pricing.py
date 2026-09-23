"""Pricing configuration and cost computation. Tokens are measured; rates come from config/pricing.yaml."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Rate:
    provider: str
    model: str
    tier: str
    input_per_mtok: float
    output_per_mtok: float
    currency: str
    effective_date: str
    source: str

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.input_per_mtok + completion_tokens * self.output_per_mtok) / 1_000_000


class Pricing:
    def __init__(self, path: Path):
        raw = path.read_bytes()
        d = yaml.safe_load(raw)
        self.version = str(d["version"])
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.currency = d.get("currency", "USD")
        self.rates = {(m["provider"], m["model"]): Rate(**m) for m in d["models"]}
        ref = d["counterfactual_reference"]
        self.reference = self.rates[(ref["provider"], ref["model"])]

    def for_model(self, model: str, provider: str | None = None) -> Rate | None:
        for (p, m), r in self.rates.items():
            if m == model and (provider is None or p == provider):
                return r
        return None

    def public(self) -> dict:
        return {"version": self.version, "sha256": self.sha256[:16], "currency": self.currency,
                "counterfactual_reference": {"provider": self.reference.provider, "model": self.reference.model},
                "rates": [r.__dict__ for r in self.rates.values()]}

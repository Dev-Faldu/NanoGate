"""Serving-side router: logistic regression over router_features + isotonic calibration.

Artifacts (produced by bench/train_router.py):
  artifacts/router/model.joblib        sklearn Pipeline(StandardScaler, LogisticRegression)
  artifacts/router/meta.json           version, schema, threshold, dataset hash, metrics, commit
  artifacts/calibration/isotonic.joblib
If artifacts are missing the router reports unavailable; the gateway never invents a score.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import router_features as rf


@dataclass
class RouterScore:
    available: bool
    raw_score: float | None = None
    p_error: float | None = None
    threshold: float | None = None
    accept_local: bool | None = None
    factors: list[dict] | None = None
    version: str | None = None
    schema_version: str | None = None
    reason: str | None = None

    def public(self) -> dict:
        return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


class Router:
    def __init__(self, router_dir: Path):
        self.dir = router_dir
        self.cal_dir = router_dir.parent / "calibration"
        self.model = None
        self.calibrator = None
        self.meta: dict[str, Any] = {}
        self.error: str | None = None

    def load(self) -> None:
        try:
            import joblib
            self.meta = json.loads((self.dir / "meta.json").read_text())
            self.model = joblib.load(self.dir / "model.joblib")
            self.calibrator = joblib.load(self.cal_dir / "isotonic.joblib")
            if self.meta.get("schema_version") != rf.SCHEMA_VERSION:
                raise ValueError(f"feature schema mismatch: artifact {self.meta.get('schema_version')} vs code {rf.SCHEMA_VERSION}")
            self.error = None
        except Exception as e:
            self.model, self.calibrator = None, None
            self.error = f"{type(e).__name__}: {e}"[:300]

    @property
    def available(self) -> bool:
        return self.model is not None and self.calibrator is not None

    @property
    def threshold(self) -> float | None:
        return self.meta.get("threshold")

    def score(self, features: dict[str, Any], threshold: float | None = None) -> RouterScore:
        if not self.available:
            return RouterScore(False, reason=self.error or "router artifacts not loaded")
        x = np.asarray([rf.vectorize(features)], dtype="float64")
        raw = float(self.model.predict_proba(x)[0, 1])
        p = float(np.clip(self.calibrator.predict([raw])[0], 0.0, 1.0))
        th = threshold if threshold is not None else float(self.meta["threshold"])
        # Per-feature contribution to the logit: coef_i * standardized x_i
        scaler, lr = self.model.named_steps["scaler"], self.model.named_steps["lr"]
        z = (x[0] - scaler.mean_) / np.where(scaler.scale_ == 0, 1, scaler.scale_)
        contrib = lr.coef_[0] * z
        names = rf.feature_names()
        order = np.argsort(-np.abs(contrib))[:5]
        factors = [{"feature": names[i], "contribution": round(float(contrib[i]), 4),
                    "direction": "raises risk" if contrib[i] > 0 else "lowers risk", "value": round(float(x[0][i]), 4)}
                   for i in order]
        return RouterScore(True, raw, p, th, p < th, factors, self.meta.get("version"), self.meta.get("schema_version"))

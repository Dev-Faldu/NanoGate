"""Local embedding + verifier model loading (offline after first download)."""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path

from .settings import ROOT, get_settings

os.environ.setdefault("HF_HOME", str(ROOT / ".runtime" / "hf"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_lock = threading.Lock()


def device() -> str:
    d = get_settings().ml_device
    if d != "auto":
        return d
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _offline_if_cached(repo: str) -> None:
    """Avoid network calls when the model snapshot is already on disk."""
    cache = Path(os.environ["HF_HOME"]) / "hub" / ("models--" + repo.replace("/", "--"))
    if cache.exists():
        os.environ["HF_HUB_OFFLINE"] = "1"


@lru_cache(maxsize=2)
def embedder(name: str | None = None):
    from sentence_transformers import SentenceTransformer
    name = name or get_settings().embedding_model
    with _lock:
        _offline_if_cached(name)
        return SentenceTransformer(name, device=device())


@lru_cache(maxsize=2)
def nli_verifier(name: str | None = None):
    from sentence_transformers import CrossEncoder
    name = name or get_settings().verifier_model
    with _lock:
        _offline_if_cached(name)
        return CrossEncoder(name, device=device())


def embed(texts: list[str]):
    import numpy as np
    return np.asarray(embedder().encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False),
                      dtype="float32")

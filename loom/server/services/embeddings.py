"""Local text embeddings for semantic lorebook retrieval.

Runs a small BGE model via fastembed (onnxruntime — no torch), in-process. Everything
degrades gracefully: if fastembed/the model isn't available, `available()` is False and
the lorebook store falls back to pure BM25. Embeddings are stored in libSQL's native
vector column (F32_BLOB) and queried with `vector_distance_cos`, so retrieval stays in the
one database.

bge-small-en-v1.5 is asymmetric: passages use `.embed()`, queries use `.query_embed()`
(which prepends the retrieval instruction) — using the right one each side improves recall.
"""
from __future__ import annotations

import os

# The hf-xet transfer backend is flaky on Windows (partial downloads); force standard HF.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

EMBED_DIM = 384
MODEL = "BAAI/bge-small-en-v1.5"

_model = None
_failed = False


def _get():
    global _model, _failed
    if _model is None and not _failed:
        try:
            from fastembed import TextEmbedding
            _model = TextEmbedding(MODEL)
        except Exception:  # noqa: BLE001 — no fastembed / no model / no network
            _failed = True
    return _model


def available() -> bool:
    return _get() is not None


def embed_passages(texts: list[str]) -> list[list[float]] | None:
    m = _get()
    if m is None:
        return None
    try:
        return [[float(x) for x in v] for v in m.embed(list(texts))]
    except Exception:  # noqa: BLE001
        return None


def embed_query(text: str) -> list[float] | None:
    m = _get()
    if m is None:
        return None
    try:
        return [float(x) for x in next(iter(m.query_embed([text])))]
    except Exception:  # noqa: BLE001
        return None


def to_sql(vec: list[float]) -> str:
    """JSON array text for libSQL's vector32() constructor."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"

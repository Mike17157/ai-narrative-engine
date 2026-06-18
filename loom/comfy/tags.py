"""Canonicalize free-form / LLM-written phrases onto the real danbooru tag
vocabulary your LoRAs actually use.

The vocabulary is the union of `ss_tag_frequency` across installed LoRAs (read
straight from each .safetensors header — cheap, no weight load). An LLM is great
at *understanding* a scene but drifts off the real tag vocabulary; this snaps its
output back: exact hits pass through, everything else maps to the nearest tag by
embedding cosine similarity (fastembed / ONNX, CPU). The vocab vectors are cached
to disk and keyed by vocab content, so they rebuild only when the LoRA set changes.

Result: prompts become valid danbooru tags (better booru-model output) AND routing
becomes exact (prompt and LoRA speak the same vocabulary).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import struct
from pathlib import Path

_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, small, CPU-friendly
# In-process cache so we don't reload the matrix / model per request.
_state: dict = {"tags": None, "vecs": None, "lower": None, "key": None, "embed": None}


def _read_meta(path: str) -> dict:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        return json.loads(f.read(n)).get("__metadata__", {})


# Keep entries inclusive — sentence fragments are fine if they're relevant
# (relevance is decided by the match, not by pruning). A generous cap only
# guards against pathological multi-KB blobs.
_MAX_CHARS = 120


def _norm(t: str) -> str:
    # Danbooru raw tags use '_' for spaces; prompts use spaces. Normalise so
    # 'huge_breasts' and 'huge breasts' merge, casing folds.
    return " ".join((t or "").replace("_", " ").lower().split())


def lora_tags(path: str) -> dict[str, int]:
    """The {tag: frequency} a single LoRA was trained on (normalised)."""
    out: dict[str, int] = {}
    try:
        tf = _read_meta(path).get("ss_tag_frequency")
    except Exception:  # noqa: BLE001
        return out
    if not tf:
        return out
    try:
        tf = json.loads(tf) if isinstance(tf, str) else tf
        for _ds, tags in tf.items():
            for t, c in tags.items():
                t = _norm(t)
                if t and len(t) <= _MAX_CHARS:
                    out[t] = out.get(t, 0) + int(c)
    except Exception:  # noqa: BLE001
        pass
    return out


def build_vocab(loras_dir: str) -> dict[str, int]:
    """Aggregate {tag: total_frequency} across every LoRA's ss_tag_frequency."""
    vocab: dict[str, int] = {}
    for p in glob.glob(os.path.join(loras_dir, "**", "*.safetensors"), recursive=True):
        for t, c in lora_tags(p).items():
            vocab[t] = vocab.get(t, 0) + c
    return vocab


def _embedder():
    if _state["embed"] is None:
        from fastembed import TextEmbedding
        _state["embed"] = TextEmbedding(model_name=_MODEL)
    return _state["embed"]


def ensure_index(root: str | Path, loras_dir: str) -> dict:
    """Build (or load from cache) the embedded tag index. Returns status."""
    import numpy as np

    vocab = build_vocab(loras_dir)
    tags = sorted(vocab, key=lambda t: -vocab[t])
    key = hashlib.md5("|".join(tags).encode("utf-8")).hexdigest()[:12]

    if _state["key"] == key and _state["vecs"] is not None:
        return {"tags": len(tags), "cached": "memory", "key": key}

    cache = Path(root) / ".cache"
    cache.mkdir(exist_ok=True)
    npy, tj = cache / f"lora_tags_{key}.npy", cache / f"lora_tags_{key}.json"

    source = "disk"
    if npy.exists() and tj.exists():
        vecs = np.load(npy)
        tags = json.loads(tj.read_text(encoding="utf-8"))
    else:
        source = "built"
        vecs = np.asarray(list(_embedder().embed(tags)), dtype="float32")
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        np.save(npy, vecs)
        tj.write_text(json.dumps(tags), encoding="utf-8")

    _state.update(tags=tags, vecs=vecs, lower={t.lower(): t for t in tags}, key=key)
    return {"tags": len(tags), "cached": source, "key": key}


def tag_idf(loras_dir: str) -> tuple[dict[str, float], int]:
    """Inverse-document-frequency of each tag across the LoRA set, so ubiquitous
    tags ('1girl', in every LoRA) carry near-zero discriminative weight while
    rare, distinctive tags (a specific outfit) carry a lot. This is what stops
    common tags from firing every state LoRA."""
    import math
    df: dict[str, int] = {}
    n = 0
    for p in glob.glob(os.path.join(loras_dir, "**", "*.safetensors"), recursive=True):
        ts = lora_tags(p)
        if not ts:
            continue
        n += 1
        for t in ts:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}
    return idf, n


def route_state(
    root: str | Path,
    loras_dir: str,
    prompt: str,
    candidates: list[dict],
    default_threshold: float = 0.25,
) -> list[dict]:
    """Decide which candidate *state* LoRAs the scene calls for, by matching the
    prompt against each LoRA's own training tags (rarity-weighted). The prompt is
    first canonicalised to real tags (so synonyms/prose snap to vocabulary), then
    each LoRA scores by how much of its *distinctive* signature the scene covers.
    Manual `keys` force-fire regardless. Returns every candidate with its score
    and matched tags (fired or not) for a transparent bench preview."""
    import re

    phrases = [p for p in re.split(r"[,\n]", prompt or "") if p.strip()]
    canon = canonicalize(phrases, root, loras_dir) if phrases else []
    ptags = {c["tag"] for c in canon if c.get("tag")}
    idf, _n = tag_idf(loras_dir)
    plow = (prompt or "").lower()

    results = []
    for cand in candidates:
        name = cand.get("name")
        if not name:
            continue
        tset = lora_tags(os.path.join(loras_dir, name))
        # the LoRA's distinctive signature: tags ranked by frequency × rarity
        sig = sorted(tset, key=lambda t: -(tset[t] * idf.get(t, 1.0)))[:40]
        matched = sorted(ptags & set(sig), key=lambda t: -idf.get(t, 1.0))
        score = sum(idf.get(t, 1.0) for t in matched)
        denom = sum(idf.get(t, 1.0) for t in sig[:8]) or 1.0  # its core distinctiveness
        norm = round(min(1.0, score / denom), 3)
        keys = [k.lower() for k in (cand.get("keys") or []) if k]
        key_hit = next((k for k in keys if k in plow), None)
        thr = cand.get("threshold")
        thr = default_threshold if thr is None else thr
        fired = bool(key_hit) or norm >= thr
        results.append({
            "name": name, "weight": cand.get("weight", 0.7), "fired": fired,
            "score": norm, "threshold": thr, "matched": matched[:8],
            "why": (f"keyword “{key_hit}”" if key_hit else f"tags {', '.join(matched[:3])}" if matched else "matched"),
        })
    return results


def canonicalize(
    phrases: list[str],
    root: str | Path,
    loras_dir: str,
    top_k: int = 3,
    threshold: float = 0.55,
) -> list[dict]:
    """Snap each phrase to the nearest real tag. Exact (case-insensitive) hits
    pass through with score 1.0; otherwise the best embedding match above
    `threshold` wins (None below it, with candidates for transparency)."""
    import numpy as np

    ensure_index(root, loras_dir)
    tags, vecs, lower = _state["tags"], _state["vecs"], _state["lower"]
    emb = _embedder()

    clean = [p.strip() for p in phrases if p and p.strip()]
    out: list[dict] = []
    to_embed = []
    for ph in clean:
        if ph.lower() in lower:
            out.append({"phrase": ph, "tag": lower[ph.lower()], "score": 1.0, "exact": True})
        else:
            out.append({"phrase": ph, "_pending": len(to_embed)})
            to_embed.append(ph)

    if to_embed:
        q = np.asarray(list(emb.embed(to_embed)), dtype="float32")
        q /= np.linalg.norm(q, axis=1, keepdims=True) + 1e-9
        sims = q @ vecs.T  # (n_phrases, n_tags)
        for rec in out:
            if "_pending" not in rec:
                continue
            row = sims[rec.pop("_pending")]
            idx = np.argsort(-row)[:top_k]
            cands = [{"tag": tags[i], "score": round(float(row[i]), 3)} for i in idx]
            best = cands[0]
            rec.update(tag=(best["tag"] if best["score"] >= threshold else None),
                       score=best["score"], candidates=cands, exact=False)
    return out

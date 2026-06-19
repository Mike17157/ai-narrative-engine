"""On-disk cache of single-LoRA triage renders.

The Classify grid renders every installed LoRA against a workflow; those renders
are expensive, so we persist them keyed by (scope, lora) — scope being the
workflow key they were rendered under — and rehydrate the grid on load instead
of re-rendering. Only single-LoRA triage shots are cached here; ad-hoc stack
renders from the TestBench are not.

Lives under out/triage_cache/<scope>/ (out/ is gitignored). Each scope has a
manifest.json mapping the sanitised lora filename → its source name + the prompt
and weight it was rendered with.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path


def _safe(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s or "")[:200]


def _scope_dir(root: Path, scope: str, *, create: bool = False) -> Path:
    d = root / "out" / "triage_cache" / _safe(scope)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path(root: Path, scope: str) -> Path:
    return _scope_dir(root, scope) / "manifest.json"


def _load_manifest(root: Path, scope: str) -> dict:
    p = _manifest_path(root, scope)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — corrupt manifest: start fresh
            pass
    return {}


def save_render(root: Path, scope: str, lora: str, data_url: str,
                *, prompt: str = "", weight: float | None = None) -> None:
    """Persist a triage render (a `data:image/...;base64,` URL) for (scope, lora)."""
    if not scope or not lora or not data_url or "," not in data_url:
        return
    d = _scope_dir(root, scope, create=True)
    safe = _safe(lora)
    (d / f"{safe}.png").write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
    man = _load_manifest(root, scope)
    man[safe] = {"lora": lora, "prompt": prompt, "weight": weight}
    _manifest_path(root, scope).write_text(
        json.dumps(man, indent=2, ensure_ascii=False), encoding="utf-8")


def list_renders(root: Path, scope: str) -> dict:
    """Map lora-name → {prompt, weight} for every cached render in `scope`.

    The caller builds the image URL itself (so query-encoding stays its concern).
    Drops manifest entries whose PNG has gone missing.
    """
    out: dict = {}
    for safe, meta in _load_manifest(root, scope).items():
        if not (_scope_dir(root, scope) / f"{safe}.png").is_file():
            continue
        out[meta.get("lora", safe)] = {"prompt": meta.get("prompt", ""), "weight": meta.get("weight")}
    return out


def render_path(root: Path, scope: str, lora: str) -> Path | None:
    p = _scope_dir(root, scope) / f"{_safe(lora)}.png"
    return p if p.is_file() else None

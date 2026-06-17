"""Best-effort enrichment from the comfyui-lora-manager plugin's HTTP API.

When ComfyUI runs with comfyui-lora-manager installed, it serves per-file metadata at
`GET /api/lm/{loras|checkpoints}/info/{name}`, including the civitai `base_model` string
("Illustrious", "Pony", "NoobAI", "Flux.1 D", …). We read that to split the SDXL bucket into
sub-families that tensor signatures can't distinguish.

Entirely optional: if the plugin or ComfyUI is down the call fails fast and family resolution falls
back to folders. Results are cached to `configs/.cache/lm_meta.json` so we never hammer the API.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote

import httpx

_TTL = 14 * 24 * 3600  # base_model never changes for a given file → cache for two weeks


def _cache_path(root: Path) -> Path:
    d = root / "configs" / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / "lm_meta.json"


def _load_cache(root: Path) -> dict:
    p = _cache_path(root)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {}
        except (ValueError, OSError):
            return {}
    return {}


def _save_cache(root: Path, data: dict) -> None:
    try:
        _cache_path(root).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _extract_base(meta: dict) -> str | None:
    if not isinstance(meta, dict):
        return None
    for key in ("base_model", "baseModel"):
        v = meta.get(key)
        if isinstance(v, str) and v.strip() and v.strip().lower() != "unknown":
            return v.strip()
    civ = meta.get("civitai") or meta.get("civitai_info") or {}
    if isinstance(civ, dict):
        for key in ("baseModel", "base_model"):
            v = civ.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def lm_base_model(root: Path, base_url: str, kind: str, name: str) -> str | None:
    """Civitai `base_model` for one file, or None. `kind` ∈ {'loras','checkpoints'}; `name` is the
    folder-relative filename. Cached (including negative results, briefly)."""
    if not name:
        return None
    cache = _load_cache(root)
    ck = f"{kind}:{name}"
    ent = cache.get(ck)
    now = time.time()
    if ent and (now - ent.get("t", 0) < _TTL):
        return ent.get("base") or None

    base = None
    try:
        url = base_url.rstrip("/") + f"/api/lm/{kind}/info/" + quote(name.replace("\\", "/"), safe="")
        with httpx.Client(timeout=8) as c:
            r = c.get(url)
            if r.status_code == 200:
                base = _extract_base(r.json())
    except Exception:  # noqa: BLE001 — plugin/ComfyUI down or odd shape → silent fallback
        base = None

    cache[ck] = {"base": base or "", "t": now}
    _save_cache(root, cache)
    return base

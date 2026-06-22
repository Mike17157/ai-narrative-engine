"""Civitai LoRA import — fetch the image-preset LoRA stacks from Civitai on demand.

Image presets (``configs/image_presets.json``) reference LoRAs by BARE filename
(``anima_qr4k_v1.0-epoch14``). Those weights aren't in the repo and aren't all on
every machine — they live on Civitai in the public "Anima base v1.0" artist-style
ecosystem. ``configs/civitai_loras.json`` maps each bare name to the Civitai model
*version* that provides it.

This module is the single resolver both the app (startup auto-download) and the
runpod tooling (manifest inclusion) use, so "which LoRAs does a preset need and
where does each come from" lives in exactly one place.

Downloads require a Civitai API token (``CIVITAI_API_TOKEN`` / ``CIVITAI_TOKEN``);
the public download endpoint returns 401 without one. With no token set, the
auto-download is a silent no-op — manifest resolution still works for files that
are already present locally.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_MAP_FILE = "civitai_loras.json"
_DOWNLOAD_URL = "https://civitai.com/api/download/models/{version_id}"
# LoraManager-style bare names carry no extension; a real file is one of these.
_LORA_EXTS = (".safetensors", ".ckpt", ".pt")


# ── source map ──────────────────────────────────────────────────────────────────
def load_lora_map(root: Path) -> dict[str, dict]:
    """``{bare_name: {filename, version_id, subdir}}`` from configs/civitai_loras.json.

    `subdir` defaults to the file-level ``subdir`` (usually "", i.e. the loras root).
    Tolerant: a missing/!malformed map yields {} so callers degrade to "nothing to do"."""
    path = root / "configs" / _MAP_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except (ValueError, OSError):
        return {}
    default_subdir = str(data.get("subdir") or "").strip().strip("/")
    out: dict[str, dict] = {}
    for entry in data.get("loras") or []:
        name = str((entry or {}).get("name") or "").strip()
        vid = (entry or {}).get("version_id")
        if not name or not vid:
            continue
        filename = str(entry.get("filename") or f"{name}.safetensors").strip()
        out[name] = {
            "filename": filename,
            "version_id": int(vid),
            "subdir": str(entry.get("subdir") or default_subdir).strip().strip("/"),
        }
    return out


# ── what the presets need ─────────────────────────────────────────────────────────
def preset_lora_names(root: Path) -> list[str]:
    """Every distinct LoRA bare-name referenced by an enabled image preset (sans 'none')."""
    from ..server.services import image_presets as IP
    names: list[str] = []
    seen: set[str] = set()
    for p in IP.load_image_presets(root).get("presets") or []:
        if p.get("id") == "none":
            continue
        for lr in p.get("loras") or []:
            n = str((lr or {}).get("name") or "").strip()
            if n and n not in seen:
                seen.add(n)
                names.append(n)
    return names


def _basenames_present(loras_dir: Path) -> set[str]:
    """Lowercase bare basenames (no ext) of every LoRA file under the loras dir, recursive —
    so a file in any subfolder counts as present (ComfyUI resolves by basename too)."""
    present: set[str] = set()
    if not loras_dir.is_dir():
        return present
    for p in loras_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in _LORA_EXTS:
            present.add(p.stem.lower())
    return present


def resolve_preset_loras(root: Path) -> list[dict]:
    """Manifest helper: for each preset LoRA that HAS a Civitai mapping, the local layout
    ``{name, filename, subdir, rel}`` where ``rel`` is the loras-dir-relative path
    (``anima_qr4k_v1.0-epoch14.safetensors`` or ``anima/…`` when a subdir is set).
    Unmapped names are skipped (logged once by callers). This is what folds preset LoRAs
    into models_manifest.json so upload_models.py ships them to the volume."""
    lmap = load_lora_map(root)
    out: list[dict] = []
    for name in preset_lora_names(root):
        ent = lmap.get(name)
        if not ent:
            continue
        rel = f"{ent['subdir']}/{ent['filename']}" if ent["subdir"] else ent["filename"]
        out.append({"name": name, "filename": ent["filename"],
                    "subdir": ent["subdir"], "rel": rel.replace("\\", "/")})
    return out


# ── download ───────────────────────────────────────────────────────────────────────
def _token() -> str:
    return (os.environ.get("CIVITAI_API_TOKEN") or os.environ.get("CIVITAI_TOKEN") or "").strip()


def download_version(version_id: int, dest: Path, token: str, *, timeout: float = 600) -> bool:
    """Stream one Civitai model version to ``dest`` (atomic via a .part temp). Returns success.
    Never raises — logs and returns False so a single bad fetch can't break startup."""
    import httpx
    url = _DOWNLOAD_URL.format(version_id=version_id)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(follow_redirects=True, timeout=timeout) as c:
            with c.stream("GET", url, params={"token": token},
                          headers={"Authorization": f"Bearer {token}"}) as r:
                if r.status_code != 200:
                    log.warning("civitai: download %s -> HTTP %s (%s)", version_id,
                                r.status_code, dest.name)
                    return False
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_bytes(1024 * 1024):
                        fh.write(chunk)
        # A 401/HTML error page would be tiny; a real LoRA is tens-to-hundreds of MB.
        if tmp.stat().st_size < 1024 * 64:
            log.warning("civitai: download %s suspiciously small (%d bytes) — discarding",
                        version_id, tmp.stat().st_size)
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dest)
        return True
    except Exception as exc:  # noqa: BLE001 — network/disk: report, never fatal
        log.warning("civitai: download %s failed: %s", version_id, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# ── metadata (description / trigger words / source page) ──────────────────────────
import re as _re

_META_TTL = 30 * 24 * 3600  # descriptions barely change → cache a month (incl. negatives)


def _meta_cache(root: Path) -> Path:
    d = root / "configs" / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / "civitai_meta.json"


def _load_meta_cache(root: Path) -> dict:
    p = _meta_cache(root)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {}
        except (ValueError, OSError):
            return {}
    return {}


def _save_meta_cache(root: Path, data: dict) -> None:
    try:
        _meta_cache(root).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _clean_html(s: str, limit: int = 700) -> str:
    """Civitai descriptions are HTML — strip tags, collapse whitespace, truncate."""
    txt = _re.sub(r"<[^>]+>", " ", s or "")
    txt = _re.sub(r"&(nbsp|amp|lt|gt|quot|#39);", lambda m: {
        "nbsp": " ", "amp": "&", "lt": "<", "gt": ">", "quot": '"', "#39": "'"}[m.group(1)], txt)
    txt = " ".join(txt.split())
    return (txt[:limit].rstrip() + "…") if len(txt) > limit else txt


def _find_lora_file(loras_dir: Path, name: str) -> Path | None:
    """Locate a LoRA file by its rel path or bare basename under the loras dir."""
    if not loras_dir or not loras_dir.is_dir():
        return None
    rel = name.replace("\\", "/")
    direct = loras_dir / Path(*rel.split("/"))
    if direct.is_file():
        return direct
    stem = Path(rel).stem.lower()
    for p in loras_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in _LORA_EXTS and p.stem.lower() == stem:
            return p
    return None


def _sha256(path: Path) -> str | None:
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _shape_version(ver: dict) -> dict:
    """Pick the human-useful fields out of a Civitai model-version response."""
    model = ver.get("model") or {}
    mid = ver.get("modelId")
    return {
        "model_id": mid,
        "model_name": model.get("name"),
        "version_name": ver.get("name"),
        "base_model": ver.get("baseModel"),
        "trained_words": [w for w in (ver.get("trainedWords") or []) if isinstance(w, str)],
        "nsfw": bool(model.get("nsfw")),
        "page_url": f"https://civitai.com/models/{mid}" if mid else None,
        "version_description": _clean_html(ver.get("description") or ""),
    }


def lora_metadata(root: Path, name: str, loras_dir: Path | None = None) -> dict:
    """Civitai metadata for one LoRA (by name): model name, description, trigger words, source
    page. Resolves the version via configs/civitai_loras.json first, else by file SHA256. Cached
    to configs/.cache/civitai_meta.json (incl. negative results). Metadata endpoints are public —
    no token needed. Returns {found: bool, ...}; never raises."""
    import time as _t
    cache = _load_meta_cache(root)
    ent = cache.get(name)
    now = _t.time()
    if ent and (now - ent.get("_t", 0) < _META_TTL):
        return {k: v for k, v in ent.items() if k != "_t"}

    out: dict = {"name": name, "found": False}
    try:
        import httpx
        vid = (load_lora_map(root).get(name) or {}).get("version_id")
        with httpx.Client(timeout=15, follow_redirects=True) as c:
            ver = None
            if vid:
                r = c.get(f"https://civitai.com/api/v1/model-versions/{vid}")
                if r.status_code == 200:
                    ver = r.json()
            if ver is None:  # no mapping → match the file by hash
                f = _find_lora_file(loras_dir, name) if loras_dir else None
                digest = _sha256(f) if f else None
                if digest:
                    r = c.get(f"https://civitai.com/api/v1/model-versions/by-hash/{digest}")
                    if r.status_code == 200:
                        ver = r.json()
            if ver:
                out.update(_shape_version(ver))
                out["found"] = True
                # Enrich with the parent model's longer description + tags + creator.
                if out.get("model_id"):
                    rm = c.get(f"https://civitai.com/api/v1/models/{out['model_id']}")
                    if rm.status_code == 200:
                        m = rm.json()
                        out["description"] = _clean_html(m.get("description") or "") or out.get("version_description") or ""
                        out["tags"] = [t for t in (m.get("tags") or []) if isinstance(t, str)][:10]
                        out["creator"] = (m.get("creator") or {}).get("username")
                out.setdefault("description", out.get("version_description") or "")
    except Exception as exc:  # noqa: BLE001 — network/shape: cache the miss briefly
        out["reason"] = str(exc)

    cache[name] = {**out, "_t": now if out["found"] else now - _META_TTL + 3600}  # retry misses in 1h
    _save_meta_cache(root, cache)
    return out


def ensure_preset_loras(root: Path, loras_dir: Path) -> dict:
    """Download every preset LoRA that's mapped but absent from ``loras_dir``.

    Returns a summary ``{present, downloaded, failed, unmapped, skipped}``. No-op (skipped)
    when no Civitai token is set. Best-effort: individual failures are collected, not raised."""
    lmap = load_lora_map(root)
    needed = preset_lora_names(root)
    present = _basenames_present(loras_dir)

    missing = [n for n in needed if n.lower() not in present]
    mapped_missing = [n for n in missing if n in lmap]
    unmapped = [n for n in missing if n not in lmap]

    summary = {"present": len(needed) - len(missing), "downloaded": [],
               "failed": [], "unmapped": unmapped, "skipped": False}

    if not mapped_missing:
        return summary

    token = _token()
    if not token:
        summary["skipped"] = True
        log.warning("civitai: %d preset LoRA(s) missing but CIVITAI_API_TOKEN unset — "
                    "skipping auto-download: %s", len(mapped_missing), ", ".join(mapped_missing))
        return summary

    for name in mapped_missing:
        ent = lmap[name]
        dest = loras_dir / ent["subdir"] / ent["filename"] if ent["subdir"] else loras_dir / ent["filename"]
        log.info("civitai: downloading %s (v%s) -> %s", name, ent["version_id"], dest.name)
        if download_version(ent["version_id"], dest, token):
            summary["downloaded"].append(name)
        else:
            summary["failed"].append(name)
    log.info("civitai: preset LoRA sync — %d downloaded, %d failed, %d unmapped",
             len(summary["downloaded"]), len(summary["failed"]), len(unmapped))
    return summary

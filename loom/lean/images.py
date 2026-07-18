"""Minimal Story-image capability shared by the lean HTTP surface.

Krea2 is currently a ComfyUI workflow in this repository.  This module keeps
only the safe, small part required for ordinary Story renders: resolve an
allow-listed Krea workflow, render it on demand, and persist an image under
the owning Story.  It intentionally knows nothing about model catalogs,
workflow editing, LoRAs, training, tags, or RunPod.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from ..server.services.images import _render


_ROLES = frozenset({"scene", "base", "sprite", "chat"})


def _safe_segment(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-_")
    return cleaned or fallback


def available_krea_models(ctx) -> list[str]:
    """Return only registered local Krea2 Comfy workflows, in stable order."""
    return sorted(
        key for key, model in ctx.base_settings.models.items()
        if key.startswith("krea2") and model.kind == "image" and model.provider == "comfyui"
    )


def resolve_krea_model(ctx, *, role: str = "scene", model: str | None = None) -> str:
    """Resolve one of the four ordinary Story roles without widening authority."""
    clean_role = str(role or "scene").strip().lower()
    if clean_role not in _ROLES:
        raise ValueError("image role must be one of: scene, base, sprite, chat")
    requested = str(model or "").strip() or ctx.role_model(clean_role)
    if requested not in available_krea_models(ctx):
        raise ValueError("the lean image capability accepts only configured Krea2 workflows")
    return requested


def story_image_directory(root: Path, story_key: str, *, create: bool = False) -> Path:
    directory = root / "configs" / "stories" / _safe_segment(story_key, "story") / "images"
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def list_story_images(root: Path, story_key: str) -> list[dict[str, str]]:
    directory = story_image_directory(root, story_key)
    if not directory.is_dir():
        return []
    out: list[dict[str, str]] = []
    for path in sorted(directory.glob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True):
        image_id = path.stem
        role = image_id.split("-", 1)[0]
        item = {"id": image_id, "url": f"/api/stories/{story_key}/images/{path.name}"}
        if role in _ROLES:
            item["role"] = role
        out.append(item)
    return out


def story_image_path(root: Path, story_key: str, image_name: str) -> Path | None:
    name = Path(str(image_name or "")).name
    if not name.endswith(".png") or name != image_name:
        return None
    candidate = story_image_directory(root, story_key) / name
    return candidate if candidate.is_file() else None


async def render_story_image(ctx, *, story_key: str, prompt: str, role: str = "scene",
                             model: str | None = None) -> dict[str, Any]:
    """Render one image with the chosen Krea2 workflow and persist it safely."""
    selected_model = resolve_krea_model(ctx, role=role, model=model)
    provider, resolved_model = ctx.image_provider(selected_model)
    if provider is None:
        raise RuntimeError(str(resolved_model or "no Krea2 image provider is configured"))
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("image prompt is required")
    if len(clean_prompt) > 12_000:
        raise ValueError("image prompt is too long")
    clean_role = str(role or "scene").strip().lower()
    raw = await _render(
        provider,
        clean_prompt,
        out_prefix=f"loom/lean/{_safe_segment(story_key, 'story')}/{clean_role}",
    )
    if not raw:
        raise RuntimeError("the Krea2 workflow returned no image")
    image_id = f"{clean_role}-{uuid.uuid4().hex}"
    path = story_image_directory(ctx.root, story_key, create=True) / f"{image_id}.png"
    path.write_bytes(raw)
    return {
        "id": image_id,
        "role": clean_role,
        "model": selected_model,
        "url": f"/api/stories/{story_key}/images/{path.name}",
    }

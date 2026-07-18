"""Isolation coverage for the Story Host's optional Krea2 image protocol."""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from loom.lean import images as image_capability
from loom.lean import stdio
from loom.server.services import story_store


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "story-image-stdio"
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    story_store.save_story(root, "bridge", {
        "name": "Bridge",
        "type": "novel",
        "premise": "A ferry approaches a storm-bound island.",
        "world": {"setting": "A wet island crossing."},
        "locations": [{"id": "ferry", "name": "Electric ferry", "description": "A quiet deck."}],
        "start": "ferry",
        "fields": {"status": "interviewing"},
    })
    return root


def _context() -> SimpleNamespace:
    # The stdio capability only needs a model projection for its allow-list and
    # an existing SQLite record for Story ownership. It must not need a running
    # Comfy process for status/list/validation work.
    return SimpleNamespace(base_settings=SimpleNamespace(models={
        "krea2_test": SimpleNamespace(kind="image", provider="comfyui"),
    }))


def test_image_status_is_non_starting_and_request_is_disabled_explicitly(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr(stdio, "_image_enabled", lambda: False)
    monkeypatch.setattr(stdio, "_image_context", lambda _root: _context())

    status = stdio.handle_request(root, {"op": "image.status", "payload": {}})
    assert status == {
        "enabled": False,
        "configured": True,
        "models": ["krea2_test"],
        "roles": ["scene", "base", "sprite", "chat"],
        "running": False,
        "message": "Krea2/Comfy rendering is disabled for this Story Host. Set LOOM_STORY_HOST_COMFY=1 to enable it.",
    }

    with pytest.raises(stdio.BridgeError, match="disabled") as disabled:
        stdio.handle_request(root, {"op": "image.request", "payload": {
            "key": "bridge", "prompt": "Rain on an empty ferry deck.",
        }})
    assert disabled.value.code == "image_disabled"

    with pytest.raises(stdio.BridgeError, match="raw workflows") as forbidden:
        stdio.handle_request(root, {"op": "image.request", "payload": {
            "key": "bridge", "prompt": "Rain on an empty ferry deck.", "workflow": "anything",
        }})
    assert forbidden.value.code == "forbidden_capability"


def test_image_request_returns_a_story_asset_not_an_http_url(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    captured: dict[str, object] = {}

    async def fake_render(ctx, *, story_key: str, prompt: str, role: str, model: str | None):
        captured.update({"ctx": ctx, "story_key": story_key, "prompt": prompt, "role": role, "model": model})
        return {"id": "scene-proof", "role": "scene", "model": "krea2_test", "url": "/api/should-not-leak"}

    monkeypatch.setattr(stdio, "_image_enabled", lambda: True)
    monkeypatch.setattr(stdio, "_image_context", lambda _root: _context())
    monkeypatch.setattr(image_capability, "render_story_image", fake_render)

    result = stdio.handle_request(root, {"op": "image.request", "payload": {
        "key": "bridge", "prompt": "Rain on an empty ferry deck.", "role": "scene", "model": "krea2_test",
    }})

    assert captured["story_key"] == "bridge"
    assert captured["prompt"] == "Rain on an empty ferry deck."
    assert result == {
        "image": {
            "id": "scene-proof",
            "role": "scene",
            "model": "krea2_test",
            "asset": {"story_key": "bridge", "image_name": "scene-proof.png", "media_type": "image/png"},
        },
    }
    assert "url" not in result["image"]

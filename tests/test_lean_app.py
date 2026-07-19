"""Regression coverage for the parallel Story-only application surface."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from loom.lean import create_lean_app


@contextmanager
def _lean_client():
    root = Path(tempfile.mkdtemp(prefix="loom-lean-app-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    # The global library lives in configs/stories.db now; seed one card so the
    # cast tests have a library character to embed.
    from loom.server.services import card_store
    card_store.upsert_character(root, "shared", {"name": "Library Shared", "system": "",
                                                 "fields": {"role": "Library role"}})
    client = TestClient(create_lean_app(root, comfy_enabled=False))
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


def _create_story_with_cast(client, character_key: str, name: str) -> str:
    """Build a persisted cast fixture without re-exposing the global cast import API."""
    return client.app.state.lean_context.create_story(
        name,
        {"cast": [{"character": character_key}], "fields": {"status": "interviewing"}},
        character_keys=[character_key],
    )


def test_lean_app_exposes_story_surface_without_legacy_domains():
    with _lean_client() as client:
        assert not (client.app.state.lean_context.root / "secrets" / "connections.json").exists()
        health = client.get("/api/lean/health")
        status = client.get("/api/lean/images/status")
        created = client.post("/api/stories/new", json={"name": "Lean proof"})
        assert created.status_code == 200, created.text
        key = created.json()["key"]
        disabled_render = client.post(f"/api/stories/{key}/images/render", json={
            "role": "scene", "prompt": "Rain on an empty ferry deck.",
        })

        assert client.get("/api/comfy/models").status_code == 404
        assert client.get(f"/api/stories/{key}/cast").status_code == 200
        assert client.get("/api/characters").status_code == 404
        assert client.post("/api/characters/nope/generate-all", json={}).status_code == 404
        assert client.get("/api/poses").status_code == 404
        assert client.get("/api/jobs").status_code == 404
        assert client.get("/api/agent/modes").status_code == 404
        assert client.get("/api/stages").status_code == 404
        assert client.get("/api/tools").status_code == 404
        assert client.get("/api/text-roles").status_code == 404
        assert client.get("/api/lorebook/_global").status_code == 404
        # The generic `GET /api/stories/{key}` shape may yield 405 here; the
        # route-table assertion below proves the retired POST route is absent.
        assert client.post("/api/stories/from-cast", json={"characters": []}).status_code in {404, 405}
        assert client.post("/api/stories/workshop", json={}).status_code in {404, 405}
        assert client.post("/api/stories/agent/dump-prompt", json={}).status_code == 404
        # `/api/stories/{key}` owns the same one-segment shape, so FastAPI may
        # return 405 rather than 404. The route-table check below proves the
        # excluded POST handler itself is not registered.
        assert client.post("/api/stories/run-stage", json={}).status_code in {404, 405}
        assert client.post(f"/api/stories/{key}/manuscript/illustrate", json={}).status_code == 404
        # Cast regeneration belongs to the retired general image/job pipeline,
        # not to the lean Story surface.
        assert client.post(f"/api/stories/{key}/regenerate-cast", json={}).status_code == 404
        assert client.get("/api/stories/session/play-anything").status_code == 404
        session = client.put(f"/api/stories/{key}/session", json={"lorebooks": ["world"]})
        assert session.status_code == 200, session.text
        assert client.get(f"/api/stories/{key}/session").json()["lorebooks"] == ["world"]
        assert client.get(f"/api/stories/{key}/state?sid=play-some-other-story").status_code == 400
        assert client.post(f"/api/stories/{key}/state/reset", json={"sid": "play-some-other-story"}).status_code == 400

    assert health.status_code == 200, health.text
    assert health.json()["surface"] == "story"
    assert status.status_code == 200, status.text
    assert status.json()["enabled"] is False
    assert disabled_render.status_code == 503, disabled_render.text


def test_lean_image_capability_rejects_non_krea_model_before_rendering():
    with _lean_client() as client:
        created = client.post("/api/stories/new", json={"name": "Lean image guard"})
        assert created.status_code == 200, created.text
        key = created.json()["key"]
        # Enablement is intentionally checked first by the HTTP API.  Exercise
        # the pure model allow-list directly so this test never launches Comfy.
        from loom.lean.images import resolve_krea_model, story_image_directory

        ctx = client.app.state.lean_context
        try:
            resolve_krea_model(ctx, role="scene", model="opus")
        except ValueError as exc:
            assert "Krea2" in str(exc)
        else:  # pragma: no cover - an accidental broad image route is a hard failure
            raise AssertionError("lean image capability accepted a text model")

        image_dir = story_image_directory(ctx.root, key, create=True)
        (image_dir / "scene-proof.png").write_bytes(b"not-a-real-png")
        listed = client.get(f"/api/stories/{key}/images").json()["images"]
        assert listed[0]["id"] == "scene-proof"
        assert listed[0]["role"] == "scene"
        assert client.get("/api/stories/not-a-story/images/scene-proof.png").status_code == 404


def test_lean_route_table_has_only_story_and_image_surfaces():
    with _lean_client() as client:
        api_paths = [route.path for route in client.app.routes if getattr(route, "path", "").startswith("/api/")]
        retired = [route for route in client.app.routes
                   if getattr(route, "path", "") == "/api/stories/from-cast"]

    unexpected = [path for path in api_paths if not (
        path in {"/api/lean/health", "/api/lean/images/status"}
        or path.startswith("/api/stories/")
        or path == "/api/stories"
    )]
    assert unexpected == []
    assert retired == []


def test_lean_cast_boundary_rejects_a_character_outside_the_story():
    with _lean_client() as client:
        created = client.post("/api/stories/new", json={"name": "Scoped cast"})
        assert created.status_code == 200, created.text
        story_key = created.json()["key"]
        unrelated_key = next(iter(client.app.state.lean_context.base_settings.characters))

        response = client.post(
            f"/api/stories/{story_key}/cast/{unrelated_key}/card",
            json={"name": "This write must not escape the story cast"},
        )

    assert response.status_code == 404, response.text


def test_lean_cast_list_contains_only_the_requested_story_and_scoped_urls():
    with _lean_client() as client:
        ctx = client.app.state.lean_context
        character_key = next(iter(ctx.base_settings.characters))
        story_key = _create_story_with_cast(client, character_key, "Scoped URL test")
        response = client.get(f"/api/stories/{story_key}/cast")

    assert response.status_code == 200, response.text
    cards = response.json()
    expected_keys = {character_key}
    assert {card["key"] for card in cards}.issubset(expected_keys)
    assert all(card["story"] == story_key for card in cards)
    for card in cards:
        for field in ("avatar", "reference"):
            if card[field]:
                assert card[field].startswith(f"/api/stories/{story_key}/cast/{card['key']}/")


def test_lean_cast_edit_isolated_when_two_stories_embed_the_same_source_card():
    """A story-local card edit must never resolve through the first matching owner."""
    with _lean_client() as client:
        ctx = client.app.state.lean_context
        character_key = next(iter(ctx.base_settings.characters))
        first_key = _create_story_with_cast(client, character_key, "First")
        second_key = _create_story_with_cast(client, character_key, "Second")
        safe_key = character_key.replace("/", "")
        first_dir = ctx.char_asset_dir(character_key, story_key=first_key)
        second_dir = ctx.char_asset_dir(character_key, story_key=second_key)
        first_dir.mkdir(parents=True, exist_ok=True)
        second_dir.mkdir(parents=True, exist_ok=True)
        (first_dir / f"{safe_key}.png").write_bytes(b"first-story-asset")
        (second_dir / f"{safe_key}.png").write_bytes(b"second-story-asset")
        original = client.get(f"/api/stories/{first_key}/cast").json()[0]["name"]
        first_avatar = client.get(f"/api/stories/{first_key}/cast/{character_key}/avatar")
        second_avatar = client.get(f"/api/stories/{second_key}/cast/{character_key}/avatar")

        updated = client.post(
            f"/api/stories/{second_key}/cast/{character_key}/card",
            json={"name": "Second-story-only name"},
        )
        assert updated.status_code == 200, updated.text
        first_cards = client.get(f"/api/stories/{first_key}/cast")
        second_cards = client.get(f"/api/stories/{second_key}/cast")
        integrity = client.get("/api/lean/health").json()["integrity"]

    assert first_cards.status_code == 200, first_cards.text
    assert second_cards.status_code == 200, second_cards.text
    assert first_avatar.content == b"first-story-asset"
    assert second_avatar.content == b"second-story-asset"
    assert integrity == {"ready": False, "duplicate_character_keys": {character_key: [first_key, second_key]}}
    assert first_cards.json()[0]["name"] == original
    assert second_cards.json()[0]["name"] == "Second-story-only name"

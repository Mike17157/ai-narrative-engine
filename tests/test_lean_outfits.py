"""Story-scoped wardrobe: outfit CRUD + active-outfit selection on the lean app."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from loom.lean import create_lean_app


@contextmanager
def _lean_client(comfy: bool = False):
    root = Path(tempfile.mkdtemp(prefix="loom-lean-outfits-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    from loom.server.services import card_store
    card_store.upsert_character(root, "shared", {"name": "Library Shared", "system": "A shared card.",
                                                 "fields": {"role": "Library role"}})
    client = TestClient(create_lean_app(root, comfy_enabled=comfy))
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


def _create_story(client, character_key: str, name: str, cast: list | None = None) -> str:
    return client.app.state.lean_context.create_story(
        name,
        {"cast": cast or [{"character": character_key}], "fields": {"status": "interviewing"}},
        character_keys=[character_key],
    )


def _seed_manifest(client, story_key: str, character_key: str) -> None:
    ctx = client.app.state.lean_context
    ctx.save_portrait_manifest(
        character_key,
        {"appearance": "1girl, silver hair, red eyes", "outfits": []},
        story_key=story_key,
    )


def test_outfit_create_list_patch_delete_round_trip_is_story_scoped():
    with _lean_client() as client:
        story_key = _create_story(client, "shared", "Wardrobe story")
        _seed_manifest(client, story_key, "shared")
        base = f"/api/stories/{story_key}/cast/shared/outfits"

        created = client.post(base, json={"name": "Evening Dress",
                                          "instruction": "a deep blue evening dress"})
        assert created.status_code == 200, created.text
        outfit = created.json()
        assert outfit["id"] == "evening-dress"
        assert outfit["instruction"] == "a deep blue evening dress"
        # No image-prompt model is configured in the fixture: the deterministic
        # fallback composes appearance + instruction instead of failing.
        assert "silver hair" in outfit["prompt"]
        assert "deep blue evening dress" in outfit["prompt"]
        assert outfit["attire_prompt"] == outfit["prompt"]
        assert outfit["expressions"] == {}

        # A same-named outfit gets a unique slug, never an overwrite.
        duplicate = client.post(base, json={"name": "Evening Dress", "instruction": "again"})
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["id"] == "evening-dress-2"

        listed = client.get(f"/api/stories/{story_key}/cast/shared/portraits")
        assert listed.status_code == 200, listed.text
        ids = [o["id"] for o in listed.json()["outfits"]]
        assert ids == ["evening-dress", "evening-dress-2"]

        patched = client.patch(f"{base}/evening-dress",
                               json={"name": "Ball Gown", "prompt": "1girl, silver hair, ball gown"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["name"] == "Ball Gown"
        assert patched.json()["prompt"] == "1girl, silver hair, ball gown"
        assert patched.json()["attire_prompt"] == "1girl, silver hair, ball gown"
        assert patched.json()["instruction"] == "a deep blue evening dress"

        deleted = client.delete(f"{base}/evening-dress-2")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"ok": True, "cleared": False}
        ids = [o["id"] for o in client.get(
            f"/api/stories/{story_key}/cast/shared/portraits").json()["outfits"]]
        assert ids == ["evening-dress"]

        # The manifest really is the STORY-scoped one.
        ctx = client.app.state.lean_context
        manifest = ctx.portrait_manifest("shared", story_key=story_key)
        assert [o["id"] for o in manifest["outfits"]] == ["evening-dress"]
        assert manifest["outfits"][0]["name"] == "Ball Gown"


def test_outfit_endpoints_reject_unknown_story_character_and_outfit():
    with _lean_client() as client:
        story_key = _create_story(client, "shared", "Scoped wardrobe")
        _seed_manifest(client, story_key, "shared")
        assert client.post("/api/stories/nope/cast/shared/outfits",
                           json={"name": "X"}).status_code == 404
        assert client.post(f"/api/stories/{story_key}/cast/nope/outfits",
                           json={"name": "X"}).status_code == 404
        assert client.patch(f"/api/stories/{story_key}/cast/shared/outfits/nope",
                            json={"name": "X"}).status_code == 404
        assert client.delete(f"/api/stories/{story_key}/cast/shared/outfits/nope").status_code == 404
        assert client.post(f"/api/stories/{story_key}/cast/shared/outfits",
                           json={"instruction": "no name"}).status_code == 422


def test_outfit_selection_persists_on_the_cast_member_and_clears():
    with _lean_client() as client:
        ctx = client.app.state.lean_context
        story_key = _create_story(client, "shared", "Selection story",
                                  cast=[{"character": "shared", "primary": True}])
        _seed_manifest(client, story_key, "shared")
        client.post(f"/api/stories/{story_key}/cast/shared/outfits",
                    json={"name": "Raincoat", "instruction": "a yellow raincoat"})

        bogus = client.post(f"/api/stories/{story_key}/cast/shared/outfit",
                            json={"outfit": "no-such-outfit"})
        assert bogus.status_code == 422, bogus.text
        unknown = client.post(f"/api/stories/{story_key}/cast/nope/outfit",
                              json={"outfit": "raincoat"})
        assert unknown.status_code == 404, unknown.text

        selected = client.post(f"/api/stories/{story_key}/cast/shared/outfit",
                               json={"outfit": "raincoat"})
        assert selected.status_code == 200, selected.text
        assert selected.json() == {"ok": True, "character": "shared", "outfit": "raincoat"}

        cast = client.get(f"/api/stories/{story_key}").json()["cast"]
        assert cast[0]["outfit"] == "raincoat"
        assert cast[0]["primary"] is True   # the rewrite preserves other member fields
        # The lean cast payload surfaces the selection for the wardrobe UI.
        card = client.get(f"/api/stories/{story_key}/cast").json()[0]
        assert card["outfit"] == "raincoat"

        # cast_doc_to_members carries the selection through a doc round trip.
        members = ctx.cast_doc_to_members(
            [{"character": "shared", "primary": True, "outfit": "raincoat"}])
        assert members == [{"character": "shared", "primary": True, "outfit": "raincoat"}]

        cleared = client.post(f"/api/stories/{story_key}/cast/shared/outfit",
                              json={"outfit": None})
        assert cleared.status_code == 200, cleared.text
        cast = client.get(f"/api/stories/{story_key}").json()["cast"]
        assert cast[0].get("outfit") in (None, "")
        assert cast[0]["primary"] is True


def test_deleting_the_selected_outfit_clears_the_cast_member():
    with _lean_client() as client:
        story_key = _create_story(client, "shared", "Delete selected")
        _seed_manifest(client, story_key, "shared")
        client.post(f"/api/stories/{story_key}/cast/shared/outfits",
                    json={"name": "Raincoat", "instruction": "a yellow raincoat"})
        client.post(f"/api/stories/{story_key}/cast/shared/outfit", json={"outfit": "raincoat"})

        deleted = client.delete(f"/api/stories/{story_key}/cast/shared/outfits/raincoat")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"ok": True, "cleared": True}

        cast = client.get(f"/api/stories/{story_key}").json()["cast"]
        assert cast[0].get("outfit") in (None, "")
        assert client.get(
            f"/api/stories/{story_key}/cast/shared/portraits").json()["outfits"] == []


# ---------------------------------------------------------------------------
# Sprite rendering through the Krea2 capability (the render itself is stubbed —
# no ComfyUI in tests).
# ---------------------------------------------------------------------------

def _seed_render_manifest(client, story_key: str, character_key: str) -> None:
    ctx = client.app.state.lean_context
    ctx.save_portrait_manifest(
        character_key,
        {"appearance": "1girl, silver hair, red eyes",
         "expression_prompts": {"happy": "beaming smile", "sad": "tearful frown"},
         "outfits": [{"id": "raincoat", "name": "Raincoat",
                      "instruction": "a yellow raincoat",
                      "prompt": "1girl, silver hair, yellow raincoat",
                      "attire_prompt": "1girl, silver hair, yellow raincoat",
                      "range": ["neutral", "happy", "sad"], "expressions": {}}]},
        story_key=story_key,
    )


def _sprite_dir(client, story_key: str, oid: str = "raincoat"):
    return client.app.state.lean_context.portrait_dir("shared", story_key=story_key) / oid


def _stub_render(monkeypatch, png: bytes = b"fake-png"):
    """Stub the low-level render + force the deterministic prompt path (no LLM, no ComfyUI)."""
    calls = []

    async def fake_render(provider, prompt, init_image=None, out_prefix=None, latent=None):
        calls.append(prompt)
        return png

    def no_compose(*_a, **_k):
        raise RuntimeError("no compose model in tests")

    monkeypatch.setattr("loom.lean.api.outfits._render", fake_render)
    monkeypatch.setattr("loom.lean.api.outfits.compose_sprite_prompt", no_compose)
    return calls


def test_render_full_set_populates_base_and_expressions_story_scoped(monkeypatch):
    with _lean_client(comfy=True) as client:
        story_key = _create_story(client, "shared", "Render story")
        _seed_render_manifest(client, story_key, "shared")
        calls = _stub_render(monkeypatch)

        r = client.post(f"/api/stories/{story_key}/cast/shared/outfits/raincoat/render", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["base"] == "base.png"
        assert body["rendered"] == ["happy", "sad"]       # neutral rode the base render
        assert body["skipped"] == ["neutral"]
        assert body["failed"] == []
        assert len(calls) == 3                            # base/neutral + happy + sad

        directory = _sprite_dir(client, story_key)
        for fn in ("base.png", "neutral.png", "happy.png", "sad.png"):
            assert (directory / fn).read_bytes() == b"fake-png"
        manifest = client.app.state.lean_context.portrait_manifest("shared", story_key=story_key)
        outfit = manifest["outfits"][0]
        assert outfit["base"] == "base.png"
        assert outfit["expressions"] == {"neutral": "neutral.png", "happy": "happy.png",
                                         "sad": "sad.png"}
        # The prompts follow the legacy formula (emotion-led deterministic assembly).
        assert any("yellow raincoat" in p and "HAPPY" in p for p in calls)

        # The portrait payload now serves sprite URLs for the emotion grid.
        payload = client.get(f"/api/stories/{story_key}/cast/shared/portraits").json()
        exprs = payload["outfits"][0]["expressions"]
        assert exprs["happy"].endswith("/portraits/img/raincoat/happy.png")


def test_render_skips_existing_and_force_rerenders(monkeypatch):
    with _lean_client(comfy=True) as client:
        story_key = _create_story(client, "shared", "Skip story")
        _seed_render_manifest(client, story_key, "shared")
        calls = _stub_render(monkeypatch)
        base = f"/api/stories/{story_key}/cast/shared/outfits/raincoat/render"

        first = client.post(base, json={})
        assert first.status_code == 200, first.text
        assert len(calls) == 3

        second = client.post(base, json={})
        assert second.status_code == 200, second.text
        assert second.json()["rendered"] == []
        assert sorted(second.json()["skipped"]) == ["happy", "neutral", "sad"]
        assert len(calls) == 3                            # nothing re-rendered

        forced = client.post(base, json={"force": True})
        assert forced.status_code == 200, forced.text
        assert sorted(forced.json()["rendered"]) == ["happy", "sad"]
        assert forced.json()["skipped"] == ["neutral"]   # rode the forced base render again
        assert len(calls) == 3 + 3                       # base + happy + sad (neutral reuses base)


def test_render_runner_down_fails_cleanly_and_leaves_manifest_untouched(monkeypatch):
    with _lean_client(comfy=True) as client:
        story_key = _create_story(client, "shared", "Down story")
        _seed_render_manifest(client, story_key, "shared")

        async def down(_provider, _prompt, init_image=None, out_prefix=None, latent=None):
            raise ConnectionError("runner unreachable")

        monkeypatch.setattr("loom.lean.api.outfits._render", down)
        monkeypatch.setattr("loom.lean.api.outfits.compose_sprite_prompt",
                            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no model")))

        r = client.post(f"/api/stories/{story_key}/cast/shared/outfits/raincoat/render", json={})
        assert r.status_code == 503, r.text
        assert r.json()["retryable"] is True
        assert "runner" in r.json()["error"]

        manifest = client.app.state.lean_context.portrait_manifest("shared", story_key=story_key)
        outfit = manifest["outfits"][0]
        assert "base" not in outfit
        assert outfit["expressions"] == {}
        assert not any(_sprite_dir(client, story_key).glob("*.png"))


def test_render_per_emotion_failure_is_reported_not_fatal(monkeypatch):
    with _lean_client(comfy=True) as client:
        story_key = _create_story(client, "shared", "Partial story")
        _seed_render_manifest(client, story_key, "shared")

        async def flaky(_provider, prompt, init_image=None, out_prefix=None, latent=None):
            if "SAD" in prompt:
                raise RuntimeError("one bad emotion")
            return b"fake-png"

        monkeypatch.setattr("loom.lean.api.outfits._render", flaky)
        monkeypatch.setattr("loom.lean.api.outfits.compose_sprite_prompt",
                            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no model")))

        r = client.post(f"/api/stories/{story_key}/cast/shared/outfits/raincoat/render", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["rendered"] == ["happy"]
        assert body["failed"] == [{"emotion": "sad", "status": "error", "error": "one bad emotion"}]
        manifest = client.app.state.lean_context.portrait_manifest("shared", story_key=story_key)
        expressions = manifest["outfits"][0]["expressions"]
        assert set(expressions) == {"neutral", "happy"}   # only successful renders recorded
        assert not (_sprite_dir(client, story_key) / "sad.png").exists()


def test_render_single_emotion_and_validation(monkeypatch):
    with _lean_client(comfy=True) as client:
        story_key = _create_story(client, "shared", "Single story")
        _seed_render_manifest(client, story_key, "shared")
        calls = _stub_render(monkeypatch)
        base = f"/api/stories/{story_key}/cast/shared/outfits/raincoat/render"

        r = client.post(f"{base}/happy", json={})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rendered"
        assert r.json()["url"].endswith("/portraits/img/raincoat/happy.png")
        assert (_sprite_dir(client, story_key) / "happy.png").exists()
        assert len(calls) == 1

        again = client.post(f"{base}/happy", json={})     # skip-existing on the single route too
        assert again.json()["status"] == "skipped"
        assert len(calls) == 1

        # Rendering neutral backfills a missing base with the same image.
        neutral = client.post(f"{base}/neutral", json={})
        assert neutral.status_code == 200, neutral.text
        manifest = client.app.state.lean_context.portrait_manifest("shared", story_key=story_key)
        assert manifest["outfits"][0]["base"] == "base.png"

        assert client.post(f"{base}/not-an-emotion", json={}).status_code == 422
        assert client.post(
            "/api/stories/nope/cast/shared/outfits/raincoat/render", json={}).status_code == 404
        assert client.post(
            f"/api/stories/{story_key}/cast/nope/outfits/raincoat/render", json={}).status_code == 404
        assert client.post(
            f"/api/stories/{story_key}/cast/shared/outfits/nope/render", json={}).status_code == 404


def test_render_reports_disabled_runner_without_touching_manifest():
    with _lean_client(comfy=False) as client:   # the standard fixture disables Comfy
        story_key = _create_story(client, "shared", "Disabled story")
        _seed_render_manifest(client, story_key, "shared")
        r = client.post(f"/api/stories/{story_key}/cast/shared/outfits/raincoat/render", json={})
        assert r.status_code == 503, r.text
        assert r.json()["retryable"] is False
        manifest = client.app.state.lean_context.portrait_manifest("shared", story_key=story_key)
        assert manifest["outfits"][0]["expressions"] == {}

"""HTTP coverage for the thin, browser-orchestrated public Architect boundary."""
from __future__ import annotations

from contextlib import contextmanager
import json
import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app
from loom.server.context_providers import ProviderContextMixin
import loom.stories.authoring.card_graph as card_graph


@contextmanager
def _isolated_client():
    root = Path(tempfile.mkdtemp(prefix="loom-public-architect-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    for card in (root / "configs" / "characters").glob("*.yaml"):
        card.unlink()
    client = TestClient(create_app(root))
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


@contextmanager
def _developer(proposal: dict):
    original_operation = card_graph.run_card_operation
    original_provider = ProviderContextMixin.text_provider_for
    calls: list[dict] = []

    async def develop(**kwargs):
        calls.append(kwargs)
        return proposal

    card_graph.run_card_operation = develop
    ProviderContextMixin.text_provider_for = lambda _self, *_args, **_kwargs: object()
    try:
        yield calls
    finally:
        card_graph.run_card_operation = original_operation
        ProviderContextMixin.text_provider_for = original_provider


def _new_story(client: TestClient) -> str:
    response = client.post("/api/stories/new", json={"name": "Public Architect test"})
    assert response.status_code == 200, response.text
    return response.json()["key"]


def _public_world_proposal() -> dict:
    return {
        "message": "I added a little weathered atmosphere to the crossing.",
        "world": {"atmosphere": "Salt mist makes the ferry windows glow."},
        "premise": "",
        "locations": [],
        "start": "",
        "time_system": {},
        "first_day_plan": {},
        "characters": [],
        "relationships": [],
        "open_questions": [],
    }


def _private_top_level_documents(private: str) -> dict:
    """Valid rich-card documents that never belong in a public worker view."""
    return {
        "scenes": [{"id": "private-scene", "title": private}],
        "arcs": [{"id": "private-arc", "name": private}],
        "chapters": [{"id": "private-chapter", "title": private}],
        "storyboard": {"heart": private},
        "lorebook": {"private": private},
    }


def test_public_context_is_model_safe_and_has_only_public_capabilities():
    private = "PRIVATE_PUBLIC_CAPABILITY_SENTINEL"
    with _isolated_client() as client:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": f"A ferry reaches an island. [[hidden]]{private}[[/hidden]]",
            "world": {
                "setting": "An island just before the rain.",
                "background": private,
                "entity": {"description": private, "knowledge": private},
                "loop": {"start": private, "reset": private},
            },
            "locations": [{"id": "ferry", "name": "Electric ferry"}],
            "start": "ferry",
            "time_system": {"slots": ["morning"], "entity_periods": [{
                "id": "hunt", "slots": ["morning"], "state": "observing",
                "capabilities": [private], "constraint": private,
            }]},
            "fields": {
                "author_notes": [f"[[hidden]]{private}[[/hidden]]"],
                "character_wounds": {"player": private},
                "first_day_plan": {"events": [{
                    "id": "arrival", "when": "morning", "location": "ferry",
                    "participants": ["player"], "visible": "The ferry slows at the dock.",
                    "hidden": private, "trigger": private, "knowledge": {"player": private},
                }]},
            },
            **_private_top_level_documents(private),
        })
        assert seeded.status_code == 200, seeded.text
        response = client.get(f"/api/stories/{key}/architect/public/context")
        private_only_update = client.put(f"/api/stories/{key}", json={
            "lorebook": {"private": f"{private}_changed"},
        })
        assert private_only_update.status_code == 200, private_only_update.text
        refreshed = client.get(f"/api/stories/{key}/architect/public/context")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["card"] == data["model_card"]
    assert data["allowed_scopes"] == ["world", "premise", "cast", "first_day"]
    assert len(data["revision"]) == 24
    assert private not in json.dumps(data)
    assert refreshed.json()["revision"] == data["revision"]
    assert "author_notes" not in data["card"].get("fields", {})
    assert "character_wounds" not in data["card"].get("fields", {})
    assert "entity_periods" not in data["card"].get("time_system", {})
    assert set(data["card"]).isdisjoint({"scenes", "arcs", "chapters", "storyboard", "lorebook"})
    assert "background" not in data["card"].get("world", {})
    assert "entity" not in data["card"].get("world", {})
    assert "loop" not in data["card"].get("world", {})


def test_public_model_returns_an_uncommitted_safe_proposal_from_server_derived_route():
    private = "PRIVATE_MODEL_INPUT_SENTINEL"
    proposal = _public_world_proposal()
    with _isolated_client() as client, _developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "world": {
                "setting": "A quiet island.", "background": private,
                "entity": {"description": private, "knowledge": private},
                "loop": {"start": private},
            },
            "fields": {"author_notes": [f"[[hidden]]{private}[[/hidden]]"]},
            **_private_top_level_documents(private),
        })
        assert seeded.status_code == 200, seeded.text
        context = client.get(f"/api/stories/{key}/architect/public/context")
        assert context.status_code == 200, context.text
        response = client.post(f"/api/stories/{key}/architect/public/model", json={
            "scope": "world", "brief": f"Make the crossing tactile. [[hidden]]{private}[[/hidden]]",
            "revision": context.json()["revision"],
        })
        after = client.get(f"/api/stories/{key}/architect/public/context")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["proposal"] == {key: value for key, value in proposal.items() if key != "open_questions"}
    assert "open_questions" not in data["proposal"]
    assert data["revision"] == context.json()["revision"]
    assert data["work_order"]["worker"] == "task"
    assert data["work_order"]["public_only"] is True
    assert len(calls) == 1
    assert calls[0]["operation"] == "public_architect_world"
    assert private not in calls[0]["prompt"]
    assert "AUTHORIZED STORY WORK ORDER" in calls[0]["system"]
    # The model pass is a proposal-only capability; canonical card mutation is
    # impossible until the separately revision-bound commit call.
    assert "Salt mist makes the ferry windows glow." not in json.dumps(after.json()["card"])


def test_public_commit_is_revision_bound_and_rejects_private_or_routed_requests():
    private = "PRIVATE_COMMIT_SENTINEL"
    proposal = _public_world_proposal()
    proposal["open_questions"] = ["This must not become a cross-section browser write."]
    with _isolated_client() as client:
        key = _new_story(client)
        context = client.get(f"/api/stories/{key}/architect/public/context")
        assert context.status_code == 200, context.text
        revision = context.json()["revision"]
        # A private-only save after proposal review must remain compatible with
        # the public revision. The commit callback rebuilds from the latest
        # locked aggregate, so it must carry this value forward rather than
        # writing the earlier snapshot over it.
        private_write = client.put(f"/api/stories/{key}", json={
            "lorebook": {"private": private},
        })
        assert private_write.status_code == 200, private_write.text
        committed = client.post(f"/api/stories/{key}/architect/public/commit", json={
            "scope": "world", "revision": revision, "proposal": proposal,
        })

        assert committed.status_code == 200, committed.text
        committed_data = committed.json()
        assert committed_data["revision"] != revision
        assert committed_data["story"]["world"]["atmosphere"] == proposal["world"]["atmosphere"]
        assert committed_data["review"]["status"] == "approved"
        assert "public_visibility_boundary" in committed_data["review"]["gates"]
        stored_after_commit = client.get(f"/api/stories/{key}")
        assert stored_after_commit.status_code == 200, stored_after_commit.text
        assert "open_questions" not in stored_after_commit.json().get("fields", {})
        assert stored_after_commit.json().get("lorebook", {}).get("private") == private

        # A public canonical change invalidates a previously reviewed proposal.
        stale_revision = committed_data["revision"]
        concurrent = client.put(f"/api/stories/{key}", json={
            "premise": "A second ferry arrives after the first has docked.",
        })
        assert concurrent.status_code == 200, concurrent.text
        stale = client.post(f"/api/stories/{key}/architect/public/commit", json={
            "scope": "world", "revision": stale_revision, "proposal": proposal,
        })

        # Browser input cannot opt into a protected route or smuggle private
        # scene/entity mechanics through the public commit capability.
        routed = client.post(f"/api/stories/{key}/architect/public/model", json={
            "scope": "world", "mode": "director_knowledge",
        })
        private_context = client.get(f"/api/stories/{key}/architect/public/context")
        private_commit = client.post(f"/api/stories/{key}/architect/public/commit", json={
            "scope": "world", "revision": private_context.json()["revision"],
            "proposal": {"world": {"entity": {"objective": private}}},
        })
        unsupported_scope = client.post(f"/api/stories/{key}/architect/public/model", json={"scope": "time_system"})
        batch_scope = client.post(f"/api/stories/{key}/architect/public/model", json={
            "scope": ["world", "premise"],
        })
        alias_scope = client.post(f"/api/stories/{key}/architect/public/model", json={"scope": "opening"})
        missing_revision = client.post(f"/api/stories/{key}/architect/public/model", json={"scope": "world"})

    assert stale.status_code == 409, stale.text
    assert stale.json()["retryable"] is True
    assert routed.status_code == 400, routed.text
    assert private_commit.status_code == 400, private_commit.text
    assert unsupported_scope.status_code == 400, unsupported_scope.text
    assert batch_scope.status_code == 400, batch_scope.text
    assert alias_scope.status_code == 400, alias_scope.text
    assert missing_revision.status_code == 400, missing_revision.text

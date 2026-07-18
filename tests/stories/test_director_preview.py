"""Regression coverage for the author-only compiled director preview."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app


def _ready_loop_story() -> dict:
    return {
        "start": "ferry",
        "locations": [
            {"id": "ferry", "name": "Electric ferry"},
            {"id": "harbor", "name": "Island harbor"},
        ],
        "cast": [{"character": "player"}, {"character": "shuri"}],
        "world": {
            "entity": {"description": "Copies victims after killing them."},
            "loop": {
                "policy": {
                    "trigger": "death",
                    "restart": "opening",
                    "preserve": {
                        "runtime": [],
                        "memories": ["player"],
                        "clear_memories_for_others": True,
                    },
                },
            },
        },
        "time_system": {
            "slots": ["morning", "evening", "night"],
            "entity_periods": [
                {
                    "id": "day-watch",
                    "slots": ["morning"],
                    "state": "observing",
                    "capabilities": ["watch"],
                    "constraint": "cannot attack",
                },
                {
                    "id": "night-hunt",
                    "slots": ["night"],
                    "state": "hunting",
                    "capabilities": ["mimicry", "ambush"],
                    "constraint": "needs isolation",
                },
            ],
        },
        "fields": {
            "status": "interviewing",
            "player_id": "player",
            "first_day_plan": {
                "objective": "Reach the island and survive the first loop.",
                "opening_present": ["player", "shuri"],
                "events": [
                    {
                        "id": "crossing",
                        "when": "morning",
                        "location": "ferry",
                        "participants": ["player", "shuri"],
                        "visible": "The quiet ferry approaches the island.",
                    },
                    {
                        "id": "ambush",
                        "when": "night",
                        "location": "harbor",
                        "participants": ["player"],
                        "visible": "Someone familiar waits beside the dark water.",
                        "hidden": "The entity isolates and kills the Returner.",
                        "entity_period": "night-hunt",
                        "trigger": "The player follows the figure alone.",
                        "evidence": "Saltwater drips from a dry coat.",
                        "knowledge": {"entity": "The target is the Returner."},
                    },
                ],
            },
        },
    }


@contextmanager
def _isolated_client():
    root = Path(tempfile.mkdtemp(prefix="loom-director-preview-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    client = TestClient(create_app(root))
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


def _seed_story(client: TestClient) -> str:
    created = client.post("/api/stories/new", json={"name": "Director preview"})
    assert created.status_code == 200, created.text
    key = created.json()["key"]
    updated = client.put(f"/api/stories/{key}", json=_ready_loop_story())
    assert updated.status_code == 200, updated.text
    return key


def test_director_preview_is_a_fresh_compiled_author_projection():
    with _isolated_client() as client:
        key = _seed_story(client)
        response = client.get(f"/api/stories/{key}/director-preview")

    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["key"] == key
    assert preview["author_only"] is True
    assert preview["readiness"]["ready"] is True
    assert preview["readiness"]["blockers"] == []
    assert preview["time_slots"] == ["morning", "evening", "night"]
    assert preview["opening"]["scene_id"] == "crossing"
    assert preview["opening"]["state"] == {
        "time": "morning",
        "location": "ferry",
        "present": ["player", "shuri"],
        "public_facts": [],
        "flags": {},
        "active_scene": None,
        "turns": [],
        "scene_memories": [],
        "character_memories": {},
    }
    assert [scene["id"] for scene in preview["scene_catalog"]] == ["crossing", "ambush"]
    assert preview["scene_catalog"][1]["requires"]["flags"] == {}
    assert preview["director_plan"]["private"] is True
    private_event = preview["director_plan"]["events"][0]
    assert private_event["scene_id"] == "ambush"
    assert private_event["hidden"] == "The entity isolates and kills the Returner."
    assert private_event["entity_period_ids"] == ["night-hunt"]
    assert preview["loop_policy"]["executable"] is True
    assert preview["loop_policy"]["reset"]["memory"] == {
        "preserve_for": ["player"],
        "clear_for_others": True,
    }


def test_director_preview_does_not_persist_or_leak_into_the_public_story_card():
    with _isolated_client() as client:
        key = _seed_story(client)
        public_before = client.get(f"/api/stories/{key}")
        assert public_before.status_code == 200, public_before.text

        preview = client.get(f"/api/stories/{key}/director-preview")
        assert preview.status_code == 200, preview.text

        public_after = client.get(f"/api/stories/{key}")
        assert public_after.status_code == 200, public_after.text
        missing = client.get("/api/stories/no-such-story/director-preview")

    before = public_before.json()
    after = public_after.json()
    assert after == before
    assert after["fields"]["status"] == "interviewing"
    assert "runtime_scenario" not in after["fields"]
    assert not {
        "author_only", "readiness", "time_slots", "opening", "scene_catalog",
        "director_plan", "loop_policy",
    } & set(after)
    assert "runtime_scenario" not in preview.json()
    assert missing.status_code == 404


def test_director_preview_marks_legacy_missing_time_as_unplaced_not_morning():
    """Keep the compiler fallback, but make its authoring status visible."""
    legacy = _ready_loop_story()
    legacy["fields"]["first_day_plan"]["events"].append({
        "id": "dock-warning",
        "location": "harbor",
        "participants": ["player", "shuri"],
        "visible": "Shuri stops at the dock before saying what brought her there.",
        "hidden": "She is avoiding a question she cannot answer yet.",
    })

    with _isolated_client() as client:
        created = client.post("/api/stories/new", json={"name": "Legacy schedule"})
        assert created.status_code == 200, created.text
        key = created.json()["key"]
        updated = client.put(f"/api/stories/{key}", json=legacy)
        assert updated.status_code == 200, updated.text
        response = client.get(f"/api/stories/{key}/director-preview")

    assert response.status_code == 200, response.text
    preview = response.json()
    scenes = {scene["id"]: scene for scene in preview["scene_catalog"]}
    assert preview["readiness"]["ready"] is False
    assert scenes["crossing"]["time_explicit"] is True
    assert scenes["crossing"]["schedule_state"] == "scheduled"
    # Compiler behavior stays intact: an unscheduled legacy event inherits the
    # opening slot internally. The author preview declares that it is not
    # actually placed on that morning timeline.
    assert scenes["dock-warning"]["slots"] == ["morning"]
    assert scenes["dock-warning"]["time_explicit"] is False
    assert scenes["dock-warning"]["schedule_state"] == "unplaced"


if __name__ == "__main__":
    test_director_preview_is_a_fresh_compiled_author_projection()
    test_director_preview_does_not_persist_or_leak_into_the_public_story_card()
    test_director_preview_marks_legacy_missing_time_as_unplaced_not_morning()
    print("ok — author-only director preview")

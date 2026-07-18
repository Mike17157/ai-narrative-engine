"""Director time-lane edits persist through the authored Day One card."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app


def _story() -> dict:
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
                    "id": "night-hunt",
                    "slots": ["night"],
                    "state": "hunting",
                    "capabilities": ["mimicry"],
                    "constraint": "needs isolation",
                },
            ],
        },
        "fields": {
            "status": "active",
            "runtime_scenario": {"version": "stale"},
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
                        # Deliberately no id or time: the Director renders a
                        # compiler-derived id in the Unplaced tray.
                        "location": "harbor",
                        "participants": ["player", "shuri"],
                        "visible": "A warning note waits at the dock.",
                    },
                ],
            },
        },
    }


@contextmanager
def _isolated_client():
    root = Path(tempfile.mkdtemp(prefix="loom-director-schedule-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    client = TestClient(create_app(root))
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


def _scene(preview: dict, scene_id: str) -> dict:
    return next(scene for scene in preview["scene_catalog"] if scene["id"] == scene_id)


def test_director_schedule_move_uses_rendered_scene_id_and_invalidates_active_snapshot():
    with _isolated_client() as client:
        created = client.post("/api/stories/new", json={"name": "Schedule drag"})
        assert created.status_code == 200, created.text
        key = created.json()["key"]
        seeded = client.put(f"/api/stories/{key}", json=_story())
        assert seeded.status_code == 200, seeded.text

        initial = client.get(f"/api/stories/{key}/director-preview")
        assert initial.status_code == 200, initial.text
        unplaced = next(scene for scene in initial.json()["scene_catalog"]
                        if scene["schedule_state"] == "unplaced")
        scene_id = unplaced["id"]

        # The endpoint accepts the exact derived id the Director rendered and
        # normalizes compiler aliases to the actual configured lane name.
        moved = client.put(
            f"/api/stories/{key}/director/scenes/{scene_id}/schedule",
            json={"slot": "dusk"},
        )
        assert moved.status_code == 200, moved.text
        payload = moved.json()
        assert payload["slot"] == "evening"
        assert _scene(payload["preview"], scene_id)["slots"] == ["evening"]
        assert _scene(payload["preview"], scene_id)["schedule_state"] == "scheduled"
        # Structural edits cannot leave a stale active runtime behind.
        assert payload["preview"]["readiness"]["status"] == "interviewing"
        assert payload["story"]["fields"]["status"] == "interviewing"
        assert "runtime_scenario" not in payload["story"]["fields"]

        # Moving an already-placed scene replaces, rather than adds to, its
        # old lane.  The drag target is a single time gate.
        moved_again = client.put(
            f"/api/stories/{key}/director/scenes/{scene_id}/schedule",
            json={"slot": "night"},
        )
        assert moved_again.status_code == 200, moved_again.text
        assert _scene(moved_again.json()["preview"], scene_id)["slots"] == ["night"]

        # The same component can return a scene to its explicit Unplaced tray.
        cleared = client.put(
            f"/api/stories/{key}/director/scenes/{scene_id}/schedule",
            json={"slot": None},
        )
        assert cleared.status_code == 200, cleared.text
        cleared_scene = _scene(cleared.json()["preview"], scene_id)
        assert cleared.json()["slot"] is None
        assert cleared_scene["schedule_state"] == "unplaced"
        assert cleared_scene["time_explicit"] is False


def test_director_schedule_rejects_unknown_lanes_and_scenes_without_mutating():
    with _isolated_client() as client:
        created = client.post("/api/stories/new", json={"name": "Schedule validation"})
        assert created.status_code == 200, created.text
        key = created.json()["key"]
        seeded = client.put(f"/api/stories/{key}", json=_story())
        assert seeded.status_code == 200, seeded.text
        preview = client.get(f"/api/stories/{key}/director-preview").json()
        scene_id = next(scene["id"] for scene in preview["scene_catalog"]
                        if scene["schedule_state"] == "unplaced")

        invalid = client.put(
            f"/api/stories/{key}/director/scenes/{scene_id}/schedule",
            json={"slot": "after the credits"},
        )
        assert invalid.status_code == 400, invalid.text
        assert invalid.json()["available_slots"] == ["morning", "evening", "night"]
        still_unplaced = _scene(client.get(f"/api/stories/{key}/director-preview").json(), scene_id)
        assert still_unplaced["schedule_state"] == "unplaced"

        missing = client.put(
            f"/api/stories/{key}/director/scenes/not-a-scene/schedule",
            json={"slot": "morning"},
        )
        assert missing.status_code == 404, missing.text


def test_director_placement_moves_only_to_declared_locations_and_can_be_atomic_with_time():
    with _isolated_client() as client:
        created = client.post("/api/stories/new", json={"name": "Placement move"})
        assert created.status_code == 200, created.text
        key = created.json()["key"]
        seeded = client.put(f"/api/stories/{key}", json=_story())
        assert seeded.status_code == 200, seeded.text
        preview = client.get(f"/api/stories/{key}/director-preview").json()
        assert preview["locations"] == [
            {"id": "ferry", "label": "Electric ferry"},
            {"id": "harbor", "label": "Island harbor"},
        ]
        scene_id = _scene(preview, "crossing")["id"]

        # The author can use a declared location's display name, but storage is
        # canonicalized to its stable id.  Time remains untouched when omitted.
        moved = client.put(
            f"/api/stories/{key}/director/scenes/{scene_id}/placement",
            json={"location": "Island harbor"},
        )
        assert moved.status_code == 200, moved.text
        payload = moved.json()
        assert payload["changes"] == {"location": "harbor"}
        changed = _scene(payload["preview"], scene_id)
        assert changed["location"] == "harbor"
        assert changed["slots"] == ["morning"]
        assert payload["story"]["fields"]["first_day_plan"]["events"][0]["location"] == "harbor"
        assert payload["preview"]["readiness"]["status"] == "interviewing"
        assert "runtime_scenario" not in payload["story"]["fields"]

        # A location and time move can be a single auditable action.  The
        # route reports both canonical values rather than hiding the rewrite.
        moved_again = client.put(
            f"/api/stories/{key}/director/scenes/{scene_id}/placement",
            json={"slot": "night", "location": "ferry"},
        )
        assert moved_again.status_code == 200, moved_again.text
        assert moved_again.json()["changes"] == {"slot": "night", "location": "ferry"}
        again = _scene(moved_again.json()["preview"], scene_id)
        assert again["slots"] == ["night"]
        assert again["location"] == "ferry"

        invalid = client.put(
            f"/api/stories/{key}/director/scenes/{scene_id}/placement",
            json={"location": "a room that is not on this story card"},
        )
        assert invalid.status_code == 400, invalid.text
        assert invalid.json()["available_locations"] == [
            {"id": "ferry", "label": "Electric ferry"},
            {"id": "harbor", "label": "Island harbor"},
        ]
        unchanged = _scene(client.get(f"/api/stories/{key}/director-preview").json(), scene_id)
        assert unchanged["location"] == "ferry"
        assert unchanged["slots"] == ["night"]


if __name__ == "__main__":
    test_director_schedule_move_uses_rendered_scene_id_and_invalidates_active_snapshot()
    test_director_schedule_rejects_unknown_lanes_and_scenes_without_mutating()
    test_director_placement_moves_only_to_declared_locations_and_can_be_atomic_with_time()
    print("ok — director schedule API")

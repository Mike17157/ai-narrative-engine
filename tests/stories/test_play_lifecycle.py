"""Regression checks for interview-card activation and deterministic loop state."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app
from loom.stories.runtime.compiled import (ScenarioTransitionError, ensure_runtime,
                                           prepare_turn, record_observation, reset_loop)
from loom.stories.runtime.scenario_compiler import compile_authored_scenario


def _ready_loop_story() -> dict:
    return {
        "start": "ferry",
        "locations": [{"id": "ferry", "name": "Electric ferry"},
                      {"id": "harbor", "name": "Island harbor"}],
        "world": {"entity": {"description": "A copying entity."}, "loop": {"policy": {
            "trigger": "death", "restart": "opening",
            "preserve": {"runtime": [], "memories": ["player"],
                         "clear_memories_for_others": True},
        }}},
        "time_system": {"slots": ["morning", "evening", "night"], "entity_periods": [
            {"id": "watch", "slots": ["morning"], "state": "observing",
             "capabilities": ["watch"], "constraint": "cannot attack"},
            {"id": "hunt", "slots": ["night"], "state": "hunting",
             "capabilities": ["ambush"], "constraint": "needs isolation"},
        ]},
        "fields": {"status": "interviewing", "player_id": "player", "first_day_plan": {
            "opening_present": ["player"], "events": [
                {"id": "crossing", "when": "morning", "location": "ferry",
                 "participants": ["player"], "visible": "The quiet ferry approaches the island."},
                {"id": "ambush", "when": "night", "location": "harbor",
                 "participants": ["player"], "hidden": "The entity attacks.",
                 "entity_period": "hunt", "visible": "A familiar figure waits by the water."},
            ],
        }},
    }


def _isolated_client() -> TestClient:
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    return TestClient(create_app(root))


def test_activated_contract_controls_scene_and_loop_reset():
    contract = compile_authored_scenario(_ready_loop_story())
    assert contract["ready"], contract["issues"]
    doc, world, runtime, fresh = ensure_runtime({}, contract)
    assert fresh
    opening = prepare_turn(contract, world, runtime, {})
    assert opening["id"] == "crossing"
    assert world["location"] == "ferry"
    assert world["scene"]["members"] == ["player"]
    record_observation(runtime, text="The ferry bell rings.", player_input="I listen.", present=[])
    assert "player" in runtime["scenario_state"]["present"]
    try:
        prepare_turn(contract, world, runtime, {"scene_seed": {"id": "ambush"}})
    except ScenarioTransitionError:
        pass
    else:
        raise AssertionError("a night-only scene was accepted in the morning")

    world["day"]["slot"] = "night"
    night = prepare_turn(contract, world, runtime, {"scene_seed": {"id": "ambush"}})
    assert night["id"] == "ambush"
    assert world["location"] == "harbor"
    assert runtime["event_status"]["ambush"]["state"] == "armed"

    world["log"] = ["The Returner was killed by the copied friend."]
    reset = reset_loop(contract, world, runtime)
    assert reset is not None
    restored, info = reset
    assert restored["location"] == "ferry"
    assert restored["scene"]["members"] == ["player"]
    assert info["iteration"] == 2
    assert runtime["loop"]["returner_memory"]
    assert doc["levels"]["runtime"]["loop"]["iteration"] == 2


def test_interview_story_cannot_play_until_explicit_activation():
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Lifecycle test"}).json()["key"]

    blocked = client.post(f"/api/stories/{key}/play", json={})
    assert blocked.status_code == 409
    assert blocked.json()["readiness"]["blockers"]

    raw = _ready_loop_story()
    raw["name"] = "Lifecycle test"
    updated = client.put(f"/api/stories/{key}", json=raw)
    assert updated.status_code == 200, updated.text
    readiness = client.get(f"/api/stories/{key}/play-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["ready"]

    activated = client.post(f"/api/stories/{key}/activate")
    assert activated.status_code == 200, activated.text
    saved = client.get(f"/api/stories/{key}").json()
    assert saved["fields"]["status"] == "active"
    assert saved["fields"]["runtime_ready"]
    assert "runtime_scenario" not in saved["fields"]

    offers = client.post(f"/api/stories/{key}/day/suggest", json={})
    assert offers.status_code == 200, offers.text
    assert offers.json()["deterministic"]
    assert offers.json()["options"][0]["id"] == "crossing"
    session = client.get(f"/api/stories/session/play-{key}").json()
    assert session["state"]["levels"]["runtime"]["loop"]["iteration"] == 1
    assert "entities" not in session["state"]
    public_state = client.get(f"/api/stories/{key}/state")
    assert public_state.status_code == 200
    assert public_state.json()["state"]["location"] == "ferry"
    assert "runtime" not in public_state.json()["state"]
    assert "runtime" not in public_state.json()["levels"]

    changed = client.put(f"/api/stories/{key}", json={"premise": "A changed opening."})
    assert changed.status_code == 200
    after_change = client.get(f"/api/stories/{key}").json()
    assert after_change["fields"]["status"] == "interviewing"
    assert "runtime_ready" not in after_change["fields"]
    assert client.post(f"/api/stories/{key}/play", json={}).status_code == 409


if __name__ == "__main__":
    test_activated_contract_controls_scene_and_loop_reset()
    test_interview_story_cannot_play_until_explicit_activation()
    print("ok — interview play lifecycle")

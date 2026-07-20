"""Per-sheet development: six ordered calls, each seeing only committed canon.

Uses the same disposable-root + stubbed-model harness as test_card_development.
The stubs return complete-sentence proposals so the assembled card is also a
prose-lint reference: a full sheets pass must produce zero prose issues.
"""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app
from loom.server.context_providers import ProviderContextMixin
from loom.stories.authoring import card_graph
from loom.stories.authoring.sheets import SHEET_NAMES, sheet_digest


@contextmanager
def _isolated_client():
    root = Path(tempfile.mkdtemp(prefix="loom-sheets-"))
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


def _world_proposal():
    return {
        "message": "Set the quiet-island premise and its world.",
        "world": {
            "genre": "mystery",
            "setting": "A small island reached by a quiet electric ferry.",
            "atmosphere": "Bright water and careful, ordinary quiet.",
            "history": "The harbor once received votive offerings during storms.",
            "customs": "Residents leave flowers at the old ferry bell before the first crossing.",
            "technology": "Contemporary island infrastructure, including the electric ferry.",
            "background": "",
            "loop": {
                "start": "", "reset": "", "memory": "", "returner": "", "end_condition": "",
                "policy": {"trigger": "", "restart": "",
                           "preserve": {"runtime": [], "memories": [], "clear_memories_for_others": False},
                           "victims_return_after_end": False},
            },
            "entity": {"description": "", "knowledge": "", "limitations": "", "tactic": "", "objective": ""},
        },
        "premise": "A graduate returns to her island home and finds the crossing unnaturally quiet.",
        "open_questions": ["Why is the ferry quieter than usual?"],
    }


def _locations_proposal():
    return {
        "message": "Mapped the ferry and the dock.",
        "locations": [
            {"id": "ferry", "name": "Electric ferry",
             "description": "Benches face wide windows over calm bright water."},
            {"id": "dock", "name": "Island dock",
             "description": "A weathered plaque lists old offerings beside the ferry bell."},
        ],
        "start": "ferry",
    }


def _cast_proposal():
    return {
        "message": "Built Shuri and Mina around the crossing.",
        "characters": [
            {
                "name": "Shuri", "role": "childhood friend",
                "appearance": "A simply dressed young woman with a canvas bag.",
                "persona": "She speaks quietly and watches the water before she answers.",
                "personality": "private and reliable",
                "background": "She grew up on the island and never left.",
                "connection": "She waits at the dock for the returning protagonist.",
                "want": "", "wound": "", "lie": "", "secret": "",
                "primary": True, "home": "dock",
            },
            {
                "name": "Mina", "role": "ferry mechanic",
                "appearance": "Windblown hair and an oil-stained jacket.",
                "persona": "She checks on passengers with blunt, practical warmth.",
                "personality": "bluntly protective",
                "background": "She maintains the island ferry alone.",
                "connection": "She knows the crossing better than anyone.",
                "want": "", "wound": "", "lie": "", "secret": "",
                "primary": False, "home": "ferry",
            },
        ],
        "character_cores": [
            {"character": "Shuri", "core": "She protects the protagonist by deciding what not to say."},
        ],
    }


def _bonds_proposal():
    return {
        "message": "Wove one bond between Shuri and Mina.",
        "relationships": [{
            "source": "Shuri", "target": "Mina", "nature": "old friends",
            "dynamic": "Mina checks whether Shuri has eaten before every shift.",
            "stance": "warm",
            "target_dynamic": "Shuri leaves flowers on the ferry bench for Mina.",
            "target_stance": "trusting",
            "note": "They grew up sharing the same crossing.",
            "potential": "Mina saw something in the water last winter that she has not told Shuri.",
            "trajectory": "trust → silence",
        }],
    }


def _arc_proposal():
    return {
        "message": "Stated the direction as open questions.",
        "open_questions": [
            "What did Mina see in the water last winter?",
            "What is Shuri deciding not to say?",
        ],
    }


def _day_one_proposal():
    return {
        "message": "Planned the crossing and the reunion.",
        "time_system": {"slots": ["morning", "afternoon", "evening", "night"], "entity_periods": []},
        "first_day_plan": {
            "objective": "Reach the dock before the last crossing.",
            "opening_time": "morning",
            "opening_location": "ferry",
            "opening_present": ["Mina"],
            "events": [
                {
                    "id": "crossing", "when": "morning", "location": "ferry",
                    "participants": ["player", "Mina"],
                    "visible": "The ferry hums over calm water while Mina pretends not to watch the protagonist.",
                    "hook": "The player can ask Mina why the crossing is so quiet or explore the empty cabin.",
                    "theme": "homecoming under watch", "tone": "quiet unease",
                    "roles": [{"character": "Mina", "role": "deflecting guide"}],
                    "hidden": "Mina is counting the protagonist's glances at the shore.",
                    "entity_action": False, "entity_period": "", "trigger": "", "evidence": "",
                    "knowledge": {"protagonist": "", "entity": "", "public": ""},
                },
                {
                    "id": "dock-reunion", "when": "afternoon", "location": "dock",
                    "participants": ["player", "Shuri"],
                    "visible": "Shuri waits at the end of the dock with fresh flowers resting on the plaque.",
                    "hook": "The player can greet Shuri warmly or ask why no one else came to meet the ferry.",
                    "theme": "reunion with held breath", "tone": "tender restraint",
                    "roles": [{"character": "Shuri", "role": "withholding welcomer"}],
                    "hidden": "", "entity_action": False, "entity_period": "", "trigger": "", "evidence": "",
                    "knowledge": {"protagonist": "", "entity": "", "public": ""},
                },
            ],
        },
    }


_PROPOSALS = {
    "develop_sheet_world": _world_proposal(),
    "develop_sheet_locations": _locations_proposal(),
    "develop_sheet_cast": _cast_proposal(),
    "develop_sheet_bonds": _bonds_proposal(),
    "develop_sheet_arc": _arc_proposal(),
    "develop_sheet_day_one": _day_one_proposal(),
}


@contextmanager
def _sheet_model(proposals=None):
    """Deterministic six-sheet model; records every call in order."""
    proposals = proposals or _PROPOSALS
    original_operation = card_graph.run_card_operation
    original_provider = ProviderContextMixin.story_agent_provider
    calls: list[dict] = []

    async def run(**kwargs):
        calls.append(kwargs)
        return proposals[kwargs["operation"]]

    card_graph.run_card_operation = run
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        yield calls
    finally:
        card_graph.run_card_operation = original_operation
        ProviderContextMixin.story_agent_provider = original_provider


def _new_story(client: TestClient) -> str:
    response = client.post("/api/stories/new", json={"name": "Sheets test"})
    assert response.status_code == 200, response.text
    return response.json()["key"]


def test_sheets_run_in_dependency_order_and_later_prompts_reference_committed_sheets():
    """Each prompt must carry real ids/names/questions saved by earlier sheets."""
    with _isolated_client() as client, _sheet_model() as calls:
        key = _new_story(client)
        response = client.post(f"/api/stories/{key}/card/develop-sheets",
                               json={"brief": "A quiet island mystery about coming home."})

    assert response.status_code == 200, response.text
    assert [call["operation"] for call in calls] == [f"develop_sheet_{name}" for name in SHEET_NAMES]
    by_op = {call["operation"]: call for call in calls}

    assert "unnaturally quiet" not in by_op["develop_sheet_world"]["prompt"]  # first sheet sees no canon
    assert "unnaturally quiet" in by_op["develop_sheet_locations"]["prompt"]  # premise committed
    assert "ferry:" in by_op["develop_sheet_cast"]["prompt"]                  # location ids committed
    assert "Shuri" in by_op["develop_sheet_bonds"]["prompt"]                  # cast committed
    assert "What is Shuri deciding not to say?" not in by_op["develop_sheet_arc"]["prompt"]
    day_prompt = by_op["develop_sheet_day_one"]["prompt"]
    assert "ferry:" in day_prompt and "dock:" in day_prompt
    assert "Shuri" in day_prompt and "Mina" in day_prompt
    assert "What did Mina see in the water last winter?" in day_prompt        # arc committed


def test_sheets_assemble_a_complete_lint_clean_card():
    """World+locations+cast+bonds+arc+day one land in one coherent, clean card."""
    with _isolated_client() as client, _sheet_model():
        key = _new_story(client)
        response = client.post(f"/api/stories/{key}/card/develop-sheets",
                               json={"brief": "A quiet island mystery about coming home."})
        assert response.status_code == 200, response.text
        persisted = client.get(f"/api/stories/{key}")
        assert persisted.status_code == 200
        assert [event["id"] for event in persisted.json()["fields"]["first_day_plan"]["events"]] == [
            "crossing", "dock-reunion"]

    data = response.json()
    assert [report["sheet"] for report in data["sheets"]] == list(SHEET_NAMES)
    assert data["prose_issues"] == []

    story = data["story"]
    assert story["premise"].startswith("A graduate returns")
    assert story["world"]["genre"] == "mystery"
    assert [loc["id"] for loc in story["locations"]] == ["ferry", "dock"]
    assert story["start"] == "ferry"
    assert len(story["cast"]) == 2
    assert len(story["relationships"]) == 1
    assert story["relationships"][0]["dynamic"].endswith("before every shift.")
    questions = story["fields"]["open_questions"]
    assert "Why is the ferry quieter than usual?" in questions
    assert "What did Mina see in the water last winter?" in questions
    plan = story["fields"]["first_day_plan"]
    assert plan["opening_location"] == "ferry"
    assert [event["id"] for event in plan["events"]] == ["crossing", "dock-reunion"]
    assert plan["events"][1]["participants"] == ["player", "shuri"]
    assert data["readiness"]


def test_sheets_abort_names_the_failing_sheet_and_keeps_prior_commits():
    """A bonds proposal naming a stranger aborts; world/locations/cast survive."""
    bad_bonds = {
        "message": "This bond cannot be committed.",
        "relationships": [{
            "source": "Shuri", "target": "A Total Stranger", "nature": "impossible bond",
            "dynamic": "This must never be silently dropped.", "stance": "wary",
            "target_dynamic": "", "target_stance": "", "note": "", "potential": "", "trajectory": "",
        }],
    }
    proposals = {**_PROPOSALS, "develop_sheet_bonds": bad_bonds}
    with _isolated_client() as client, _sheet_model(proposals) as calls:
        key = _new_story(client)
        response = client.post(f"/api/stories/{key}/card/develop-sheets",
                               json={"brief": "A quiet island mystery about coming home."})

        assert response.status_code == 502, response.text
        assert "bonds sheet" in response.json()["error"]
        # The pass stopped at bonds; arc and day one never ran.
        assert [call["operation"] for call in calls] == [
            "develop_sheet_world", "develop_sheet_locations", "develop_sheet_cast", "develop_sheet_bonds"]

        story = client.get(f"/api/stories/{key}")
        assert story.status_code == 200, story.text
        card = story.json()
        assert card["world"]["genre"] == "mystery"
        assert [loc["id"] for loc in card["locations"]] == ["ferry", "dock"]
        assert len(card["cast"]) == 2
        assert card["relationships"] == []
        assert not (card["fields"].get("first_day_plan") or {}).get("events")


def test_sheet_digest_is_plain_lines_with_referenceable_ids_and_names():
    """The cross-reference context is a fact sheet, never a JSON blob."""
    raw = {
        "premise": "A graduate returns home.",
        "world": {"setting": "A small island."},
        "locations": [{"id": "ferry", "name": "Electric ferry", "description": "Calm water."}],
        "cast": [{"character": "shuri"}],
        "relationships": [{"source": "shuri", "target": "mina", "nature": "old friends",
                           "dynamic": "Mina checks whether Shuri has eaten."}],
        "fields": {"open_questions": ["Why is the ferry so quiet?"]},
    }
    embedded = {
        "shuri": {"name": "Shuri", "system": "She watches the water.", "fields": {"role": "friend"}},
        "mina": {"name": "Mina", "system": "She keeps the ferry running.", "fields": {"role": "mechanic"}},
    }
    digest = sheet_digest(raw, embedded)
    assert digest.startswith("premise: A graduate returns home.")
    assert "ferry: Electric ferry" in digest
    assert "Shuri (friend)" in digest
    assert "Shuri → Mina: old friends" in digest
    assert "- Why is the ferry so quiet?" in digest
    assert "{" not in digest and "[" not in digest


if __name__ == "__main__":
    test_sheets_run_in_dependency_order_and_later_prompts_reference_committed_sheets()
    test_sheets_assemble_a_complete_lint_clean_card()
    test_sheets_abort_names_the_failing_sheet_and_keeps_prior_commits()
    test_sheet_digest_is_plain_lines_with_referenceable_ids_and_names()
    print("ok — per-sheet development pipeline")

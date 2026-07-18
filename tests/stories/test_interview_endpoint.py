"""End-to-end persistence checks for focused interview commits (no live model call)."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app
from loom.server.context_providers import ProviderContextMixin
from loom.stories.authoring.interview_graph import InterviewResult
import loom.stories.authoring.interview_graph as interview_graph


def _isolated_client() -> TestClient:
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    return TestClient(create_app(root))


def test_focused_plan_and_schedule_survive_the_endpoint_commit():
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Endpoint test"}).json()["key"]
    seeded = client.put(f"/api/stories/{key}", json={
        "locations": [{"id": "ferry", "name": "Electric ferry"}],
        "start": "ferry",
    })
    assert seeded.status_code == 200, seeded.text
    author_only = "The ferryman is already copied."
    director_only = "The copy has memorized the ferry timetable."
    plan = {"objective": "Reach the dock before the hunt.", "events": [{
        "id": "arrival", "when": "morning", "location": "ferry", "participants": ["player"],
        "visible": "The ferry arrives as a familiar whistle cuts across the water.",
        "hook": "The player can search the deck for the whistle or step ashore to find its source.",
        "hidden": "The entity watches.", "trigger": "Leave the ferry.",
        "evidence": "A familiar whistle.", "knowledge": {"protagonist": "uneasy"},
    }]}
    schedule = {"slots": ["morning", "evening", "night"], "entity_periods": [{
        "id": "night-hunt", "slots": ["night"], "state": "hunting",
        "capabilities": ["mimicry"], "constraint": "No daytime attack.",
    }]}
    patches = iter([
        {"fields": {"first_day_plan": plan, "author_notes": [director_only]}, "world": {"must_not_write": "scope leak"}},
        {"time_system": schedule},
    ])
    original = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider

    async def fake_turn(**_kwargs):
        return InterviewResult(reply="Recorded.", patch=next(patches))

    interview_graph.run_interview_turn = fake_turn
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        day = client.post(f"/api/stories/{key}/interview", json={
            "focus": "first_day", "messages": [{
                "role": "user",
                "text": f"Plan the arrival. [[hidden]]{author_only}[[/hidden]]",
            }],
        })
        assert day.status_code == 200
        clock = client.post(f"/api/stories/{key}/interview", json={
            "focus": "time_system", "messages": [{"role": "user", "text": "The hunt is at night."}],
        })
        assert clock.status_code == 200
    finally:
        interview_graph.run_interview_turn = original
        ProviderContextMixin.story_agent_provider = original_provider

    story = client.get(f"/api/stories/{key}").json()
    assert story["fields"]["first_day_plan"] == plan
    assert story["time_system"] == schedule
    assert "must_not_write" not in story["world"]
    assert story["fields"]["author_notes"] == [
        f"[[hidden]]{director_only}[[/hidden]]",
        f"[[hidden]]{author_only}[[/hidden]]",
    ]


def test_cast_turn_persists_a_wound_written_inline_on_a_newly_invented_character():
    """The library.py character-minting loop reduces a cast item to {character, home}
    before apply_patch ever runs — a wound left on the item itself has to be pulled out
    at mint time, using the just-resolved key, or it is lost with nowhere to land."""
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Inline wound test"}).json()["key"]
    patch = {"cast": [{
        "name": "Riya Kavadi", "role": "Disgraced ferry engineer",
        "core": "Runs the bait-and-tackle shop at the dock.",
        "wound": "Riya was blamed for a fire she did not cause and lost her license.",
    }]}
    original = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider

    async def fake_turn(**_kwargs):
        return InterviewResult(reply="Recorded.", patch=patch)

    interview_graph.run_interview_turn = fake_turn
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        turn = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Invent an interesting new character."}],
        })
        assert turn.status_code == 200, turn.text
    finally:
        interview_graph.run_interview_turn = original
        ProviderContextMixin.story_agent_provider = original_provider

    story = client.get(f"/api/stories/{key}").json()
    cast_key = story["cast"][0]["character"]
    assert "role" not in story["cast"][0] and "wound" not in story["cast"][0]  # moved to the character record, not lost
    assert story["fields"]["character_wounds"][cast_key].startswith("Riya was blamed")
    assert story["fields"]["character_cores"][cast_key] == "Runs the bait-and-tackle shop at the dock."


def test_cast_turn_resolves_a_display_name_keyed_wound_to_the_story_key():
    """fields.character_wounds gets the same display-name -> stable-key resolution
    fields.character_cores already has. Without it, a wound the model files under
    "Riya Kavadi" sits under a key nothing else — cast_snapshot included — looks up."""
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Wound key resolution test"}).json()["key"]
    seed = client.post(f"/api/stories/{key}/interview", json={"focus": "cast", "messages": []})
    original = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider

    async def fake_turn_seed(**_kwargs):
        return InterviewResult(reply="Recorded.", patch={"cast": [{"name": "Riya Kavadi", "role": "Engineer"}]})

    async def fake_turn_wound(**_kwargs):
        return InterviewResult(reply="Recorded.",
                               patch={"fields": {"character_wounds": {"Riya Kavadi": "A concrete backstory paragraph."}}})

    interview_graph.run_interview_turn = fake_turn_seed
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        first = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Add Riya Kavadi, an engineer."}],
        })
        assert first.status_code == 200, first.text
        cast_key = client.get(f"/api/stories/{key}").json()["cast"][0]["character"]

        interview_graph.run_interview_turn = fake_turn_wound
        second = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Give Riya a backstory."}],
        })
        assert second.status_code == 200, second.text
    finally:
        interview_graph.run_interview_turn = original
        ProviderContextMixin.story_agent_provider = original_provider

    story = client.get(f"/api/stories/{key}").json()
    assert story["fields"]["character_wounds"][cast_key] == "A concrete backstory paragraph."
    assert "Riya Kavadi" not in story["fields"]["character_wounds"]


def test_cast_turn_ignores_a_player_alias_cast_item_and_salvages_its_wound():
    """A model shown the synthetic 'player' entry in its own context snapshot (cast_snapshot
    surfaces the protagonist's wound that way) sometimes echoes it back as a literal SET cast
    item — measured live. Minting a character literally named "player" would be a bug; the
    fix drops the item but keeps any inline wound/core under the real "player" key."""
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Player alias test"}).json()["key"]
    patch = {"cast": [
        {"character": "player", "name": "player", "wound": "A concrete backstory for the protagonist."},
        {"name": "Walt Hargrove", "role": "Patriarch"},
    ]}
    original = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider

    async def fake_turn(**_kwargs):
        return InterviewResult(reply="Recorded.", patch=patch)

    interview_graph.run_interview_turn = fake_turn
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        turn = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Add Walt."}],
        })
        assert turn.status_code == 200, turn.text
    finally:
        interview_graph.run_interview_turn = original
        ProviderContextMixin.story_agent_provider = original_provider

    story = client.get(f"/api/stories/{key}").json()
    assert len(story["cast"]) == 1  # only Walt — no bogus "player" cast member minted
    assert "player" not in [m["character"] for m in story["cast"]]
    assert story["fields"]["character_wounds"]["player"] == "A concrete backstory for the protagonist."


def test_cast_turn_resolves_the_models_own_proposed_cast_key_for_a_same_turn_wound():
    """A model that proposes its own short identity on a fresh cast item (SET cast
    [{"character": "walt", "name": "Walt Hargrove", ...}]) then reuses that same short key
    in the same turn's fields.character_wounds — measured live (deepseek-chat). The mint
    only derives a key from `name` ("walt_hargrove"), which would orphan "walt" without
    this alias."""
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Proposed key alias test"}).json()["key"]
    patch = {
        "cast": [{"character": "walt", "name": "Walt Hargrove", "role": "Patriarch"}],
        "fields": {"character_wounds": {"walt": "A concrete backstory for Walt."}},
    }
    original = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider

    async def fake_turn(**_kwargs):
        return InterviewResult(reply="Recorded.", patch=patch)

    interview_graph.run_interview_turn = fake_turn
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        turn = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Add Walt."}],
        })
        assert turn.status_code == 200, turn.text
    finally:
        interview_graph.run_interview_turn = original
        ProviderContextMixin.story_agent_provider = original_provider

    story = client.get(f"/api/stories/{key}").json()
    cast_key = story["cast"][0]["character"]
    assert story["fields"]["character_wounds"][cast_key] == "A concrete backstory for Walt."
    assert "walt" not in story["fields"]["character_wounds"] or cast_key == "walt"


def test_cast_turn_persists_a_relationship_naming_the_protagonist():
    """"player" is the standard protagonist identifier everywhere else in this schema
    (character_cores, character_wounds, arc_design owner) — measured live, a model uses it
    the same way in SET relationships. The protagonist is never a cast[] member by design,
    so the old validity check silently dropped every relationship naming them."""
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Player relationship test"}).json()["key"]
    patch = {"cast": [{"name": "Walt Hargrove", "role": "Patriarch"}]}
    original = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider

    async def fake_turn_cast(**_kwargs):
        return InterviewResult(reply="Recorded.", patch=patch)

    interview_graph.run_interview_turn = fake_turn_cast
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        first = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Add Walt."}],
        })
        assert first.status_code == 200, first.text
        cast_key = client.get(f"/api/stories/{key}").json()["cast"][0]["character"]

        async def fake_turn_bond(**_kwargs):
            return InterviewResult(reply="Recorded.", patch={"relationships": [
                {"source": "player", "target": cast_key, "relationship": "Strained, unspoken blame."},
            ]})

        interview_graph.run_interview_turn = fake_turn_bond
        second = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Dana and Walt are strained."}],
        })
        assert second.status_code == 200, second.text
    finally:
        interview_graph.run_interview_turn = original
        ProviderContextMixin.story_agent_provider = original_provider

    story = client.get(f"/api/stories/{key}").json()
    assert len(story["relationships"]) == 1
    assert story["relationships"][0]["source"] == "player"
    assert story["relationships"][0]["target"] == cast_key


if __name__ == "__main__":
    test_focused_plan_and_schedule_survive_the_endpoint_commit()
    test_cast_turn_persists_a_wound_written_inline_on_a_newly_invented_character()
    test_cast_turn_resolves_a_display_name_keyed_wound_to_the_story_key()
    test_cast_turn_ignores_a_player_alias_cast_item_and_salvages_its_wound()
    test_cast_turn_resolves_the_models_own_proposed_cast_key_for_a_same_turn_wound()
    test_cast_turn_persists_a_relationship_naming_the_protagonist()
    print("ok — focused interview plans, schedules, and cast wounds persist end to end")

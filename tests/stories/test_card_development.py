"""Regression coverage for focused interview and batch card development.

These tests deliberately use a disposable app root and stub model turns.  They
exercise the real HTTP/persistence boundary without touching a user's story or
requiring a configured provider.
"""
from __future__ import annotations

from copy import deepcopy
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app
from loom.server.context_providers import ProviderContextMixin
from loom.stories.authoring.starter_set import DevelopError, apply_develop_proposal, develop_schema
from loom.stories.authoring.interview_graph import InterviewResult
import loom.stories.authoring.card_graph as card_graph
import loom.stories.authoring.interview_graph as interview_graph


@contextmanager
def _isolated_client():
    root = Path(tempfile.mkdtemp(prefix="loom-card-development-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    # The developer's library may contain existing generated people.  A blank
    # temporary library makes identity assertions stable and does not alter it.
    for card in (root / "configs" / "characters").glob("*.yaml"):
        card.unlink()
    client = TestClient(create_app(root))
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


@contextmanager
def _interviewer(turn):
    """Install one async interviewer stub and restore global hooks afterward."""
    original_turn = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider
    interview_graph.run_interview_turn = turn
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        yield
    finally:
        interview_graph.run_interview_turn = original_turn
        ProviderContextMixin.story_agent_provider = original_provider


@contextmanager
def _card_developer(proposal: dict):
    """Make the batch developer deterministic without a live model."""
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


def _new_story(client: TestClient, name: str = "Card development test") -> str:
    response = client.post("/api/stories/new", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["key"]


def _updated_story(data: dict) -> dict:
    """The public name is ``story``; keep ``card`` as a harmless compatibility alias."""
    story = data.get("story") or data.get("card")
    assert isinstance(story, dict), data
    return story


def test_interview_returns_the_updated_card_for_immediate_ui_sync():
    """The UI should not need a second GET to know what just changed."""
    async def turn(**_kwargs):
        return InterviewResult(
            reply="That gives us a clear genre.",
            patch={"world": {"genre": "mystery"}},
        )

    with _isolated_client() as client, _interviewer(turn):
        key = _new_story(client)
        response = client.post(f"/api/stories/{key}/interview", json={
            "focus": "world",
            "messages": [{"role": "user", "text": "I want a mystery."}],
        })

    assert response.status_code == 200, response.text
    data = response.json()
    story = _updated_story(data)
    assert story["key"] == key
    assert story["world"]["genre"] == "mystery"
    assert story["fields"]["status"] == "interviewing"
    assert "runtime_scenario" not in story.get("fields", {})


def test_public_interview_cannot_invoke_director_knowledge_without_an_architect_capability():
    """A serialized work order is a trace, never a client bearer credential."""
    with _isolated_client() as client:
        key = _new_story(client)
        response = client.post(f"/api/stories/{key}/interview", json={
            "focus": "first_day",
            "target": {"section": "first_day"},
            "messages": [{"role": "user", "text": "Assign the private observation."}],
            "knowledge_only": {"event_ids": ["arrival"], "observer_ids": ["player"]},
            "work_order": {
                "worker": "oracle", "mode": "director_knowledge", "section": "first_day",
            },
        })

    assert response.status_code == 403, response.text
    assert "approved Architect hand-off" in response.json()["error"]


def test_repeating_a_named_cast_fact_reuses_its_existing_character_card():
    """A follow-up about Shuri must enrich Shuri, never mint Shuri_2."""
    patch = {
        "cast": [{
            "name": "Shuri",
            "role": "childhood friend",
            "appearance": "private and simple",
            "connection": "waiting at the dock",
        }],
    }

    async def turn(**_kwargs):
        return InterviewResult(reply="Recorded.", patch=patch)

    with _isolated_client() as client, _interviewer(turn):
        key = _new_story(client)
        first = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast",
            "messages": [{"role": "user", "text": "Shuri is my childhood friend."}],
        })
        assert first.status_code == 200, first.text
        first_key = _updated_story(first.json())["cast"][0]["character"]

        second = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast",
            "messages": [{"role": "user", "text": "Shuri is still my childhood friend."}],
        })
        assert second.status_code == 200, second.text
        card = _updated_story(second.json())

    assert [member["character"] for member in card["cast"]] == [first_key]
    assert first_key == "shuri"


def test_day_one_turn_receives_named_cast_context_and_resolves_participants_to_keys():
    """The model sees Shuri's card and a natural-language plan stores `shuri`."""
    prompts: list[str] = []

    async def turn(**kwargs):
        prompt = kwargs["prompt"]
        if "ACTIVE AUTHORING TARGET: world" in prompt:
            return InterviewResult(reply="The opening map is set.", patch={
                "world": {"genre": "mystery", "setting": "an island"},
                "locations": [{"id": "ferry", "name": "Electric ferry", "description": "quiet water"}],
                "start": "ferry",
            })
        if "ACTIVE AUTHORING TARGET: cast" in prompt:
            return InterviewResult(reply="Shuri is on the card.", patch={
                "cast": [{
                    "name": "Shuri", "role": "childhood friend",
                    "appearance": "private and simple", "connection": "waiting at the dock",
                }],
            })
        assert "ACTIVE AUTHORING TARGET: first_day" in prompt
        prompts.append(prompt)
        return InterviewResult(reply="The arrival is planned.", patch={
            "fields": {"first_day_plan": {
                "objective": "Reach the dock before the hunt.",
                "opening_time": "morning",
                "opening_location": "ferry",
                "opening_present": ["Shuri"],
                "events": [{
                    "id": "arrival", "when": "morning", "location": "ferry",
                    "participants": ["Shuri"],
                    "visible": "The ferry slows as the island dock comes into view.",
                    "hook": "The player can watch for Shuri at the dock or ask the crew why the crossing is so quiet.",
                    "hidden": "The entity watches from shore.",
                    "trigger": "The player disembarks.", "evidence": "A familiar whistle.",
                    "knowledge": {"protagonist": "The crossing feels peaceful."},
                }],
            }},
        })

    with _isolated_client() as client, _interviewer(turn):
        key = _new_story(client)
        for focus, text in (
            ("world", "The story opens on an electric ferry."),
            ("cast", "Shuri is waiting at the dock."),
        ):
            response = client.post(f"/api/stories/{key}/interview", json={
                "focus": focus, "messages": [{"role": "user", "text": text}],
            })
            assert response.status_code == 200, response.text

        response = client.post(f"/api/stories/{key}/interview", json={
            "focus": "first_day",
            "messages": [{"role": "user", "text": "Plan the ferry arrival."}],
        })

    assert response.status_code == 200, response.text
    assert len(prompts) == 1
    assert '"name": "Shuri"' in prompts[0]
    assert '"character": "shuri"' in prompts[0]
    assert '"role": "childhood friend"' in prompts[0]
    plan = _updated_story(response.json())["fields"]["first_day_plan"]
    assert plan["opening_present"] == ["shuri"]
    assert plan["events"][0]["participants"] == ["player", "shuri"]


def test_scoped_scene_interview_merges_one_scene_without_dropping_siblings():
    """An item popup only sees one scene, so its patch must not replace the plan."""
    prompts: list[str] = []

    async def turn(**kwargs):
        prompts.append(kwargs["prompt"])
        return InterviewResult(reply="The encounter now has a clear way in.", patch={
            "fields": {"first_day_plan": {"events": [{
                "id": "arrival",
                "visible": "The ferry slows beside the dock while a sealed parcel rattles under a bench.",
                "hook": "The player can inspect the parcel or ask the ferryman who put it there.",
            }]}},
        })

    with _isolated_client() as client, _interviewer(turn):
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "locations": [
                {"id": "ferry", "name": "Electric ferry"},
                {"id": "dock", "name": "Island dock"},
            ],
            "start": "ferry",
            "fields": {"first_day_plan": {"events": [
                {
                    "id": "arrival", "when": "morning", "location": "ferry",
                    "participants": ["player"], "visible": "The ferry approaches the island.",
                    "hook": "The player can go out on deck or stay in the cabin.",
                    "hidden": "PRIVATE_SCENE_LAYER_MUST_SURVIVE",
                },
                {
                    "id": "dock-reunion", "when": "morning", "location": "dock",
                    "participants": ["player"], "visible": "Someone waits at the end of the dock.",
                    "hook": "The player can approach them or look for another way off the ferry.",
                },
            ]}},
        })
        assert seeded.status_code == 200, seeded.text
        response = client.post(f"/api/stories/{key}/interview", json={
            "target": {"section": "first_day", "item": {"kind": "scene", "id": "arrival"}},
            "messages": [{"role": "user", "text": "Make the ferry encounter more active."}],
        })

    assert response.status_code == 200, response.text
    plan = _updated_story(response.json())["fields"]["first_day_plan"]
    assert [event["id"] for event in plan["events"]] == ["arrival", "dock-reunion"]
    assert plan["events"][0]["hook"].startswith("The player can inspect")
    assert "PRIVATE_SCENE_LAYER_MUST_SURVIVE" not in prompts[0]
    assert '"hidden"' not in prompts[0]


def test_batch_develop_persists_a_complete_starting_cast_as_story_bound_cards():
    """One approved agent proposal creates every card and relation as one story update."""
    proposal = {
        "message": "Built Shuri and Mina as the people waiting at the dock.",
        "world": {"genre": "mystery", "setting": "an island"},
        "premise": "A graduate returns home by electric ferry.",
        "locations": [{"id": "ferry", "name": "Electric ferry", "description": "quiet water"}],
        "start": "ferry",
        "time_system": {},
        "first_day_plan": {},
        "characters": [
            {
                "name": "Shuri", "role": "childhood friend",
                "appearance": "a private, simply dressed young woman",
                "personality": "quiet and reliable", "background": "grew up on the island",
                "connection": "waiting at the dock for the returning protagonist",
            },
            {
                "name": "Mina", "role": "ferry mechanic",
                "appearance": "windblown hair and an oil-stained jacket",
                "personality": "bluntly protective", "background": "maintains the island ferry",
                "connection": "knows the crossing better than anyone",
            },
        ],
        "relationships": [{
            "source": "Shuri", "target": "Mina", "nature": "old friends",
            "dynamic": "Mina checks whether Shuri has eaten before every shift.",
        }],
        "open_questions": ["Why is the ferry quieter than usual?"],
    }

    with _isolated_client() as client, _card_developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "world": {"genre": "mystery", "setting": "an island"},
            "premise": "A graduate returns home by electric ferry.",
            "locations": [{"id": "ferry", "name": "Electric ferry", "description": "quiet water"}],
            "start": "ferry",
        })
        assert seeded.status_code == 200, seeded.text

        response = client.post(f"/api/stories/{key}/card/develop", json={
            "scope": "cast", "brief": "Keep the ferry crossing central.",
        })

        assert response.status_code == 200, response.text
        data = response.json()
        story = _updated_story(data)
        created = data["created"]["characters"]
        keys = [person["key"] for person in created]
        assert len(calls) == 1
        assert [person["name"] for person in created] == ["Shuri", "Mina"]
        assert [member["character"] for member in story["cast"]] == keys
        assert len(story["relationships"]) == 1
        assert {story["relationships"][0]["source"], story["relationships"][0]["target"]} == set(keys)

        persisted = client.get(f"/api/stories/{key}")
        assert persisted.status_code == 200, persisted.text
        assert [member["character"] for member in persisted.json()["cast"]] == keys
        characters = client.get("/api/characters")
        assert characters.status_code == 200, characters.text
        by_key = {item["key"]: item for item in characters.json()}
        assert all(by_key[char_key]["story"] == key for char_key in keys)
        assert all(by_key[char_key]["generated"] for char_key in keys)


def test_batch_developer_never_receives_director_only_scene_mechanics():
    """The high-level co-author may see a scene's public surface, never its secret machinery."""
    private = "PRIVATE_SCENE_MECHANIC__SHURI_IS_COPIED_AFTER_MIDNIGHT"
    proposal = {
        "message": "No new public canon was needed.",
        "world": {}, "premise": "", "locations": [], "start": "", "time_system": {},
        "first_day_plan": {}, "characters": [], "relationships": [], "open_questions": [],
    }
    with _isolated_client() as client, _card_developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": "The ferry reaches the island in the morning.",
            "locations": [{"id": "ferry", "name": "Electric ferry", "description": "Quiet water."}],
            "start": "ferry",
            "time_system": {
                "slots": ["morning", "night"],
                "entity_periods": [{
                    "id": "hunt", "slots": ["night"], "state": "hunting",
                    "capabilities": [private], "constraint": private,
                }],
            },
            "fields": {
                "author_notes": [private],
                "first_day_plan": {
                    "objective": "Reach the dock.", "opening_time": "morning", "opening_location": "ferry",
                    "events": [{
                        "id": "arrival", "when": "morning", "location": "ferry",
                        "visible": "The ferry eases toward the dock.",
                        "hidden": private, "trigger": private, "evidence": private,
                        "knowledge": {"protagonist": private}, "entity_action": True, "entity_period": "hunt",
                    }],
                },
            },
        })
        assert seeded.status_code == 200, seeded.text
        response = client.post(f"/api/stories/{key}/card/develop", json={"scope": "first_day"})
        assert response.status_code == 200, response.text

    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    assert private not in prompt
    assert '"hidden"' not in prompt
    assert '"trigger"' not in prompt
    assert '"evidence"' not in prompt
    assert '"knowledge"' not in prompt
    assert '"entity_periods"' not in prompt
    assert '"author_notes"' not in prompt
    assert "The ferry eases toward the dock." in prompt


def test_batch_develop_can_fill_only_blank_annotations_on_a_matching_day_one_event():
    """A same-id proposal may repair missing gates, never rewrite scene canon."""
    existing = {
        "id": "dock-warning",
        "when": "evening",
        "location": "dock",
        "participants": ["shuri"],
        "visible": "Shuri hesitates at the dock.",
        "hidden": "The copy watches from the dark water.",
        "trigger": "The ferry bell rings.",
        "entity_action": True,
        "entity_period": "hunt",
        "evidence": "",
        "knowledge": {"protagonist": "", "entity": "The copy is waiting.", "public": ""},
    }
    card = {
        "name": "Ferry repair",
        "locations": [{"id": "dock", "name": "Dock"}, {"id": "harbor", "name": "Harbor"}],
        "cast": [{"character": "shuri"}, {"character": "mio"}],
        "fields": {"first_day_plan": {"events": [deepcopy(existing)]}},
    }
    proposal = {
        "first_day_plan": {
            "events": [{
                "id": "dock-warning",
                # These are deliberate rewrite attempts and must all be ignored.
                "when": "morning", "location": "harbor", "participants": ["mio"],
                "visible": "A different scene.", "hidden": "A different secret.",
                "trigger": "A different trigger.", "entity_action": False, "entity_period": "rest",
                "evidence": "Saltwater stains a dry coat.",
                "knowledge": {
                    "protagonist": "The player notices a familiar whistle.",
                    "entity": "A rewritten entity fact.",
                    "public": "The dockworkers mention an odd tide.",
                },
            }],
        },
    }

    candidate = apply_develop_proposal(card, proposal, story_key="ferry-repair", scopes={"first_day"})
    repaired = candidate.story["fields"]["first_day_plan"]["events"][0]

    expected = deepcopy(existing)
    expected["evidence"] = "Saltwater stains a dry coat."
    expected["knowledge"] = {
        "protagonist": "The player notices a familiar whistle.",
        "entity": "The copy is waiting.",
        "public": "The dockworkers mention an odd tide.",
    }
    assert repaired == expected
    assert candidate.updated_sections == ["first_day"]
    assert candidate.patch["fields"]["first_day_plan"]["events"][0] == expected


def test_batch_develop_same_id_annotation_repairs_are_first_wins_and_first_day_scoped():
    """A second same-id item cannot fill extra leaves, and other scopes cannot touch events."""
    card = {
        "name": "Ferry duplicate repair",
        "fields": {"first_day_plan": {"events": [{
            "id": "dock-warning", "visible": "Shuri hesitates at the dock.",
            "evidence": "", "knowledge": {},
        }]}},
    }
    proposal = {"first_day_plan": {"events": [
        {
            "id": "dock-warning", "evidence": "First trace.",
            "knowledge": {"protagonist": "First observation."},
        },
        {
            "id": "dock-warning", "evidence": "Second trace.",
            "knowledge": {"entity": "This second item must be ignored."},
        },
    ]}}

    repaired = apply_develop_proposal(card, proposal, story_key="ferry-duplicate", scopes={"first_day"})
    event = repaired.story["fields"]["first_day_plan"]["events"][0]
    assert event["evidence"] == "First trace."
    assert event["knowledge"] == {"protagonist": "First observation."}

    out_of_scope = apply_develop_proposal(card, proposal, story_key="ferry-duplicate", scopes={"world"})
    assert out_of_scope.story == card
    assert out_of_scope.updated_sections == []


def test_batch_develop_reports_a_same_id_annotation_conflict_as_a_clear_noop():
    """Existing evidence/knowledge wins over a misleading same-id model proposal."""
    existing = {
        "id": "dock-warning",
        "when": "evening",
        "location": "dock",
        "visible": "Shuri hesitates at the dock.",
        "hidden": "The copy watches from the dark water.",
        "evidence": "Saltwater stains a dry coat.",
        "knowledge": {
            "protagonist": "The player hears a familiar whistle.",
            "entity": "The copy is waiting.",
            "public": "The dockworkers mention an odd tide.",
        },
    }
    card = {"name": "Ferry conflict", "fields": {"first_day_plan": {"events": [deepcopy(existing)]}}}
    proposal = {
        "message": "I rewrote the scene.",
        "first_day_plan": {"events": [{
            "id": "dock-warning",
            "evidence": "A different clue.",
            "knowledge": {
                "protagonist": "A different reaction.",
                "entity": "A different fact.",
                "public": "A different rumor.",
            },
        }]},
    }

    candidate = apply_develop_proposal(card, proposal, story_key="ferry-conflict", scopes={"first_day"})

    assert candidate.story == card
    assert candidate.patch == {}
    assert candidate.updated_sections == []
    assert candidate.message.startswith("No changes were made: matching Day One scenes already had established")


def test_public_only_completion_rejects_private_model_output_at_the_write_boundary():
    """Completion must not trust a prompt to keep the model out of private canon."""
    private = "PRIVATE_SECRET__THE_ENTITY_WAS_SHURI_FIRST"
    proposal = {
        "world": {"entity": {"objective": private}},
        "time_system": {"entity_periods": [{"id": "hunt", "constraint": private}]},
        "characters": [{"name": "Mina", "secret": private}],
        "relationships": [{"source": "Mina", "target": "Shuri", "potential": private}],
        "first_day_plan": {"events": [{
            "id": "ambush", "hidden": private, "trigger": private,
            "knowledge": {"entity": private}, "entity_action": True,
        }]},
    }

    try:
        apply_develop_proposal(
            {"premise": "A ferry reaches an island."}, proposal,
            story_key="public-only", scopes={"world", "cast", "first_day", "time_system"},
            public_only=True,
        )
    except DevelopError as exc:
        assert str(exc) == "public completion cannot introduce private story mechanics"
    else:
        raise AssertionError("private completion output must be rejected")


def test_public_only_develop_endpoint_rejects_private_proposal_without_writing():
    """The HTTP completion flag must reach the write-boundary invariant."""
    private = "PRIVATE_SECRET__DO_NOT_PERSIST"
    proposal = {
        "message": "A secret scene.",
        "world": {}, "premise": "", "locations": [], "start": "", "time_system": {},
        "characters": [], "relationships": [], "open_questions": [],
        "first_day_plan": {"events": [{
            "id": "ambush", "when": "night", "hidden": private,
            "trigger": private, "entity_action": True, "knowledge": {"entity": private},
        }]},
    }
    with _isolated_client() as client, _card_developer(proposal):
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": "The ferry reaches the island.",
            "locations": [{"id": "ferry", "name": "Ferry"}], "start": "ferry",
        })
        assert seeded.status_code == 200, seeded.text
        response = client.post(f"/api/stories/{key}/card/develop", json={
            "scope": "first_day", "brief": "Add a public scene.", "public_only": True,
        })
        assert response.status_code == 502, response.text
        assert response.json()["error"] == "invalid developer response: public completion cannot introduce private story mechanics"
        persisted = client.get(f"/api/stories/{key}")
        assert persisted.status_code == 200, persisted.text
        assert private not in persisted.text
        assert not (persisted.json().get("fields") or {}).get("first_day_plan", {}).get("events")


def test_batch_develop_rejects_a_standalone_lore_fact_as_a_possible_scene():
    """A Day One offer must be a scene, not an encyclopedia entry in disguise."""
    card = {
        "name": "Island history",
        "locations": [{"id": "harbor", "name": "Harbor"}],
        "cast": [{"character": "shuri"}],
    }
    proposal = {"first_day_plan": {"events": [{
        "id": "old-sacrifice", "when": "morning", "location": "harbor",
        "participants": ["shuri"],
        "visible": "The island was once the site of human sacrifice.",
        "hook": "The player can ask Shuri about the island's history.",
    }]}}

    try:
        apply_develop_proposal(card, proposal, story_key="island-history", scopes={"first_day"})
    except DevelopError as exc:
        assert "immediate encounter" in str(exc)
    else:
        raise AssertionError("a bare history fact must not enter the scene catalog")


def test_batch_develop_rejects_an_abstract_hint_placeholder_as_a_possible_scene():
    """A scene must name the clue or encounter, not promise vague lore later."""
    card = {
        "name": "Island hints",
        "locations": [{"id": "village-square", "name": "Village square"}],
        "cast": [{"character": "shuri"}],
    }
    proposal = {"first_day_plan": {"events": [{
        "id": "subtle-hints", "when": "evening", "location": "village-square",
        "participants": ["shuri"],
        "visible": (
            "Subtle hints of the island's history and recent vanishings surface "
            "through conversation or environment."
        ),
        "hook": "The player can investigate the hints.",
    }]}}

    try:
        apply_develop_proposal(card, proposal, story_key="island-hints", scopes={"first_day"})
    except DevelopError as exc:
        assert "concrete clue" in str(exc)
    else:
        raise AssertionError("an abstract hint placeholder must not enter the scene catalog")


def test_batch_develop_accepts_history_only_when_it_is_a_playable_clue():
    """History can texture a scene when the player has a present way to engage it."""
    card = {
        "name": "Island clue",
        "locations": [{"id": "harbor", "name": "Harbor"}],
        "cast": [{"character": "shuri"}],
    }
    proposal = {"first_day_plan": {"events": [{
        "id": "weathered-plaque", "when": "morning", "location": "harbor",
        "participants": ["shuri"],
        "visible": "A weathered harbor plaque lists old offerings, and fresh flowers have appeared beneath it.",
        "hook": "The player can inspect the flowers or ask Shuri why no one has cleared them away.",
    }]}}

    candidate = apply_develop_proposal(card, proposal, story_key="island-clue", scopes={"first_day"})
    event = candidate.story["fields"]["first_day_plan"]["events"][0]

    assert event["participants"] == ["player", "shuri"]
    assert event["hook"] == "The player can inspect the flowers or ask Shuri why no one has cleared them away."
    assert candidate.updated_sections == ["first_day"]


def test_batch_develop_has_named_world_sections_for_history_and_lived_context():
    """The co-author can store island history without stuffing it into scene offers."""
    proposal = {
        "world": {
            "setting": "A small island reached by an electric ferry.",
            "atmosphere": "Bright water and careful, ordinary quiet.",
            "history": "The harbor once received votive offerings during storms.",
            "customs": "Residents leave flowers at the old ferry bell before the first crossing.",
            "technology": "Contemporary island infrastructure, including the quiet electric ferry.",
        },
    }
    candidate = apply_develop_proposal({"name": "Island sections"}, proposal,
                                       story_key="island-sections", scopes={"world"})

    world = candidate.story["world"]
    assert world["history"].startswith("The harbor")
    assert world["customs"].startswith("Residents")
    assert world["atmosphere"].startswith("Bright")
    schema_fields = develop_schema()["properties"]["world"]["properties"]
    assert {"atmosphere", "history", "customs", "technology"} <= set(schema_fields)


def test_batch_develop_preserves_a_name_already_established_in_story_prose():
    """A generated surname must not split the author's existing Shuri in two."""
    proposal = {
        "message": "Completed the existing Shuri's character card.",
        "world": {}, "premise": "", "locations": [], "start": "", "time_system": {},
        "first_day_plan": {}, "relationships": [], "open_questions": [],
        "characters": [{
            "name": "Shuri Yukawa", "role": "childhood friend",
            "appearance": "simple clothes", "personality": "private and reliable",
            "background": "grew up on the island", "connection": "waits at the dock",
        }],
    }

    with _isolated_client() as client, _card_developer(proposal):
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": "Shuri, the player's private childhood friend, waits at the dock.",
        })
        assert seeded.status_code == 200, seeded.text
        response = client.post(f"/api/stories/{key}/card/develop", json={"scope": "cast"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["created"]["characters"] == [{"key": "shuri", "name": "Shuri"}]
    cast = _updated_story(data)["cast"]
    assert len(cast) == 1
    assert cast[0]["character"] == "shuri"
    assert cast[0]["primary"] is True


def test_batch_develop_does_not_hijack_a_same_named_global_character():
    """New story people are local even when the global library has the same key."""
    proposal = {
        "characters": [{
            "name": "Shuri", "role": "childhood friend",
            "appearance": "simple clothes", "personality": "reliable",
            "background": "the island", "connection": "waiting at the dock",
        }],
        "relationships": [], "open_questions": [],
    }
    candidate = apply_develop_proposal(
        {"premise": "Shuri waits at the dock.", "cast": []}, proposal,
        story_key="fresh-story", embedded_characters={},
        known_characters={"shuri": {"name": "A global Shuri", "fields": {}}},
        scopes={"cast"},
    )

    assert candidate.created == [{"key": "shuri", "name": "Shuri"}]
    assert candidate.story["cast"][0]["character"] == "shuri"
    assert candidate.characters["shuri"]["fields"]["story"] == "fresh-story"


def test_invalid_batch_development_never_leaves_a_partially_created_cast():
    """Validate the whole proposal before saving even its otherwise-valid people."""
    invalid = {
        "message": "This relation cannot be committed.",
        "world": {}, "premise": "", "locations": [], "start": "", "time_system": {},
        "first_day_plan": {}, "open_questions": [],
        "characters": [{
            "name": "Shuri", "role": "childhood friend",
            "appearance": "simple clothes", "personality": "reliable",
            "background": "the island", "connection": "waiting at the dock",
        }],
        "relationships": [{
            "source": "Shuri", "target": "Not a created character", "nature": "impossible bond",
            "dynamic": "This must never be silently dropped.",
        }],
    }

    with _isolated_client() as client, _card_developer(invalid):
        key = _new_story(client)
        response = client.post(f"/api/stories/{key}/card/develop", json={"scope": "cast"})
        assert response.status_code == 502, response.text

        story = client.get(f"/api/stories/{key}")
        assert story.status_code == 200, story.text
        assert story.json()["cast"] == []
        characters = client.get("/api/characters")
        assert characters.status_code == 200, characters.text
        assert not any(item["name"] == "Shuri" for item in characters.json())


def test_batch_development_uses_a_server_derived_work_order_and_rejects_forgery():
    """A multi-scope pass uses the bounded task worker, never a client-selected role."""
    proposal = {
        "message": "The existing card already supplies the needed foundation.",
        "world": {}, "premise": "", "locations": [], "start": "", "time_system": {},
        "first_day_plan": {}, "characters": [], "relationships": [], "open_questions": [],
    }
    with _isolated_client() as client, _card_developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "world": {"setting": "A quiet island reached by ferry."},
            "premise": "A returner arrives before a long-delayed reunion.",
        })
        assert seeded.status_code == 200, seeded.text

        response = client.post(f"/api/stories/{key}/card/develop", json={
            "scope": ["world", "premise"], "brief": "Preserve the ferry as the entry point.",
        })
        assert response.status_code == 200, response.text

        data = response.json()
        assert data["work_order"]["planner"] == "architect"
        assert data["work_order"]["scout"] == "explore"
        assert data["work_order"]["worker"] == "task"
        assert data["work_order"]["specialist"] == "task"
        assert data["review"]["agent"] == "reviewer"
        assert calls[0]["operation"] == "develop_task"
        assert "AUTHORIZED STORY WORK ORDER" in calls[0]["system"]

        forged = client.post(f"/api/stories/{key}/card/develop", json={
            "scope": "world", "brief": "Try a different worker.",
            "work_order": {"worker": "oracle"},
        })

    assert forged.status_code == 409, forged.text
    assert len(calls) == 1


if __name__ == "__main__":
    test_interview_returns_the_updated_card_for_immediate_ui_sync()
    test_repeating_a_named_cast_fact_reuses_its_existing_character_card()
    test_day_one_turn_receives_named_cast_context_and_resolves_participants_to_keys()
    test_scoped_scene_interview_merges_one_scene_without_dropping_siblings()
    test_batch_develop_persists_a_complete_starting_cast_as_story_bound_cards()
    test_batch_develop_can_fill_only_blank_annotations_on_a_matching_day_one_event()
    test_batch_develop_same_id_annotation_repairs_are_first_wins_and_first_day_scoped()
    test_batch_develop_reports_a_same_id_annotation_conflict_as_a_clear_noop()
    test_public_only_completion_rejects_private_model_output_at_the_write_boundary()
    test_public_only_develop_endpoint_rejects_private_proposal_without_writing()
    test_batch_develop_rejects_a_standalone_lore_fact_as_a_possible_scene()
    test_batch_develop_accepts_history_only_when_it_is_a_playable_clue()
    test_batch_develop_has_named_world_sections_for_history_and_lived_context()
    test_batch_develop_preserves_a_name_already_established_in_story_prose()
    test_batch_develop_does_not_hijack_a_same_named_global_character()
    test_invalid_batch_development_never_leaves_a_partially_created_cast()
    print("ok — card development and identity-preserving interview flow")

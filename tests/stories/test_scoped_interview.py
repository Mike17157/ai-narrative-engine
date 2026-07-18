"""Focused editorial conversations stay isolated per card item and field."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app
from loom.server.context_providers import ProviderContextMixin
from loom.stories.authoring.interview_graph import InterviewResult
from loom.stories.authoring.interview_scope import (EditorialTargetError,
                                                    normalize_editorial_target,
                                                    target_snapshot)
import loom.stories.authoring.interview_graph as interview_graph


def _card() -> dict:
    return {
        "world": {
            "curse": {"description": "It takes a familiar face."},
            "loop": {"trigger": "death"},
        },
        "locations": [{"id": "ferry", "name": "Electric ferry"}],
        "premise": "A return crossing goes wrong.",
        "cast": [{"character": "shuri"}, {"character": "mina"}],
        "relationships": [{"source": "shuri", "target": "mina", "nature": "old friends"}],
        "time_system": {
            "slots": ["morning", "night"],
            "entity_periods": [{
                "id": "hunt", "slots": ["night"],
                "capabilities": ["PRIVATE_ENTITY_CAPABILITY"],
                "constraint": "PRIVATE_ENTITY_CONSTRAINT",
            }],
        },
        "fields": {
            "first_day_plan": {"events": [
                {"id": "arrival", "participants": ["shuri"], "public_surface": "The ferry docks."},
                {"id": "night-attack", "participants": ["mina"], "public_surface": "A bell rings."},
            ]},
            "arc_design": {"arcs": [{"id": "shuri-impermanence", "owner": "shuri", "theme_id": "impermanence"}]},
            "author_notes": ["[[hidden]]The copied Shuri remembers every death.[[/hidden]]"],
        },
    }


def test_target_normalization_requires_existing_item_and_keeps_field_in_key():
    card = _card()
    targets = [
        ({"section": "world", "item": {"kind": "location", "id": "ferry"}}, "world:location:ferry"),
        ({"section": "world", "item": {"kind": "world_rule", "id": "loop"}}, "world:world_rule:loop"),
        ({"section": "world", "item": {"kind": "author_note", "id": "0"}}, "world:author_note:note-0"),
        ({"section": "first_day", "item": {"kind": "scene", "id": "arrival", "field": "public_surface"}}, "first_day:scene:arrival:field:public_surface"),
        ({"section": "time_system", "item": {"kind": "entity-period", "id": "hunt"}}, "time_system:entity_period:hunt"),
        ({"section": "cast", "item": {"kind": "character", "id": "shuri"}}, "cast:character:shuri"),
        ({"section": "cast", "item": {"kind": "relationship", "id": "shuri--mina"}}, "cast:relationship:shuri--mina"),
        ({"section": "arcs", "item": {"kind": "arc", "id": "shuri-impermanence"}}, "arcs:arc:shuri-impermanence"),
    ]
    for source, expected_key in targets:
        assert normalize_editorial_target(source, card=card)["key"] == expected_key

    for bad in (
        {"section": "cast", "item": {"kind": "scene", "id": "arrival"}},
        {"section": "cast", "item": {"kind": "character", "id": "missing"}},
        {"section": "world", "item": {"kind": "world_rule", "id": "../../escape"}},
    ):
        try:
            normalize_editorial_target(bad, card=card)
        except EditorialTargetError:
            pass
        else:  # pragma: no cover - assertion makes a target leak visible
            raise AssertionError(f"accepted invalid target: {bad}")


def test_resolver_accepts_legacy_card_fallbacks_used_by_the_director_ui():
    card = _card()
    card["cast"] = ["shuri"]
    card["fields"]["first_day_plan"]["events"] = [{"visible": "The ferry docks."}]
    card["time_system"]["entity_periods"] = [{"state": "hunting"}]
    card["fields"].pop("arc_design")
    card["fields"]["arc_outline"] = {"themes": [{"id": "impermanence", "label": "Impermanence"}], "arcs": [{"owner": "shuri", "theme_id": "impermanence"}]}

    # A title/name may be the only legacy UI id; it is resolved to a safe,
    # deterministic stored key.  ID-less records get the documented fallback.
    assert normalize_editorial_target(
        {"section": "first_day", "item": {"kind": "scene", "id": "scene-0"}}, card=card,
    )["item"]["id"] == "the-ferry-docks"
    assert normalize_editorial_target(
        {"section": "time_system", "item": {"kind": "entity_period", "id": "entity-period-0"}}, card=card,
    )["item"]["id"] == "entity-period-1"
    assert normalize_editorial_target(
        {"section": "cast", "item": {"kind": "character", "id": "shuri"}}, card=card,
    )["item"]["id"] == "shuri"
    assert normalize_editorial_target(
        {"section": "arcs", "item": {"kind": "arc", "id": "shuri"}}, card=card,
    )["item"]["id"] == "shuri"
    assert normalize_editorial_target(
        {"section": "arcs", "item": {"kind": "theme", "id": "theme"}}, card=card,
    )["item"]["id"] == "theme"
    assert normalize_editorial_target(
        {"section": "premise", "item": {"kind": "opening_state", "id": "premise"}}, card=card,
    )["item"]["id"] == "opening-state"
    assert normalize_editorial_target(
        {"section": "world", "item": {"kind": "author_note", "id": card["fields"]["author_notes"][0]}}, card=card,
    )["item"]["id"] == "note-0"

    # The Director timeline uses the compiler's derived id rather than the
    # raw event's positional fallback, including compiler-style duplicate
    # suffixes when two source events have the same visible title.
    card["fields"]["first_day_plan"]["events"] = [
        {"title": "Ferry approach and disembarkation"},
        {"title": "Ferry approach and disembarkation"},
    ]
    assert normalize_editorial_target(
        {"section": "first_day", "item": {"kind": "scene", "id": "ferry-approach-and-disembarkation"}}, card=card,
    )["item"]["id"] == "ferry-approach-and-disembarkation"
    assert normalize_editorial_target(
        {"section": "first_day", "item": {"kind": "scene", "id": "ferry-approach-and-disembarkation-2"}}, card=card,
    )["item"]["id"] == "ferry-approach-and-disembarkation-2"


def test_item_snapshot_omits_siblings_and_never_includes_author_note_content():
    card = _card()
    scene = normalize_editorial_target(
        {"section": "first_day", "item": {"kind": "scene", "id": "arrival", "field": "public_surface"}},
        card=card,
    )
    snapshot = target_snapshot(card, scene, section_snapshot={"cast": []})
    assert snapshot["item"] == {"id": "arrival", "field": "public_surface", "value": "The ferry docks."}
    assert "night-attack" not in str(snapshot)
    assert snapshot["time_slots"] == ["morning", "night"]
    assert "entity_periods" not in snapshot
    assert "PRIVATE_ENTITY_CAPABILITY" not in str(snapshot)
    assert "PRIVATE_ENTITY_CONSTRAINT" not in str(snapshot)

    note = normalize_editorial_target(
        {"section": "world", "item": {"kind": "author_note", "id": "note-0"}}, card=card,
    )
    note_snapshot = target_snapshot(card, note, section_snapshot={"world": card["world"]})
    assert "copied Shuri" not in str(note_snapshot)
    assert note_snapshot["item"]["visibility"] == "author-only"


@contextmanager
def _client():
    root = Path(tempfile.mkdtemp(prefix="loom-scoped-interview-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    client = TestClient(create_app(root))
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


@contextmanager
def _stub_interviewer(prompts: list[str]):
    original_turn = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider

    async def turn(**kwargs):
        prompts.append(kwargs["prompt"])
        return InterviewResult(reply="A focused question follows.", patch={"world": {"curse": {"description": "Sharper."}}})

    interview_graph.run_interview_turn = turn
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        yield
    finally:
        interview_graph.run_interview_turn = original_turn
        ProviderContextMixin.story_agent_provider = original_provider


def test_endpoint_persists_each_field_thread_separately_and_returns_suggestions():
    prompts: list[str] = []
    first_target = {"section": "world", "item": {"kind": "world_rule", "id": "curse", "field": "description"}}
    second_target = {"section": "world", "item": {"kind": "world_rule", "id": "loop", "field": "trigger"}}
    with _client() as client, _stub_interviewer(prompts):
        made = client.post("/api/stories/new", json={"name": "Scoped interview"})
        assert made.status_code == 200, made.text
        key = made.json()["key"]
        saved = client.put(f"/api/stories/{key}", json={"world": _card()["world"]})
        assert saved.status_code == 200, saved.text

        first = client.post(f"/api/stories/{key}/interview", json={
            "target": first_target,
            "messages": [{"role": "user", "text": "Make the copy rule more personal."}],
        })
        assert first.status_code == 200, first.text
        first_data = first.json()
        assert first_data["target"]["key"] == "world:world_rule:curse:field:description"
        assert len(first_data["conversation"]["history"]) == 2
        assert first_data["suggestions"] and first_data["suggestions"][0]["kind"] == "prompt"

        second = client.post(f"/api/stories/{key}/interview", json={
            "target": second_target,
            "messages": [{"role": "user", "text": "Make the reset rule explicit."}],
        })
        assert second.status_code == 200, second.text
        second_data = second.json()
        assert len(second_data["conversation"]["history"]) == 2
        assert "Make the copy rule more personal." not in prompts[-1]
        assert "night-attack" not in prompts[-1]

        fetched = client.get(f"/api/stories/{key}/interview-history", params={
            "section": "world", "item_kind": "world_rule", "item_id": "curse", "field": "description",
        })
        assert fetched.status_code == 200, fetched.text
        history = fetched.json()
        assert history["target"] == first_data["target"]
        assert history["history"][0]["text"] == "Make the copy rule more personal."

        public = client.get(f"/api/stories/{key}").json()
        assert "interview_history" not in public["fields"]
        assert "interview_histories" not in public["fields"]

        bad = client.post(f"/api/stories/{key}/interview", json={
            "target": {"section": "cast", "item": {"kind": "character", "id": "not-a-card"}},
            "messages": [{"role": "user", "text": "Nope."}],
        })
        assert bad.status_code == 400
        assert "invalid editorial target" in bad.json()["error"]


if __name__ == "__main__":
    test_target_normalization_requires_existing_item_and_keeps_field_in_key()
    test_resolver_accepts_legacy_card_fallbacks_used_by_the_director_ui()
    test_item_snapshot_omits_siblings_and_never_includes_author_note_content()
    test_endpoint_persists_each_field_thread_separately_and_returns_suggestions()
    print("ok — scoped interview conversations")

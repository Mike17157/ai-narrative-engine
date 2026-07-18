"""Focused coverage for the compact public character/scene authoring layer."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from loom.stories.authoring.card_payload import model_story_card
from loom.stories.authoring.dramatic_kernel import DramaticKernelError, normalize_scene_kernel
from loom.stories.authoring.interview import apply_patch
from loom.stories.authoring.starter_set import apply_develop_proposal, develop_schema


class _Context:
    def __init__(self):
        self.root = Path(".")
        self.base_settings = SimpleNamespace(characters={})

    def portrait_manifest(self, _key: str) -> dict:
        return {}


def test_scene_kernel_normalizes_pair_roles_and_rejects_offstage_or_hidden_content():
    event = normalize_scene_kernel({
        "participants": ["player", "shuri"],
        "theme": "  return  versus belonging ",
        "tone": "  warm unease ",
        "roles": [
            {"character": "player", "role": "returning witness"},
            {"character": "shuri", "role": "guarded welcome"},
        ],
    }, known_characters={"shuri"})

    assert event["theme"] == "return versus belonging"
    assert event["tone"] == "warm unease"
    assert event["roles"] == {"player": "returning witness", "shuri": "guarded welcome"}

    for invalid in (
        {"participants": ["player"], "roles": {"shuri": "off-stage observer"}},
        {"participants": ["player"], "theme": "[[hidden]]private[[/hidden]]"},
    ):
        try:
            normalize_scene_kernel(invalid, known_characters={"shuri"})
            raise AssertionError("invalid public scene kernel was accepted")
        except DramaticKernelError:
            pass


def test_interview_persists_public_cores_and_canonical_scene_kernel():
    card = {"fields": {"character_cores": {"player": "returns hoping nothing changed"}}}
    plan = {
        "objective": "Reach the dock.",
        "events": [{
            "id": "dock-reunion", "when": "morning", "location": "dock",
            "participants": ["shuri"],
            "visible": "Shuri waits beside a wet suitcase at the end of the dock.",
            "hook": "The player can approach Shuri or inspect the suitcase before she notices.",
            "theme": "homecoming under strain", "tone": "bright, careful",
            "roles": {"shuri": "the waiting friend"},
        }],
    }

    updated = apply_patch(card, {"fields": {
        "character_cores": {"you": "returns hoping nothing changed", "shuri": "keeps warmth practical"},
        "first_day_plan": plan,
    }}, "interview")

    assert updated["fields"]["character_cores"] == {
        "player": "returns hoping nothing changed", "shuri": "keeps warmth practical",
    }
    event = updated["fields"]["first_day_plan"]["events"][0]
    assert event["participants"] == ["player", "shuri"]
    assert event["roles"] == {"shuri": "the waiting friend"}


def test_developer_maps_character_names_and_exposes_only_public_kernel_fields():
    card = {
        "locations": [{"id": "dock", "name": "Island dock"}],
        "cast": [{"character": "shuri", "primary": True, "home": "dock"}],
        "fields": {},
    }
    embedded = {"shuri": {"name": "Shuri", "fields": {"story": "quiet-ferry"}}}
    proposal = {
        "message": "Added the reunion as a playable offer.",
        "characters": [],
        "character_cores": [
            {"character": "player", "core": "returns expecting old routines"},
            {"character": "Shuri", "core": "makes care look practical"},
        ],
        "first_day_plan": {
            "objective": "Reach the dock.", "opening_time": "morning", "opening_location": "dock",
            "opening_present": ["player"],
            "events": [{
                "id": "dock-reunion", "when": "morning", "location": "dock",
                "participants": ["player", "Shuri"],
                "visible": "Shuri stands at the dock with a thermos she insists is only for the ferry ride.",
                "hook": "The player can accept the thermos, ask why she came alone, or look toward town.",
                "theme": "return and restraint", "tone": "sunlit awkwardness",
                "roles": [
                    {"character": "player", "role": "the unexpected returner"},
                    {"character": "Shuri", "role": "the friend keeping distance useful"},
                ],
            }],
        },
    }

    candidate = apply_develop_proposal(
        card, proposal, story_key="quiet-ferry", embedded_characters=embedded,
        known_characters={}, scopes={"cast", "first_day"},
    )

    assert candidate.story["fields"]["character_cores"] == {
        "player": "returns expecting old routines", "shuri": "makes care look practical",
    }
    event = candidate.story["fields"]["first_day_plan"]["events"][0]
    assert event["roles"] == {
        "player": "the unexpected returner", "shuri": "the friend keeping distance useful",
    }

    projected = model_story_card(_Context(), "quiet-ferry", {
        **candidate.story,
        "fields": {
            **candidate.story["fields"],
            "author_notes": ["private author note"],
        },
    })
    projected_event = projected["fields"]["first_day_plan"]["events"][0]
    assert projected["fields"]["character_cores"]["shuri"] == "makes care look practical"
    assert projected_event["theme"] == "return and restraint"
    assert projected_event["tone"] == "sunlit awkwardness"
    assert projected_event["roles"]["shuri"] == "the friend keeping distance useful"
    assert "hidden" not in projected_event and "author_notes" not in projected["fields"]


def test_development_schema_makes_the_kernel_available_to_structured_models():
    properties = develop_schema()["properties"]
    event = properties["first_day_plan"]["properties"]["events"]["items"]
    assert {"character_cores"}.issubset(properties)
    assert {"theme", "tone", "roles"}.issubset(event["properties"])


if __name__ == "__main__":
    test_scene_kernel_normalizes_pair_roles_and_rejects_offstage_or_hidden_content()
    test_interview_persists_public_cores_and_canonical_scene_kernel()
    test_developer_maps_character_names_and_exposes_only_public_kernel_fields()
    test_development_schema_makes_the_kernel_available_to_structured_models()
    print("ok — compact dramatic kernels")

"""Theme/arc design stays private while its present-tense behavior remains usable."""
from __future__ import annotations

from loom.stories.authoring.arc_design import (
    arc_design_issues,
    author_arc_outline,
    arc_public_outline,
    narrator_arc_surface,
    normalize_arc_design,
    protagonist_arc,
)


def _design() -> dict:
    return {
        "version": 1,
        "themes": [{
            "id": "impermanence",
            "label": "Impermanence",
            "question": "What do people lose by waiting to live?",
        }],
        "arcs": [{
            "id": "shuri-impermanence",
            "title": "Shuri learns what delay costs",
            "theme_id": "impermanence",
            "owner": "shuri",
            "dramatic_question": "Can Shuri keep treating love as something that can wait?",
            "starting_belief": "If she stays useful and quiet, nothing precious has to change.",
            "truth": "SECRET: Shuri loves the Returner and is terrified time will take him.",
            "turning_points": [{
                "id": "dock-pressure",
                "kind": "pressure",
                "scene_id": "dock-warning",
                "when": "evening",
                "public_surface": "A missed meeting makes the old routine impossible to pretend is enough.",
                "private_pressure": "SECRET: the entity has copied someone Shuri failed to protect.",
                "revelation": "SECRET: Shuri recognizes the depth of her love.",
                "changes": "SECRET: she finally admits it.",
            }],
            "character_threads": [{
                "character": "shuri",
                "want": "Keep the Returner safe without asking anything of him.",
                "protective_strategy": "She turns every difficult feeling into a practical errand.",
                "blind_spot": "She believes care only counts when it asks for nothing.",
                "unacknowledged_need": "SECRET: she needs to be chosen in return.",
                "limitation": "She cannot name affection directly while she thinks it could burden someone.",
                "visible_tell": "When cornered, she starts straightening objects that are already straight.",
                "pressure_points": [{
                    "scene_id": "dock-warning",
                    "when": "evening",
                    "public_pressure": "The player asks why she missed their old meeting place.",
                }],
                "recognition": "SECRET: she admits she has been waiting for him.",
                "possible_outcomes": ["She risks an honest request.", "She withdraws further."],
            }],
        }],
    }


def test_arc_design_normalizes_and_projects_only_safe_story_card_material():
    design = normalize_arc_design(_design())
    assert design["themes"][0]["id"] == "impermanence"
    assert design["arcs"][0]["character_threads"][0]["character"] == "shuri"

    public = arc_public_outline(design)
    serialized = repr(public)
    assert public == {
        "themes": [{"id": "impermanence", "label": "Impermanence"}],
        "arcs": [{"owner": "shuri", "theme_id": "impermanence", "theme": "Impermanence"}],
    }
    assert "SECRET" not in serialized
    assert "unacknowledged_need" not in serialized
    assert "recognition" not in serialized
    assert "truth" not in serialized
    assert design["themes"][0]["question"] not in serialized
    assert design["arcs"][0]["title"] not in serialized
    assert design["arcs"][0]["dramatic_question"] not in serialized

    author = author_arc_outline(design)
    assert author == {
        "arcs": [{
            "id": "shuri-impermanence",
            "title": "Shuri learns what delay costs",
            "owner": "shuri",
            "theme": {
                "id": "impermanence",
                "label": "Impermanence",
                "question": "What do people lose by waiting to live?",
            },
            "dramatic_question": "Can Shuri keep treating love as something that can wait?",
        }],
    }
    author_serialized = repr(author)
    assert "SECRET" not in author_serialized
    assert "unacknowledged_need" not in author_serialized
    assert "recognition" not in author_serialized
    assert "truth" not in author_serialized


def test_narrator_surface_gates_private_truth_and_only_opens_current_pressure():
    surface = narrator_arc_surface(
        _design(), scene_id="dock-warning", slot="evening", present=["shuri"], flags={}
    )
    assert len(surface) == 1
    shuri = surface[0]
    assert shuri["starting_belief"].startswith("If she stays useful")
    assert shuri["protective_strategy"].startswith("She turns")
    assert shuri["visible_tell"].startswith("When cornered")
    assert shuri["public_pressure"] == ["The player asks why she missed their old meeting place."]
    assert shuri["possible_public_turns"][0]["surface"].startswith("A missed meeting")
    serialized = repr(surface)
    assert "SECRET" not in serialized
    assert "unacknowledged_need" not in serialized
    assert "recognition" not in serialized
    assert "private_pressure" not in serialized

    elsewhere = narrator_arc_surface(
        _design(), scene_id="crossing", slot="morning", present=["shuri"], flags={}
    )
    assert elsewhere[0]["public_pressure"] == []
    assert elsewhere[0]["possible_public_turns"] == []


def test_narrator_surface_never_uses_a_blind_spot_as_a_safe_belief():
    design = _design()
    design["arcs"][0]["starting_belief"] = ""
    design["arcs"][0]["character_threads"][0]["blind_spot"] = "SECRET BLIND SPOT: Shuri already knows the ending."

    surface = narrator_arc_surface(
        design, scene_id="dock-warning", slot="evening", present=["shuri"], flags={}
    )

    assert surface[0]["starting_belief"] == ""
    assert "SECRET BLIND SPOT" not in repr(surface)


def test_director_issues_flag_threads_that_do_not_belong_to_the_cast():
    design = _design()
    design["arcs"][0]["character_threads"][0]["character"] = "not-in-cast"
    issues = arc_design_issues(design, card={"cast": [{"character": "shuri"}]})
    assert any(issue["code"] == "arc_thread_character_unknown" for issue in issues)


def test_protagonist_arc_resolves_by_player_alias_and_returns_none_otherwise():
    # The protagonist's own arc is the story's root — everything else relates back to it.
    # No arc at all, and no cast:
    assert protagonist_arc(None) is None
    assert protagonist_arc({"version": 1, "themes": [], "arcs": []}) is None

    # An arc exists, but it belongs to a supporting character, not the protagonist.
    assert protagonist_arc(_design()) is None

    # Any recognized protagonist alias resolves the same arc.
    for alias in ("player", "you", "protagonist", "returner", "Player"):
        design = _design()
        design["arcs"][0]["owner"] = alias
        arc = protagonist_arc(design)
        assert arc is not None
        assert arc["id"] == "shuri-impermanence"


def test_arc_design_issues_flags_missing_protagonist_arc_only_when_warranted():
    card = {"cast": [{"character": "shuri"}]}

    # A supporting character's arc exists, but nobody has authored the protagonist's own.
    issues = arc_design_issues(_design(), card=card)
    codes = [issue["code"] for issue in issues]
    assert "protagonist_arc_missing" in codes
    assert "character_arcs_missing" not in codes  # arcs DO exist; this is the more specific gap

    # No arcs at all yet: the existing generic warning covers it, not the protagonist-specific one.
    empty = {"version": 1, "themes": [], "arcs": []}
    issues_empty = arc_design_issues(empty, card=card)
    codes_empty = [issue["code"] for issue in issues_empty]
    assert "character_arcs_missing" in codes_empty
    assert "protagonist_arc_missing" not in codes_empty

    # No cast at all: don't nag about a protagonist arc on a story with nobody in it yet.
    issues_no_cast = arc_design_issues(_design(), card={"cast": []})
    assert "protagonist_arc_missing" not in [issue["code"] for issue in issues_no_cast]

    # Once the protagonist has their own arc, the warning clears.
    design_with_protagonist = _design()
    design_with_protagonist["arcs"][0]["owner"] = "player"
    issues_resolved = arc_design_issues(design_with_protagonist, card=card)
    assert "protagonist_arc_missing" not in [issue["code"] for issue in issues_resolved]


if __name__ == "__main__":
    test_arc_design_normalizes_and_projects_only_safe_story_card_material()
    test_narrator_surface_gates_private_truth_and_only_opens_current_pressure()
    test_narrator_surface_never_uses_a_blind_spot_as_a_safe_belief()
    test_director_issues_flag_threads_that_do_not_belong_to_the_cast()
    test_protagonist_arc_resolves_by_player_alias_and_returns_none_otherwise()
    test_arc_design_issues_flags_missing_protagonist_arc_only_when_warranted()
    print("ok — thematic arcs have private truths and safe live pressure")

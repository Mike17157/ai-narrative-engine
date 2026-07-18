"""Direct regression checks for the deterministic authored-card scenario compiler."""
from __future__ import annotations

from copy import deepcopy

from loom.stories.runtime.scenario_compiler import compile_authored_scenario, readiness_issues


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
                "end_condition": "The Returner merges with the entity and defeats it.",
                "policy": {
                    "trigger": "death",
                    "restart": "opening",
                    "preserve": {
                        "runtime": [],
                        "memories": ["player"],
                        "clear_memories_for_others": True,
                    },
                    "victims_return_after_end": False,
                },
            },
        },
        "time_system": {
            "slots": ["morning", "evening", "night"],
            "entity_periods": [
                {"id": "day-watch", "slots": ["morning"], "state": "observing",
                 "capabilities": ["watch"], "constraint": "cannot attack"},
                {"id": "night-hunt", "slots": ["night"], "state": "hunting",
                 "capabilities": ["mimicry", "ambush"], "constraint": "needs isolation"},
            ],
        },
        "fields": {
            "player_id": "player",
            "first_day_plan": {
                "objective": "Reach the island and survive the first loop.",
                "opening_present": ["player", "shuri"],
                "events": [
                    {
                        "id": "crossing", "when": "morning", "location": "ferry",
                        "participants": ["player", "shuri"],
                        "visible": "The quiet ferry approaches the island.",
                        "trigger": "The player speaks to Shuri or watches the docks.",
                        "evidence": "A familiar whistle carries over the water.",
                        "knowledge": {"public": "The ferry arrived on time."},
                    },
                    {
                        "id": "ambush", "when": "night", "location": "harbor",
                        "participants": ["player"],
                        "requires": {"flags": {"left_ferry": True}},
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


def test_compiles_an_executable_loop_without_mutating_the_card():
    story = _ready_loop_story()
    before = deepcopy(story)
    compiled = compile_authored_scenario(story)

    assert story == before
    assert compiled["ready"], compiled["issues"]
    assert compiled["opening"]["scene_id"] == "crossing"
    assert compiled["opening"]["state"]["location"] == "ferry"
    assert compiled["opening"]["state"]["present"] == ["player", "shuri"]
    assert [scene["id"] for scene in compiled["scene_catalog"]] == ["crossing", "ambush"]
    assert compiled["scene_catalog"][1]["requires"]["flags"] == {"left_ferry": True}
    assert compiled["director_plan"]["events"][0]["entity_period_ids"] == ["night-hunt"]
    assert compiled["loop_policy"]["executable"]
    assert compiled["loop_policy"]["reset"]["memory"] == {
        "preserve_for": ["player"], "clear_for_others": True,
    }
    assert "turns" in compiled["loop_policy"]["reset"]["clear_runtime"]


def test_flags_legacy_prose_and_unscheduled_hidden_events_as_not_ready():
    story = {
        "world": {
            "entity": {"description": "A mimic."},
            # This is useful narrator prose, but cannot execute a reset safely.
            "loop": {"start": "the ferry", "reset": "everything resets",
                     "memory": "only the Returner remembers"},
        },
        "fields": {
            "first_day_plan": {
                "events": [{"id": "attack", "when": "night", "hidden": "The mimic attacks."}],
            },
        },
    }
    compiled = compile_authored_scenario(story)
    codes = {issue["code"] for issue in compiled["issues"]}

    assert not compiled["ready"]
    assert compiled["opening"]["state"]["location"] == "the-ferry"
    assert "entity_schedule_missing" in codes
    assert "loop_trigger_missing" in codes
    assert "loop_memory_policy_missing" in codes
    assert "loop_runtime_policy_missing" in codes
    assert readiness_issues(story) == compiled["issues"]


def test_private_hidden_notes_do_not_consume_an_entity_window():
    story = _ready_loop_story()
    story["fields"]["first_day_plan"]["events"].insert(1, {
        "id": "quiet-warning", "when": "evening", "location": "harbor",
        "participants": ["player", "shuri"],
        "visible": "Shuri grows quiet as the lights come on around the harbor.",
        "hidden": "Shuri changes the subject rather than explain why she is afraid.",
        "entity_action": False,
        "trigger": "The player asks about the missing posters.",
        "evidence": "Shuri's unfinished sentence.",
        "knowledge": {"protagonist": "Shuri is holding something back."},
    })

    compiled = compile_authored_scenario(story)

    assert compiled["ready"], compiled["issues"]
    note = next(event for event in compiled["director_plan"]["events"] if event["id"] == "quiet-warning")
    assert note["entity_action"] is False
    assert note["entity_period_ids"] == []


def test_restart_at_opening_location_is_a_safe_opening_shorthand():
    story = _ready_loop_story()
    story["world"]["loop"]["policy"]["restart"] = "ferry"

    compiled = compile_authored_scenario(story)

    assert compiled["ready"], compiled["issues"]
    assert compiled["loop_policy"]["restart"]["location"] == "ferry"


def test_accepts_the_pydantic_story_model_as_well_as_raw_cards():
    from loom.config.schema import Story

    compiled = compile_authored_scenario(Story(name="Empty interview story"))

    assert not compiled["ready"]
    assert compiled["opening"]["state"]["time"] == "morning"
    assert any(issue["code"] == "missing_first_day_events" for issue in compiled["issues"])


def test_rejects_opening_people_without_real_cast_keys():
    story = _ready_loop_story()
    story["fields"]["first_day_plan"]["opening_present"] = ["player", "mystery-person"]
    compiled = compile_authored_scenario(story)
    assert not compiled["ready"]
    assert any(issue["code"] == "opening_participant_unknown" for issue in compiled["issues"])


def test_explicit_player_only_opening_is_not_an_unresolved_cast_prompt():
    story = _ready_loop_story()
    story["fields"]["first_day_plan"]["opening_present"] = ["player"]

    compiled = compile_authored_scenario(story)

    assert compiled["ready"], compiled["issues"]
    assert not any(issue["code"] == "opening_cast_unspecified" for issue in compiled["issues"])


def test_opening_scene_player_participant_also_records_a_deliberate_solo_opening():
    story = _ready_loop_story()
    story["fields"]["first_day_plan"].pop("opening_present")
    story["fields"]["first_day_plan"]["events"][0]["participants"] = ["player"]

    compiled = compile_authored_scenario(story)

    assert compiled["ready"], compiled["issues"]
    assert not any(issue["code"] == "opening_cast_unspecified" for issue in compiled["issues"])


def test_canonicalizes_player_roles_and_keeps_hidden_entity_aliases_out_of_public_cast():
    """Prose roles must not become fake cards in the executable public roster."""
    story = _ready_loop_story()
    plan = story["fields"]["first_day_plan"]
    plan["opening_present"] = ["protagonist"]
    plan["events"][0]["participants"] = ["Returner"]
    plan["events"][1]["participants"] = ["protagonist", "entity-shuri"]
    before = deepcopy(story)

    compiled = compile_authored_scenario(story)

    assert story == before
    assert compiled["ready"], compiled["issues"]
    assert compiled["opening"]["state"]["present"] == ["player"]
    assert compiled["scene_catalog"][0]["participants"] == ["player"]
    # The second scene is still director-planned, but the entity's Shuri-shaped
    # disguise is not a real Shuri presence for the public runtime roster.
    assert compiled["scene_catalog"][1]["participants"] == ["player"]
    assert not any("participant_unknown" in issue["code"] for issue in compiled["issues"])


def test_keeps_unknown_public_participants_as_readiness_gaps_after_alias_cleanup():
    story = _ready_loop_story()
    story["fields"]["first_day_plan"]["events"][0]["participants"] = [
        "returner", "unintroduced-stranger",
    ]

    compiled = compile_authored_scenario(story)

    assert not compiled["ready"]
    issue = next(issue for issue in compiled["issues"] if issue["code"] == "scene_participant_unknown")
    assert "unintroduced-stranger" in issue["message"]
    assert "returner" not in issue["message"].lower()
    assert compiled["scene_catalog"][0]["participants"] == ["player", "unintroduced-stranger"]


if __name__ == "__main__":
    test_compiles_an_executable_loop_without_mutating_the_card()
    test_flags_legacy_prose_and_unscheduled_hidden_events_as_not_ready()
    test_private_hidden_notes_do_not_consume_an_entity_window()
    test_restart_at_opening_location_is_a_safe_opening_shorthand()
    test_accepts_the_pydantic_story_model_as_well_as_raw_cards()
    test_rejects_opening_people_without_real_cast_keys()
    test_explicit_player_only_opening_is_not_an_unresolved_cast_prompt()
    test_opening_scene_player_participant_also_records_a_deliberate_solo_opening()
    test_canonicalizes_player_roles_and_keeps_hidden_entity_aliases_out_of_public_cast()
    test_keeps_unknown_public_participants_as_readiness_gaps_after_alias_cleanup()
    print("ok — authored card scenario compiler")

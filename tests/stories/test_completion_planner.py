"""Unit coverage for the bounded Story Architect completion planner."""
from __future__ import annotations

import json

from loom.stories.authoring.completion_planner import (
    build_completion_plan,
    build_director_knowledge_task,
    build_public_baseline_population_task,
    normalize_mechanical_setup,
    sanitize_public_baseline_population,
)


def _gap(ident: str, section: str, *, paths: list[str] | None = None, detail: str = "") -> dict:
    return {
        "id": ident,
        "section": section,
        "paths": paths or [],
        # These deliberately test that the plan never repeats untrusted model
        # or director text into its own public operational projection.
        "detail": detail,
        "title": detail,
        "suggested_scope": detail,
    }


def _seed_card(**extra) -> dict:
    card = {
        "premise": "A returner steps from a ferry onto a quiet island.",
        "world": {"setting": "An island."},
        "locations": [{"id": "ferry", "name": "Ferry"}],
        "fields": {"first_day_plan": {"events": []}},
    }
    card.update(extra)
    return card


def test_blank_card_requires_a_starting_spark_before_any_scaffold_work():
    plan = build_completion_plan({}, {"gaps": [_gap("cast-missing", "cast")]})

    assert plan["phase"] == "author_decision"
    assert plan["safe_scopes"] == []
    assert plan["safe_gap_ids"] == []
    assert plan["authorial_hinges"] == [{
        "id": "starting-spark", "section": "world", "reason": "starting_spark", "blocked": False,
    }]


def test_public_baseline_batches_safe_scopes_but_queues_a_theme_hinge():
    plan = build_completion_plan(_seed_card(), {"gaps": [
        _gap("world-map-missing", "world"),
        _gap("premise-missing", "premise"),
        _gap("cast-missing", "cast"),
        _gap("cast-unintroduced", "first_day"),
        _gap("theme-missing", "arcs"),
    ]})

    assert plan["phase"] == "safe_baseline"
    assert plan["safe_scopes"] == ["world", "premise", "cast", "first_day"]
    assert plan["safe_gap_ids"] == [
        "world-map-missing", "premise-missing", "cast-missing", "cast-unintroduced",
    ]
    assert plan["authorial_hinges"] == [{
        "id": "theme-missing", "section": "arcs", "reason": "character_or_theme_truth", "blocked": False,
    }]


def test_private_day_one_gate_prevents_the_batch_from_touching_any_day_one_gap():
    secret = "THE ENTITY COPIES A VICTIM'S LAST MEMORY"
    card = _seed_card(fields={"first_day_plan": {"events": [{
        "id": "ambush", "when": "night", "hidden": secret,
        "knowledge": {"entity": secret}, "evidence": secret,
    }]}})
    plan = build_completion_plan(card, {"gaps": [
        _gap("cast-unintroduced", "first_day", detail=secret),
        _gap("readiness-scene-id-derived-fields-first-day-plan-events-0-id", "first_day", detail=secret),
    ]})

    assert plan["phase"] == "author_decision"
    assert plan["safe_scopes"] == []
    assert {item["reason"] for item in plan["authorial_hinges"]} == {"private_story_logic"}
    # The planner can safely give the coordinator ids, but it must never leak
    # the private material embedded in a card or arbitrary report description.
    assert secret not in json.dumps(plan)


def test_loop_time_relationship_and_arc_work_are_authorial_hinges():
    plan = build_completion_plan(_seed_card(), {"gaps": [
        _gap("readiness-loop-restart-missing-world-loop-policy-restart", "world",
             paths=["world.loop.policy.restart"]),
        _gap("readiness-scene-time-unknown-fields-first-day-plan-events-0-when", "first_day",
             paths=["fields.first_day_plan.events[0].when"]),
        _gap("relationships-missing", "cast"),
        _gap("knowledge-gate-ambush", "first_day"),
        _gap("arc-design-missing", "arcs"),
    ]})

    assert plan["phase"] == "author_decision"
    assert plan["safe_scopes"] == []
    reasons = {item["id"]: item["reason"] for item in plan["authorial_hinges"]}
    assert reasons["readiness-loop-restart-missing-world-loop-policy-restart"] == "loop_or_reset_rule"
    assert reasons["readiness-scene-time-unknown-fields-first-day-plan-events-0-when"] == "time_or_entity_rule"
    assert reasons["relationships-missing"] == "relationship_truth"
    assert reasons["knowledge-gate-ambush"] == "private_story_logic"
    assert reasons["arc-design-missing"] == "character_or_theme_truth"


def test_existing_cast_scene_placement_is_an_authorial_hinge_not_a_model_fill():
    card = _seed_card(cast=[{"character": "shuri"}])
    plan = build_completion_plan(card, {"gaps": [
        _gap("readiness-opening-cast-unspecified-fields-first-day-plan-opening-present", "first_day",
             paths=["fields.first_day_plan.opening_present"]),
        _gap("readiness-scene-location-inherited-fields-first-day-plan-events-1-location", "first_day",
             paths=["fields.first_day_plan.events[1].location"]),
        _gap("cast-unintroduced", "first_day", paths=["cast", "fields.first_day_plan.events"]),
    ]})

    assert plan["phase"] == "author_decision"
    assert plan["safe_scopes"] == []
    assert {item["reason"] for item in plan["authorial_hinges"]} == {"scene_placement"}


def test_legacy_scene_offer_quality_is_a_protected_authorial_repair_not_a_generic_develop_pass():
    plan = build_completion_plan(_seed_card(), {"gaps": [
        _gap(
            "scene-offer-subtle-hints",
            "first_day",
            paths=["fields.first_day_plan.events[0]"],
        ),
    ]})

    assert plan["phase"] == "author_decision"
    assert plan["safe_scopes"] == []
    assert plan["safe_gap_ids"] == []
    assert plan["authorial_hinges"] == [{
        "id": "scene-offer-subtle-hints",
        "section": "first_day",
        "reason": "scene_offer_quality",
        "blocked": False,
    }]


def test_blocked_safe_gap_is_not_retried_and_becomes_an_explicit_hinge():
    plan = build_completion_plan(
        _seed_card(),
        {"gaps": [_gap("cast-missing", "cast"), _gap("world-map-missing", "world")]},
        blocked_gap_ids={"cast-missing"},
    )

    assert plan["phase"] == "safe_baseline"
    assert plan["safe_scopes"] == ["world"]
    assert plan["safe_gap_ids"] == ["world-map-missing"]
    assert plan["authorial_hinges"] == [{
        "id": "cast-missing", "section": "cast", "reason": "blocked_scaffold", "blocked": True,
    }]
    assert plan["summary"]["blocked_task_count"] == 1


def test_no_remaining_gaps_marks_the_plan_ready():
    plan = build_completion_plan(_seed_card(), {"gaps": []})

    assert plan == {
        "phase": "ready",
        "safe_scopes": [],
        "safe_gap_ids": [],
        "authorial_hinges": [],
        "summary": {"safe_task_count": 0, "authorial_hinge_count": 0, "blocked_task_count": 0},
    }


def test_mechanical_normalizer_repairs_only_unambiguous_start_restart_and_entity_token():
    card = _seed_card(
        start="",
        locations=[{"id": "electric-ferry", "name": "Electric Ferry"}],
        world={
            "entity": {"description": "An old god."},
            "loop": {
                "start": "Aboard the electric ferry while the island comes into view.",
                "reset": "Each death returns the player back to the ferry at the beginning of the day.",
                "policy": {"preserve": {"runtime": ["time", "entity", "flags"]}},
            },
        },
    )

    updated, adjustments, unresolved = normalize_mechanical_setup(card)

    assert updated["start"] == "electric-ferry"
    assert updated["world"]["loop"]["policy"]["restart"] == "opening"
    assert updated["world"]["loop"]["policy"]["preserve"]["runtime"] == ["time", "flags"]
    assert {item["id"] for item in adjustments} == {
        "normalize-start-location", "normalize-loop-restart", "normalize-loop-runtime-entity",
    }
    assert unresolved == []
    # The caller's canonical object is untouched; the normalizer is safe to
    # evaluate before a route decides whether to persist it.
    assert card["start"] == ""
    assert card["world"]["loop"]["policy"].get("restart") is None


def test_mechanical_normalizer_refuses_ambiguous_or_vague_repairs():
    card = _seed_card(
        start="",
        locations=[
            {"id": "east-dock", "name": "Island Dock"},
            {"id": "west-dock", "name": "Island Dock"},
        ],
        world={
            "loop": {
                "start": "The island dock is quiet.",
                # Mentioning a ferry as part of the reset world is not an
                # explicit instruction to restart the runtime on the ferry.
                "reset": "The town, ferry, and people revert when the player dies.",
                "policy": {"preserve": {"runtime": ["entity"]}},
            },
        },
    )

    updated, adjustments, unresolved = normalize_mechanical_setup(card)

    assert updated == card
    assert adjustments == []
    assert {item["id"] for item in unresolved} == {
        "opening-location-ambiguous", "loop-restart-unresolved", "loop-runtime-entity-unresolved",
    }


def test_mechanical_normalizer_does_not_echo_private_loop_or_entity_text():
    secret = "PRIVATE KILL ROUTE"
    card = _seed_card(
        start="",
        locations=[{"id": "electric-ferry", "name": "Electric Ferry"}],
        world={
            "entity": {"description": secret},
            "loop": {
                "start": secret,
                "reset": secret,
                "policy": {"preserve": {"runtime": ["entity"]}},
            },
        },
    )

    _updated, adjustments, unresolved = normalize_mechanical_setup(card)

    assert secret not in json.dumps({"adjustments": adjustments, "unresolved": unresolved})


def test_mechanical_normalizer_uses_only_a_direct_author_answer_to_active_restart_question():
    card = _seed_card(
        start="",
        locations=[{"id": "electric-ferry", "name": "Electric Ferry"}],
        world={
            "loop": {
                "start": "Aboard the electric ferry.",
                # This old generic prose is not executable by itself.
                "reset": "The world, ferry, and island revert after death.",
                "policy": {"restart": "The world returns to how it was before death."},
            },
        },
        fields={
            "first_day_plan": {"events": []},
            "architect_state": {
                "pending_question": {"id": "readiness-loop-restart-missing-world-loop-policy-restart"},
                "history": [
                    {"role": "architect", "text": "The loop policy must say where the reset resumes."},
                    {"role": "author", "text": "It starts at the first scene of the day."},
                ],
            },
        },
    )

    updated, adjustments, unresolved = normalize_mechanical_setup(card)

    assert updated["world"]["loop"]["policy"]["restart"] == "opening"
    assert any(item["id"] == "normalize-loop-restart-from-author-answer" for item in adjustments)
    assert not any(item["id"] == "loop-restart-unresolved" for item in unresolved)


def test_mechanical_normalizer_ignores_a_similar_author_phrase_without_the_active_loop_prompt():
    card = _seed_card(
        world={
            "loop": {
                "reset": "The ferry and town revert after death.",
                "policy": {"restart": "The world returns to how it was before death."},
            },
        },
        fields={
            "first_day_plan": {"events": []},
            "architect_state": {
                "pending_question": {"id": "theme-missing"},
                "history": [{"role": "author", "text": "It starts at the first scene of the day."}],
            },
        },
    )

    updated, adjustments, unresolved = normalize_mechanical_setup(card)

    assert updated["world"]["loop"]["policy"]["restart"] != "opening"
    assert not any(item["id"] == "normalize-loop-restart-from-author-answer" for item in adjustments)
    assert any(item["id"] == "loop-restart-unresolved" for item in unresolved)


def test_mechanical_normalizer_normalizes_only_existing_public_day_one_structure():
    card = _seed_card(
        locations=[
            {"id": "electric-ferry", "name": "Electric Ferry"},
            {"id": "island-dock", "name": "Island Dock"},
            {"id": "sleeping-chamber", "name": "Sleeping Chamber"},
        ],
        fields={"first_day_plan": {"events": [
            {"event": "Ferry approach and disembarkation.", "when": "morning"},
            {"visible": "Shuri waits at the dock.", "when": "morning"},
            # The attack is existing public prose but does not name a location;
            # the normalizer must not manufacture one just to suppress a warning.
            {"event": "The entity ambushes the returner.", "when": "night"},
            # Protected mechanics make even an obvious surface location off
            # limits for this mechanical batch.
            {"event": "A ferry bell rings.", "when": "night", "hidden": "PRIVATE"},
        ]}},
    )

    updated, adjustments, unresolved = normalize_mechanical_setup(card)
    events = updated["fields"]["first_day_plan"]["events"]

    assert [event["id"] for event in events[:3]] == [
        "ferry-approach-and-disembarkation", "shuri-waits-at-the-dock", "the-entity-ambushes-the-returner",
    ]
    assert "id" not in events[3]
    assert events[0]["location"] == "electric-ferry"
    assert events[1]["location"] == "island-dock"
    assert "location" not in events[2]
    assert "location" not in events[3]
    assert updated["fields"]["first_day_plan"]["opening_time"] == "morning"
    adjustment_ids = {item["id"] for item in adjustments}
    assert "normalize-day-one-scene-id-1" in adjustment_ids
    assert "normalize-day-one-location-1" in adjustment_ids
    assert "normalize-opening-time" in adjustment_ids
    unresolved_ids = {item["id"] for item in unresolved}
    assert "day-one-location-unresolved" in unresolved_ids
    assert "day-one-private-mechanics-preserved" in unresolved_ids


def test_public_baseline_population_task_is_tiny_and_tied_only_to_public_places():
    card = _seed_card(
        locations=[
            {"id": "electric-ferry", "name": "Electric Ferry"},
            {"id": "island-dock", "name": "Island Dock"},
            # This is established map data, but its role in the central mystery
            # means no automatic supporting person may be attached to it.
            {"id": "sleeping-chamber", "name": "Sleeping Chamber"},
            {"id": "village-cafe", "name": "Village Cafe"},
        ],
        cast=[{"character": "shuri", "home": ""}],
        cast_details=[{"character": "shuri", "name": "Shuri", "role": "childhood friend"}],
        fields={"first_day_plan": {"events": [
            {"id": "crossing", "location": "electric-ferry", "visible": "The ferry arrives."},
            {"id": "arrival", "location": "island-dock", "visible": "The dock comes into view."},
        ]}},
    )
    task = build_public_baseline_population_task(card, {"gaps": [
        _gap("cast-unintroduced", "first_day"),
    ]})

    assert task is not None
    assert task["kind"] == "public_supporting_cast"
    assert task["scope"] == "cast"
    assert task["maximum_characters"] == 2
    assert task["archetypes"] == [
        {"id": "public-ferry-crew-electric-ferry", "role": "ferry crew member", "location_id": "electric-ferry"},
        {"id": "public-dock-attendant-island-dock", "role": "dock attendant", "location_id": "island-dock"},
    ]
    assert task["constraints"] == {
        "public_only": True,
        "allow_relationships": False,
        "allow_private_fields": False,
        "allow_primary": False,
    }
    assert "sleeping" not in json.dumps(task).lower()


def test_public_baseline_population_refuses_to_run_when_day_one_has_private_gates():
    secret = "PRIVATE ENTITY WINDOW"
    card = _seed_card(
        locations=[{"id": "electric-ferry", "name": "Electric Ferry"}],
        cast=[{"character": "shuri", "home": ""}],
        fields={"first_day_plan": {"events": [
            {"id": "bell", "location": "electric-ferry", "visible": "A ferry bell rings."},
            {"id": "ambush", "location": "electric-ferry", "hidden": secret, "knowledge": {"entity": secret}},
        ]}},
    )

    task = build_public_baseline_population_task(card, {"gaps": [_gap("cast-missing", "cast", detail=secret)]})

    assert task is None


def test_director_knowledge_task_uses_only_existing_on_scene_observers():
    secret = "THE MIMIC COPIED THE LAST VICTIM"
    card = _seed_card(
        cast=[{"character": "shuri", "home": "island-dock"}],
        fields={"first_day_plan": {"events": [{
            "id": "dock-warning", "when": "night", "location": "island-dock",
            "participants": ["player", "shuri"], "hidden": secret,
        }]}},
    )
    gap = _gap("knowledge-gate-dock-warning", "first_day", detail=secret)
    gap["related"] = ["dock-warning"]

    task = build_director_knowledge_task(card, {"gaps": [gap]})
    plan = build_completion_plan(card, {"gaps": [gap]})

    assert task == {
        "id": "director-knowledge-dock-warning",
        "kind": "director_knowledge_boundary",
        "section": "first_day",
        "gap_id": "knowledge-gate-dock-warning",
        "event_id": "dock-warning",
        "observer_ids": ["player", "shuri"],
        "constraints": {
            "existing_event_only": True,
            "existing_observers_only": True,
            "fill_blank_knowledge_only": True,
        },
    }
    assert plan["phase"] == "protected_baseline"
    assert plan["protected_tasks"] == [task]
    assert plan["authorial_hinges"] == []
    assert secret not in json.dumps(plan)


def test_director_knowledge_task_never_invents_an_off_scene_observer():
    card = _seed_card(
        cast=[{"character": "shuri", "home": "island-dock"}],
        fields={"first_day_plan": {"events": [{
            "id": "dock-warning", "hidden": "PRIVATE", "participants": [],
        }]}},
    )
    gap = _gap("knowledge-gate-dock-warning", "first_day")
    gap["related"] = ["dock-warning"]

    plan = build_completion_plan(card, {"gaps": [gap]})

    assert build_director_knowledge_task(card, {"gaps": [gap]}) is None
    assert plan["phase"] == "author_decision"
    assert plan["authorial_hinges"][0]["id"] == "knowledge-gate-dock-warning"


def test_public_baseline_population_skips_a_gated_location_but_can_use_another_public_place():
    card = _seed_card(
        locations=[
            {"id": "electric-ferry", "name": "Electric Ferry"},
            {"id": "village-cafe", "name": "Village Cafe"},
        ],
        cast=[{"character": "shuri", "home": ""}],
        fields={"first_day_plan": {"events": [
            {"id": "ambush", "location": "electric-ferry", "hidden": "PRIVATE"},
            {"id": "coffee", "location": "village-cafe", "visible": "The cafe opens."},
        ]}},
    )

    task = build_public_baseline_population_task(card, {"gaps": [_gap("cast-unintroduced", "first_day")]})

    assert task is not None
    assert task["archetypes"] == [{
        "id": "public-cafe-worker-village-cafe", "role": "cafe worker", "location_id": "village-cafe",
    }]


def test_public_baseline_population_sanitizer_enforces_public_supporting_fields():
    task = {
        "kind": "public_supporting_cast",
        "archetypes": [
            {"id": "public-ferry-crew-electric-ferry", "role": "ferry crew member", "location_id": "electric-ferry"},
            {"id": "public-dock-attendant-island-dock", "role": "dock attendant", "location_id": "island-dock"},
        ],
    }
    secret = "THE ENTITY TOLD THEM EVERYTHING"
    fragment = sanitize_public_baseline_population({
        "characters": [
            {
                "name": "Mio", "role": "entity avatar", "appearance": f"blue coat [[hidden]]{secret}[[/hidden]]",
                "persona": secret, "secret": secret, "want": secret, "wound": secret, "lie": secret,
                "primary": True, "home": "sleeping-chamber",
            },
            {
                "name": "Jun", "role": "villain", "appearance": "rain jacket",
                "connection": "knows the answer", "primary": True, "home": "sleeping-chamber",
            },
            {"name": "Ignored Third", "secret": secret},
        ],
        "relationships": [{"source": "Mio", "target": "Jun", "nature": secret}],
        "world": {"entity": {"description": secret}},
    }, task, existing_names={"Shuri"})

    assert fragment["relationships"] == []
    assert [person["name"] for person in fragment["characters"]] == ["Mio", "Jun"]
    assert [person["role"] for person in fragment["characters"]] == ["ferry crew member", "dock attendant"]
    assert [person["home"] for person in fragment["characters"]] == ["electric-ferry", "island-dock"]
    assert all(person["primary"] is False for person in fragment["characters"])
    assert all(person[field] == "" for person in fragment["characters"]
               for field in ("persona", "personality", "background", "connection", "want", "wound", "lie", "secret"))
    assert fragment["characters"][0]["appearance"] == ""
    assert fragment["characters"][1]["appearance"] == "rain jacket"
    assert secret not in json.dumps(fragment)


if __name__ == "__main__":
    test_blank_card_requires_a_starting_spark_before_any_scaffold_work()
    test_public_baseline_batches_safe_scopes_but_queues_a_theme_hinge()
    test_private_day_one_gate_prevents_the_batch_from_touching_any_day_one_gap()
    test_loop_time_relationship_and_arc_work_are_authorial_hinges()
    test_existing_cast_scene_placement_is_an_authorial_hinge_not_a_model_fill()
    test_legacy_scene_offer_quality_is_a_protected_authorial_repair_not_a_generic_develop_pass()
    test_blocked_safe_gap_is_not_retried_and_becomes_an_explicit_hinge()
    test_no_remaining_gaps_marks_the_plan_ready()
    test_mechanical_normalizer_repairs_only_unambiguous_start_restart_and_entity_token()
    test_mechanical_normalizer_refuses_ambiguous_or_vague_repairs()
    test_mechanical_normalizer_does_not_echo_private_loop_or_entity_text()
    test_mechanical_normalizer_uses_only_a_direct_author_answer_to_active_restart_question()
    test_mechanical_normalizer_ignores_a_similar_author_phrase_without_the_active_loop_prompt()
    test_mechanical_normalizer_normalizes_only_existing_public_day_one_structure()
    test_public_baseline_population_task_is_tiny_and_tied_only_to_public_places()
    test_public_baseline_population_refuses_to_run_when_day_one_has_private_gates()
    test_public_baseline_population_skips_a_gated_location_but_can_use_another_public_place()
    test_director_knowledge_task_uses_only_existing_on_scene_observers()
    test_director_knowledge_task_never_invents_an_off_scene_observer()
    test_public_baseline_population_sanitizer_enforces_public_supporting_fields()
    print("ok — deterministic completion planner")

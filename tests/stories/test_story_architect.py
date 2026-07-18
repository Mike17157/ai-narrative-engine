"""Regression coverage for the bounded primary Story Architect coordinator."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import shutil
import tempfile
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from loom.config.schema import Character
from loom.server.app import create_app
from loom.server.context_providers import ProviderContextMixin
from loom.server.services import story_store
from loom.stories.authoring.interview_graph import InterviewResult
from loom.stories.authoring.story_architect import (
    author_plan_presentation,
    card_revision,
    choose_question,
    current_pending_question,
    plan_comment_target,
    question_for_answer,
)
from loom.stories.authoring.gap_analysis import analyze_story_card
import loom.stories.authoring.card_graph as card_graph
import loom.stories.authoring.interview_graph as interview_graph


@contextmanager
def _isolated_client():
    root = Path(tempfile.mkdtemp(prefix="loom-story-architect-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    for card in (root / "configs" / "characters").glob("*.yaml"):
        card.unlink()
    client = TestClient(create_app(root))
    try:
        yield client, root
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


def _new_story(client: TestClient) -> str:
    response = client.post("/api/stories/new", json={"name": "Architect test"})
    assert response.status_code == 200, response.text
    return response.json()["key"]


def _author_plan_seed(*, private: str = "PRIVATE_PLAN_SECRET") -> dict:
    """A rich enough card to exercise every public Architect plan section.

    The private sentinel is deliberately scattered through director-only
    mechanics.  The response's ``author_plan`` is an author-facing plan, not
    an alternate channel for card internals, so these tests ensure the plan
    never serializes it.
    """
    return {
        "premise": "A returner reaches an island on a quiet electric ferry after four years away.",
        "world": {"setting": "A sunlit island town that remembers the returner."},
        "locations": [
            {"id": "ferry", "name": "Electric ferry", "description": "Bright water and quiet engines."},
            {"id": "island-dock", "name": "Island dock", "description": "Wet boards below the town."},
        ],
        "start": "ferry",
        "time_system": {
            "slots": ["morning", "afternoon", "night"],
            "entity_periods": [{
                "id": "hunt", "slots": ["night"], "state": "hunting",
                "capabilities": [private], "constraint": private,
            }],
        },
        "cast": [{"character": "shuri", "primary": True, "home": "island-dock"}],
        "fields": {"first_day_plan": {
            "objective": "Reach the island and reconnect with Shuri.",
            "opening_time": "morning", "opening_location": "ferry", "opening_present": ["player"],
            "events": [
                {
                    "id": "ferry-crossing", "when": "morning", "location": "ferry",
                    "participants": ["player"], "visible": "The electric ferry glides toward the island.",
                },
                {
                    "id": "dock-reunion", "when": "morning", "location": "island-dock",
                    "participants": ["player", "shuri"], "visible": "Shuri waits at the dock.",
                },
                {
                    "id": "entity-ambush", "when": "night", "location": "island-dock",
                    "participants": ["player"], "visible": "The lights at the dock go out.",
                    "hidden": private, "trigger": private, "evidence": private,
                    "knowledge": {"shuri": private}, "entity_action": True, "entity_period": "hunt",
                },
            ],
        }},
    }


def _assert_full_author_plan(plan: dict, *, private: str) -> None:
    """Assert the public contract for plan-first Architect surfaces.

    This deliberately tests stable section identifiers and comment targets,
    rather than exact prose.  The Architect may improve its wording without
    breaking direct section comments or exposing worker diagnostics.
    """
    assert plan["version"] == 1
    assert isinstance(plan["title"], str) and plan["title"].strip()
    assert isinstance(plan["summary"], str) and plan["summary"].strip()
    assert isinstance(plan["comment_prompt"], str) and plan["comment_prompt"].strip()

    expected_comment_ids = {
        "opening": "architect-plan-opening",
        "cast": "architect-plan-cast",
        "scene_flow": "architect-plan-scenes",
        "mystery_boundary": "architect-plan-mystery",
        "arc_theme": "architect-plan-arcs",
        "build_order": "architect-plan-build-order",
    }
    sections = plan["sections"]
    assert isinstance(sections, list)
    by_id = {section["id"]: section for section in sections}
    assert set(by_id) == set(expected_comment_ids)
    for ident, comment_id in expected_comment_ids.items():
        section = by_id[ident]
        assert isinstance(section["label"], str) and section["label"].strip()
        assert isinstance(section["status"], str) and section["status"].strip()
        assert isinstance(section["summary"], str) and section["summary"].strip()
        assert section["comment_id"] == comment_id
        target = section["target"]
        assert target["section"] in {"world", "premise", "cast", "first_day", "time_system", "arcs"}
        assert isinstance(target["plan_kind"], str) and target["plan_kind"].strip()

    build_order = plan["build_order"]
    assert isinstance(build_order, list) and build_order
    assert {step["id"] for step in build_order}.issubset(set(expected_comment_ids))
    assert all(isinstance(step["label"], str) and step["label"].strip() for step in build_order)
    next_step = plan["next_step"]
    assert next_step["id"] in expected_comment_ids
    assert next_step["comment_id"] == expected_comment_ids[next_step["id"]]

    serialized = json.dumps(plan, sort_keys=True).lower()
    # Plan prose must never inherit hidden card text or the worker's raw
    # compiler language.  The latter is exactly what previously leaked into
    # the Architect conversation as a chain of technical questions.
    for forbidden in (
        private.lower(), "prose-derived", "smallest missing operational",
        "opening names people who are not cast keys", "readiness-opening-participant",
    ):
        assert forbidden not in serialized


@contextmanager
def _developer(proposal: dict):
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


@contextmanager
def _interviewer(turn):
    original_turn = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider
    calls: list[dict] = []

    async def wrapped(**kwargs):
        calls.append(kwargs)
        return await turn(**kwargs)

    interview_graph.run_interview_turn = wrapped
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True, "selected_model": "test"})
    try:
        yield calls
    finally:
        interview_graph.run_interview_turn = original_turn
        ProviderContextMixin.story_agent_provider = original_provider


def test_architect_bootstrap_returns_a_full_public_author_plan_without_worker_diagnostics():
    """The primary surface introduces a plan, rather than a question chain.

    ``question`` may remain in the payload for old clients, but the plan is
    the complete, commentable representation a current UI must render.
    """
    private = "PRIVATE_PLAN_SECRET"
    with _isolated_client() as (client, _root):
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json=_author_plan_seed(private=private))
        assert seeded.status_code == 200, seeded.text
        first = client.post(f"/api/stories/{key}/architect/turn")
        second = client.post(f"/api/stories/{key}/architect/turn")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    payload = first.json()
    assert payload["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    _assert_full_author_plan(payload["author_plan"], private=private)
    # Reloads do not turn a fixed plan into another worker prompt or mutate
    # its public structure merely because legacy ``question`` routing exists.
    assert second.json()["author_plan"] == payload["author_plan"]


def test_author_can_comment_on_any_live_plan_section_without_answering_a_question_first():
    """A direct plan comment routes to its fixed, safe section target.

    This is the central interaction change: even if a compatibility question
    is still persisted, an author may say what they want about ``opening``
    without first answering whichever section happened to be selected next.
    """
    async def turn(**kwargs):
        assert "ACTIVE AUTHORING TARGET: first_day" in kwargs["prompt"]
        assert "solo" in kwargs["prompt"].lower()
        return InterviewResult(reply="The opening brief is established.", patch={})

    with _isolated_client() as (client, _root), _interviewer(turn) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json=_author_plan_seed())
        assert seeded.status_code == 200, seeded.text
        bootstrap = client.post(f"/api/stories/{key}/architect/turn")
        assert bootstrap.status_code == 200, bootstrap.text
        assert "opening" in {item["id"] for item in bootstrap.json()["author_plan"]["sections"]}

        # No answer_to: this must use the selected plan section rather than
        # force the caller through the legacy next-question chain.
        response = client.post(f"/api/stories/{key}/architect/turn", json={
            "plan_section": "opening",
            "message": "Keep the ferry crossing solo; Shuri waits at the dock after disembarkation.",
        })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "interview"
    assert payload["bounded"] == {"model_calls": 1, "maximum_model_calls": 1}
    assert len(calls) == 1
    _assert_full_author_plan(payload["author_plan"], private="PRIVATE_PLAN_SECRET")


def test_author_plan_rejects_an_unknown_section_before_waking_a_worker():
    """Section comments cannot become an arbitrary edit-target escape hatch."""
    async def turn(**_kwargs):
        raise AssertionError("an unknown plan section must not wake an interviewer")

    with _isolated_client() as (client, _root), _interviewer(turn) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json=_author_plan_seed())
        assert seeded.status_code == 200, seeded.text
        response = client.post(f"/api/stories/{key}/architect/turn", json={
            "plan_section": "rewrite-private-entity-logic",
            "message": "Change it.",
        })

    assert response.status_code == 409, response.text
    assert calls == []


def test_every_visible_author_plan_section_has_a_fixed_safe_comment_target():
    """The plan's section controls are a whitelist, not free-form routing."""
    card = _author_plan_seed()
    report = analyze_story_card(card)
    presentation = author_plan_presentation(card, report)
    expected = {
        "opening": ("architect-plan-opening", "first_day", "opening_presence"),
        "cast": ("architect-plan-cast", "cast", "cast_direction"),
        "scene_flow": ("architect-plan-scenes", "first_day", "scene_flow"),
        "mystery_boundary": ("architect-plan-mystery", "first_day", "investigation"),
        "arc_theme": ("architect-plan-arcs", "arcs", "character_arc"),
    }
    for section_id, (comment_id, section, plan_kind) in expected.items():
        target = plan_comment_target(card, report, section_id)
        assert target is not None
        assert target["id"] == comment_id
        assert target["section"] == section
        assert target["target"]["plan_kind"] == plan_kind
        assert target["mode"] == "protected_interview"

    # Build order uses the current bounded completion decision, rather than
    # becoming a generic edit route.  It must still be a recognized visible
    # section and never contain an arbitrary client-selected target.
    build_target = plan_comment_target(card, report, "build_order")
    assert build_target is not None
    assert build_target["section"] in {"world", "premise", "cast", "first_day", "time_system", "arcs"}
    assert plan_comment_target(card, report, "arbitrary-private-path") is None
    assert {item["id"] for item in presentation["sections"]} == {
        *expected,
        "build_order",
    }


def test_architect_blank_bootstrap_persists_one_question_without_mutating_canon():
    with _isolated_client() as (client, _root):
        key = _new_story(client)
        before = client.get(f"/api/stories/{key}")
        first = client.post(f"/api/stories/{key}/architect/turn")
        second = client.post(f"/api/stories/{key}/architect/turn")
        after = client.get(f"/api/stories/{key}")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    payload = first.json()
    assert payload["phase"] == "awaiting_author"
    assert payload["action"] == "assess"
    assert payload["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    assert payload["question"]["id"] == "starting-spark"
    assert second.json()["question"] == payload["question"]
    # State is persisted author-side, but it never appears on the normal card
    # and the blank bootstrap leaves every canonical field untouched.
    assert "architect_state" not in after.json().get("fields", {})
    assert before.json()["premise"] == after.json()["premise"] == ""
    assert before.json()["cast"] == after.json()["cast"] == []


def test_architect_invalidates_a_pending_question_after_an_external_card_edit():
    proposal = {"message": "must not run", "world": {}, "premise": "", "locations": [], "start": "",
                "time_system": {}, "first_day_plan": {}, "characters": [], "relationships": [], "open_questions": []}
    with _isolated_client() as (client, _root), _developer(proposal) as calls:
        key = _new_story(client)
        first = client.post(f"/api/stories/{key}/architect/turn")
        assert first.status_code == 200, first.text
        edited = client.put(f"/api/stories/{key}", json={
            "premise": "A returner reaches an island on a quiet ferry.",
            "locations": [{"id": "ferry", "name": "Electric ferry", "description": "Bright water."}],
            "start": "ferry",
        })
        assert edited.status_code == 200, edited.text
        refreshed = client.post(f"/api/stories/{key}/architect/turn")

    assert refreshed.status_code == 200, refreshed.text
    payload = refreshed.json()
    assert payload["action"] == "assess"
    assert payload["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    assert "changed outside this conversation" in payload["reply"]
    assert payload["question"]["id"] != "starting-spark"
    assert calls == []


def test_architect_bootstrap_replaces_a_stale_compiler_question_without_a_model_call():
    """Compiler fixes must not strand a reload on an obsolete saved question."""
    prior_history = [{"role": "architect", "text": "The opening is taking shape."}]
    with _isolated_client() as (client, root):
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "locations": [{"id": "ferry", "name": "Ferry", "description": "Quiet water."}],
            "start": "ferry",
            "time_system": {"slots": ["morning", "night"]},
            "fields": {
                "first_day_plan": {
                    "opening_time": "morning", "opening_location": "ferry",
                    "opening_present": ["protagonist"],
                    "events": [{"id": "crossing", "when": "morning", "location": "ferry",
                                "participants": ["protagonist"], "visible": "The ferry approaches."}],
                },
                "architect_state": {"pending_question": {
                    "id": "opening_participant_unknown", "section": "first_day", "mode": "develop",
                    "text": "The opening names people who are not cast keys: protagonist.",
                    "target": {"section": "first_day"}, "scopes": ["first_day"],
                }, "history": prior_history},
            },
        })
        assert seeded.status_code == 200, seeded.text
        response = client.post(f"/api/stories/{key}/architect/turn", json={})
        repeated = client.post(f"/api/stories/{key}/architect/turn", json={})
        stored, _characters = story_store.load_story(root, key) or ({}, {})

    assert response.status_code == 200, response.text
    assert repeated.status_code == 200, repeated.text
    payload = response.json()
    assert payload["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    assert payload["question"]["id"] != "opening_participant_unknown"
    assert payload["action"] == "assess"
    # Reloading the rebased plan question is a read; it must not append the
    # old compiler wording or duplicate the Architect's history.
    assert repeated.json()["question"] == payload["question"]
    assert stored["fields"]["architect_state"]["history"] == prior_history


def _card_with_supporting_cast_but_no_protagonist_arc() -> dict:
    """A minimal card shaped exactly like the one that already reaches
    ``authoring-plan-character-arc`` in `test_architect_routes_private_arc_answer_to_protected_interview_once`
    below — reused here as a direct, HTTP-free check on the question text itself."""
    return {
        "premise": "A returner reaches the island by ferry.",
        "world": {"setting": "A bright island."},
        "locations": [{"id": "dock", "name": "Dock", "description": "Wet boards."}],
        "start": "dock",
        "time_system": {"slots": ["morning", "evening"]},
        "cast": [{"character": "shuri", "primary": True, "home": "dock"}],
        "fields": {"first_day_plan": {
            "objective": "Reach the dock.", "opening_time": "morning", "opening_location": "dock",
            "opening_present": ["shuri"],
            "events": [{"id": "arrival", "when": "morning", "location": "dock",
                        "participants": ["shuri"], "visible": "Shuri waits at the dock."}],
        }},
    }


def test_architect_asks_about_the_protagonist_first_when_no_protagonist_arc_exists():
    """The protagonist's own ordinary backstory is the story's root — ask about them
    before any supporting character. Fixes the historical `_first_cast_name` exclusion
    that always steered this question toward a supporting character instead."""
    card = _card_with_supporting_cast_but_no_protagonist_arc()
    report = analyze_story_card(card)
    names = {"player": "Mara", "shuri": "Shuri"}

    question = choose_question(card, report, names=names)
    assert question["id"] == "authoring-plan-character-arc"
    assert question["section"] == "arcs"
    assert "Mara" in question["text"]
    assert "Shuri" not in question["text"]
    assert "ordinary" in question["text"].lower()

    # Once the protagonist has their own arc, the Architect falls back to
    # today's behavior: asking about a supporting character's arc.
    with_protagonist_arc = dict(card)
    with_protagonist_arc["fields"] = {
        **card["fields"],
        "arc_design": {"version": 1, "themes": [], "arcs": [
            {"id": "mara-arc", "owner": "player", "title": "Mara's arc"},
        ]},
    }
    report2 = analyze_story_card(with_protagonist_arc)
    question2 = choose_question(with_protagonist_arc, report2, names=names)
    assert question2["id"] == "authoring-plan-character-arc"
    assert "Shuri" in question2["text"]


def test_arc_summary_leads_with_the_protagonists_backstory_when_present():
    card = _card_with_supporting_cast_but_no_protagonist_arc()
    card["fields"] = {
        **card["fields"],
        "arc_design": {"version": 1, "themes": [], "arcs": [{
            "id": "mara-arc", "owner": "player", "title": "Mara's arc",
            "starting_belief": "People leave, eventually.",
        }]},
    }
    report = analyze_story_card(card)
    presentation = author_plan_presentation(card, report, names={"player": "Mara"})
    arc_theme = next(section for section in presentation["sections"] if section["id"] == "arc_theme")
    assert "Mara" in arc_theme["summary"]
    assert "People leave, eventually." in arc_theme["summary"]


def test_no_gap_card_still_receives_a_creative_architect_question():
    question = choose_question({"premise": "A complete little story."}, {"gaps": []}, names={"shuri": "Shuri"})

    assert question["id"] == "creative-next-step"
    assert question["section"] == "arcs"
    assert question["mode"] == "protected_interview"
    assert "open a section" not in question["text"].lower()


def _worker_gap(ident: str, *, path: str, detail: str) -> dict:
    """Build a compiler/worker diagnostic with deliberately tempting prose.

    These records are not author decisions.  The regression tests below make
    sure the Architect never leaks their raw compiler wording into its chat
    just because an older UI or a background implementation pass hands it one.
    """
    return {
        "id": ident,
        "section": "first_day",
        "title": "Resolve a play-readiness constraint",
        "detail": detail,
        "suggested_scope": "Repair the generated reference.",
        "paths": [path],
        "related": [],
    }


def _worker_noise_report(*, include_investigation_hinge: bool = False) -> dict:
    gaps = [
        _worker_gap(
            "readiness-opening-participant-unknown-fields-first-day-plan-opening-present",
            path="fields.first_day_plan.opening_present",
            detail="The opening names people who are not cast keys: protagonist.",
        ),
        _worker_gap(
            "readiness-scene-location-undeclared-fields-first-day-plan-events-2-location",
            path="fields.first_day_plan.events[2].location",
            detail="Scene 'entity-ambush' uses prose-derived location 'island'.",
        ),
        _worker_gap(
            "readiness-opening-cast-unspecified-fields-first-day-plan-opening-present",
            path="fields.first_day_plan.opening_present",
            detail="The opening is playable with the player alone, but no cast member is explicitly present.",
        ),
    ]
    if include_investigation_hinge:
        gaps.extend([
            {
                "id": "knowledge-gate-entity-ambush",
                "section": "first_day",
                "title": "Assign who can know the hidden part of this scene",
                "detail": "A protected scene needs an investigation boundary.",
                "suggested_scope": "Plan the investigation.",
                "paths": ["fields.first_day_plan.events[2].knowledge"],
                "related": ["entity-ambush"],
            },
            {
                "id": "evidence-link-entity-ambush",
                "section": "first_day",
                "title": "Leave a discoverable trace for the hidden scene layer",
                "detail": "A protected scene has no authored evidence.",
                "suggested_scope": "Plan the investigation.",
                "paths": ["fields.first_day_plan.events[2].evidence"],
                "related": ["entity-ambush"],
            },
        ])
    return {"gaps": gaps}


def _worker_noise_card() -> dict:
    return {
        "premise": "A returner arrives at an island after four years away.",
        "cast": [{"character": "shuri"}],
        "fields": {"first_day_plan": {"events": [{"id": "crossing"}]}},
    }


def test_worker_diagnostics_collapse_to_one_public_authoring_plan_question():
    """Raw compiler work must never masquerade as a series of author questions."""
    card = _worker_noise_card()
    report = _worker_noise_report()

    question = choose_question(card, report)

    # The meaningful author choice is whether Shuri belongs in the opening,
    # not any of the compiler's stale alias/location bookkeeping.
    assert question["id"] == "authoring-plan-opening"
    assert question["mode"] == "protected_interview"
    raw_worker_terms = (
        "protagonist", "prose-derived", "smallest missing operational",
        "opening names people", "entity-ambush",
    )
    assert not any(term in question["text"].lower() for term in raw_worker_terms)


def test_worker_only_diagnostics_use_one_opening_plan_not_their_raw_gap():
    """Opening placement is one plan decision, not three compiler prompts."""
    question = choose_question(
        {"premise": "A returner arrives by ferry."},
        _worker_noise_report(),
    )

    assert question["id"] == "authoring-plan-opening"
    assert question["mode"] == "protected_interview"
    assert "prose-derived" not in question["text"].lower()
    assert "protagonist" not in question["text"].lower()


def test_legacy_worker_question_ids_rebase_to_the_current_public_plan_without_mutation():
    """Reloads and old clients cannot reopen a raw worker diagnostic."""
    card = _worker_noise_card()
    report = _worker_noise_report()
    legacy_id = report["gaps"][0]["id"]
    state = {
        "pending_question": {
            "id": legacy_id,
            "section": "first_day",
            "mode": "develop",
            "text": "The opening names people who are not cast keys: protagonist.",
            "target": {"section": "first_day"},
            "scopes": ["first_day"],
        },
        "history": [{"role": "architect", "text": "old worker question"}],
    }
    before = {**state, "pending_question": dict(state["pending_question"]), "history": list(state["history"])}

    from_answer = question_for_answer(card, report, state, legacy_id)
    from_reload = current_pending_question(card, report, state)

    assert from_answer is not None
    assert from_reload is not None
    assert from_answer["id"] == from_reload["id"] == "authoring-plan-opening"
    assert state == before  # resolving/rebasing is pure; the route owns history writes.


def test_legacy_worker_hinge_cannot_skip_the_current_public_baseline_plan():
    """A stale evidence/knowledge id never jumps ahead of the one live plan."""
    card = {
        "premise": "A returner arrives by ferry.",
        "fields": {"first_day_plan": {"events": [{"id": "crossing"}]}},
    }
    report = {
        "gaps": [
            {
                "id": "readiness-world-structure",
                "section": "world",
                "paths": ["world.name"],
                "related": [],
            },
            {
                "id": "knowledge-gate-crossing",
                "section": "first_day",
                "paths": ["fields.first_day_plan.events[0].knowledge"],
                "related": ["crossing"],
            },
        ],
    }

    current = choose_question(card, report)
    from_legacy_answer = question_for_answer(card, report, {}, "knowledge-gate-crossing")

    assert current["id"] == "authoring-plan-public-baseline"
    assert from_legacy_answer is not None
    assert from_legacy_answer["id"] == current["id"]


def test_investigation_is_one_architect_level_decision_not_a_worker_evidence_prompt():
    """Evidence/knowledge still require judgment, but at the plan level."""
    question = choose_question(
        _worker_noise_card(),
        _worker_noise_report(include_investigation_hinge=True),
    )

    assert question["id"] == "authoring-plan-investigation"
    assert question["mode"] == "protected_interview"
    text = question["text"].lower()
    assert "investigation" in text
    assert "what concrete public trace" not in text
    assert "entity-ambush" not in text


def test_day_one_scene_placement_is_a_protected_interview_not_a_generic_develop_pass():
    card = {
        "premise": "A returner arrives by ferry.",
        "fields": {"first_day_plan": {"events": [{"id": "arrival", "when": "morning"}]}},
    }
    report = {"gaps": [{
        "id": "readiness-scene-location-inherited-fields-first-day-plan-events-0-location",
        "section": "first_day",
        "detail": "The arrival scene inherits the opening location.",
        "paths": ["fields.first_day_plan.events[0].location"],
    }]}

    question = choose_question(card, report)
    assert question["mode"] == "protected_interview"
    assert question["scopes"] == []


def test_legacy_scene_offer_quality_is_a_protected_scene_flow_conversation():
    private = "PRIVATE_SCENE_OFFER_SECRET"
    card = {
        "premise": "A returner arrives on an island by ferry.",
        "fields": {"first_day_plan": {"events": [{
            "id": "subtle-hints",
            "visible": "Subtle hints surface through conversation or environment.",
            "hidden": private,
        }]}},
    }
    report = {"gaps": [{
        "id": "scene-offer-subtle-hints",
        "section": "first_day",
        "paths": ["fields.first_day_plan.events[0]"],
        "detail": private,
    }]}

    question = choose_question(card, report)

    assert question["id"] == "authoring-plan-scene-offer"
    assert question["section"] == "first_day"
    assert question["mode"] == "protected_interview"
    assert question["target"] == {
        "section": "first_day",
        "plan_kind": "scene_flow",
        "item": {"kind": "scene", "id": "subtle-hints"},
    }
    assert question["scopes"] == []
    assert private not in question["text"]
    assert "subtle-hints" not in question["text"]


def test_architect_runs_exactly_one_safe_development_pass_and_keeps_private_text_out_of_prompt():
    private = "PRIVATE_ARCHITECT_DIRECTOR_MECHANIC"
    proposal = {
        "message": "Shuri is now a concrete part of the ferry story.",
        "world": {}, "premise": "", "locations": [], "start": "", "time_system": {},
        "first_day_plan": {}, "relationships": [], "open_questions": [],
        "characters": [{
            "name": "Shuri", "role": "childhood friend", "appearance": "simple coat",
            "persona": "quiet and careful", "personality": "private", "background": "the island",
            "connection": "waits at the dock", "want": "", "wound": "", "lie": "", "secret": "",
            "primary": True, "home": "ferry",
        }],
    }
    with _isolated_client() as (client, _root), _developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": "A returner approaches an island on a silent electric ferry.",
            "locations": [{"id": "ferry", "name": "Electric ferry", "description": "Bright water."}],
            "start": "ferry",
            "time_system": {"slots": ["morning", "night"], "entity_periods": [{
                "id": "hunt", "slots": ["night"], "state": "hunting",
                "capabilities": [private], "constraint": private,
            }]},
            "fields": {"author_notes": [private], "first_day_plan": {"events": [{
                "id": "crossing", "when": "morning", "location": "ferry",
                "visible": "The ferry slides toward the island.", "hidden": private,
                "trigger": private, "evidence": private, "knowledge": {"entity": private},
                "entity_action": True, "entity_period": "hunt",
            }]}},
        })
        assert seeded.status_code == 200, seeded.text
        response = client.post(f"/api/stories/{key}/architect/turn", json={
            "answer_to": "cast-missing",
            "message": "Make the person waiting at the dock emotionally important, but keep the mystery quiet.",
        })
        public = client.get(f"/api/stories/{key}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "develop"
    assert payload["bounded"] == {"model_calls": 1, "maximum_model_calls": 1}
    assert payload["model_route"] is not None
    assert payload["architect"]["work_order"] == {
        "version": 1,
        "planner": "architect",
        "scout": "explore",
        "worker": "task",
        "specialist": "cast",
        "scopes": ["cast"],
        "mode": "develop",
        "protected": False,
        "public_only": True,
        "reviewer": "reviewer",
        "reviewer_gates": ["scope_boundary", "public_visibility_boundary", "aggregate_card_validation", "reference_integrity"],
        "maximum_model_calls": 1,
    }
    assert len(calls) == 1
    assert "AUTHORIZED STORY WORK ORDER" in calls[0]["system"]
    prompt = calls[0]["prompt"]
    assert private not in prompt
    assert '"hidden"' not in prompt
    assert '"trigger"' not in prompt
    assert '"evidence"' not in prompt
    assert '"knowledge"' not in prompt
    assert '"entity_periods"' not in prompt
    assert '"author_notes"' not in prompt
    assert payload["card"]["cast"][0]["character"] == "shuri"
    assert "architect_state" not in public.json().get("fields", {})
    assert payload["architect"]["mission"].startswith("Make the person")
    assert payload["architect"]["history"][-1]["role"] == "architect"


def test_architect_keeps_an_entirely_hidden_author_note_out_of_every_model_call():
    secret = "The copy remembers every loop, but no public model may read that."
    proposal = {"message": "must not run", "world": {}, "premise": "", "locations": [], "start": "",
                "time_system": {}, "first_day_plan": {}, "characters": [], "relationships": [], "open_questions": []}
    with _isolated_client() as (client, _root), _developer(proposal) as calls:
        key = _new_story(client)
        bootstrap = client.post(f"/api/stories/{key}/architect/turn")
        assert bootstrap.status_code == 200, bootstrap.text
        response = client.post(f"/api/stories/{key}/architect/turn", json={
            "message": f"[[hidden]]{secret}[[/hidden]]",
        })
        public = client.get(f"/api/stories/{key}")

    assert response.status_code == 200, response.text
    assert response.json()["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    assert response.json()["action"] == "assess"
    assert calls == []
    assert public.json()["fields"]["author_notes"] == [f"[[hidden]]{secret}[[/hidden]]"]


def test_complete_mode_requests_a_public_plan_before_running_its_safe_batch():
    proposal = {
        "message": "Added the opening public scaffold.", "world": {}, "premise": "", "locations": [],
        "start": "", "time_system": {}, "relationships": [], "open_questions": [],
        "characters": [{
            "name": "Shuri", "role": "childhood friend", "appearance": "simple coat",
            "persona": "careful and private", "personality": "private", "background": "the island",
            "connection": "waits at the dock", "want": "", "wound": "", "lie": "", "secret": "",
            "primary": True, "home": "ferry",
        }],
        "first_day_plan": {
            "objective": "Reach the island.", "opening_time": "morning", "opening_location": "ferry",
            "opening_present": ["Shuri"], "events": [{
                "id": "ferry-arrival", "when": "morning", "location": "ferry", "participants": ["Shuri"],
                "visible": "The ferry nears the island while the dock stays oddly empty.",
                "hook": "The player can look for Shuri on shore or ask the crew why no one is waiting at the pier.",
                "hidden": "", "entity_action": False,
                "entity_period": "", "trigger": "", "evidence": "",
                "knowledge": {"protagonist": "", "entity": "", "public": ""},
            }],
        },
    }
    with _isolated_client() as (client, _root), _developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": "A returner reaches an island by ferry.",
            "world": {"setting": "A bright island."},
            "locations": [{"id": "ferry", "name": "Electric ferry", "description": "Bright water."}],
            "start": "ferry",
        })
        assert seeded.status_code == 200, seeded.text
        planned = client.post(f"/api/stories/{key}/architect/turn", json={
            "mode": "complete", "new_mission": True,
        })
        assert planned.status_code == 200, planned.text
        planned_payload = planned.json()
        assert planned_payload["action"] == "assess"
        assert planned_payload["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
        assert planned_payload["question"]["id"] == "authoring-plan-public-baseline"
        assert calls == []

        # The author's high-level opening direction is the handoff.  Only
        # after it is supplied may the bounded public worker run.
        response = client.post(f"/api/stories/{key}/architect/turn", json={
            "answer_to": planned_payload["question"]["id"],
            "message": "Keep the ferry opening peaceful enough that the return home feels briefly possible.",
        })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "develop"
    assert payload["bounded"] == {"model_calls": 1, "maximum_model_calls": 1}
    assert len(calls) == 1
    assert "AUTONOMOUS COMPLETION PASS:" in calls[0]["prompt"]
    assert {"cast", "first_day"}.issubset(set(payload["architect"]["actions"][-1]["sections"]))
    assert payload["plan"]["phase"] == "author_decision", payload["plan"]
    assert payload["plan"]["authorial_hinges"]
    assert payload["question"]["mode"] == "protected_interview"


def test_approved_public_plan_marks_a_noop_safe_batch_blocked_instead_of_retrying_it():
    proposal = {
        "message": "No additions.", "world": {}, "premise": "", "locations": [], "start": "",
        "time_system": {}, "first_day_plan": {}, "characters": [], "relationships": [], "open_questions": [],
    }
    with _isolated_client() as (client, _root), _developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": "A returner reaches an island by ferry.",
            "world": {"setting": "A bright island."},
            "locations": [{"id": "ferry", "name": "Electric ferry", "description": "Bright water."}],
            "start": "ferry",
            "fields": {"first_day_plan": {
                "objective": "Reach the island.", "opening_time": "morning", "opening_location": "ferry",
                "events": [{
                    "id": "ferry-arrival", "when": "morning", "location": "ferry", "participants": [],
                    "visible": "The ferry nears the island.",
                }],
            }},
        })
        assert seeded.status_code == 200, seeded.text
        planned = client.post(f"/api/stories/{key}/architect/turn", json={"mode": "complete"})
        assert planned.status_code == 200, planned.text
        assert planned.json()["question"]["id"] == "authoring-plan-public-baseline"
        assert calls == []
        first = client.post(f"/api/stories/{key}/architect/turn", json={
            "answer_to": planned.json()["question"]["id"],
            "message": "Keep the public opening quiet and grounded.",
        })
        second = client.post(f"/api/stories/{key}/architect/turn", json={"mode": "complete"})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_payload = first.json()
    assert first_payload["action"] == "develop"
    assert first_payload["bounded"] == {"model_calls": 1, "maximum_model_calls": 1}
    assert first_payload["plan"]["summary"]["blocked_task_count"] >= 1
    assert first_payload["question"]["id"] != "cast-missing", first_payload["question"]
    # The follow-up completion call sees the blocked public batch, so it does
    # not pay for an identical worker retry and asks for the actual decision.
    second_payload = second.json()
    assert second_payload["action"] == "assess"
    assert second_payload["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    assert len(calls) == 1


def test_repeated_complete_at_the_same_hinge_is_idempotent_and_does_not_duplicate_history():
    """The Plan button must not recreate the same visible question on every click."""
    proposal = {
        "message": "No additions.", "world": {}, "premise": "", "locations": [], "start": "",
        "time_system": {}, "first_day_plan": {}, "characters": [], "relationships": [], "open_questions": [],
    }
    with _isolated_client() as (client, root), _developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": "A returner reaches an island by ferry.",
            "world": {"setting": "A bright island."},
            "locations": [{"id": "ferry", "name": "Electric ferry", "description": "Bright water."}],
            "start": "ferry",
            "fields": {"first_day_plan": {
                "opening_location": "ferry", "opening_time": "morning",
                "events": [{"id": "arrival", "when": "morning", "location": "ferry", "visible": "The ferry nears the island."}],
            }},
        })
        assert seeded.status_code == 200, seeded.text
        # Complete is a plan review, not a worker shortcut.  Repeated clicks
        # must leave the one visible author decision and its history intact.
        first = client.post(f"/api/stories/{key}/architect/turn", json={
            "mode": "complete", "new_mission": True,
            "message": "Complete the story from established canon.",
        })
        assert first.status_code == 200, first.text
        second = client.post(f"/api/stories/{key}/architect/turn", json={
            "mode": "complete", "new_mission": True,
            "message": "Complete the story from established canon.",
        })
        assert second.status_code == 200, second.text
        raw_after_second, _characters = story_store.load_story(root, key)
        second_state = raw_after_second["fields"]["architect_state"]
        third = client.post(f"/api/stories/{key}/architect/turn", json={
            "mode": "complete", "new_mission": True,
            "message": "Complete the story from established canon.",
        })
        raw_after_third, _characters = story_store.load_story(root, key)
        third_state = raw_after_third["fields"]["architect_state"]

    assert first.json()["bounded"]["model_calls"] == 0
    assert second.json()["bounded"]["model_calls"] == 0
    assert third.status_code == 200, third.text
    assert third.json()["bounded"]["model_calls"] == 0
    assert third.json()["reply"] == "The plan is already paused at the next authorial decision."
    assert third.json()["question"] == second.json()["question"]
    assert third_state["history"] == second_state["history"]
    assert third_state["actions"] == second_state["actions"]
    assert len(calls) == 0


def test_complete_mode_persists_mechanical_loop_and_public_opening_repairs_before_plan_review():
    proposal = {
        "message": "No additions.", "world": {}, "premise": "", "locations": [], "start": "",
        "time_system": {}, "first_day_plan": {}, "characters": [], "relationships": [], "open_questions": [],
    }
    with _isolated_client() as (client, root), _developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": "A returner reaches an island by ferry.",
            "locations": [{"id": "electric-ferry", "name": "Electric Ferry", "description": "Bright water."}],
            "world": {
                "entity": {"description": "An old god."},
                "loop": {
                    "start": "Aboard the electric ferry as the island comes into view.",
                    "reset": "The world, ferry, and island revert after death.",
                    "policy": {
                        "restart": "The world returns to how it was before death.",
                        "preserve": {"runtime": ["entity"], "memories": ["player"],
                                     "clear_memories_for_others": True},
                    },
                },
            },
            "fields": {
                "architect_state": {
                    "pending_question": {"id": "readiness-loop-restart-missing-world-loop-policy-restart",
                                         "section": "world", "mode": "protected_interview", "text": "Where does it restart?"},
                    "history": [{"role": "author", "text": "It starts at the first scene of the day."}],
                },
                "first_day_plan": {"events": [{
                    "event": "Ferry approach and disembarkation.", "when": "morning",
                }]},
            },
        })
        assert seeded.status_code == 200, seeded.text
        response = client.post(f"/api/stories/{key}/architect/turn", json={"mode": "complete"})
        raw, _characters = story_store.load_story(root, key)

    assert response.status_code == 200, response.text
    assert response.json()["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    assert len(calls) == 0
    assert raw["start"] == "electric-ferry"
    policy = raw["world"]["loop"]["policy"]
    assert policy["restart"] == "opening"
    assert policy["preserve"]["runtime"] == []
    event = raw["fields"]["first_day_plan"]["events"][0]
    assert event["id"] == "ferry-approach-and-disembarkation"
    assert event["location"] == "electric-ferry"
    persisted_plan = raw["fields"]["architect_state"]["plan"]
    assert persisted_plan["phase"] in {"safe_baseline", "author_decision", "ready"}
    assert "authorial_hinges" in persisted_plan


def test_scene_move_reconciles_only_arc_time_mirrors_without_a_model_call():
    proposal = {
        "message": "Shuri is present.", "world": {}, "premise": "", "locations": [], "start": "",
        "time_system": {}, "first_day_plan": {}, "relationships": [], "open_questions": [],
        "characters": [{
            "name": "Shuri", "role": "friend", "appearance": "simple coat", "persona": "careful",
            "personality": "private", "background": "the island", "connection": "waits at the dock",
            "want": "", "wound": "", "lie": "", "secret": "", "primary": True, "home": "dock",
        }],
    }
    design = {
        "version": 1,
        "themes": [{"id": "impermanence", "label": "Impermanence", "question": "What does delay cost?"}],
        "arcs": [{
            "id": "shuri-arc", "title": "The missed crossing", "theme_id": "impermanence", "owner": "shuri",
            "dramatic_question": "", "starting_belief": "", "truth": "", "stakes": "",
            "turning_points": [{"id": "dock-pressure", "kind": "pressure", "scene_id": "dock-warning",
                                "when": "morning", "public_surface": "The bell interrupts her."}],
            "character_threads": [{
                "character": "shuri", "want": "", "protective_strategy": "", "blind_spot": "",
                "unacknowledged_need": "", "limitation": "", "visible_tell": "", "recognition": "",
                "possible_outcomes": [], "pressure_points": [{"id": "dock-thread", "scene_id": "dock-warning",
                    "when": "morning", "public_pressure": "The player asks why she left."}],
            }],
        }],
    }
    with _isolated_client() as (client, _root), _developer(proposal) as calls:
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "premise": "The ferry reaches the island.",
            "locations": [{"id": "dock", "name": "Dock", "description": "Wet boards."}],
            "start": "dock", "time_system": {"slots": ["morning", "evening"]},
        })
        assert seeded.status_code == 200, seeded.text
        made = client.post(f"/api/stories/{key}/card/develop", json={"scope": "cast", "brief": "Add Shuri."})
        assert made.status_code == 200, made.text
        configured = client.put(f"/api/stories/{key}", json={"fields": {"first_day_plan": {
            "objective": "Reach the dock.", "opening_time": "morning", "opening_location": "dock",
            "events": [{"id": "dock-warning", "when": "evening", "location": "dock", "participants": ["shuri"],
                        "visible": "Shuri hesitates at the dock."}],
        }, "arc_design": design}})
        assert configured.status_code == 200, configured.text
        calls.clear()
        response = client.post(f"/api/stories/{key}/architect/turn", json={
            "event": {"type": "scene_moved", "scene_id": "dock-warning", "before": {"slot": "morning"}},
        })
        director = client.get(f"/api/stories/{key}/director-preview")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "reconcile"
    assert payload["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    assert "synced 2 derived arc timing references" in payload["reply"]
    assert calls == []
    assert director.status_code == 200, director.text
    arc = director.json()["arc_design"]["arcs"][0]
    assert arc["turning_points"][0]["when"] == "evening"
    assert arc["character_threads"][0]["pressure_points"][0]["when"] == "evening"


def test_scene_move_with_private_mechanics_preserves_them_without_leaking_a_worker_question():
    private = "PRIVATE_ENTITY_ROUTE"
    with _isolated_client() as (client, _root):
        key = _new_story(client)
        seeded = client.put(f"/api/stories/{key}", json={
            "locations": [{"id": "dock", "name": "Dock", "description": "Wet boards."}],
            "start": "dock", "time_system": {"slots": ["morning", "evening"], "entity_periods": [{
                "id": "hunt", "slots": ["evening"], "state": "hunting", "capabilities": [private], "constraint": private,
            }]},
            "fields": {"first_day_plan": {"events": [{
                "id": "dock-warning", "when": "evening", "location": "dock", "visible": "A bell rings.",
                "hidden": private, "trigger": private, "evidence": private,
                "knowledge": {"entity": private}, "entity_action": True, "entity_period": "hunt",
            }]}},
        })
        assert seeded.status_code == 200, seeded.text
        response = client.post(f"/api/stories/{key}/architect/turn", json={
            "event": {"type": "scene_moved", "scene_id": "dock-warning", "before": {"slot": "morning"}},
        })
        latest = client.get(f"/api/stories/{key}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "reconcile"
    assert payload["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    assert payload["question"]["mode"] == "protected_interview"
    # Dragging a private scene is an implementation/reconciliation event, not
    # a fresh mini-interview about raw knowledge/evidence/entity fields.  The
    # next question must be the Architect's current high-level plan decision.
    assert payload["question"]["id"].startswith("authoring-plan-")
    question_text = payload["question"]["text"].lower()
    assert "you moved a scene" not in question_text
    assert "knowledge boundary" not in question_text
    assert "entity availability" not in question_text
    event = latest.json()["fields"]["first_day_plan"]["events"][0]
    assert event["hidden"] == private
    assert event["evidence"] == private
    assert event["knowledge"] == {"entity": private}


def test_architect_routes_private_arc_answer_to_protected_interview_once():
    async def turn(**kwargs):
        assert "ACTIVE AUTHORING TARGET: arcs" in kwargs["prompt"]
        return InterviewResult(reply="The pressure has a shape now.", patch={
            "themes": ["Impermanence"],
            "fields": {"arc_design": {
                "version": 1,
                "themes": [{"id": "impermanence", "label": "Impermanence", "question": "What does delay cost?"}],
                "arcs": [{
                    "id": "shuri-arc", "title": "The missed crossing", "theme_id": "impermanence",
                    "owner": "shuri", "dramatic_question": "Can she ask before it is too late?",
                    "starting_belief": "Being useful is safer than asking.", "truth": "author-only",
                    "stakes": "", "turning_points": [],
                    "character_threads": [{
                        "character": "shuri", "want": "Keep the returner safe.",
                        "protective_strategy": "Turns tenderness into practical tasks.",
                        "blind_spot": "Need is a burden.", "unacknowledged_need": "author-only",
                        "limitation": "Leaves before she can ask.", "visible_tell": "Straightens a timetable.",
                        "pressure_points": [], "recognition": "author-only", "possible_outcomes": [],
                    }],
                }],
            }},
        })

    with _isolated_client() as (client, root), _interviewer(turn) as calls:
        key = _new_story(client)
        loaded = story_store.load_story(root, key)
        assert loaded is not None
        raw, _characters = loaded
        raw.update({
            "premise": "A returner reaches the island by ferry.",
            "world": {"setting": "A bright island."},
            "locations": [{"id": "dock", "name": "Dock", "description": "Wet boards."}],
            "start": "dock",
            "time_system": {"slots": ["morning", "evening"]},
            "cast": [{"character": "shuri", "primary": True, "home": "dock"}],
            "fields": {"first_day_plan": {
                "objective": "Reach the dock.", "opening_time": "morning", "opening_location": "dock",
                "opening_present": ["shuri"],
                "events": [{"id": "arrival", "when": "morning", "location": "dock",
                            "participants": ["shuri"], "visible": "Shuri waits at the dock."}],
            }},
        })
        story_store.save_story(root, key, raw, {
            "shuri": Character(name="Shuri", fields={"role": "childhood friend"}).model_dump(),
        })
        planned = client.post(f"/api/stories/{key}/architect/turn")
        assert planned.status_code == 200, planned.text
        assert planned.json()["question"]["id"] == "authoring-plan-character-arc"
        assert calls == []
        response = client.post(f"/api/stories/{key}/architect/turn", json={
            "answer_to": planned.json()["question"]["id"],
            "message": "Shuri is afraid to ask for anything because she expects people to leave.",
        })
        public = client.get(f"/api/stories/{key}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "interview"
    assert payload["bounded"] == {"model_calls": 1, "maximum_model_calls": 1}
    assert len(calls) == 1
    assert payload["model_route"] == {"available": True, "selected_model": "test"}
    assert payload["architect"]["work_order"]["worker"] == "oracle"
    assert payload["architect"]["work_order"]["specialist"] == "arcs"
    assert payload["architect"]["work_order"]["protected"] is True
    assert "arc_design" not in public.json().get("fields", {})
    assert payload["question"]  # The Architect always leaves one next decision.


def test_architect_direct_edit_is_one_model_pass_and_preserves_hidden_author_notes():
    """An Architect hand-off cannot trigger interview recovery or lose private text."""
    secret = "DIRECT_EDIT_PRIVATE_NOTE"
    original_operation = card_graph.run_card_operation

    async def recovery_must_not_run(**_kwargs):
        raise AssertionError("an Architect hand-off must use exactly one model pass")

    async def turn(**_kwargs):
        # No structured patch deliberately exercises the old recovery path.
        return InterviewResult(reply="I have the direction.", patch={})

    card_graph.run_card_operation = recovery_must_not_run
    try:
        with _isolated_client() as (client, root), _interviewer(turn) as calls:
            key = _new_story(client)
            response = client.post(f"/api/stories/{key}/architect/turn", json={
                "direct_edit": True,
                "target": {"section": "world"},
                "message": f"Make the harbor feel watchful. [[hidden]]{secret}[[/hidden]]",
            })
            stored, _characters = story_store.load_story(root, key) or ({}, {})
    finally:
        card_graph.run_card_operation = original_operation

    assert response.status_code == 200, response.text
    assert response.json()["bounded"] == {"model_calls": 1, "maximum_model_calls": 1}
    assert len(calls) == 1
    assert stored["fields"]["author_notes"] == [f"[[hidden]]{secret}[[/hidden]]"]
    assert secret not in response.json()["model_input"]["prompt"]


def test_architect_rejects_a_stale_interview_result_without_overwriting_a_concurrent_edit():
    """The bounded worker must not commit its old snapshot over newer canon."""
    started = threading.Event()
    release = threading.Event()
    result: dict[str, object] = {}

    async def turn(**_kwargs):
        started.set()
        await asyncio.to_thread(release.wait)
        return InterviewResult(reply="The harbor is established.", patch={
            "world": {"setting": "The old, stale harbor."},
        })

    with _isolated_client() as (client, root), _interviewer(turn):
        key = _new_story(client)

        def run_architect() -> None:
            result["response"] = client.post(f"/api/stories/{key}/architect/turn", json={
                "direct_edit": True,
                "target": {"section": "world"},
                "message": "Set the harbor's atmosphere.",
            })

        worker = threading.Thread(target=run_architect)
        worker.start()
        assert started.wait(timeout=5), "the interview worker did not start"
        concurrent_client = TestClient(create_app(root))
        try:
            concurrent = concurrent_client.put(f"/api/stories/{key}", json={
                "premise": "CONCURRENT_AUTHOR_EDIT",
            })
        finally:
            concurrent_client.close()
        assert concurrent.status_code == 200, concurrent.text
        release.set()
        worker.join(timeout=10)
        assert not worker.is_alive(), "the bounded interview did not finish"
        stored, _characters = story_store.load_story(root, key) or ({}, {})

    response = result["response"]
    assert getattr(response, "status_code", None) == 409, getattr(response, "text", "")
    assert stored["premise"] == "CONCURRENT_AUTHOR_EDIT"
    assert not stored.get("world")


def test_protected_complete_requires_an_architect_plan_before_assigning_existing_scene_knowledge():
    secret = "PRIVATE_MIMIC_ACTION_THE_NARRATOR_MUST_NOT_SEE"

    async def turn(**kwargs):
        if "DIRECTOR-ONLY KNOWLEDGE ASSIGNMENT" not in kwargs["prompt"]:
            # The author-facing investigation plan is committed first.  An
            # empty model patch still makes the interview boundary retain the
            # author's answer as a Day One note.
            assert "ACTIVE AUTHORING TARGET: first_day" in kwargs["prompt"]
            return InterviewResult(reply="The investigation plan is established.", patch={})
        # This is the later protected Director call, not the public card
        # developer. Its proposed rewrite is intentionally hostile so the
        # existing knowledge-only boundary proves it can fill only the live
        # observer's blank knowledge entry.
        return InterviewResult(reply="The boundary is assigned.", patch={
            "fields": {"first_day_plan": {
                "objective": "A changed objective must be ignored.",
                "events": [{
                    "id": "dock-warning", "when": "night", "location": "elsewhere",
                    "participants": ["someone-new"], "visible": "A changed public scene.",
                    "hidden": "A changed secret.", "entity_action": True,
                    "trigger": "A changed trigger.", "evidence": "A changed clue.",
                    "knowledge": {
                        "shuri": "She recognized the old whistle before the lights failed.",
                        "someone-new": "This must not create a witness.",
                    },
                }],
            }},
        })

    with _isolated_client() as (client, root), _interviewer(turn) as calls:
        key = _new_story(client)
        loaded = story_store.load_story(root, key)
        assert loaded is not None
        raw, _characters = loaded
        raw.update({
            "premise": "A returner reaches the island by ferry.",
            "world": {"setting": "A bright island."},
            "locations": [{"id": "island-dock", "name": "Island dock", "description": "Wet boards."}],
            "start": "island-dock",
            "cast": [{"character": "shuri", "primary": True, "home": "island-dock"}],
            "fields": {"first_day_plan": {
                "objective": "Reach the dock.", "opening_time": "morning",
                "opening_location": "island-dock", "opening_present": ["player", "shuri"],
                "events": [{
                    "id": "dock-warning", "when": "morning", "location": "island-dock",
                    "participants": ["player", "shuri"], "visible": "Shuri hears a distant whistle.",
                    "hidden": secret, "entity_action": False,
                }],
            }},
        })
        story_store.save_story(root, key, raw, {
            "shuri": Character(name="Shuri", fields={"role": "childhood friend"}).model_dump(),
        })

        # Complete cannot use a private worker as a shortcut. It first stops
        # at the Architect's one high-level investigation plan, without waking
        # a model.
        planned = client.post(f"/api/stories/{key}/architect/turn", json={"mode": "complete"})
        assert planned.status_code == 200, planned.text
        planned_payload = planned.json()
        assert planned_payload["action"] == "assess"
        assert planned_payload["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
        assert planned_payload["question"]["id"] == "authoring-plan-investigation"
        assert calls == []

        planned_answer = client.post(f"/api/stories/{key}/architect/turn", json={
            "answer_to": planned_payload["question"]["id"],
            "message": "Let the player find a mundane trace at the dock and misread it as Shuri's warning.",
        })
        assert planned_answer.status_code == 200, planned_answer.text
        assert planned_answer.json()["action"] == "interview"
        approved, _characters = story_store.load_story(root, key) or ({}, {})
        authorization = approved["fields"]["architect_state"]["worker_authorization"]
        assert authorization["plan_id"] == "authoring-plan-investigation"
        assert authorization["card_revision"]
        assert planned_answer.json()["architect"]["execution_ready"] is True
        assert len(calls) == 1

        # The opaque readiness signal survives a reload without exposing the
        # private task. A later explicit Complete can now execute exactly the
        # re-derived live Director task and consumes the authorization.
        reloaded = client.post(f"/api/stories/{key}/architect/turn")
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json()["architect"]["execution_ready"] is True
        assert len(calls) == 1
        response = client.post(f"/api/stories/{key}/architect/turn", json={"mode": "complete"})
        latest, _characters = story_store.load_story(root, key) or ({}, {})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "knowledge"
    assert payload["bounded"] == {"model_calls": 1, "maximum_model_calls": 1}
    assert payload["architect"]["execution_ready"] is False
    assert len(calls) == 2
    event = latest["fields"]["first_day_plan"]["events"][0]
    assert latest["fields"]["first_day_plan"]["objective"] == "Reach the dock."
    assert event["when"] == "morning"
    assert event["location"] == "island-dock"
    assert event["participants"] == ["player", "shuri"]
    assert event["visible"] == "Shuri hears a distant whistle."
    assert event["hidden"] == secret
    assert event.get("trigger", "") == ""
    assert event.get("evidence", "") == ""
    assert event["knowledge"] == {"shuri": "She recognized the old whistle before the lights failed."}
    assert "worker_authorization" not in latest["fields"]["architect_state"]


def test_external_card_edit_invalidates_a_pending_protected_worker_authorization():
    """A Director task cannot run after any outside card edit changes its snapshot."""
    async def turn(**_kwargs):
        raise AssertionError("an invalidated authorization must not wake a worker")

    with _isolated_client() as (client, root), _interviewer(turn) as calls:
        key = _new_story(client)
        loaded = story_store.load_story(root, key)
        assert loaded is not None
        raw, _characters = loaded
        raw.update({
            "premise": "A returner reaches the island by ferry.",
            "world": {"setting": "A bright island."},
            "locations": [{"id": "island-dock", "name": "Island dock", "description": "Wet boards."}],
            "start": "island-dock",
            "cast": [{"character": "shuri", "primary": True, "home": "island-dock"}],
            "fields": {"first_day_plan": {
                "objective": "Reach the dock.", "opening_time": "morning",
                "opening_location": "island-dock", "opening_present": ["player", "shuri"],
                "events": [{
                    "id": "dock-warning", "when": "morning", "location": "island-dock",
                    "participants": ["player", "shuri"], "visible": "Shuri hears a distant whistle.",
                    "hidden": "private", "entity_action": False,
                }],
            }},
        })
        approved_revision = card_revision(raw)
        raw["fields"]["architect_state"] = {
            "card_revision": approved_revision,
            "worker_authorization": {
                "plan_id": "authoring-plan-investigation",
                "card_revision": approved_revision,
            },
        }
        story_store.save_story(root, key, raw, {
            "shuri": Character(name="Shuri", fields={"role": "childhood friend"}).model_dump(),
        })

        edited = client.put(f"/api/stories/{key}", json={
            "premise": "The returner reaches the island by ferry as storm clouds gather.",
        })
        assert edited.status_code == 200, edited.text
        response = client.post(f"/api/stories/{key}/architect/turn", json={"mode": "complete"})
        latest, _characters = story_store.load_story(root, key) or ({}, {})

    assert response.status_code == 200, response.text
    assert response.json()["action"] == "assess"
    assert response.json()["bounded"] == {"model_calls": 0, "maximum_model_calls": 1}
    assert calls == []
    assert "worker_authorization" not in latest["fields"]["architect_state"]


if __name__ == "__main__":
    test_architect_asks_about_the_protagonist_first_when_no_protagonist_arc_exists()
    test_arc_summary_leads_with_the_protagonists_backstory_when_present()
    test_architect_bootstrap_returns_a_full_public_author_plan_without_worker_diagnostics()
    test_author_can_comment_on_any_live_plan_section_without_answering_a_question_first()
    test_author_plan_rejects_an_unknown_section_before_waking_a_worker()
    test_every_visible_author_plan_section_has_a_fixed_safe_comment_target()
    test_architect_blank_bootstrap_persists_one_question_without_mutating_canon()
    test_architect_invalidates_a_pending_question_after_an_external_card_edit()
    test_architect_bootstrap_replaces_a_stale_compiler_question_without_a_model_call()
    test_no_gap_card_still_receives_a_creative_architect_question()
    test_worker_diagnostics_collapse_to_one_public_authoring_plan_question()
    test_worker_only_diagnostics_use_one_opening_plan_not_their_raw_gap()
    test_legacy_worker_question_ids_rebase_to_the_current_public_plan_without_mutation()
    test_legacy_worker_hinge_cannot_skip_the_current_public_baseline_plan()
    test_investigation_is_one_architect_level_decision_not_a_worker_evidence_prompt()
    test_day_one_scene_placement_is_a_protected_interview_not_a_generic_develop_pass()
    test_legacy_scene_offer_quality_is_a_protected_scene_flow_conversation()
    test_architect_runs_exactly_one_safe_development_pass_and_keeps_private_text_out_of_prompt()
    test_architect_keeps_an_entirely_hidden_author_note_out_of_every_model_call()
    test_complete_mode_requests_a_public_plan_before_running_its_safe_batch()
    test_approved_public_plan_marks_a_noop_safe_batch_blocked_instead_of_retrying_it()
    test_repeated_complete_at_the_same_hinge_is_idempotent_and_does_not_duplicate_history()
    test_complete_mode_persists_mechanical_loop_and_public_opening_repairs_before_plan_review()
    test_scene_move_reconciles_only_arc_time_mirrors_without_a_model_call()
    test_scene_move_with_private_mechanics_preserves_them_without_leaking_a_worker_question()
    test_architect_routes_private_arc_answer_to_protected_interview_once()
    test_protected_complete_requires_an_architect_plan_before_assigning_existing_scene_knowledge()
    test_external_card_edit_invalidates_a_pending_protected_worker_authorization()
    print("ok — bounded Story Architect coordinator")

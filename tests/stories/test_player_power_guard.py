"""Regression checks for the player-ability boundary in live story play.

The player can attempt anything, but an attempt is not authoring.  In
particular, a persona description, an object the player claims to carry, or a
dramatic pose must never turn an un-authored supernatural ability into canon.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from loom.config.schema import Story
from loom.stories.runtime.context import build_turn_context
from loom.stories.runtime.engine import PlayState, step_consequence, step_prose
from loom.stories.runtime.player_guard import (assess_player_action, breaks_player_reality,
                                               compile_player_capabilities, sanitize_state_deltas)
from loom.stories.runtime.scenario_compiler import compile_authored_scenario
from loom.stories.workflows import WorkflowTrace


class _Context:
    def __init__(self, root: Path):
        self.root = root
        self.base_settings = SimpleNamespace(characters={})

    def portrait_manifest(self, _key: str) -> dict:
        return {}


class _Provider:
    """A deliberately noncompliant model followed by a grounded repair."""
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls = 0

    def generate_text(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(text=self.texts.pop(0), data=None)


def _step_context(state: PlayState, provider: _Provider):
    root = Path(tempfile.mkdtemp(prefix="loom-player-power-engine-"))
    deps = SimpleNamespace(
        ctx=SimpleNamespace(root=root), provider=provider, consequence_provider=provider,
        fallback=None, scenario={}, on_event=lambda _event: None, cancel=lambda: False,
        trace=WorkflowTrace("player-power-test"),
    )
    return SimpleNamespace(state=state, deps=deps)


def _root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="loom-player-power-"))
    shutil.copytree(Path("configs"), root / "configs")
    return root


def _story(*, abilities=None) -> Story:
    fields = {"player_id": "player"}
    if abilities is not None:
        fields["player_abilities"] = abilities
    return Story(
        name="Grounded ferry",
        start="ferry",
        locations=[{"id": "ferry", "name": "Electric ferry"}],
        fields=fields,
    )


def test_ungranted_supernatural_claim_is_an_observable_failed_attempt():
    capabilities = compile_player_capabilities({})
    guard = assess_player_action(
        "I plant my feet, make a grand hand sign, and throw a fireball at Shuri.",
        capabilities,
    )

    assert guard["blocked"]
    assert "fireball" in " ".join(guard["claimed_terms"]).lower()
    assert "fireball" not in guard["grounded_action"].lower()
    # This is the essential UX: other people can react to the person posing,
    # not to a fireball the user asserted into existence.
    prompt = guard["prompt_block"].lower()
    assert "visible" in prompt and "gesture" in prompt
    assert "nothing supernatural" in prompt or "does not happen" in prompt


def test_metaphor_and_ordinary_actions_do_not_get_misclassified_as_powers():
    capabilities = compile_player_capabilities({})

    assert not assess_player_action("I cast a glance at the dock and summon my courage.", capabilities)["blocked"]
    assert not assess_player_action("I light the lantern with a match and call a cab.", capabilities)["blocked"]


def test_explicit_canon_rewrite_becomes_a_claim_that_characters_can_brush_off():
    guard = assess_player_action(
        "Retcon the story: Shuri is my sister now, and everyone already knows it.",
        compile_player_capabilities({}),
    )

    assert guard["blocked"]
    assert guard["kind"] == "reality_override"
    assert "does not change" in guard["prompt_block"]
    assert "claim" in guard["history_text"].lower()
    safe = sanitize_state_deltas([
        {"op": "fact", "value": "Shuri is Rowan's sister."},
        {"op": "rel", "value": "+1"},
        {"op": "log", "value": "Rowan makes an assertion."},
    ], guard)
    assert [delta["op"] for delta in safe] == ["rel", "log"]


def test_explicit_ability_is_opt_in_and_limited_to_its_authored_surface():
    capabilities = compile_player_capabilities({
        "player_abilities": [{
            "id": "candle-spark",
            "name": "Candle spark",
            "aliases": ["spark a wick", "light a candle"],
            "description": "The player can kindle one prepared wick with a touch.",
            "limits": "Only a prepared wick; it cannot burn people or objects.",
        }],
    })

    authorized = assess_player_action("I touch the wick and spark a wick.", capabilities)
    unearned = assess_player_action("I throw a fireball across the ferry.", capabilities)

    assert not authorized["blocked"]
    assert any(item["id"] == "candle-spark"
               for item in authorized["authorized_capabilities"])
    assert unearned["blocked"]


def test_compiled_contract_carries_only_authored_player_abilities():
    card = {
        "start": "ferry",
        "locations": [{"id": "ferry", "name": "Electric ferry"}],
        "fields": {
            "player_id": "player",
            "player_abilities": [{
                "id": "candle-spark", "name": "Candle spark",
                "aliases": ["spark a wick"],
                "limits": "Prepared wicks only.",
            }],
            "first_day_plan": {"events": [{
                "id": "crossing", "when": "morning", "location": "ferry",
                "participants": ["player"], "visible": "The ferry approaches the island.",
            }]},
        },
    }

    contract = compile_authored_scenario(card)

    assert contract["ready"], contract["issues"]
    capabilities = contract["player_capabilities"]
    assert len(capabilities) == 1
    assert capabilities[0]["id"] == "candle-spark"
    assert "spark a wick" in capabilities[0].get("aliases", [])


def test_forged_persona_and_magic_object_do_not_authorize_a_power_in_context():
    story = _story()
    body = {
        # The player is free to describe their persona, but it is not an
        # authoring channel for abilities.
        "player": {"name": "Rowan", "description": "A telekinetic mage who controls fire."},
        "history": [{
            "role": "user",
            "text": "I raise my ancient fire staff, strike a dramatic pose, and launch a fireball.",
        }],
    }
    tc = build_turn_context(
        _Context(_root()), story, "grounded-ferry", body, {"scene": {"members": []}},
        story_scope="story-grounded-ferry", thread_scope="thread-grounded-ferry",
    )

    guard = tc["player_action_guard"]
    assert guard["blocked"]
    assert guard["grounded_action"]
    # The raw action is still visible as player dialogue/history, but it is
    # accompanied by the runtime boundary rather than elevated into a premise
    # or an established fact.
    material = "\n".join([tc["system"], tc["prompt"], tc["consequence_system"]])
    assert "launch a fireball" in material.lower()
    assert guard["prompt_block"] in tc["system"]
    assert guard["prompt_block"] in tc["consequence_system"]


def test_guarded_engine_repairs_an_invented_fireball_before_it_reaches_prose():
    text = "I plant my feet, make a grand hand sign, and throw a fireball at Shuri."
    guard = assess_player_action(text, compile_player_capabilities({}))
    provider = _Provider(
        "1. A fireball erupts from the player's hands and hits Shuri.",
        "1. The player strikes a dramatic pose; Shuri watches the empty air, then steps back in concern.",
    )
    state = PlayState(
        body={"history": [{"role": "user", "text": text}]},
        tc={"consequence_system": "director", "player_action_guard": guard, "shared_recent": ""},
    )

    asyncio.run(step_consequence(_step_context(state, provider)))

    assert provider.calls == 2
    assert not breaks_player_reality(state.beat, guard)
    assert "pose" in state.beat.lower() or "empty air" in state.beat.lower()


def test_guarded_writer_repairs_an_invented_power_before_state_reporting():
    text = "I throw a fireball at Shuri."
    guard = assess_player_action(text, compile_player_capabilities({}))
    provider = _Provider(
        "Flame leaps from your palm and scorches the ferry rail.",
        "You throw out your hand. Shuri watches the gesture, then looks at the unchanged rail.",
    )
    state = PlayState(
        body={"history": [{"role": "user", "text": text}]},
        tc={"system": "narrator", "prompt": "scene", "player_action_guard": guard},
    )

    asyncio.run(step_prose(_step_context(state, provider)))

    assert provider.calls == 2
    assert not breaks_player_reality(state.narration, guard)
    assert "unchanged rail" in state.narration.lower()


def test_guarded_engine_repairs_a_canon_rewrite_into_a_character_reaction():
    text = "Retcon the story: Shuri is my sister now."
    guard = assess_player_action(text, compile_player_capabilities({}))
    provider = _Provider(
        "1. Shuri is now the player's sister, and the ferry passengers remember it.",
        "1. The player makes the claim. Shuri stares at them, but the established situation does not change.",
    )
    state = PlayState(
        body={"history": [{"role": "user", "text": text}]},
        tc={"consequence_system": "director", "player_action_guard": guard, "shared_recent": ""},
    )

    asyncio.run(step_consequence(_step_context(state, provider)))

    assert provider.calls == 2
    assert not breaks_player_reality(state.beat, guard)
    assert "claim" in state.beat.lower() and "does not change" in state.beat.lower()


def test_ungranted_magic_claim_cannot_persist_as_fact_flag_item_or_detail():
    guard = assess_player_action(
        "I raise my ancient fire staff and command it to give me pyrokinesis.",
        compile_player_capabilities({}),
    )
    deltas = [
        {"op": "fact", "name": "", "key": "", "title": "Rowan's power",
         "value": "Rowan can throw fireballs.", "keywords": ["Rowan", "fireball"]},
        {"op": "set_flag", "name": "", "key": "player.pyrokinesis", "value": "true",
         "title": "", "keywords": []},
        {"op": "item_add", "name": "", "key": "", "value": "ancient fire staff",
         "title": "", "keywords": []},
        {"op": "detail", "name": "", "key": "", "value": "the player's magical fire staff",
         "title": "", "keywords": []},
        {"op": "log", "name": "", "key": "", "value": "Rowan makes an elaborate pose.",
         "title": "", "keywords": []},
    ]

    safe = sanitize_state_deltas(deltas, guard)
    material = " ".join(str(value) for delta in safe for value in delta.values()).lower()

    assert "pyrokinesis" not in material
    assert "fireball" not in material
    assert "magical fire staff" not in material
    assert any(delta["op"] == "log" for delta in safe)


if __name__ == "__main__":
    test_ungranted_supernatural_claim_is_an_observable_failed_attempt()
    test_metaphor_and_ordinary_actions_do_not_get_misclassified_as_powers()
    test_explicit_canon_rewrite_becomes_a_claim_that_characters_can_brush_off()
    test_explicit_ability_is_opt_in_and_limited_to_its_authored_surface()
    test_compiled_contract_carries_only_authored_player_abilities()
    test_forged_persona_and_magic_object_do_not_authorize_a_power_in_context()
    test_guarded_engine_repairs_an_invented_fireball_before_it_reaches_prose()
    test_guarded_writer_repairs_an_invented_power_before_state_reporting()
    test_guarded_engine_repairs_a_canon_rewrite_into_a_character_reaction()
    test_ungranted_magic_claim_cannot_persist_as_fact_flag_item_or_detail()
    print("ok — player power boundary")

"""The compiled runtime must not hand private plan/transcript material to prose."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from loom.config.schema import Story
from loom.stories.runtime.compiled import ensure_runtime, prepare_turn
from loom.stories.runtime.context import build_turn_context
from loom.stories.runtime.scenario_compiler import compile_authored_scenario


class _Context:
    def __init__(self, root: Path):
        self.root = root
        self.base_settings = SimpleNamespace(characters={})

    def portrait_manifest(self, _key: str) -> dict:
        return {}


def _story_data() -> dict:
    return {
        "name": "Gated ferry",
        "start": "ferry",
        "locations": [{"id": "ferry", "name": "Electric ferry"},
                      {"id": "harbor", "name": "Island harbor"}],
        "world": {"entity": {"description": "Copies victims."}},
        "time_system": {"entity_periods": [{"id": "night", "slots": ["night"],
                                                "state": "hunting", "capabilities": ["copy"],
                                                "constraint": "needs dark"}]},
        "fields": {"player_id": "player", "first_day_plan": {"events": [
            {"id": "crossing", "when": "morning", "location": "ferry",
             "participants": ["player"], "visible": "The quiet ferry approaches the island."},
            {"id": "night-attack", "when": "night", "location": "harbor",
             "participants": ["player"], "visible": "A familiar figure waits by the water.",
             "hidden": "The entity copies the victim after killing them.", "entity_period": "night"},
        ]}},
    }


def _impermanence_arc() -> dict:
    """A deliberately spoiler-heavy author document for the prompt boundary test."""
    return {
        "version": 1,
        "themes": [{
            "id": "impermanence",
            "label": "Impermanence",
            "question": "Can love matter when time runs out?",
            "statement": "The future can disappear before people admit what they need.",
        }],
        "arcs": [{
            "id": "shuri-last-ferry",
            "title": "The last ferry",
            "theme_id": "impermanence",
            "owner": "shuri",
            "dramatic_question": "Will Shuri ask to be chosen before departure?",
            "starting_belief": "Being useful is safer than asking to be chosen.",
            "external_promise": "The return crossing repeatedly leaves Shuri and the player one goodbye apart.",
            "stakes": "The ferry will leave whether either of them speaks.",
            "truth": "Shuri has loved the player for years.",
            "character_threads": [{
                "id": "shuri-denial",
                "character": "shuri",
                "protective_strategy": "Makes every intimate moment practical.",
                "limitation": "Leaves before anyone can ask her to stay.",
                "visible_tell": "Answers tenderness with a task.",
                "wound": "THE LANTERN ROOM: she watched her mother vanish without goodbye.",
                "blind_spot": "She mistakes love for duty.",
                "unacknowledged_need": "To choose and be chosen before time closes.",
                "recognition": "She loves the player.",
            }],
            "turning_points": [{
                "id": "ferry-bell",
                "kind": "pressure",
                "scene_id": "crossing",
                "when": "morning",
                "requires": {"flags": {"scenario_gate": True}},
                "characters": ["shuri"],
                "public_surface": "The ferry bell cuts off the conversation before Shuri can finish it.",
                "private_pressure": "Her avoidance costs the only chance to tell the player the truth.",
                "revelation": "She understands she loves the player only after the ferry leaves.",
                "changes": "The loss becomes permanent.",
            }],
        }],
    }


def test_compiled_context_excludes_private_plan_and_unshared_browser_history():
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    card = _story_data()
    contract = compile_authored_scenario(card)
    assert contract["ready"], contract["issues"]
    _doc, world, runtime, _fresh = ensure_runtime({}, contract)
    scene = prepare_turn(contract, world, runtime, {})
    story = Story(**card)
    body = {"history": [
        {"role": "assistant", "text": "A prior stranger learned the ferry's hidden signal."},
        {"role": "user", "text": "I look over the railing."},
    ]}
    tc = build_turn_context(_Context(root), story, "gated-ferry", body, world,
                            story_scope="story-gated-ferry", thread_scope="thread-gated",
                            scenario=contract, runtime_state=runtime, scenario_scene=scene)
    material = "\n".join([tc["system"], tc["prompt"], tc["consequence_system"]])
    assert "hidden signal" not in material
    assert "copies the victim" not in material
    assert "KNOWLEDGE BOUNDARY" in tc["system"]
    assert tc["shared_recent"] == ""


def test_compiled_context_gets_only_the_current_arc_surface_not_private_arc_truth():
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    card = _story_data()
    card["themes"] = ["Impermanence"]
    card["cast"] = [{"character": "player"}, {"character": "shuri"}]
    card["fields"]["arc_design"] = _impermanence_arc()
    # The arc surface is presence-gated: Shuri must be in this authored scene
    # before the narrator receives even her safe behavioral projection.
    card["fields"]["first_day_plan"]["opening_present"] = ["player", "shuri"]
    card["fields"]["first_day_plan"]["events"][0]["participants"] = ["player", "shuri"]
    contract = compile_authored_scenario(card)
    assert contract["ready"], contract["issues"]
    _doc, world, runtime, _fresh = ensure_runtime({}, contract)
    # Scenario flags are the executable gate ledger.  Keep a conflicting
    # public-world value to prove the context merges both and gives the
    # deterministic scenario state authority.
    runtime["scenario_state"]["flags"]["scenario_gate"] = True
    world["flags"]["scenario_gate"] = False
    scene = prepare_turn(contract, world, runtime, {})
    story = Story(**card)
    tc = build_turn_context(
        _Context(root), story, "gated-ferry", {"history": []}, world,
        story_scope="story-gated-ferry", thread_scope="thread-gated",
        scenario=contract, runtime_state=runtime, scenario_scene=scene,
    )

    narrator_material = "\n".join([tc["system"], tc["prompt"], tc["scribe_system"]])
    consequence_material = tc["consequence_system"]
    safe_surface = (
        "Being useful is safer than asking to be chosen.",
        "Answers tenderness with a task.",
        "The ferry bell cuts off the conversation before Shuri can finish it.",
    )
    for text in safe_surface:
        assert text in narrator_material
        assert text in consequence_material
    for private_text in (
        "THE LANTERN ROOM",
        "She mistakes love for duty.",
        "To choose and be chosen before time closes.",
        "She loves the player.",
        "Her avoidance costs the only chance",
        "The loss becomes permanent.",
    ):
        assert private_text not in narrator_material
        assert private_text not in consequence_material
    assert tc["lanes"]["arc_surface"] > 0


if __name__ == "__main__":
    test_compiled_context_excludes_private_plan_and_unshared_browser_history()
    test_compiled_context_gets_only_the_current_arc_surface_not_private_arc_truth()
    print("ok — compiled context gates private knowledge")

"""The compiled runtime must not hand private plan/transcript material to prose."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from loom.config.schema import Story
from loom.stories.runtime.compiled import ensure_runtime, prepare_turn, record_observation
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


def _solo_story_data() -> dict:
    """A two-scene day ending in a DELIBERATELY solo scene (empty participants)."""
    card = _story_data()
    card["cast"] = [{"character": "player"}, {"character": "npc_a"}, {"character": "npc_b"}]
    card["fields"]["first_day_plan"]["events"] = [
        {"id": "crossing", "when": "morning", "location": "ferry",
         "participants": ["npc_a"], "visible": "The quiet ferry approaches the island."},
        {"id": "night-deck", "when": "night", "location": "ferry",
         "participants": [], "visible": "The deck is empty; the island lights pulse offshore."},
    ]
    return card


def _build(root, card, contract, world, runtime, scene, history):
    return build_turn_context(_Context(root), Story(**card), "gated-ferry",
                              {"history": history}, world,
                              story_scope="story-gated-ferry", thread_scope="thread-gated",
                              scenario=contract, runtime_state=runtime, scenario_scene=scene)


def test_compiled_transcript_follows_the_player_witness_not_the_roster():
    """The player is the constant witness: the narrator must keep what the PLAYER
    experienced no matter who shares the current scene — and an empty roster must
    not vacuously unlock every turn or wipe the player's own past."""
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    card = _solo_story_data()
    contract = compile_authored_scenario(card)
    assert contract["ready"], contract["issues"]
    _doc, world, runtime, _fresh = ensure_runtime({}, contract)
    scene = prepare_turn(contract, world, runtime, {})
    # A morning turn witnessed with npc_a on stage; the player caused the narration.
    record_observation(runtime, text="The ferry hums under a grey sky.",
                       player_input="I look over the railing.", present=["npc_a"])
    history = [{"role": "user", "text": "I listen to the hum."}]
    tc_morning = _build(root, card, contract, world, runtime, scene, history)
    # The player's own action precedes the narration it caused (chronological).
    assert "I look over the railing." in tc_morning["shared_recent"]
    assert "grey sky" in tc_morning["shared_recent"]
    assert (tc_morning["shared_recent"].index("I look over the railing.")
            < tc_morning["shared_recent"].index("grey sky"))
    # The ledger entry carries the player witness without polluting the roster.
    turns = runtime["scenario_state"]["turns"]
    assert all("player" in t["present"] for t in turns)
    assert "player" not in runtime["scenario_state"]["present"]

    # Later: a DELIBERATELY solo scene (empty roster). The player's morning must
    # still be visible — roster emptiness is not a memory wipe and not an unlock-all.
    world["day"] = {"n": 1, "slot": "night"}
    solo_scene = prepare_turn(contract, world, runtime, {"scene_seed": {"id": "night-deck"}})
    assert runtime["scenario_state"]["present"] == []
    tc_solo = _build(root, card, contract, world, runtime, solo_scene, history)
    assert tc_solo["shared_recent"] == tc_morning["shared_recent"]
    # The consequence pass frames the solo scene as solo instead of pushing to populate it.
    assert "the player ALONE" in tc_solo["consequence_system"]
    assert "deliberately solo" in tc_solo["consequence_system"]


def test_compiled_transcript_legacy_ledger_keeps_roster_filter():
    """Pre-fix ledgers have no player witness stamp: keep the old roster-subset
    behavior for them instead of showing nothing."""
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    card = _solo_story_data()
    contract = compile_authored_scenario(card)
    assert contract["ready"], contract["issues"]
    _doc, world, runtime, _fresh = ensure_runtime({}, contract)
    scene = prepare_turn(contract, world, runtime, {})
    runtime["scenario_state"]["turns"] = [
        {"text": "A legacy turn only npc_a saw.", "speaker": "narrator",
         "addressed": "", "present": ["npc_a"], "scene": "crossing"},
    ]
    history = [{"role": "user", "text": "I listen."}]
    tc = _build(root, card, contract, world, runtime, scene, history)
    assert "legacy turn" in tc["shared_recent"]
    # A roster the legacy turn was not witnessed by still excludes it.
    runtime["scenario_state"]["present"] = ["npc_b"]
    tc = _build(root, card, contract, world, runtime, scene, history)
    assert "legacy turn" not in tc["shared_recent"]


def test_compiled_context_skips_nsfw_lore_injection():
    """An activated scenario's register is authored. The adult lorebook's keyword
    triggers are common English words ('edge', 'pull') and must never hijack the
    narrator's system prompt of a non-adult authored story."""
    import re as _re
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    card = _story_data()
    contract = compile_authored_scenario(card)
    assert contract["ready"], contract["issues"]
    _doc, world, runtime, _fresh = ensure_runtime({}, contract)
    scene = prepare_turn(contract, world, runtime, {})
    history = [{"role": "user", "text": "I pull toward the forest's edge and press on."}]
    tc = _build(root, card, contract, world, runtime, scene, history)
    assert "SYSTEM NOTE: When the scene contains intimacy" not in tc["system"]
    # Non-vacuous fixture: the trigger WOULD have fired — an enabled _nsfw entry
    # really does match this history text in the copied production lorebook.
    from loom.server.services import lorebook_store as _LS
    recent = history[-1]["text"].lower()
    assert any(
        _re.search(rf"\b{_re.escape(k.lower())}\b", recent)
        for e in _LS.load_lorebook(root, "_nsfw") if e.enabled
        for k in e.keywords if k
    )


if __name__ == "__main__":
    test_compiled_context_excludes_private_plan_and_unshared_browser_history()
    test_compiled_context_gets_only_the_current_arc_surface_not_private_arc_truth()
    test_compiled_transcript_follows_the_player_witness_not_the_roster()
    test_compiled_transcript_legacy_ledger_keeps_roster_filter()
    test_compiled_context_skips_nsfw_lore_injection()
    print("ok — compiled context gates private knowledge")

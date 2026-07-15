"""Regression checks for the deterministic compiled-story live beat."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from loom.config.schema import Story
from loom.stories.runtime.compiled import ensure_runtime
from loom.stories.runtime.engine import (
    PlayDeps, PlayState, _compiled_live_beat, _freeplay_live_beat, step_compile,
)
from loom.stories.runtime.live_beat import director_live_beat_block, select_live_beat
from loom.stories.runtime.scenario_compiler import compile_authored_scenario


def test_live_beat_is_deterministic_and_cannot_project_private_inputs():
    scene = {
        "id": "dock-rumor",
        "title": "The closed ice-cream shop",
        "location": "dock",
        "participants": ["player", "shuri"],
        "visible": "A delivery crate sits outside the closed shop. [[hidden]]THE PRIVATE KILLER PLAN[[/hidden]]",
        "hook": "Will you inspect the crate or ask Shuri why the shop is closed?",
        "theme": "The ordinary routines people rely on can vanish overnight.",
        "tone": "quiet unease",
        "roles": {"shuri": "childhood friend"},
        # These are compiled-scene private/director fields and must not even
        # enter the selector's output if a caller hands it the whole mapping.
        "hidden": "THE PRIVATE KILLER PLAN",
        "evidence": "THE PRIVATE EVIDENCE",
        "knowledge": {"shuri": "THE PRIVATE KNOWLEDGE"},
    }
    cores = {
        "shuri": "Private and practical; she answers tenderness with a task.",
        "other": {"public_core": "ordinary", "secret": "THE PRIVATE CHARACTER SECRET"},
    }
    recent = {"turn_count": 7, "scene_turn_count": 0, "time": "morning", "location": "dock"}

    first = select_live_beat(scene, character_cores=cores, recent_state=recent)
    second = select_live_beat(scene, character_cores=cores, recent_state=recent)
    assert first == second
    assert first["kind"] == "investigation"
    assert first["phase"] == "opening"
    assert first["focus"] == {
        "key": "shuri",
        "role": "childhood friend",
        "surface": "Private and practical; she answers tenderness with a task.",
    }
    assert "THE PRIVATE" not in json.dumps(first)
    assert "THE PRIVATE" not in director_live_beat_block(first)
    assert "delivery crate" in first["pressure"]


def test_live_beat_requires_a_real_public_scene_surface():
    assert select_live_beat({"id": "gate-only", "location": "dock", "participants": ["player"]}) == {}


def test_live_beat_prefers_an_authored_role_and_keeps_continuations_grounded():
    beat = select_live_beat(
        {
            "id": "harbor-talk", "participants": ["player", "dockworker", "shuri"],
            "visible": "A boatman is closing up the last ferry line.",
            "roles": {"shuri": "childhood friend"},
        },
        recent_state={"scene_turn_count": 1},
    )
    assert beat["phase"] == "continuing"
    assert beat["focus"]["key"] == "shuri"
    assert "grounded NPC move" in beat["next_move"]


def test_compiled_adapter_uses_author_public_cores_and_structural_turn_state_only():
    deps = SimpleNamespace(
        st=SimpleNamespace(fields={
            "player_id": "returner",
            "character_cores": {"shuri": "Reliable, private, and too practical when hurt."},
        }),
        ctx=SimpleNamespace(base_settings=SimpleNamespace(characters={})),
        runtime_state={"scenario_state": {
            "time": "evening",
            "location": "dock",
            "turns": [{"scene": "dock-rumor", "text": "THE OLD PRIVATE TURN"}],
            "event_status": {"ambush": {"private": "THE PRIVATE EVENT"}},
        }},
    )
    scene = {
        "id": "dock-rumor",
        "title": "The closed shop",
        "location": "dock",
        "participants": ["returner", "shuri"],
        "visible": "The shop's bell rings once behind its locked door.",
        "hook": "Ask Shuri whether she heard it too.",
        "roles": {"shuri": "childhood friend"},
        "hidden": "THE PRIVATE SCENE ACTION",
    }
    beat = _compiled_live_beat(deps, scene)
    material = json.dumps(beat) + director_live_beat_block(beat)
    assert beat["phase"] == "continuing"
    assert beat["focus"]["surface"] == "Reliable, private, and too practical when hurt."
    assert "THE OLD PRIVATE TURN" not in material
    assert "THE PRIVATE EVENT" not in material
    assert "THE PRIVATE SCENE ACTION" not in material


def test_freeplay_adapter_uses_current_members_and_never_leaks_private_plan():
    deps = SimpleNamespace(
        st=SimpleNamespace(fields={
            "player_id": "player",
            "character_cores": {"shuri": "Reliable, private, and too practical when hurt."},
        }),
    )
    world_state = {
        "step": 4,
        "scene": {"space": "dock", "members": ["player", "shuri"], "pov": "player"},
        # goal/pressure/exit are the StoryMaster's own private per-scene agenda
        # (scene_block's job) — the adapter must never let them reach the beat.
        "scene_plan": {
            "space": "dock", "loc": "Island dock", "opened": 2,
            "goal": "THE PRIVATE DIRECTOR GOAL", "pressure": "THE PRIVATE PRESSURE",
            "exit": "THE PRIVATE EXIT CONDITION",
            "theme": "a reunion neither of them is ready to name",
            "tone": "gentle, unfinished",
            "roles": {"shuri": "childhood friend who turned him down once"},
        },
    }
    beat = _freeplay_live_beat(deps, world_state)
    material = json.dumps(beat) + director_live_beat_block(beat)
    assert beat["theme"] == "a reunion neither of them is ready to name"
    assert beat["tone"] == "gentle, unfinished"
    assert beat["phase"] == "continuing"  # step 4, opened at 2 -> scene_turn_count 2
    assert beat["focus"] == {
        "key": "shuri", "role": "childhood friend who turned him down once",
        "surface": "Reliable, private, and too practical when hurt.",
    }
    assert "THE PRIVATE" not in material


def test_freeplay_adapter_is_empty_without_a_scene_plan_or_theme():
    deps = SimpleNamespace(st=SimpleNamespace(fields={}))
    assert _freeplay_live_beat(deps, {}) == {}
    # A plan with only the private goal/pressure/exit agenda (no theme) is not
    # something the Director can honestly turn into a public beat.
    stub_plan = {"scene_plan": {"space": "dock", "goal": "THE PRIVATE GOAL"},
                 "scene": {"members": ["player"]}}
    assert _freeplay_live_beat(deps, stub_plan) == {}


def test_compiler_projects_public_scene_theme_tone_and_roles():
    card = {
        "start": "dock",
        "locations": [{"id": "dock", "name": "Island dock"}],
        "cast": [{"character": "shuri"}],
        "fields": {"player_id": "player", "first_day_plan": {
            "opening_present": ["player", "shuri"],
            "events": [{
                "id": "dock-rumor", "when": "morning", "location": "dock",
                "participants": ["player", "shuri"],
                "visible": "A locked shop bell rings once behind the glass.",
                "hook": "Ask Shuri whether she heard it too.",
                "theme": "ordinary life interrupted",
                "tone": "quiet unease",
                "roles": {"shuri": "childhood friend"},
            }],
        }},
    }
    contract = compile_authored_scenario(card)
    assert contract["ready"], contract["issues"]
    scene = contract["scene_catalog"][0]
    assert scene["theme"] == "ordinary life interrupted"
    assert scene["tone"] == "quiet unease"
    assert scene["roles"] == {"shuri": "childhood friend"}


def test_compiled_turn_injects_live_beat_only_into_director_consequence_context():
    class _Context:
        def __init__(self, root: Path):
            self.root = root
            self.base_settings = SimpleNamespace(characters={})

        def portrait_manifest(self, _key: str) -> dict:
            return {}

    card = {
        "name": "Live beat context", "premise": "A dock mystery.", "tone": "quiet", "start": "dock",
        "locations": [{"id": "dock", "name": "Island dock"}],
        "cast": [{"character": "shuri"}],
        "fields": {"player_id": "player", "character_cores": {
            "shuri": "Reliable, private, and too practical when hurt.",
        }, "first_day_plan": {"opening_present": ["player", "shuri"], "events": [{
            "id": "dock-rumor", "when": "morning", "location": "dock",
            "participants": ["player", "shuri"],
            "visible": "The shop bell rings once behind its locked door.",
            "hook": "Ask Shuri whether she heard it too.",
            "theme": "ordinary life interrupted", "roles": {"shuri": "childhood friend"},
            "hidden": "THE PRIVATE SCENE ACTION",
        }]}}
    }
    contract = compile_authored_scenario(card)
    assert contract["ready"], contract["issues"]
    _doc, world, runtime, _fresh = ensure_runtime({}, contract)
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    deps = PlayDeps(
        ctx=_Context(root), st=Story(**card), key="live-beat", sid="test-session", sess={},
        provider=None, scribe_provider=None, scenario=contract, runtime_state=runtime,
    )
    state = PlayState(body={"history": []}, world_state=world)
    asyncio.run(step_compile(SimpleNamespace(state=state, deps=deps)))

    director_context = state.tc["consequence_system"]
    narrator_context = state.tc["system"]
    assert "LIVE SCENE BEAT" in director_context
    assert "LIVE SCENE BEAT" not in narrator_context
    assert state.tc["director_live_beat"]["scene_id"] == "dock-rumor"
    assert state.tc["lanes"]["live_beat"] > 0
    assert "THE PRIVATE SCENE ACTION" not in director_context


if __name__ == "__main__":
    test_live_beat_is_deterministic_and_cannot_project_private_inputs()
    test_live_beat_requires_a_real_public_scene_surface()
    test_live_beat_prefers_an_authored_role_and_keeps_continuations_grounded()
    test_compiled_adapter_uses_author_public_cores_and_structural_turn_state_only()
    test_freeplay_adapter_uses_current_members_and_never_leaks_private_plan()
    test_freeplay_adapter_is_empty_without_a_scene_plan_or_theme()
    test_compiler_projects_public_scene_theme_tone_and_roles()
    test_compiled_turn_injects_live_beat_only_into_director_consequence_context()
    print("ok — deterministic public live beats")

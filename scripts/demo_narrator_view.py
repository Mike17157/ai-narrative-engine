"""Render the REAL narrator context for a sheets-built story card.

Assembles the card exactly as the six-sheet pass would store it (same canned
ferry-island data as tests/stories/test_sheets.py), then runs the actual
play-time pipeline: compile_authored_scenario -> ensure_runtime -> prepare_turn
-> build_turn_context, and prints the system text the narrative model receives.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loom.config.schema import Character, Story
from loom.stories.runtime.compiled import ensure_runtime, prepare_turn
from loom.stories.runtime.context import build_turn_context
from loom.stories.runtime.scenario_compiler import compile_authored_scenario


class _Context:
    def __init__(self, root: Path, characters: dict):
        self.root = root
        self.base_settings = SimpleNamespace(characters=characters)

    def portrait_manifest(self, _key: str) -> dict:
        return {}


def _person(name, persona, **fields) -> Character:
    return Character(name=name, system=persona, fields=fields, playable=False)


CARD = {
    "name": "The Quiet Crossing",
    "premise": "A graduate returns to her island home and finds the crossing unnaturally quiet.",
    "world": {
        "genre": "mystery",
        "setting": "A small island reached by a quiet electric ferry.",
        "atmosphere": "Bright water and careful, ordinary quiet.",
        "history": "The harbor once received votive offerings during storms.",
        "customs": "Residents leave flowers at the old ferry bell before the first crossing.",
        "technology": "Contemporary island infrastructure, including the electric ferry.",
    },
    "locations": [
        {"id": "ferry", "name": "Electric ferry",
         "description": "Benches face wide windows over calm bright water."},
        {"id": "dock", "name": "Island dock",
         "description": "A weathered plaque lists old offerings beside the ferry bell."},
    ],
    "start": "ferry",
    "time_system": {"slots": ["morning", "afternoon", "evening", "night"], "entity_periods": []},
    "cast": [
        {"character": "shuri", "primary": True, "home": "dock"},
        {"character": "mina", "primary": False, "home": "ferry"},
    ],
    "relationships": [{
        "id": "develop-bond-1", "source": "shuri", "target": "mina", "nature": "old friends",
        "dynamic": "Mina checks whether Shuri has eaten before every shift.",
        "stance": "warm",
        "target_dynamic": "Shuri leaves flowers on the ferry bench for Mina.",
        "target_stance": "trusting",
        "note": "They grew up sharing the same crossing.",
        "potential": "Mina saw something in the water last winter that she has not told Shuri.",
        "trajectory": "trust → silence",
    }],
    "fields": {
        "status": "interviewing",
        "player_id": "player",
        "open_questions": [
            "Why is the ferry quieter than usual?",
            "What did Mina see in the water last winter?",
            "What is Shuri deciding not to say?",
        ],
        "character_cores": {
            "shuri": "She protects the protagonist by deciding what not to say.",
        },
        "first_day_plan": {
            "objective": "Reach the dock before the last crossing.",
            "opening_time": "morning",
            "opening_location": "ferry",
            "opening_present": ["mina"],
            "events": [
                {
                    "id": "crossing", "when": "morning", "location": "ferry",
                    "participants": ["player", "mina"],
                    "visible": "The ferry hums over calm water while Mina pretends not to watch the protagonist.",
                    "hook": "The player can ask Mina why the crossing is so quiet or explore the empty cabin.",
                    "theme": "homecoming under watch", "tone": "quiet unease",
                    "roles": [{"character": "mina", "role": "deflecting guide"}],
                    "hidden": "Mina is counting the protagonist's glances at the shore.",
                    "entity_action": False, "entity_period": "", "trigger": "", "evidence": "",
                    "knowledge": {"protagonist": "", "entity": "", "public": ""},
                },
                {
                    "id": "dock-reunion", "when": "afternoon", "location": "dock",
                    "participants": ["player", "shuri"],
                    "visible": "Shuri waits at the end of the dock with fresh flowers resting on the plaque.",
                    "hook": "The player can greet Shuri warmly or ask why no one else came to meet the ferry.",
                    "theme": "reunion with held breath", "tone": "tender restraint",
                    "roles": [{"character": "shuri", "role": "withholding welcomer"}],
                    "hidden": "", "entity_action": False, "entity_period": "", "trigger": "",
                    "evidence": "",
                    "knowledge": {"protagonist": "", "entity": "", "public": ""},
                },
            ],
        },
    },
}

CHARACTERS = {
    "shuri": _person(
        "Shuri", "She speaks quietly and watches the water before she answers.",
        role="childhood friend",
        appearance="A simply dressed young woman with a canvas bag.",
        personality="private and reliable",
        background="She grew up on the island and never left.",
        connection="She waits at the dock for the returning protagonist.",
    ),
    "mina": _person(
        "Mina", "She checks on passengers with blunt, practical warmth.",
        role="ferry mechanic",
        appearance="Windblown hair and an oil-stained jacket.",
        personality="bluntly protective",
        background="She maintains the island ferry alone.",
        connection="She knows the crossing better than anyone.",
    ),
}


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="loom-narrator-view-"))
    shutil.copytree(Path("configs"), root / "configs")
    try:
        contract = compile_authored_scenario(CARD)
        if not contract["ready"]:
            print("CONTRACT NOT READY:", contract["issues"])
            sys.exit(1)
        _doc, world, runtime, _fresh = ensure_runtime({}, contract)
        scene = prepare_turn(contract, world, runtime, {})
        story = Story(**CARD)
        body = {"history": [], "player": {"name": "Aya", "description": "The returning graduate."}}
        tc = build_turn_context(
            _Context(root, CHARACTERS), story, "quiet-crossing", body, world,
            story_scope="story-quiet-crossing", thread_scope="thread-quiet",
            scenario=contract, runtime_state=runtime, scenario_scene=scene,
        )
        out = Path("configs/narrator_view_example.md")
        out.write_text(
            "# What the narrative model receives (turn 1, scene: crossing)\n\n"
            "## SYSTEM\n\n```markdown\n" + tc["system"] + "\n```\n\n"
            "## PROMPT (user turn envelope)\n\n```markdown\n" + tc["prompt"] + "\n```\n",
            encoding="utf-8",
        )
        print(tc["system"])
        print("\n" + "=" * 72 + "\nPROMPT:\n")
        print(tc["prompt"])
        print("\n" + "=" * 72)
        print(f"system chars: {len(tc['system'])}   prompt chars: {len(tc['prompt'])}")
        print(f"saved -> {out.resolve()}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()

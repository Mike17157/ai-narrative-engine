"""Outfit visibility at play time: narrator attire lines + resolved outfit ids."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from loom.config.schema import Story
from loom.stories.runtime.compiled import ensure_runtime, prepare_turn
from loom.stories.runtime.context import build_turn_context
from loom.stories.runtime.engine import PlayDeps, PlayState, _apply_turn, _resolved_outfits
from loom.stories.runtime.scenario_compiler import compile_authored_scenario


def _card(outfit: str | None = None, instruction_len: int = 0) -> dict:
    member = {"character": "shuri"}
    if outfit:
        member["outfit"] = outfit
    return {
        "name": "Outfit narration", "premise": "A dock mystery.", "tone": "quiet",
        "start": "dock",
        "locations": [{"id": "dock", "name": "Island dock"}],
        "cast": [{"character": "player"}, member],
        "fields": {"player_id": "player", "first_day_plan": {
            "opening_present": ["player", "shuri"],
            "events": [{"id": "dock-rumor", "when": "morning", "location": "dock",
                        "participants": ["player", "shuri"],
                        "visible": "The shop bell rings once behind the glass."}],
        }},
    }


_MANIFEST = {"appearance": "1girl, dark hair", "outfits": [
    {"id": "base", "name": "Base", "instruction": ""},
    {"id": "raincoat", "name": "Yellow raincoat",
     "instruction": "a bright yellow raincoat, salt-stained at the hem"},
]}


class _Context:
    def __init__(self, root: Path, manifest: dict | None = None):
        self.root = root
        self.base_settings = SimpleNamespace(
            characters={"shuri": SimpleNamespace(name="Shuri", system="A dockhand.")})
        self._manifest = manifest if manifest is not None else _MANIFEST

    def portrait_manifest(self, _key: str) -> dict:
        return self._manifest


def _turn_context(root: Path, card: dict, manifest: dict | None = None) -> dict:
    contract = compile_authored_scenario(card)
    assert contract["ready"], contract["issues"]
    _doc, world, runtime, _fresh = ensure_runtime({}, contract)
    scene = prepare_turn(contract, world, runtime, {})
    return build_turn_context(
        _Context(root, manifest), Story(**card), "outfit-story", {"history": []}, world,
        story_scope="story-outfit-story", thread_scope="thread-outfit",
        scenario=contract, runtime_state=runtime, scenario_scene=scene)


def test_narrator_cast_line_includes_attire_only_when_outfit_selected():
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    try:
        tc = _turn_context(root, _card(outfit="raincoat"))
        material = "\n".join([tc["system"], tc["prompt"]])
        assert "Wears Yellow raincoat" in material
        assert "salt-stained at the hem" in material

        tc_plain = _turn_context(root, _card())
        plain = "\n".join([tc_plain["system"], tc_plain["prompt"]])
        assert "raincoat" not in plain

        # An outfit id that no longer exists in the manifest is omitted silently.
        tc_gone = _turn_context(root, _card(outfit="deleted-outfit"))
        gone = "\n".join([tc_gone["system"], tc_gone["prompt"]])
        assert "wears" not in gone.lower()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_narrator_attire_summary_is_capped():
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    try:
        long_instruction = "a tailored coat with " + "many pockets " * 30
        manifest = {"appearance": "", "outfits": [
            {"id": "coat", "name": "Coat", "instruction": long_instruction}]}
        tc = _turn_context(root, _card(outfit="coat"), manifest)
        material = "\n".join([tc["system"], tc["prompt"]])
        assert "Wears Coat" in material
        assert long_instruction not in material
        attire = next(line for line in material.splitlines() if "Wears Coat" in line)
        assert len(attire.split("Wears Coat: ", 1)[1]) <= 152  # 150-char cap + sentence period
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_apply_turn_response_carries_resolved_outfit_ids():
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    try:
        card = {
            "name": "Outfit play", "premise": "A dock mystery.", "tone": "quiet",
            "start": "dock",
            "locations": [{"id": "dock", "name": "Island dock"}],
            "cast": [{"character": "shuri", "outfit": "raincoat"}],
        }
        deps = PlayDeps(
            ctx=_Context(root), st=Story(**card), key="outfit-play", sid="test-outfit",
            sess={"state": {}}, provider=None, scribe_provider=None,
        )
        state = PlayState(
            body={"history": []},
            world_state={},
            narration="Shuri waves from the dock.",
            beat="Shuri greets the player.",
            tc={"cur": "dock", "prior_pov": "", "roster": ["Shuri"],
                "player_action_guard": {}},
            report={"present": ["Shuri"], "location": "dock", "emotions": [],
                    "state_deltas": [], "lines": []},
        )
        result = _apply_turn(deps, state)
        assert result["present"] == ["shuri"]
        assert result["outfits"] == {"shuri": "raincoat"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resolved_outfits_defaults_to_everyday_and_omits_missing_manifests():
    root = Path(tempfile.mkdtemp())
    try:
        card = {"name": "x", "start": "dock",
                "locations": [{"id": "dock", "name": "Island dock"}],
                "cast": [{"character": "shuri"}]}
        ctx = _Context(root)
        st = Story(**card)
        # No selection: the EVERYDAY default = first non-base/swim outfit.
        assert _resolved_outfits(ctx, st, "story", ["shuri"]) == {"shuri": "raincoat"}
        # A stale selection falls back to the everyday default.
        st.cast[0].outfit = "deleted-outfit"
        assert _resolved_outfits(ctx, st, "story", ["shuri"]) == {"shuri": "raincoat"}
        # Only base/swim looks: the first outfit is the default.
        ctx._manifest = {"outfits": [{"id": "base", "name": "Base"}]}
        assert _resolved_outfits(ctx, st, "story", ["shuri"]) == {"shuri": "base"}
        # No manifest / no outfits: the character is omitted, never an error.
        ctx._manifest = {}
        assert _resolved_outfits(ctx, st, "story", ["shuri"]) == {}
    finally:
        shutil.rmtree(root, ignore_errors=True)

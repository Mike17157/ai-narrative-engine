"""Regression checks for unresolved thematic-arc resistance in live play."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from loom.stories.authoring.arc_design import active_arc_resistance_locks
from loom.stories.runtime.arc_guard import breaks_resistance, prompt_block
from loom.stories.runtime.engine import PlayState, step_consequence, step_prose
from loom.stories.workflows import WorkflowTrace


def _arc() -> dict:
    return {
        "version": 1,
        "themes": [{"id": "impermanence", "label": "Impermanence"}],
        "arcs": [{
            "id": "shuri-last-ferry", "title": "The Helper's Exit", "theme_id": "impermanence",
            "owner": "shuri", "truth": "She loves the player but calls it duty.",
            "turning_points": [
                {"id": "bell", "kind": "pressure", "scene_id": "crossing", "when": "morning",
                 "public_surface": "The docking bell cuts off the conversation."},
                # A revelation without a Director flag is only a possible moment.
                {"id": "possible-reveal", "kind": "revelation", "scene_id": "crossing", "when": "morning",
                 "public_surface": "Shuri stops at the gangway."},
            ],
            "character_threads": [{
                "character": "shuri", "protective_strategy": "turns intimate moments into ferry errands",
                "blind_spot": "She mistakes love for duty.",
                "unacknowledged_need": "To be chosen before the ferry leaves.",
                "limitation": "She leaves before anyone can ask her to stay.",
                "visible_tell": "straightens the timetable until its corners align",
                "recognition": "She loves the player.",
            }],
        }],
    }


def _locks(*, flags: dict | None = None) -> list[dict]:
    locks = active_arc_resistance_locks(
        _arc(), scene_id="crossing", slot="morning", present=["shuri"], flags=flags or {},
    )
    for lock in locks:
        lock["name"] = "Shuri"
        lock["challenged"] = True
    return locks


def test_pressure_and_ungated_revelation_do_not_unlock_a_thread():
    locks = _locks()
    assert len(locks) == 1
    # The model-facing boundary has behavioral names only, never the private
    # truth or its detector terms.
    boundary = prompt_block(locks)
    assert "HARD ARC RESISTANCE BOUNDARY" in boundary
    assert "loves the player" not in boundary
    assert "duty" not in boundary


def test_explicit_director_flag_can_open_a_revelation():
    raw = _arc()
    raw["arcs"][0]["turning_points"][1]["requires"] = {"flags": {"director.shuri_reveal": True}}
    locked = active_arc_resistance_locks(raw, scene_id="crossing", slot="morning", present=["shuri"], flags={})
    opened = active_arc_resistance_locks(
        raw, scene_id="crossing", slot="morning", present=["shuri"],
        flags={"director.shuri_reveal": True},
    )
    assert locked
    assert opened == []


def test_detector_catches_an_unearned_direct_admission_but_not_deflection():
    locks = _locks()
    assert breaks_resistance('Shuri says, "Yes. I have loved you for years."', locks)
    assert breaks_resistance('"Yes," Shuri says. "But I cannot stay."', locks)
    assert not breaks_resistance(
        'Shuri straightens the timetable until its corners align. "The ferry is docking," she says.', locks,
    )


class _Provider:
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls = 0

    def generate_text(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(text=self.texts.pop(0), data=None)


def _step_context(state: PlayState, provider: _Provider):
    root = Path(tempfile.mkdtemp(prefix="loom-arc-resistance-"))
    deps = SimpleNamespace(
        ctx=SimpleNamespace(root=root), provider=provider, consequence_provider=provider,
        fallback=None, scenario={}, on_event=lambda _event: None, cancel=lambda: False,
        trace=WorkflowTrace("arc-resistance-test"),
    )
    return SimpleNamespace(state=state, deps=deps)


def test_engine_repairs_a_leaking_director_beat_before_prose_sees_it():
    provider = _Provider(
        '1. Shuri says, "Yes. I have loved you for years."',
        '1. Shuri aligns the ferry timetable and says the docking crew needs a clear aisle.',
    )
    state = PlayState(
        body={"history": [{"role": "user", "text": "Shuri, do you love me?"}]},
        tc={"consequence_system": "director", "arc_resistance_locks": _locks(), "shared_recent": ""},
    )
    asyncio.run(step_consequence(_step_context(state, provider)))
    assert provider.calls == 2
    assert "loved you" not in state.beat.lower()
    assert "timetable" in state.beat.lower()


def test_engine_repairs_a_leaking_narration_and_preserves_specific_resistance():
    provider = _Provider(
        'Shuri looks at you. "Yes. I love you."',
        'Shuri straightens the timetable until its corners align. "The ferry is docking," she says.',
    )
    state = PlayState(
        body={"history": [{"role": "user", "text": "Shuri, do you love me?"}]},
        tc={"system": "narrator", "prompt": "scene", "arc_resistance_locks": _locks()},
    )
    asyncio.run(step_prose(_step_context(state, provider)))
    assert provider.calls == 2
    assert "love you" not in state.narration.lower()
    assert "timetable" in state.narration.lower()
    assert state.prose_guard["arc_repaired"]


if __name__ == "__main__":
    test_pressure_and_ungated_revelation_do_not_unlock_a_thread()
    test_explicit_director_flag_can_open_a_revelation()
    test_detector_catches_an_unearned_direct_admission_but_not_deflection()
    test_engine_repairs_a_leaking_director_beat_before_prose_sees_it()
    test_engine_repairs_a_leaking_narration_and_preserves_specific_resistance()
    print("ok — arc resistance")

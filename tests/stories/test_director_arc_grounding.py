"""The macro play-pacing arc (generate_arc/design_arc, runtime/director.py) dramatizes
the protagonist's own already-established situation instead of inventing an unrelated
one — grounded via arc_design.narrator_arc_surface, the same redacted accessor the
scene planner uses (see test_freeplay_scene_kernel.py). These checks cover that
threading and its privacy boundary, not arc generation quality itself."""
from __future__ import annotations

from types import SimpleNamespace

from loom.stories.runtime.director import generate_arc, design_arc


class _Provider:
    def __init__(self, data: dict):
        self.data = data
        self.calls = 0
        self.prompts: list[str] = []

    def generate_text(self, **kwargs):
        self.calls += 1
        self.prompts.append(kwargs.get("prompt", ""))
        return SimpleNamespace(text="", data=self.data)


def _cast(*keys):
    return [SimpleNamespace(character=k) for k in keys]


def _ctx():
    return SimpleNamespace(root=None, base_settings=SimpleNamespace(characters={}))


def _arc_response():
    return {"name": "n", "question": "q", "conditions": [], "stages": [
        {"title": "t", "purpose": "p", "milestone": "m", "events": []},
        {"title": "t2", "purpose": "p2", "milestone": "m2", "events": []},
        {"title": "t3", "purpose": "p3", "milestone": "m3", "events": []},
    ]}


def _protagonist_arc_design():
    return {
        "version": 1, "themes": [], "arcs": [{
            "id": "a1", "owner": "player",
            "starting_belief": "People leave, eventually.",
            "truth": "SECRET: she is the one who always leaves first.",
            "character_threads": [{
                "character": "player",
                "protective_strategy": "keeps everyone at arm's length",
                "blind_spot": "SECRET BLIND SPOT: she leaves before anyone else can.",
            }],
        }],
    }


def test_generate_arc_is_grounded_by_the_protagonists_situation_when_present():
    provider = _Provider(_arc_response())
    st = SimpleNamespace(premise="A quiet island story.", tone="wistful", cast=_cast("player"),
                        conditions=[], fields={"arc_design": _protagonist_arc_design()})
    generate_arc(provider, _ctx(), st, {}, "a slow-burn homecoming")
    assert provider.calls == 1
    prompt = provider.prompts[0]
    assert "People leave, eventually." in prompt
    assert "keeps everyone at arm's length" in prompt
    assert "SECRET" not in prompt  # never the raw truth/blind_spot, per the privacy boundary


def test_generate_arc_prompt_is_unaffected_without_a_protagonist_arc():
    provider = _Provider(_arc_response())
    st = SimpleNamespace(premise="A quiet island story.", tone="wistful", cast=_cast("player"), conditions=[])
    generate_arc(provider, _ctx(), st, {}, "a slow-burn homecoming")
    assert "PROTAGONIST'S SITUATION" not in provider.prompts[0]


def test_design_arc_is_grounded_by_the_protagonists_situation_when_present():
    provider = _Provider({"reply": "ok", "arc": _arc_response()})
    st = SimpleNamespace(premise="A quiet island story.", tone="wistful", cast=_cast("player"),
                        conditions=[], fields={"arc_design": _protagonist_arc_design()})
    design_arc(provider, _ctx(), st, [{"role": "user", "text": "let's plan it"}], None)
    prompt = provider.prompts[0]
    assert "People leave, eventually." in prompt
    assert "SECRET" not in prompt


def test_design_arc_prompt_is_unaffected_without_a_protagonist_arc():
    provider = _Provider({"reply": "ok", "arc": _arc_response()})
    st = SimpleNamespace(premise="A quiet island story.", tone="wistful", cast=_cast("player"), conditions=[])
    design_arc(provider, _ctx(), st, [{"role": "user", "text": "let's plan it"}], None)
    assert "PROTAGONIST'S SITUATION" not in provider.prompts[0]


if __name__ == "__main__":
    test_generate_arc_is_grounded_by_the_protagonists_situation_when_present()
    test_generate_arc_prompt_is_unaffected_without_a_protagonist_arc()
    test_design_arc_is_grounded_by_the_protagonists_situation_when_present()
    test_design_arc_prompt_is_unaffected_without_a_protagonist_arc()
    print("ok - macro arc grounded by the protagonist's situation, never the raw private fields")

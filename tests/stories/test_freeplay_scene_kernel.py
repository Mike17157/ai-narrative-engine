"""Regression checks for the free-play StoryMaster's theme/tone/roles wiring.

``_h_scene`` (runtime/director.py) requests theme/tone/roles from the same
scene-boundary call as goal/pressure/exit, then validates the result through
``dramatic_kernel.normalize_scene_kernel`` before it ever reaches
``world["scene_plan"]``. These checks cover that wiring, not the kernel
validator itself (see test_dramatic_kernel.py) or the selector that later
reads scene_plan (see test_live_beat.py).
"""
from __future__ import annotations

from types import SimpleNamespace

from loom.stories.runtime.director import StoryMaster


class _Provider:
    def __init__(self, data: dict):
        self.data = data
        self.calls = 0
        self.systems: list[str] = []

    def generate_text(self, **kwargs):
        self.calls += 1
        self.systems.append(kwargs.get("system", ""))
        return SimpleNamespace(text="", data=self.data)


def _cast(*keys):
    return [SimpleNamespace(character=k) for k in keys]


def _ctx():
    return SimpleNamespace(root=None, base_settings=SimpleNamespace(characters={}))


def test_scene_boundary_stores_a_valid_theme_tone_and_roles():
    world = {"step": 0}
    provider = _Provider({
        "goal": "get the two of them alone on the dock",
        "pressure": "the ferry bell will cut this short",
        "exit": "the bell rings",
        "theme": "a reunion neither of them is ready to name",
        "tone": "gentle, unfinished",
        "roles": [{"character": "shuri", "role": "guards her own hope with small talk"}],
    })
    st = SimpleNamespace(cast=_cast("player", "shuri"))
    sm = StoryMaster(_ctx(), st, world, provider=provider, location="Island dock")
    plan = sm.open_scene(loc_id="dock")
    assert plan["theme"] == "a reunion neither of them is ready to name"
    assert plan["tone"] == "gentle, unfinished"
    assert plan["roles"] == {"shuri": "guards her own hope with small talk"}
    # The private planning fields are a separate, already-trusted path — untouched.
    assert plan["goal"] == "get the two of them alone on the dock"


def test_a_role_naming_an_unknown_character_degrades_to_no_theme_at_all():
    world = {"step": 0}
    # The model hallucinated a character who isn't in the cast.
    provider = _Provider({
        "goal": "g", "pressure": "p", "exit": "e",
        "theme": "should not survive", "tone": "should not survive",
        "roles": [{"character": "a_character_who_does_not_exist", "role": "villain"}],
    })
    st = SimpleNamespace(cast=_cast("player", "shuri"))
    sm = StoryMaster(_ctx(), st, world, provider=provider, location="Island dock")
    plan = sm.open_scene(loc_id="dock")
    # Fails closed: an invalid roster reference blanks theme/tone/roles together
    # rather than silently keeping a role that names someone who doesn't exist.
    assert plan["theme"] == ""
    assert plan["tone"] == ""
    assert plan["roles"] == {}
    assert plan["goal"] == "g"  # unaffected


def _location(loc_id, history=""):
    return SimpleNamespace(id=loc_id, history=history)


def _bare_scene_response():
    return {"goal": "g", "pressure": "p", "exit": "e", "theme": "", "tone": "", "roles": []}


def test_scene_planner_is_grounded_by_location_history_and_protagonist_situation():
    world = {"step": 0}
    provider = _Provider(_bare_scene_response())
    st = SimpleNamespace(
        cast=_cast("player", "shuri"),
        locations=[_location("dock", history="The dock was rebuilt after the last storm took the old one.")],
        fields={"arc_design": {
            "version": 1, "themes": [], "arcs": [{
                "id": "a1", "owner": "player",
                "starting_belief": "People leave, eventually.",
                "truth": "SECRET: she is the one who always leaves first.",
                "character_threads": [{
                    "character": "player",
                    "protective_strategy": "keeps everyone at arm's length",
                    "blind_spot": "SECRET BLIND SPOT: she leaves before anyone else can.",
                    "visible_tell": "packs a bag before any trip is confirmed",
                }],
            }],
        }},
    )
    sm = StoryMaster(_ctx(), st, world, provider=provider, location="Island dock")
    sm.open_scene(loc_id="dock")

    assert provider.calls == 1
    system = provider.systems[0]
    assert "The dock was rebuilt after the last storm took the old one." in system
    assert "People leave, eventually." in system
    assert "keeps everyone at arm's length" in system
    assert "packs a bag before any trip is confirmed" in system
    # The privacy boundary this depends on: never the raw truth/blind_spot/recognition,
    # only arc_design.narrator_arc_surface's already-redacted fields.
    assert "SECRET" not in system


def test_scene_planner_prompt_is_unaffected_when_no_location_history_or_protagonist_arc():
    """Fail-open: a story with no locations/arc_design produces the same system
    prompt as before this feature existed — no regression for existing stories."""
    world = {"step": 0}
    provider = _Provider(_bare_scene_response())
    st = SimpleNamespace(cast=_cast("player", "shuri"))  # no .locations, no .fields at all
    sm = StoryMaster(_ctx(), st, world, provider=provider, location="Island dock")
    sm.open_scene(loc_id="dock")

    system = provider.systems[0]
    assert "LOCATION HISTORY" not in system
    assert "PROTAGONIST'S SITUATION" not in system


def test_no_provider_leaves_theme_tone_roles_empty():
    world = {"step": 0}
    st = SimpleNamespace(cast=_cast("player"))
    sm = StoryMaster(_ctx(), st, world, provider=None, location="Island dock")
    plan = sm.open_scene(loc_id="dock")
    assert plan == {
        "space": "dock", "loc": "Island dock", "opened": 0,
        "goal": "", "pressure": "", "exit": "", "theme": "", "tone": "", "roles": {},
    }


if __name__ == "__main__":
    test_scene_boundary_stores_a_valid_theme_tone_and_roles()
    test_a_role_naming_an_unknown_character_degrades_to_no_theme_at_all()
    test_scene_planner_is_grounded_by_location_history_and_protagonist_situation()
    test_scene_planner_prompt_is_unaffected_when_no_location_history_or_protagonist_arc()
    test_no_provider_leaves_theme_tone_roles_empty()
    print("ok - free-play scene theme/tone/roles wiring")

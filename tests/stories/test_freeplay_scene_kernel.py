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

    def generate_text(self, **_kwargs):
        self.calls += 1
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
    test_no_provider_leaves_theme_tone_roles_empty()
    print("ok - free-play scene theme/tone/roles wiring")

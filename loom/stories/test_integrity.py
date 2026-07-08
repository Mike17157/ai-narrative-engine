"""Self-check for the Story referential-integrity validator + the update_story_fields self-heal.

The validator (Story._check_references) catches dangling intra-story id-refs at every write
chokepoint. The self-heal (_self_heal_refs in context.py) repairs the common repairable cases
(start, cast.home, location.parent, connections) BEFORE validation, so a pre-existing stale ref
doesn't block unrelated edits. Genuinely unrepairable refs (relationship → deleted character) still
raise. Run: ``python -m loom.stories.test_integrity``.
"""
from __future__ import annotations

import copy

from loom.config.schema import Story


def _story():
    return {
        "name": "T", "type": "novel",
        "cast": [{"character": "eli", "primary": True, "home": "home"},
                 {"character": "mara", "primary": False}],
        "locations": [{"id": "home", "name": "Home", "parent": "village"},
                      {"id": "village", "name": "Village", "parent": ""}],
        "relationships": [{"id": "r1", "source": "eli", "target": "mara"}],
        "conditions": [{"id": "c1", "name": "Flood"}],
        "arcs": [{"id": "a1", "name": "A1", "owner": "eli", "cast": ["eli", "mara"],
                  "pressures": ["r1"], "conditions": ["c1"]}],
        "scenes": [],  # VN scene harnesses (not the place-kind Scene)
    }


def test_valid_story_passes():
    Story(**_story())   # no raise


def test_dangling_cast_home_rejected():
    d = copy.deepcopy(_story())
    d["cast"][0]["home"] = "nowhere"
    try:
        Story(**d); raise AssertionError("should raise")
    except Exception as e:
        assert "dangling" in str(e) and "home" in str(e)


def test_dangling_location_parent_rejected():
    d = copy.deepcopy(_story())
    d["locations"][0]["parent"] = "ghost"
    try:
        Story(**d); raise AssertionError("should raise")
    except Exception as e:
        assert "parent" in str(e)


def test_dangling_relationship_source_rejected():
    d = copy.deepcopy(_story())
    d["relationships"].append({"id": "r2", "source": "ghost", "target": "eli"})
    try:
        Story(**d); raise AssertionError("should raise")
    except Exception as e:
        assert "r2" in str(e) and "source" in str(e)


def test_dangling_arc_pressure_rejected():
    d = copy.deepcopy(_story())
    d["arcs"][0]["pressures"] = ["nonexistent_rel"]
    try:
        Story(**d); raise AssertionError("should raise")
    except Exception as e:
        assert "pressures" in str(e)


def test_dangling_arc_condition_rejected():
    d = copy.deepcopy(_story())
    d["arcs"][0]["conditions"] = ["nonexistent_cond"]
    try:
        Story(**d); raise AssertionError("should raise")
    except Exception as e:
        assert "conditions" in str(e)


def test_dangling_arc_owner_rejected():
    d = copy.deepcopy(_story())
    d["arcs"][0]["owner"] = "ghost"
    try:
        Story(**d); raise AssertionError("should raise")
    except Exception as e:
        assert "owner" in str(e)


def test_multiple_dangling_refs_collected_in_one_message():
    d = copy.deepcopy(_story())
    d["cast"][0]["home"] = "nowhere"
    d["locations"][0]["parent"] = "ghost"
    d["relationships"].append({"id": "r2", "source": "ghost", "target": "eli"})
    try:
        Story(**d); raise AssertionError("should raise")
    except Exception as e:
        msg = str(e)
        # all three should be reported (up to the cap of 8 shown)
        assert msg.count("dangling") >= 1
        assert "3 dangling" in msg, msg


def test_self_heal_repairs_repairable_refs():
    from loom.server.context import _self_heal_refs
    d = {
        "start": "deleted", "start_scene": "deleted_scene",
        "cast": [{"character": "eli", "home": "deleted"}],
        "locations": [{"id": "real", "parent": "deleted"}],
        "connections": [{"id": "c1", "source": "deleted", "target": "real"}],
        "scenes": [],
    }
    _self_heal_refs(d)
    assert d["start"] is None
    assert d["start_scene"] == ""
    assert d["cast"][0]["home"] == ""
    assert d["locations"][0]["parent"] == ""
    assert d["connections"][0]["source"] == ""


def test_self_heal_leaves_valid_refs_alone():
    from loom.server.context import _self_heal_refs
    d = {
        "start": "real",
        "cast": [{"character": "eli", "home": "real"}],
        "locations": [{"id": "real", "parent": ""}],
        "connections": [{"id": "c1", "source": "real", "target": "real"}],
        "scenes": [],
    }
    before = copy.deepcopy(d)
    _self_heal_refs(d)
    assert d == before   # nothing touched


def test_scene_on_location_anchored_to_cast_key():
    # A scene's character must be a cast key
    d = copy.deepcopy(_story())
    d["locations"][0]["scenes"] = [{"id": "home_eli", "character": "eli"}]
    Story(**d)   # passes
    d["locations"][0]["scenes"] = [{"id": "home_ghost", "character": "ghost"}]
    try:
        Story(**d); raise AssertionError("should raise")
    except Exception as e:
        assert "scene" in str(e)


if __name__ == "__main__":
    g = dict(globals())
    for name, fn in sorted(g.items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  · {name}")
    print("ok — integrity validator + self-heal: dangling refs rejected, repairable refs healed")

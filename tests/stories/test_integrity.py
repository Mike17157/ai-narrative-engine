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


def test_story_reference_errors_flags_unknown_cast():
    from loom.config.schema import story_reference_errors
    story = {"cast": [{"character": "eli"}, {"character": "bram"}]}
    assert story_reference_errors(story, {"eli"}) == ["casts unknown character 'bram'"]
    assert story_reference_errors(story, {"eli", "bram"}) == []   # both known → clean


def test_self_heal_drops_phantom_cast_and_cascades():
    # A section edit invented a cast member ('bram') with no character record, and bonded it. The
    # heal must drop the phantom AND its bond, while a valid co-edit (the eli↔mara bond) survives —
    # so one bad op no longer nukes the whole turn, and the result stays Story-valid.
    from loom.server.context import _self_heal_refs
    from loom.config.schema import Story
    d = {
        "name": "T", "type": "novel",
        "cast": [{"character": "eli"}, {"character": "mara"}, {"character": "bram"}],
        "relationships": [{"id": "r1", "source": "eli", "target": "mara"},
                          {"id": "r2", "source": "eli", "target": "bram"}],
        "arcs": [{"id": "a1", "name": "A", "owner": "bram", "cast": ["eli", "bram"]}],
        "locations": [{"id": "home", "name": "Home",
                       "scenes": [{"id": "s1", "character": "bram", "characters": ["eli", "bram"]}]}],
        "scenes": [],
    }
    repairs = _self_heal_refs(d, {"eli", "mara"})
    assert repairs and "bram" in repairs[0]
    assert [m["character"] for m in d["cast"]] == ["eli", "mara"]          # phantom dropped
    assert [r["id"] for r in d["relationships"]] == ["r1"]                 # bad bond gone, good survives
    assert d["arcs"][0]["cast"] == ["eli"] and d["arcs"][0]["owner"] == ""  # arc refs cascaded
    assert d["locations"][0]["scenes"][0]["character"] is None
    assert d["locations"][0]["scenes"][0]["characters"] == ["eli"]
    Story(**d)   # the healed story is valid — no raise


def test_self_heal_without_known_chars_leaves_cast_alone():
    # Omitting known_chars keeps the old location-only behaviour (never touches cast).
    from loom.server.context import _self_heal_refs
    d = {"cast": [{"character": "whoever"}], "locations": [], "scenes": []}
    assert _self_heal_refs(d) == []
    assert [m["character"] for m in d["cast"]] == ["whoever"]


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

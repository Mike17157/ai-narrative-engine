"""Self-check for relationship-first genesis — the referential-integrity boundary.

The one bug that silently corrupts a story is a dangling id reference (an edge or candidate
pointing at a harness that was re-rolled away). These asserts prove every step drops bad
refs. Run: `python -m loom.stories.test_genesis` (or pytest). Stub provider, no network.
"""
from __future__ import annotations

from loom.stories.world.creation import (
    compose_world, derive_stories, design_harnesses, name_cast, persona_from_harness,
    project_web, scene_cast, weave_relationships, world_full_brief,
)


class _Res:
    def __init__(self, data):
        self.data = data


class _Stub:
    """Returns deliberately DIRTY data (self-edge, dangling id, dup, bad protagonist) so the
    guards have something to catch. Branches on which schema the caller asked to emit."""

    def generate_text(self, system, prompt, emits):
        props = emits.get("properties") or {}
        if "harnesses" in props:
            return _Res({"harnesses": [
                {"role": "the one who left", "want": "sell the house", "lie": "she owes no one", "wound": "left young", "secret": "she's broke"},
                {"role": "the one who stayed", "want": "keep the house", "lie": "loyalty is love", "wound": "abandoned", "secret": "forged the will"},
                {"role": "the creditor", "want": "the debt paid", "lie": "money is fairness", "wound": "unrequited", "secret": "loved their mother"},
            ]})
        if "relationships" in props:
            return _Res({"relationships": [
                {"source": "h0", "target": "h1", "nature": "sibling", "dynamic": "an old grudge that has festered for years", "stance": "strained", "note": "h1 forged the will"},
                {"source": "h1", "target": "h2", "nature": "debtor", "dynamic": "secret debt", "stance": "hostile", "note": "owes more than he admits"},
                {"source": "h0", "target": "h2", "nature": "ally", "dynamic": "wary", "stance": "warm", "note": "just met"},
                {"source": "h0", "target": "h0", "nature": "self", "dynamic": "x", "stance": "neutral", "note": ""},     # self → drop
                {"source": "h0", "target": "h99", "nature": "ghost", "dynamic": "y", "stance": "warm", "note": ""},      # dangling → drop
                {"source": "h0", "target": "h1", "nature": "dup", "dynamic": "z", "stance": "warm", "note": ""},         # dup → drop
            ]})
        if "candidates" in props:
            return _Res({"candidates": [
                {"title": "The Forged Will", "dramatic_question": "Will the house survive the siblings?",
                 "logline": "two siblings, one house", "premise": "...", "protagonist": "h1",
                 "anchors": ["h0", "h2"], "stakes": "the home", "intended_ending": "the truth surfaces",
                 "tone": "tense", "themes": ["family"], "opening_edge": {"source": "h0", "target": "h1"}},
                {"title": "Bad refs", "dramatic_question": "?", "logline": "", "premise": "",
                 "protagonist": "h99", "anchors": ["h99", "h2"], "stakes": "", "intended_ending": "",
                 "tone": "", "themes": [], "opening_edge": {"source": "h0", "target": "h42"}},
            ]})
        if "names" in props:
            return _Res({"names": [
                {"id": "h0", "name": "Mara", "appearance": "long dark hair, tired eyes"},
                {"id": "h1", "name": "Theo", "appearance": ""},
            ]})  # h2 omitted on purpose → caller must fall back to role
        return _Res({})


def main() -> None:
    stub = _Stub()

    # 1) design — stable ids, n cap, provider guard
    hs = design_harnesses(stub, "a sold family house", n=3)
    assert [h["id"] for h in hs] == ["h0", "h1", "h2"], hs
    assert all(h["role"] and h["want"] for h in hs)
    assert len(design_harnesses(stub, n=2)) == 2          # caps to n even though stub returns 3
    assert design_harnesses(None) == []
    ids = {h["id"] for h in hs}

    # 2) weave — self / dangling / dup edges dropped
    edges = weave_relationships(stub, hs)
    assert len(edges) == 3, edges                          # 3 valid of 6 returned
    assert all(e["source"] in ids and e["target"] in ids for e in edges)
    assert all(e["source"] != e["target"] for e in edges)
    assert all(e["stance"] in ("devoted", "warm", "neutral", "strained", "hostile") for e in edges)
    assert weave_relationships(stub, hs[:1]) == []         # need >= 2

    # 3) derive — protagonist valid + inside anchors; bad opening_edge → None
    cands = derive_stories(stub, hs, edges)
    for c in cands:
        assert c["protagonist"] in ids
        assert c["protagonist"] in c["anchors"]
        assert all(a in ids for a in c["anchors"])
        oe = c["opening_edge"]
        assert oe is None or (oe["source"] in ids and oe["target"] in ids)
    assert "h1" in cands[0]["anchors"]                     # protagonist forced into anchors
    assert cands[0]["opening_edge"] == {"source": "h0", "target": "h1"}
    assert cands[1]["protagonist"] == "h2"                 # invalid h99 replaced by an anchor
    assert cands[1]["opening_edge"] is None                # h42 invalid → dropped

    # 4) scene_cast — tension-weighted, focal first, distance-2, isolated focal
    assert scene_cast(edges, "h1", 2) == ["h1", "h2"]      # h2 hostile(3) beats h0 strained(2)
    assert scene_cast(edges, "h1", 3) == ["h1", "h2", "h0"]
    assert scene_cast(edges, "h0", 1)[0] == "h0"
    assert scene_cast([], "h0", 3) == ["h0"]               # isolated → just itself
    chain = [{"source": "h0", "target": "ha", "stance": "warm"},
             {"source": "ha", "target": "hb", "stance": "hostile"}]
    assert scene_cast(chain, "h0", 3) == ["h0", "ha", "hb"]  # reaches hb only via ha (distance-2)

    # 5) naming + persona seed
    names = name_cast(stub, hs)
    assert names["h0"]["name"] == "Mara"
    assert "h2" not in names                               # model skipped it → caller falls back
    p = persona_from_harness(hs[0])
    assert p and hs[0]["want"] in p

    # 6) project_web — only edges touching the focus set, grouped perspectivally
    web = [{"source": "a", "target": "b", "nature": "rival", "dynamic": "grudge", "stance": "hostile"},
           {"source": "b", "target": "c", "nature": "ally", "dynamic": "", "stance": "warm"}]
    p = project_web(web, {"a"})
    assert set(p) == {"a"}                                  # only a in focus → b-c edge excluded
    assert p["a"][0]["other"] == "b" and p["a"][0]["outward"] is True
    assert project_web(web, {"z"}) == {}                    # nobody in focus → nothing leaks
    p2 = project_web([{"source": "a", "target": "b", "stance": "warm"}], {"a", "b"})
    assert "a" in p2 and "b" in p2 and p2["b"][0]["outward"] is False   # both perspectives

    # 7) compose_world — fold the genesis draft into the ONE persisted world; premise distils from it
    w = compose_world(
        {"genre": "low fantasy", "pressure": "the wood is dying", "forces": [{"name": "Rootward", "stance": "let it die"}]},
        {"place": "Thornwick", "preoccupation": "inherited debt",
         "traditions": [{"name": "Red-Ledger", "logic": "blood pays"}], "people": [{"name": "Maris", "life": "reads the rain-pan"}]},
        [{"kind": "object", "text": "a root-chain in the undercroft"}])
    assert w["pressure"] == "the wood is dying" and w["place"] == "Thornwick", w
    assert w["forces"][0]["name"] == "Rootward" and w["traditions"][0]["name"] == "Red-Ledger", w
    assert w["fragments"][0]["text"].startswith("a root-chain") and "situation" not in w, w   # empties dropped
    assert compose_world({}, {}, []) == {}                       # a thin world stays thin
    fb = world_full_brief(w)
    assert "PRESSURE" in fb and "Rootward" in fb and "Red-Ledger" in fb and "root-chain" in fb, fb
    assert world_full_brief({}) == ""                            # empty world → nothing to distil from

    print("ok — genesis integrity asserts passed")


if __name__ == "__main__":
    main()

from loom.stories.records.network import build_network, scene_context


def _story():
    return {"name": "A House in Winter", "premise": "Two siblings return home.",
            "cast": [{"character": "mara", "primary": True, "home": "house"}, {"character": "theo", "home": "house"}, {"character": "ian"}],
            "locations": [{"id": "house", "name": "The House", "description": "cold rooms"}],
            "relationships": [{"id": "r1", "source": "mara", "target": "theo", "nature": "siblings", "dynamic": "old resentment", "stance": "strained"}, {"id": "r2", "source": "ian", "target": "theo", "nature": "creditor", "dynamic": "patient pressure", "stance": "hostile"}],
            "arcs": [{"id": "a1", "name": "The Will", "cast": ["mara", "theo"], "pressures": ["r1"]}]}


def test_network_keeps_authored_and_runtime_cards_distinct():
    net = build_network(_story(), {"entities": {"mara": {"location": "house", "mood": "wary"}}, "details": [{"text": "Mara carries the brass key", "name": "mara", "step": 4}], "promises": [{"setup": "Theo will open the attic", "status": "open", "step": 2}]})
    kinds = {n["kind"] for n in net["nodes"]}
    assert {"story", "character", "location", "arc", "state", "fact", "promise"} <= kinds
    assert not any(n["kind"] == "cross_character" for n in net["nodes"])
    assert any(e["kind"] == "pressures" and e["link"] == "edge:r1" for e in net["edges"])


def test_scene_query_is_local_and_ranks_runtime_evidence():
    net = build_network(_story(), {"details": [{"text": "Mara carries the brass key", "name": "mara", "step": 4}, {"text": "Ian keeps a ledger", "name": "ian", "step": 5}], "promises": [{"setup": "Theo will open the attic", "status": "open", "step": 2}]})
    packet = scene_context(net, location="house", present=["mara", "theo"], query="brass key attic")
    assert packet["location"]["title"] == "The House"
    assert [r["id"] for r in packet["links"]] == ["edge:r1"]
    assert [a["key"] for a in packet["arcs"]] == ["a1"]
    assert packet["facts"][0]["text"] == "Mara carries the brass key"
    assert packet["open_promises"][0]["text"] == "Theo will open the attic"


def test_scene_query_includes_evidence_backed_residuals():
    net = build_network(_story(), {"residuals": [{"id": "res-1", "subject": "mara", "predicate": "carries", "object": "brass key", "evidence": [4], "status": "observed", "active": True}]})
    packet = scene_context(net, location="house", present=["mara"], query="brass key")
    assert packet["residuals"] == [{"id": "residual:res-1", "kind": "residual", "key": "res-1", "subject": "mara", "predicate": "carries", "object": "brass key", "evidence": [4], "status": "observed", "requires_source": False}]

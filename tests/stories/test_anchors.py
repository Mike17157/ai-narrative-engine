"""Self-check for the hashline (hash-anchored) edit engine — the OhMyPi-style surgical
update protocol for the story section editor.

The contract that matters: a stale anchor must never silently clobber a field edited
elsewhere, while a valid op in the same batch still applies; `before` must snapshot
the whole top-level field so the client's Undo restores it. Run:
`python -m loom.stories.test_anchors` (or pytest).
"""
from __future__ import annotations

import copy
import json

from loom.stories.records.anchors import (
    anchored_view, apply_ops, node_hash, resolve_path,
    any_stale_rejections, is_stale_rejection, merge_results,
)


def _story():
    return {
        "premise": "the famine",
        "tone": "",
        "premise_parts": {"root": "the famine", "question": "who eats?"},
        "arcs": [
            {"id": "arc-1", "name": "Old", "premise": "p1"},
            {"id": "arc-2", "name": "Lanterns", "premise": "p2", "cast": ["a"]},
        ],
        "locations": [{"id": "home", "name": "Home", "description": "cozy"}],
    }


def test_node_hash_is_deterministic_and_order_independent():
    assert node_hash("x") == node_hash("x")
    assert node_hash({"a": 1, "b": 2}) == node_hash({"b": 2, "a": 1})
    assert len(node_hash("x")) == 8
    assert node_hash("a") != node_hash("b")


def test_resolve_path_covers_all_granularities():
    s = _story()
    assert resolve_path(s, "premise") == "the famine"
    assert resolve_path(s, "premise_parts/root") == "the famine"
    assert resolve_path(s, "arcs/arc-2") == s["arcs"][1]
    assert resolve_path(s, "arcs/arc-2/name") == "Lanterns"
    assert resolve_path(s, "locations/home/description") == "cozy"


def test_resolve_path_raises_for_missing_node():
    for bad in ("", "nope", "arcs/missing", "arcs/arc-2/nope"):
        try:
            resolve_path(_story(), bad)
            raise AssertionError(f"{bad!r} should have raised")
        except KeyError:
            pass


def test_anchored_view_renders_paths_and_hashes():
    view = anchored_view(_story(), ("premise", "premise_parts", "arcs", "locations"))
    assert "premise " in view
    assert "arcs/arc-2 " in view
    assert "arcs/arc-2/name" in view
    assert "locations/home/description" in view
    assert "#" in view  # anchors present


def test_anchored_view_handles_empty_section():
    assert "empty" in anchored_view({}, ("premise", "tone"))


def test_set_on_scalar_field_snapshots_before():
    d = copy.deepcopy(_story())
    r = apply_ops(d, [{"path": "premise", "anchor": node_hash("the famine"),
                       "op": "set", "value": "the drought"}])
    assert r["applied"] == [{"path": "premise", "op": "set"}]
    assert d["premise"] == "the drought"
    assert r["before"]["premise"] == "the famine"


def test_set_on_list_item_subfield_leaves_siblings_intact():
    d = copy.deepcopy(_story())
    r = apply_ops(d, [{"path": "arcs/arc-2/premise",
                       "anchor": node_hash("p2"), "op": "set", "value": "new"}])
    assert d["arcs"][1]["premise"] == "new"
    assert d["arcs"][1]["name"] == "Lanterns"     # untouched sibling field
    assert d["arcs"][0] == _story()["arcs"][0]    # untouched sibling item
    assert "arcs" in r["before"]


def test_merge_on_premise_parts_updates_one_key_keeps_others():
    d = copy.deepcopy(_story())
    apply_ops(d, [{"path": "premise_parts",
                   "anchor": node_hash(_story()["premise_parts"]),
                   "op": "merge", "value": {"root": "the deep snow"}}])
    assert d["premise_parts"]["root"] == "the deep snow"
    assert d["premise_parts"]["question"] == "who eats?"


def test_remove_list_item_by_id():
    d = copy.deepcopy(_story())
    apply_ops(d, [{"path": "arcs/arc-1", "anchor": node_hash(_story()["arcs"][0]),
                   "op": "remove"}])
    assert [a["id"] for a in d["arcs"]] == ["arc-2"]


def test_stale_anchor_rejected_while_valid_op_in_same_batch_applies():
    d = copy.deepcopy(_story())
    r = apply_ops(d, [
        {"path": "premise", "anchor": "deadbeef", "op": "set", "value": "stale"},
        {"path": "tone", "op": "set", "value": "wry"},   # no anchor → create
    ])
    assert d["premise"] == "the famine"               # stale NOT applied
    assert d["tone"] == "wry"                          # valid op DID apply
    assert len(r["rejected"]) == 1
    assert r["rejected"][0]["path"] == "premise"
    assert "stale" in r["rejected"][0]["reason"]
    assert r["applied"] == [{"path": "tone", "op": "set"}]


def test_empty_anchor_set_creates_new_dict_key():
    d = copy.deepcopy(_story())
    apply_ops(d, [{"path": "premise_parts/stakes", "op": "set", "value": "everyone"}])
    assert d["premise_parts"]["stakes"] == "everyone"


def test_empty_anchor_set_creates_new_top_field():
    d = copy.deepcopy(_story())
    apply_ops(d, [{"path": "tone", "op": "set", "value": "wry"}])
    assert d["tone"] == "wry"


def test_bad_op_rejected_not_applied():
    d = copy.deepcopy(_story())
    r = apply_ops(d, [{"path": "premise", "op": "frobnicate", "value": "x"}])
    assert not r["applied"]
    assert len(r["rejected"]) == 1
    assert d["premise"] == "the famine"


def test_merge_rejected_on_non_object_value():
    d = copy.deepcopy(_story())
    r = apply_ops(d, [{"path": "premise_parts", "anchor": node_hash(_story()["premise_parts"]),
                       "op": "merge", "value": "not an object"}])
    assert not r["applied"] and len(r["rejected"]) == 1


def test_removed_node_anchor_then_seen_as_gone():
    # An op that targets a now-deleted node (with an anchor) is rejected as gone,
    # not silently re-created.
    d = copy.deepcopy(_story())
    apply_ops(d, [{"path": "arcs/arc-1", "anchor": node_hash(_story()["arcs"][0]), "op": "remove"}])
    r = apply_ops(d, [{"path": "arcs/arc-1/name", "anchor": node_hash("p1"), "op": "set", "value": "x"}])
    assert not r["applied"] and r["rejected"]


# ── stale-anchor recovery helpers ─────────────────────────────────────────────

def test_is_stale_rejection_classifies_drift_vs_gone_vs_bad():
    drift = {"path": "premise", "reason": "stale — node changed since you read it",
             "current_hash": "abc12345"}
    gone = {"path": "premise", "reason": "node not found (deleted?)"}
    bad = {"path": "premise", "reason": "merge target isn't an object"}
    assert is_stale_rejection(drift) is True
    assert is_stale_rejection(gone) is False     # deleted → not recoverable
    assert is_stale_rejection(bad) is False      # structural → not recoverable
    assert any_stale_rejections([gone, bad]) is False
    assert any_stale_rejections([drift, bad]) is True


def test_merge_results_retry_resolves_path_drops_from_rejected():
    prior = {"applied": [{"path": "tone", "op": "set"}],
             "rejected": [{"path": "premise", "reason": "stale — node changed since you read it",
                           "current_hash": "abc12345"}],
             "before": {"premise": "the famine"}}
    retry = {"applied": [{"path": "premise", "op": "set"}],   # retry applied it
             "rejected": [],
             "before": {"premise": "the drought"}}            # post-prior value
    m = merge_results(prior, retry)
    assert m["applied"] == [{"path": "tone", "op": "set"}, {"path": "premise", "op": "set"}]
    assert m["rejected"] == []                                # premise resolved → gone
    assert m["before"]["premise"] == "the famine"             # EARLIEST snapshot wins


def test_merge_results_retry_fails_again_carries_freshest_rejection():
    prior = {"applied": [{"path": "tone", "op": "set"}],
             "rejected": [{"path": "premise", "reason": "stale — node changed since you read it",
                           "current_hash": "abc12345"}],
             "before": {"premise": "the famine"}}
    retry = {"applied": [],
             "rejected": [{"path": "premise", "reason": "stale — node changed since you read it",
                           "current_hash": "def67890"}],     # still stale, new hash
             "before": {}}
    m = merge_results(prior, retry)
    # Applied accumulates the first-pass op; premise rejected once (retry's freshest verdict)
    assert m["applied"] == [{"path": "tone", "op": "set"}]
    assert len(m["rejected"]) == 1
    assert m["rejected"][0]["current_hash"] == "def67890"     # freshest wins, no dup
    assert m["before"]["premise"] == "the famine"


def test_merge_results_untouched_prior_rejection_is_carried():
    prior = {"applied": [],
             "rejected": [{"path": "premise", "reason": "merge target isn't an object"}],
             "before": {}}
    retry = {"applied": [{"path": "tone", "op": "set"}],
             "rejected": [],
             "before": {"tone": ""}}
    m = merge_results(prior, retry)
    # The structural failure (not retried) is carried alongside the retry's success
    assert m["applied"] == [{"path": "tone", "op": "set"}]
    assert m["rejected"] == [{"path": "premise", "reason": "merge target isn't an object"}]


def test_merge_results_before_earliest_snapshot_wins_across_fields():
    # prior saw premise pre-turn; retry touches a NEW field (arcs). before should carry
    # prior's premise snapshot AND retry's arcs snapshot — each field's earliest view.
    prior = {"applied": [], "rejected": [],
             "before": {"premise": "the famine"}}
    retry = {"applied": [{"path": "arcs/arc-1", "op": "remove"}],
             "rejected": [],
             "before": {"arcs": [{"id": "arc-1"}]}}
    m = merge_results(prior, retry)
    assert m["before"] == {"premise": "the famine", "arcs": [{"id": "arc-1"}]}


if __name__ == "__main__":
    # Run every test_* above; fail fast on the first assertion.
    g = dict(globals())
    for name, fn in sorted(g.items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  · {name}")
    print("ok — hashline anchors: hash, view, resolve, apply (set/merge/remove + stale rejection)")

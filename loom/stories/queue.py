"""The story WORK QUEUE — what to do next, in the card's layer order.

Generation workloads that need a human decision (weave-bonds, setting-stage proposals)
PERSIST their output as pending approvals on the story (`fields.pending[kind] = {items}`),
so a user can kick work off, navigate away, and come back to review — nothing is lost with
the page. The queue derives ONE ordered list over the card: for each layer (overview → cast),
pending approvals first, then the layer's own todos. It is advisory, never locking — every
item deep-links to its tab; the order just says what the pipeline wants next.
"""
from __future__ import annotations

from .card import LAYERS

# Approval kinds → the layer whose tab reviews them. Adding a reviewable workload = one row
# here + the generator persisting `fields.pending[kind]` (see router: weave-bonds, conditions).
PENDING_KINDS: dict[str, dict] = {
    "arc":        {"layer": "plot",          "label": "arc draft in design"},
    "bonds":      {"layer": "relationships", "label": "bond proposals to review"},
    "conditions": {"layer": "map",           "label": "setting stages to review"},
}


def set_pending(fields: dict, kind: str, items: list) -> dict:
    """Set (or clear, when items is empty) one kind's pending approvals on a story's
    `fields` dict. Returns the updated fields (mutated in place for convenience)."""
    if kind not in PENDING_KINDS:
        raise KeyError(kind)
    pending = dict(fields.get("pending") or {})
    if items:
        pending[kind] = {"items": list(items)}
    else:
        pending.pop(kind, None)
    fields["pending"] = pending
    return fields


def build_queue(card: dict, pending: dict | None) -> list[dict]:
    """Derive the ordered work queue from a built card + the story's pending approvals.
    Items: {order, id, layer, type: approve|todo, label, count?} — approvals first within
    their layer, layers in pipeline order."""
    pending = pending or {}
    by_layer = {l["id"]: l for l in (card.get("layers") or [])}
    items: list[dict] = []
    for spec in LAYERS:
        lid, tier = spec["id"], spec.get("tier", "")
        for kind, meta in PENDING_KINDS.items():
            if meta["layer"] != lid:
                continue
            n = len((pending.get(kind) or {}).get("items") or [])
            if n:
                items.append({"id": f"approve:{kind}", "layer": lid, "tier": tier, "type": "approve",
                              "label": f"{n} {meta['label']}", "count": n})
        for i, t in enumerate((by_layer.get(lid) or {}).get("todo") or []):
            items.append({"id": f"todo:{lid}:{i}", "layer": lid, "tier": tier, "type": "todo", "label": t})
    for i, it in enumerate(items):
        it["order"] = i + 1
    return items


if __name__ == "__main__":   # ponytail: one runnable check — ordering + approvals-first + clear
    card = {"layers": [
        {"id": "overview", "todo": ["write a premise"]},
        {"id": "plot", "todo": []},
        {"id": "relationships", "todo": ["no bonds yet: a"]},
        {"id": "map", "todo": []},
        {"id": "cast", "todo": ["a: no wardrobe yet"]},
    ]}
    fields: dict = {}
    set_pending(fields, "bonds", [{"id": "r1"}, {"id": "r2"}])
    q = build_queue(card, fields["pending"])
    ids = [i["id"] for i in q]
    assert ids == ["todo:overview:0", "approve:bonds", "todo:relationships:0", "todo:cast:0"], ids
    assert [i["order"] for i in q] == [1, 2, 3, 4]
    assert q[1]["count"] == 2 and q[1]["type"] == "approve"
    set_pending(fields, "bonds", [])
    assert build_queue(card, fields["pending"])[1]["id"] == "todo:relationships:0"
    try:
        set_pending(fields, "nope", [1])
        raise AssertionError("unknown kind must raise")
    except KeyError:
        pass
    print("ok — queue order, approvals-first, pending set/clear")

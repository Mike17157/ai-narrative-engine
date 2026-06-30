"""Self-check for the living-web consolidation (GENESIS.md §7) — restructure ops + the
co-presence invariant. Pure helper, no provider/ctx. Run: `python -m loom.stories.test_consolidate`.
"""
from __future__ import annotations

from loom.stories.stage_tools import apply_rel_changes


def _idgen():
    n = {"i": 0}
    def f():
        n["i"] += 1
        return f"r{n['i']}"
    return f


def main() -> None:
    bykey = {"aria": "aria", "theo": "theo", "mara": "mara"}
    present = {"aria", "theo"}                       # mara is NOT in the scene

    # create — both co-present → a new bond forms
    rels, ch, blk = apply_rel_changes(
        [], {}, "aria",
        [{"toward": "Theo", "nature": "rival", "dynamic": "fresh grudge", "stance": "strained", "retire": False}],
        bykey, present, _idgen())
    assert len(rels) == 1 and not blk
    assert rels[0]["source"] == "aria" and rels[0]["target"] == "theo"
    assert rels[0]["nature"] == "rival" and rels[0]["stance"] == "strained"

    # CO-PRESENCE INVARIANT — aria→mara dropped (mara absent from the scene)
    rels, ch, blk = apply_rel_changes(
        [], {}, "aria",
        [{"toward": "Mara", "nature": "", "dynamic": "a pull", "stance": "warm", "retire": False}],
        bykey, present, _idgen())
    assert rels == [] and len(blk) == 1 and blk[0]["target"] == "mara"

    # flip nature on an existing edge (ally → rival)
    e = {"id": "r0", "source": "aria", "target": "theo", "nature": "ally", "dynamic": "warm", "stance": "warm"}
    rels, ch, blk = apply_rel_changes(
        [e], {("aria", "theo"): e}, "aria",
        [{"toward": "Theo", "nature": "rival", "dynamic": "", "stance": "hostile", "retire": False}],
        bykey, present, _idgen())
    assert e["nature"] == "rival" and e["stance"] == "hostile"

    # retire — sever an existing edge
    e = {"id": "r0", "source": "aria", "target": "theo", "nature": "ally", "dynamic": "", "stance": "warm"}
    rels, ch, blk = apply_rel_changes(
        [e], {("aria", "theo"): e}, "aria",
        [{"toward": "Theo", "nature": "", "dynamic": "", "stance": "neutral", "retire": True}],
        bykey, present, _idgen())
    assert rels == [] and ch and ch[0].get("retired") is True

    # retire is ALSO gated — can't sever a bond toward an absent character
    e = {"id": "r0", "source": "aria", "target": "mara", "nature": "ally", "dynamic": "", "stance": "warm"}
    rels, ch, blk = apply_rel_changes(
        [e], {("aria", "mara"): e}, "aria",
        [{"toward": "Mara", "nature": "", "dynamic": "", "stance": "neutral", "retire": True}],
        bykey, present, _idgen())
    assert rels == [e] and len(blk) == 1

    print("ok — consolidation restructure + co-presence asserts passed")


if __name__ == "__main__":
    main()

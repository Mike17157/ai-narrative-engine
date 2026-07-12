"""Hashline edits for the story section editor — the OhMyPi hash-anchored pattern,
adapted to JSON nodes.

The section collaborator (`POST /api/stories/{key}/card/{layer}/chat`) shows the
model a flat, ANCHORED view of the editable nodes:

    premise                       #a1b2c3d4  "In the city of…"
    premise_parts/root            #1a2b3c4d  "the famine that…"
    arcs/arc-2                    #9a8b7c6d  {…whole item…}
    arcs/arc-2/premise            #0f1e2d3c  "…"
    arcs/arc-2/name               #ab12cd34  "Paper Lanterns"

Each node carries a short content hash. The model edits by returning `ops`, each
pointing at a node by slash-path AND citing the #hash it saw there:

    {"path": "arcs/arc-2/premise", "anchor": "0f1e2d3c", "op": "set", "value": "…"}

`apply_ops` re-reads the live story, recomputes each node's hash, and REJECTS the op
if the node has drifted since the model saw it — a stale read can no longer silently
clobber a field edited elsewhere. The whole-Story validator on write is still the
structural safety net; anchors prevent *logically* stale writes.

This module is PURE (no provider, no IO) so the resolution/staleness/apply logic is
fully unit-testable without an LLM.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# How many addressable nodes to surface in one anchored view. Past this the model
# still gets the top-level fields, but deep per-item subfields are summarized. Keeps
# the prompt bounded for big bibles without hiding the editable shape.
_MAX_NODES = 60
# blake2b digest size in bytes → 8 hex chars (4.3B space; plenty per story).
_HASH_DIGEST = 4

# Which key names identify an item inside each list. Almost every story list uses
# `id`, EXCEPT `cast`, whose items are identified by `character` (the key into the
# characters registry). Without this the resolver can't target a cast member — the
# one real-world wrinkle the test fixtures didn't cover.
_ID_KEYS = ("id", "character", "key")


def _item_id(item: dict) -> str | None:
    """The identity value of a list item — the first of the known id-keys it has."""
    for k in _ID_KEYS:
        v = item.get(k)
        if v:
            return str(v)
    return None


def node_hash(value: Any) -> str:
    """A short, stable content hash for any JSON-serializable value.

    Deterministic across runs: keys are sorted so dict key order can't move the hash,
    and non-ASCII is preserved (`ensure_ascii=False`) so equivalent content collides
    regardless of escaping. `default=str` keeps odd types (None, datetimes) from
    raising instead of being meaningful — but our nodes are JSON-native."""
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=_HASH_DIGEST).hexdigest()


# ── Path resolution ────────────────────────────────────────────────────────── #
# A path is slash-joined segments. Each segment is either a dict key or, when the
# current node is a list, an item's `id`. So:
#   premise                          → top-level field
#   premise_parts/root               → dict key under premise_parts
#   arcs/arc-2                       → the arc whose id == "arc-2"
#   arcs/arc-2/premise               → that arc's premise field
#   locations/home/description       → location "home"'s description

def resolve_path(data: dict, path: str) -> Any:
    """Resolve a slash-path to the node it names. Raises KeyError for a missing
    segment (the caller decides whether that's a stale anchor or a valid NEW node
    by checking whether the op carried an anchor)."""
    segs = [s for s in (path or "").split("/") if s]
    if not segs:
        raise KeyError("empty path")
    cur: Any = data
    for seg in segs:
        cur = _descend(cur, seg)
    return cur


def _descend(node: Any, seg: str) -> Any:
    if isinstance(node, dict):
        if seg in node:
            return node[seg]
        raise KeyError(seg)
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and _item_id(item) == seg:
                return item
        raise KeyError(seg)
    raise KeyError(seg)


def _parent_and_key(data: dict, path: str) -> tuple[Any, str]:
    """Resolve to (parent_container, last_segment) so a caller can set/remove the
    node in place. parent is a dict or list; last_segment is the key or id."""
    segs = [s for s in (path or "").split("/") if s]
    if not segs:
        raise KeyError("empty path")
    parent = resolve_path(data, "/".join(segs[:-1])) if len(segs) > 1 else data
    return parent, segs[-1]


# ── Addressable view ───────────────────────────────────────────────────────── #
# Build the flat `path #hash preview` block the model reads. We surface:
#   • every allowed top-level field (always — the model needs to see them)
#   • dict-key children of dict fields (premise_parts/*, world/*)
#   • list items by id, plus a few of their most-edited scalar subfields, until the
#     node budget runs out. Sibling items in the same list always surface together
#     (you never see half a list's items) — the cap only trims deep subfields.
# Deeply-nested noise (scenes inside places, nodes inside arcs) is NOT enumerated
# field-by-field; the model can still target it by id path, it just writes the op
# from the item-level anchor.

def _preview(value: Any) -> str:
    """One-line JSON preview shown beside the hash. Scalars get quoted; containers
    show a compact shape hint so the model knows what's there without the full body."""
    if isinstance(value, str):
        snip = value if len(value) <= 72 else value[:69] + "…"
        return json.dumps(snip, ensure_ascii=False)
    if isinstance(value, (dict, list)):
        body = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return body if len(body) <= 96 else body[:93] + "…"
    return json.dumps(value, ensure_ascii=False)


def _node_lines(field: str, value: Any, budget: int) -> list[tuple[str, str, Any]]:
    """Yield (path, hash, value) rows for one top-level field and its addressable
    children, respecting `budget` (the remaining node count)."""
    rows: list[tuple[str, str, Any]] = []
    # The field itself is always addressable — even if it's a big container, the
    # model can set/replace the whole thing.
    rows.append((field, node_hash(value), value))
    budget -= 1

    if isinstance(value, dict):
        for k, v in value.items():
            if budget <= 0:
                break
            rows.append((f"{field}/{k}", node_hash(v), v))
            budget -= 1
    elif isinstance(value, list):
        # Always surface every item (never half a list) — the cap only trims
        # subfields, not siblings.
        for item in value:
            if not isinstance(item, dict):
                continue
            iid = _item_id(item)
            if not iid:
                continue
            if budget <= 0:
                break
            rows.append((f"{field}/{iid}", node_hash(item), item))
            budget -= 1
            # Then a few high-churn scalar subfields, while budget allows.
            for k, v in item.items():
                if k in _ID_KEYS or isinstance(v, (dict, list)):
                    continue
                if budget <= 0:
                    break
                rows.append((f"{field}/{iid}/{k}", node_hash(v), v))
                budget -= 1
    return rows


def anchored_view(layer_data: dict, allowed_fields) -> str:
    """The flat `path #hash preview` block shown to the model. `layer_data` is the
    raw story dict; `allowed_fields` is LAYER_FIELDS[layer] (the whitelist)."""
    rows: list[tuple[str, str, Any]] = []
    budget = _MAX_NODES
    for field in allowed_fields:
        value = layer_data.get(field)
        if value in (None, [], {}, ""):
            continue
        if budget <= 0:
            break
        rows.extend(_node_lines(field, value, budget))
        budget = _MAX_NODES - len(rows)
        if budget <= 0:
            break
    if not rows:
        return "(this section is empty — use `set` to create its first field)"
    lines = []
    for path, h, value in rows:
        lines.append(f"{path:<34}#{h}  {_preview(value)}")
    out = "\n".join(lines)
    if budget <= 0:        # we trimmed something
        out += "\n…(more nodes available — target any item by its id path)"
    return out


# ── Apply ──────────────────────────────────────────────────────────────────── #

def _deep_merge(base: dict, over: dict) -> dict:
    """Deep-merge `over` onto a copy of `base` (lists/scalars replace). Mirrors the
    shape in loom/stories/agent_config.py:_deep_merge — kept local so this module is
    self-contained and the merge contract is explicit at the call site."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _top_field(path: str) -> str:
    """The top-level story field a path lives under (its first segment)."""
    segs = [s for s in (path or "").split("/") if s]
    return segs[0] if segs else ""


def _set_in(data: dict, path: str, value: Any) -> None:
    """Set the node at `path`, creating intermediate containers as needed when the
    segment names a NEW key; for list ids the item must already exist (you edit
    items in place — creation of a brand-new list item goes via `set` on the list
    field with a new id, or merge onto an existing item)."""
    segs = [s for s in (path or "").split("/") if s]
    if not segs:
        raise KeyError("empty path")
    parent, last = _parent_and_key(data, path)
    if isinstance(parent, dict):
        parent[last] = value
    elif isinstance(parent, list):
        # Setting a subfield of an existing list item.
        for item in parent:
            if isinstance(item, dict) and _item_id(item) == last:
                if len(segs) == 1:
                    # Replacing the whole item — splice in place.
                    parent[parent.index(item)] = value
                else:
                    item[segs[-1]] = value
                return
        raise KeyError(last)
    else:
        raise KeyError(f"can't set into {type(parent).__name__}")


def _remove_at(data: dict, path: str) -> None:
    parent, last = _parent_and_key(data, path)
    if isinstance(parent, dict):
        parent.pop(last, None)
    elif isinstance(parent, list):
        for item in parent:
            if isinstance(item, dict) and _item_id(item) == last:
                parent.remove(item)
                return
        raise KeyError(last)
    else:
        raise KeyError(f"can't remove from {type(parent).__name__}")


def apply_ops(data: dict, ops: list) -> dict:
    """Apply a batch of hash-anchored ops to `data` (mutated in place). Returns
    {applied: [{path, op}], rejected: [{path, reason, current_hash?}],
     before: {top_field: old_value}}.

    Each op: {path, anchor?, op: set|merge|remove, value?}.
    Stale-anchor ops are skipped (not applied) but do NOT abort the batch — the
    others still go through. `before` snapshots the WHOLE top-level field each
    touched op lives under, so the client's Undo (a PUT of {field: old}) restores it.
    """
    applied: list[dict] = []
    rejected: list[dict] = []
    before: dict[str, Any] = {}

    for op in ops or []:
        if not isinstance(op, dict):
            continue
        path = (op.get("path") or "").strip()
        kind = (op.get("op") or "").strip().lower()
        anchor = (op.get("anchor") or "").strip().lstrip("#").lower()
        value = op.get("value")
        if not path or kind not in {"set", "merge", "remove"}:
            rejected.append({"path": path or "?", "reason": "bad op (need path + op in set|merge|remove)"})
            continue
        top = _top_field(path)

        # Resolve the current node + its hash. A missing node is only valid when
        # the op has no anchor (creating something new); otherwise it's stale/gone.
        try:
            current = resolve_path(data, path)
            current_hash = node_hash(current)
        except KeyError:
            if anchor:
                rejected.append({"path": path, "reason": "node not found (deleted?)", "current_hash": ""})
                continue
            current = None
            current_hash = ""

        # Anchor verification — the hashline core. An empty anchor means "create",
        # so we only reject when an anchor was claimed AND it no longer matches.
        if anchor and current_hash and anchor != current_hash:
            rejected.append({"path": path, "reason": "stale — node changed since you read it",
                             "current_hash": current_hash})
            continue

        # Snapshot the whole top-level field once per touched field (for Undo).
        if top and top not in before:
            before[top] = data.get(top)

        try:
            if kind == "set":
                _set_in(data, path, value)
            elif kind == "merge":
                if not isinstance(value, dict):
                    rejected.append({"path": path, "reason": "merge needs a JSON object value"})
                    # we already snapshotted; that's fine — no mutation happened
                    continue
                if current is None and not current_hash:
                    # merging into a new dict node — seed it
                    _set_in(data, path, value)
                elif not isinstance(current, dict):
                    rejected.append({"path": path, "reason": "merge target isn't an object"})
                    continue
                else:
                    parent, last = _parent_and_key(data, path)
                    if isinstance(parent, dict):
                        parent[last] = _deep_merge(parent.get(last) or {}, value)
                    elif isinstance(parent, list):
                        for item in parent:
                            if isinstance(item, dict) and _item_id(item) == last:
                                item.update(_deep_merge(dict(item), value))
                                break
            elif kind == "remove":
                _remove_at(data, path)
        except KeyError as exc:
            rejected.append({"path": path, "reason": f"can't apply: missing {exc}"})
            continue
        applied.append({"path": path, "op": kind})

    return {"applied": applied, "rejected": rejected, "before": before}


# ── Stale-anchor recovery ──────────────────────────────────────────────────── #
# When a batch rejects ops purely because their anchors drifted (the node changed
# since the model read it), the caller can re-show the model the FRESH anchored
# view, ask it to re-emit just those ops, and merge the retry's result. A rejection
# is recoverable when it's *only* about freshness — a structurally-bad op (merge on
# a non-object, a path that's genuinely gone) must NOT loop forever.

_STALE_REASONS = ("stale", "changed since", "deleted")


def is_stale_rejection(rej: dict) -> bool:
    """True if a rejection is purely a staleness drift the model could fix by
    re-reading — i.e. the node still EXISTS, it just changed. A 'node not found'
    is NOT recoverable (the model targeted something that's gone, not drifted)."""
    reason = (rej.get("reason") or "").lower()
    return any(s in reason for s in _STALE_REASONS) and "deleted" not in reason


def any_stale_rejections(rejected: list) -> bool:
    """True if at least one rejection is a recoverable staleness drift."""
    return any(is_stale_rejection(r) for r in (rejected or []))


def merge_results(prior: dict, retry: dict) -> dict:
    """Fold a retry's apply result into the prior result.

    Semantics: `applied` ACCUMULATES (the client sees every op that landed across
    both passes). `rejected` is REBUILT by path: a path the retry APPLIED is
    resolved (dropped); a path the retry rejected AGAIN keeps its (freshest)
    rejection; a path only in `prior`'s rejected that the retry didn't re-touch is
    carried. `before` keeps the EARLIEST snapshot per top-level field — the retry
    re-read fresh data and its `before` reflects the post-prior state, not the
    original; earliest-wins guarantees one Undo restores the field to its pre-turn
    state, exactly as the user expects."""
    merged_applied = list(prior.get("applied") or []) + list(retry.get("applied") or [])
    resolved = {a["path"] for a in merged_applied if isinstance(a, dict)}
    # Retry's verdict wins when a path appears in both; carry prior's only when the
    # retry didn't touch the path at all.
    retry_paths = {r.get("path") for r in (retry.get("rejected") or [])}
    carried = [r for r in (prior.get("rejected") or [])
               if r.get("path") not in resolved and r.get("path") not in retry_paths]
    merged_rejected = carried + [r for r in (retry.get("rejected") or [])
                                 if r.get("path") not in resolved]
    # before: earliest snapshot wins (prior's view of the field is pre-turn).
    merged_before = dict(prior.get("before") or {})
    for f, v in (retry.get("before") or {}).items():
        merged_before.setdefault(f, v)
    return {"applied": merged_applied, "rejected": merged_rejected, "before": merged_before}


_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f"}


def partial_reply(raw: str) -> str:
    """Decode the `"reply"` string field out of a possibly-truncated JSON object.

    The card-chat model streams a structured `{reply, ops}` object; we want to show
    the reply text live as it forms. This walks the streamed prefix, so it returns
    the reply-so-far even when the JSON isn't closed yet. It's a PREVIEW only — the
    authoritative reply comes from the final parsed object — so a mid-stream `\\uXXXX`
    that hasn't fully arrived is left as-is rather than complicating the walk."""
    i = raw.find('"reply"')
    if i < 0:
        return ""
    j = raw.find(":", i + 7)
    if j < 0:
        return ""
    k = raw.find('"', j + 1)
    if k < 0:
        return ""
    out: list[str] = []
    x, n = k + 1, len(raw)
    while x < n:
        c = raw[x]
        if c == "\\":
            if x + 1 >= n:
                break                       # incomplete escape at the tail — drop it
            out.append(_ESC.get(raw[x + 1], raw[x + 1]))
            x += 2
            continue
        if c == '"':
            break                           # closing quote — reply done
        out.append(c)
        x += 1
    return "".join(out)


# ── self-check ─────────────────────────────────────────────────────────────── #
if __name__ == "__main__":   # ponytail: a runnable check of the core contracts
    # node_hash is deterministic + stable
    assert node_hash("x") == node_hash("x")
    assert node_hash({"a": 1, "b": 2}) == node_hash({"b": 2, "a": 1})   # key-order-independent
    assert len(node_hash("x")) == 8

    story = {
        "premise": "the famine",
        "premise_parts": {"root": "the famine", "question": "who eats?"},
        "arcs": [
            {"id": "arc-1", "name": "Old", "premise": "p1"},
            {"id": "arc-2", "name": "Lanterns", "premise": "p2", "cast": ["a"]},
        ],
        "locations": [{"id": "home", "name": "Home", "description": "cozy"}],
    }

    # resolve_path: field, dict-key, list-by-id, list-id/subfield, missing
    assert resolve_path(story, "premise") == "the famine"
    assert resolve_path(story, "premise_parts/root") == "the famine"
    assert resolve_path(story, "arcs/arc-2/name") == "Lanterns"
    try:
        resolve_path(story, "arcs/nope"); raise AssertionError("should miss")
    except KeyError:
        pass

    # anchored view renders
    view = anchored_view(story, ("premise", "premise_parts", "arcs", "locations"))
    assert "premise " in view and "arcs/arc-2 " in view and "arcs/arc-2/name" in view

    # set on a scalar field
    d = json.loads(json.dumps(story))
    r = apply_ops(d, [{"path": "premise", "anchor": node_hash(story["premise"]),
                       "op": "set", "value": "the drought"}])
    assert r["applied"] == [{"path": "premise", "op": "set"}] and d["premise"] == "the drought"
    assert r["before"]["premise"] == "the famine"

    # set on a list-item subfield — only that field changes, sibling items intact
    d = json.loads(json.dumps(story))
    r = apply_ops(d, [{"path": "arcs/arc-2/premise",
                       "anchor": node_hash(story["arcs"][1]["premise"]),
                       "op": "set", "value": "new"}])
    assert d["arcs"][1]["premise"] == "new" and d["arcs"][1]["name"] == "Lanterns"
    assert d["arcs"][0]["name"] == "Old" and "arcs" in r["before"]

    # merge on premise_parts updates one key, keeps the other
    d = json.loads(json.dumps(story))
    r = apply_ops(d, [{"path": "premise_parts",
                       "anchor": node_hash(story["premise_parts"]),
                       "op": "merge", "value": {"root": "the deep snow"}}])
    assert d["premise_parts"]["root"] == "the deep snow"
    assert d["premise_parts"]["question"] == "who eats?"

    # remove a list item by id
    d = json.loads(json.dumps(story))
    apply_ops(d, [{"path": "arcs/arc-1", "anchor": node_hash(story["arcs"][0]),
                   "op": "remove"}])
    assert [a["id"] for a in d["arcs"]] == ["arc-2"]

    # stale anchor rejected while a second valid op in the same batch still applies
    d = json.loads(json.dumps(story))
    r = apply_ops(d, [
        {"path": "premise", "anchor": "deadbeef", "op": "set", "value": "stale"},
        {"path": "tone", "op": "set", "value": "wry"},   # no anchor → create new field
    ])
    assert d["premise"] == "the famine"               # stale NOT applied
    assert d["tone"] == "wry"                          # valid op DID apply
    assert len(r["rejected"]) == 1 and r["applied"] == [{"path": "tone", "op": "set"}]

    # empty-anchor set creates a new dict key
    d = json.loads(json.dumps(story))
    apply_ops(d, [{"path": "premise_parts/stakes", "op": "set", "value": "everyone"}])
    assert d["premise_parts"]["stakes"] == "everyone"

    # ── stale recovery helpers ──────────────────────────────────────────────
    # is_stale_rejection: a drift is recoverable; a deletion / bad op is not
    assert is_stale_rejection({"path": "premise", "reason": "stale — node changed since you read it"})
    assert not is_stale_rejection({"path": "premise", "reason": "node not found (deleted?)"})
    assert not is_stale_rejection({"path": "premise", "reason": "merge target isn't an object"})
    assert any_stale_rejections([{"path": "x", "reason": "stale"}])
    assert not any_stale_rejections([{"path": "x", "reason": "deleted"}])

    # merge_results: a retry that APPLIES a previously-rejected path drops it from
    # rejected; `before` keeps the EARLIEST (pre-turn) snapshot so one Undo restores
    # the field to where it was before any of this turn's edits.
    prior = {"applied": [{"path": "tone", "op": "set"}],
             "rejected": [{"path": "premise", "reason": "stale — node changed since you read it"}],
             "before": {"premise": "the famine"}}
    retry = {"applied": [{"path": "premise", "op": "set"}],   # retry applied it
             "rejected": [],
             "before": {"premise": "the drought"}}            # post-prior value
    m = merge_results(prior, retry)
    assert m["applied"] == [{"path": "tone", "op": "set"}, {"path": "premise", "op": "set"}]
    assert m["rejected"] == []                                # premise resolved → gone
    assert m["before"]["premise"] == "the famine"             # EARLIEST snapshot wins

    # a path that's STILL rejected on retry stays in rejected
    retry2 = {"applied": [], "rejected": [{"path": "premise", "reason": "stale"}], "before": {}}
    m2 = merge_results(prior, retry2)
    assert m2["rejected"] == [{"path": "premise", "reason": "stale"}]
    assert m2["applied"] == [{"path": "tone", "op": "set"}]

    # ── partial_reply: reply-so-far from truncated streamed JSON ─────────────
    assert partial_reply('{"reply":"Hello wor') == "Hello wor"          # mid-string
    assert partial_reply('{"reply":"a \\"quote\\" b","ops":[]}') == 'a "quote" b'  # escapes + closed
    assert partial_reply('{"reply":"line\\nbreak') == "line\nbreak"     # escape decoded
    assert partial_reply('{"reply":"tail\\') == "tail"                  # dangling escape dropped
    assert partial_reply('{"ops":[]}') == ""                            # no reply field yet

    print("ok — hashline anchors: hash, view, resolve, apply (set/merge/remove + stale rejection + recovery)")

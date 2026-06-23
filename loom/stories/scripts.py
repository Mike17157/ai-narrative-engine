"""Built-in story **scripts** — functions the model can call, as ordinary CODE.

This is the standard pattern (LLM tool-calling): a *script* is a named function with a
small parameter schema. The model emits a call `{fn, params}`; we run the real function.
The function body IS the behavior — version-controlled, grep-able, unit-testable — instead
of a JSON-Patch op-spec stored as a string in a libSQL row (the homegrown DSL this
replaces). The lorebook keeps only the job it's good at: **triggering** (keywords/embeddings
→ which scripts to surface). It no longer stores logic.

Label a function with `@script(...)`. The name is the registry key the model calls AND the
key a lorebook entry references via `{"fn": "<name>"}`:

    @script("add_beat", describe="Add a new beat to the arc of change.",
            keywords=["add a beat", "new beat"],
            params={"title": "the beat's title", "after": "id of the beat it follows (optional)"})
    def add_beat(doc, *, _id, title="", after=None):
        doc.setdefault("nodes", []).append({"id": _id, "title": title, ...})

Conventions for an impl:
  • `doc`  — the artifact/level dict it mutates IN PLACE (the dev graph, a locations doc, …).
  • `_id`  — a freshly-minted unique id, always supplied (use it for CREATE ops).
  • `**params` — the model-provided params (filtered to the declared `params` keys).
A target id the model chose is just a normal param (e.g. `id` on set_beat_field) — distinct
from `_id`, so create- and target-ops never collide.

`writes` is the State-doc level the script mutates; built-ins default to "graph" because the
artifact-agnostic graph-ops endpoint passes whatever artifact (graph/locations/cast) in that
slot. The same code runs state-natively once callers target a specific level.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ScriptDef:
    name: str
    describe: str
    params: dict                      # {param_name: human description} — the model schema
    keywords: list[str]               # default triggers (a lorebook entry may override)
    writes: str                       # State-doc level mutated
    impl: Callable                    # def impl(doc, *, _id, **params) -> None


REGISTRY: dict[str, ScriptDef] = {}


def script(name: str, *, describe: str, params: dict | None = None,
           keywords: list | None = None, writes: str = "graph") -> Callable:
    """Decorator: register `impl` as the script called by `name`."""
    def deco(fn: Callable) -> Callable:
        REGISTRY[name] = ScriptDef(name=name, describe=describe, params=dict(params or {}),
                                   keywords=[str(k) for k in (keywords or [])], writes=writes, impl=fn)
        return fn
    return deco


def get(name: str) -> ScriptDef | None:
    return REGISTRY.get(name)


# ── helpers ──────────────────────────────────────────────────────────────────────

def _by_id(items: Any, _id: Any) -> dict | None:
    for it in items or []:
        if isinstance(it, dict) and str(it.get("id")) == str(_id):
            return it
    return None


def _new_node(_id: str, title: str) -> dict:
    return {"id": _id, "title": title, "inflection": "", "start": "",
            "end": "", "what_happened": "", "next": []}


# ── Graph (the development graph: {logline,wound,lie,truth,nodes:[…]}) ────────────

@script("add_beat", describe="Add a new beat to the arc of change.",
        keywords=["add a beat", "new beat", "insert beat", "add beat", "another beat"],
        params={"title": "the beat's title", "after": "id of the beat it follows (optional)"})
def add_beat(doc, *, _id, title="", after=None):
    doc.setdefault("nodes", []).append(_new_node(_id, title))
    if after:
        n = _by_id(doc.get("nodes"), after)
        if n is not None:
            n.setdefault("next", []).append(_id)


@script("insert_between", describe="Insert a new beat BETWEEN two connected beats (rewires the arrow through it).",
        keywords=["insert between", "in between", "split the arrow", "between"],
        params={"a": "id of the beat before", "b": "id of the beat after", "title": "the new beat's title"})
def insert_between(doc, *, _id, a, b, title=""):
    doc.setdefault("nodes", []).append(_new_node(_id, title))
    na = _by_id(doc.get("nodes"), a)
    if na is not None:
        nx = na.setdefault("next", [])
        if b in nx:
            nx.remove(b)
        nx.append(_id)
    ni = _by_id(doc.get("nodes"), _id)
    if ni is not None:
        ni.setdefault("next", []).append(b)


@script("set_beat_field", describe="Set a field on an existing beat.",
        keywords=["rename", "retitle", "change the", "edit the beat", "set the", "update beat"],
        params={"id": "beat id", "field": "one of: title, inflection, start, end, what_happened, location",
                "value": "new text"})
def set_beat_field(doc, *, _id, id, field, value=""):
    n = _by_id(doc.get("nodes"), id)
    if n is not None and field:
        n[field] = value


@script("connect", describe="Connect one beat to another (draw an arrow source -> target).",
        keywords=["connect", "branch", "link", "leads to", "arrow", "then", "sequence"],
        params={"source": "id the arrow starts from", "target": "id it points to"})
def connect(doc, *, _id, source, target):
    n = _by_id(doc.get("nodes"), source)
    if n is not None:
        n.setdefault("next", []).append(target)


@script("disconnect", describe="Remove the arrow from one beat to another.",
        keywords=["disconnect", "unlink", "remove arrow", "detach"],
        params={"source": "id the arrow starts from", "target": "id it currently points to"})
def disconnect(doc, *, _id, source, target):
    n = _by_id(doc.get("nodes"), source)
    nx = n.get("next") if n is not None else None
    if isinstance(nx, list) and target in nx:
        nx.remove(target)


@script("delete_beat", describe="Delete a beat from the graph.",
        keywords=["delete", "remove beat", "drop the beat", "cut the beat"],
        params={"id": "id of the beat to delete"})
def delete_beat(doc, *, _id, id):
    nodes = doc.get("nodes")
    if isinstance(nodes, list):
        doc["nodes"] = [n for n in nodes if str(n.get("id")) != str(id)]


@script("move_beat", describe="Reposition a beat to sit right after another in the sequence.",
        keywords=["move", "reposition", "put after", "relocate", "moved"],
        params={"id": "id of the beat to move", "after": "id of the beat it should follow (blank = end)"})
def move_beat(doc, *, _id, id, after=None):
    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        return
    i = next((k for k, n in enumerate(nodes) if str(n.get("id")) == str(id)), None)
    if i is None:
        return
    el = nodes.pop(i)
    j = next((k for k, n in enumerate(nodes) if str(n.get("id")) == str(after)), None) if after else None
    nodes.insert((j + 1) if j is not None else len(nodes), el)


@script("reorder_beats", describe="Set the full beat order at once (give every beat id in the new order).",
        keywords=["reorder", "re-order", "order the beats", "sequence them", "rearrange"],
        params={"order": "the complete list of beat ids in the desired order"})
def reorder_beats(doc, *, _id, order=None):
    nodes = doc.get("nodes")
    if not isinstance(nodes, list) or not isinstance(order, list):
        return
    want = [str(x) for x in order]
    ranked = sorted(range(len(nodes)),
                    key=lambda i: (want.index(str(nodes[i].get("id")))
                                   if str(nodes[i].get("id")) in want else len(want) + i))
    doc["nodes"] = [nodes[i] for i in ranked]


@script("set_spine", describe="Set a top-level spine field (logline, wound, lie, or truth).",
        keywords=["wound", "lie", "truth", "logline", "spine", "misbelief"],
        params={"field": "one of: logline, wound, lie, truth", "value": "new text"})
def set_spine(doc, *, _id, field, value=""):
    if field:
        doc[field] = value


# ── Locations (a {start, locations:[…]} artifact) ────────────────────────────────

@script("add_location", describe="Add a neutral location to the story.",
        keywords=["add a location", "new location", "another place", "add place", "new place"],
        params={"name": "the place's name", "description": "the place objectively (no people/events)"})
def add_location(doc, *, _id, name="", description=""):
    doc.setdefault("locations", []).append(
        {"id": _id, "name": name, "description": description, "background_prompt": ""})


@script("set_location_field", describe="Set a field on an existing location.",
        keywords=["rename location", "change the place", "edit location", "set the location", "update place"],
        params={"id": "location id", "field": "one of: name, description, background_prompt", "value": "new text"})
def set_location_field(doc, *, _id, id, field, value=""):
    l = _by_id(doc.get("locations"), id)
    if l is not None and field:
        l[field] = value


@script("remove_location", describe="Delete a location.",
        keywords=["remove location", "delete place", "drop the location", "cut the place"],
        params={"id": "id of the location to delete"})
def remove_location(doc, *, _id, id):
    locs = doc.get("locations")
    if isinstance(locs, list):
        doc["locations"] = [l for l in locs if str(l.get("id")) != str(id)]


@script("set_start", describe="Set which location the story opens in.",
        keywords=["start location", "opening location", "begins at", "starts in", "set start"],
        params={"id": "id of the starting location"})
def set_start(doc, *, _id, id):
    doc["start"] = id


# ── Cast (a {cast:[…]} artifact — the wizard's characters step) ───────────────────

@script("add_character", describe="Add a character to the cast.",
        keywords=["add a character", "new character", "another character", "add npc", "new npc",
                  "add a cast member", "introduce a character"],
        params={"name": "the character's name", "role": "their role in the story (e.g. mentor, rival)",
                "persona": "who they are — personality, voice, wants (a paragraph)"})
def add_character(doc, *, _id, name="", role="", persona=""):
    doc.setdefault("cast", []).append(
        {"id": _id, "name": name, "role": role, "persona": persona,
         "appearance": "", "base_prompt": "", "primary": False})


@script("set_character_field", describe="Set a field on an existing character.",
        keywords=["rename character", "change the character", "edit character", "set the character",
                  "update character", "change their role", "rewrite the persona"],
        params={"id": "character id", "field": "one of: name, role, persona, appearance, base_prompt",
                "value": "new text"})
def set_character_field(doc, *, _id, id, field, value=""):
    c = _by_id(doc.get("cast"), id)
    if c is not None and field:
        c[field] = value


@script("remove_character", describe="Remove a character from the cast (never the ★ main character).",
        keywords=["remove character", "delete character", "drop the character", "cut the character",
                  "remove npc", "kill off"],
        params={"id": "id of the character to remove"})
def remove_character(doc, *, _id, id):
    cast = doc.get("cast")
    if isinstance(cast, list):
        doc["cast"] = [c for c in cast if str(c.get("id")) != str(id)]

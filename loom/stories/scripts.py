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

Each PARAM is described for the model. The value can be a plain string (the human
description — typed `string`, required unless it says "optional"), or a dict for a richer
schema: `{"desc": str, "type": "string"|"array", "enum": [...], "required": bool}`. Use
`enum` for a fixed set of choices (e.g. which field to set) — then the model literally cannot
pass an invalid value. `_norm_param` normalizes both forms; the schema is built in
`graph_ops.tools_spec`.

Conventions for an impl:
  • `doc`  — the artifact/level dict it mutates IN PLACE (the dev graph, a locations doc, …).
  • `_id`  — a freshly-minted unique id, always supplied (use it for CREATE ops).
  • `**params` — the model-provided params (filtered to the declared `params` keys).
An impl RAISES `ValueError` with a clear message when the thing it targets doesn't exist
(e.g. "no beat with id 'b7'"); `graph_ops.apply_calls` catches it and logs it as a failed
call, so a bad id surfaces instead of silently doing nothing. A target id the model chose is
just a normal param (e.g. `id` on set_beat_field) — distinct from `_id`, so create- and
target-ops never collide.

`writes` is the State-doc level the script mutates; built-ins default to "graph" because the
artifact-agnostic graph-ops endpoint passes whatever artifact (graph/locations/cast) in that
slot. The same code runs state-natively once callers target a specific level.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ScriptDef:
    name: str
    describe: str
    params: dict                      # {param_name: {desc,type,enum,required}} — normalized
    keywords: list[str]               # default triggers (a lorebook entry may override)
    writes: str                       # State-doc level mutated
    impl: Callable                    # def impl(doc, *, _id, **params) -> None


REGISTRY: dict[str, ScriptDef] = {}


def _norm_param(v: Any) -> dict:
    """Normalize a param spec to the rich form {desc,type,enum,required}. A bare string is the
    description (type `string`, required unless it says "optional"); a dict overrides any field."""
    if isinstance(v, dict):
        desc = str(v.get("desc", ""))
        return {"desc": desc, "type": v.get("type", "string"),
                "enum": list(v["enum"]) if v.get("enum") else None,
                "required": bool(v.get("required", "optional" not in desc.lower()))}
    s = str(v)
    return {"desc": s, "type": "string", "enum": None, "required": "optional" not in s.lower()}


def script(name: str, *, describe: str, params: dict | None = None,
           keywords: list | None = None, writes: str = "graph") -> Callable:
    """Decorator: register `impl` as the script called by `name`."""
    def deco(fn: Callable) -> Callable:
        REGISTRY[name] = ScriptDef(
            name=name, describe=describe,
            params={k: _norm_param(v) for k, v in (params or {}).items()},
            keywords=[str(k) for k in (keywords or [])], writes=writes, impl=fn)
        return fn
    return deco


def get(name: str) -> ScriptDef | None:
    return REGISTRY.get(name)


def new_id() -> str:
    """A fresh unique id for CREATE scripts (passed to every impl as `_id`)."""
    return "n" + uuid.uuid4().hex[:6]


def invoke(script: "ScriptDef | str", doc: dict, params: dict | None = None,
           *, _id: str | None = None) -> dict:
    """Run a registered script against `doc` (mutates in place; returns it). This is the
    ONE place a script executes — `graph_ops.apply_calls` (the model path) and the test
    flow both go through here, so behavior can't drift between them. Params are filtered to
    the script's declared keys (a stray model param can't crash the call) and a fresh `_id`
    is supplied. Raises KeyError for an unregistered name; an impl may raise ValueError when
    its target doesn't exist."""
    sd = script if isinstance(script, ScriptDef) else REGISTRY.get(script)
    if sd is None:
        raise KeyError(f"no such script: {script!r}")
    kw = {k: v for k, v in (params or {}).items() if k in sd.params}
    sd.impl(doc, _id=_id or new_id(), **kw)
    return doc


# ── helpers ──────────────────────────────────────────────────────────────────────

def _by_id(items: Any, _id: Any) -> dict | None:
    for it in items or []:
        if isinstance(it, dict) and str(it.get("id")) == str(_id):
            return it
    return None


def _require(item: dict | None, msg: str) -> dict:
    """Return the found item, or raise ValueError(msg) so a bad id surfaces as a failed call."""
    if item is None:
        raise ValueError(msg)
    return item


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
        _require(_by_id(doc.get("nodes"), after), f"no beat with id {after!r} to attach after") \
            .setdefault("next", []).append(_id)


@script("insert_between", describe="Insert a new beat BETWEEN two connected beats (rewires the arrow through it).",
        keywords=["insert between", "in between", "split the arrow", "between"],
        params={"a": "id of the beat before", "b": "id of the beat after", "title": "the new beat's title"})
def insert_between(doc, *, _id, a, b, title=""):
    na = _require(_by_id(doc.get("nodes"), a), f"no beat with id {a!r}")
    _require(_by_id(doc.get("nodes"), b), f"no beat with id {b!r}")
    doc.setdefault("nodes", []).append(_new_node(_id, title))
    nx = na.setdefault("next", [])
    if b in nx:
        nx.remove(b)
    nx.append(_id)
    _by_id(doc.get("nodes"), _id).setdefault("next", []).append(b)


@script("set_beat_field", describe="Set a field on an existing beat.",
        keywords=["rename", "retitle", "change the", "edit the beat", "set the", "update beat"],
        params={"id": "beat id",
                "field": {"desc": "which field to set", "required": True,
                          "enum": ["title", "inflection", "start", "end", "what_happened", "location"]},
                "value": "new text"})
def set_beat_field(doc, *, _id, id, field, value=""):
    _require(_by_id(doc.get("nodes"), id), f"no beat with id {id!r}")[field] = value


@script("connect", describe="Connect one beat to another (draw an arrow source -> target).",
        keywords=["connect", "branch", "link", "leads to", "arrow", "then", "sequence"],
        params={"source": "id the arrow starts from", "target": "id it points to"})
def connect(doc, *, _id, source, target):
    _require(_by_id(doc.get("nodes"), target), f"no beat with id {target!r} to point at")
    _require(_by_id(doc.get("nodes"), source), f"no beat with id {source!r}") \
        .setdefault("next", []).append(target)


@script("disconnect", describe="Remove the arrow from one beat to another.",
        keywords=["disconnect", "unlink", "remove arrow", "detach"],
        params={"source": "id the arrow starts from", "target": "id it currently points to"})
def disconnect(doc, *, _id, source, target):
    n = _require(_by_id(doc.get("nodes"), source), f"no beat with id {source!r}")
    nx = n.get("next")
    if not isinstance(nx, list) or target not in nx:
        raise ValueError(f"no arrow {source!r} -> {target!r} to remove")
    nx.remove(target)


@script("delete_beat", describe="Delete a beat from the graph.",
        keywords=["delete", "remove beat", "drop the beat", "cut the beat"],
        params={"id": "id of the beat to delete"})
def delete_beat(doc, *, _id, id):
    _require(_by_id(doc.get("nodes"), id), f"no beat with id {id!r} to delete")
    doc["nodes"] = [n for n in doc.get("nodes", []) if str(n.get("id")) != str(id)]


@script("move_beat", describe="Reposition a beat to sit right after another in the sequence.",
        keywords=["move", "reposition", "put after", "relocate", "moved"],
        params={"id": "id of the beat to move", "after": "id of the beat it should follow (blank = end)"})
def move_beat(doc, *, _id, id, after=None):
    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("no beats to move")
    i = next((k for k, n in enumerate(nodes) if str(n.get("id")) == str(id)), None)
    if i is None:
        raise ValueError(f"no beat with id {id!r} to move")
    el = nodes.pop(i)
    j = None
    if after:
        j = next((k for k, n in enumerate(nodes) if str(n.get("id")) == str(after)), None)
        if j is None:
            nodes.insert(i, el)   # put it back; don't lose the beat on a bad target
            raise ValueError(f"no beat with id {after!r} to move after")
    nodes.insert((j + 1) if j is not None else len(nodes), el)


@script("reorder_beats", describe="Set the full beat order at once (give every beat id in the new order).",
        keywords=["reorder", "re-order", "order the beats", "sequence them", "rearrange"],
        params={"order": {"desc": "the complete list of beat ids in the desired order", "type": "array"}})
def reorder_beats(doc, *, _id, order=None):
    nodes = doc.get("nodes")
    if not isinstance(nodes, list) or not isinstance(order, list):
        raise ValueError("reorder needs the full list of beat ids")
    want = [str(x) for x in order]
    ranked = sorted(range(len(nodes)),
                    key=lambda i: (want.index(str(nodes[i].get("id")))
                                   if str(nodes[i].get("id")) in want else len(want) + i))
    doc["nodes"] = [nodes[i] for i in ranked]


@script("set_spine", describe="Set a top-level spine field (logline, wound, lie, or truth).",
        keywords=["wound", "lie", "truth", "logline", "spine", "misbelief"],
        params={"field": {"desc": "which spine field to set", "required": True,
                          "enum": ["logline", "wound", "lie", "truth"]},
                "value": "new text"})
def set_spine(doc, *, _id, field, value=""):
    doc[field] = value


# ── Locations (a {start, locations:[…]} artifact) ────────────────────────────────

@script("add_location", describe="Add a neutral location to the story.",
        keywords=["add a location", "new location", "another place", "add place", "new place"],
        params={"name": "the place's name", "description": "the place objectively (no people/events)"})
def add_location(doc, *, _id, name="", description=""):
    locs = doc.setdefault("locations", [])
    # Idempotent by name: a re-add UPDATES the existing place instead of duplicating it.
    cur = next((l for l in locs if name.strip()
                and str(l.get("name", "")).strip().lower() == name.strip().lower()), None)
    if cur is not None:
        if description:
            cur["description"] = description
        return
    locs.append({"id": _id, "name": name, "description": description, "background_prompt": "", "parent": ""})


@script("set_location_area",
        describe="Group a location under an AREA — a larger region/zone that contains it. This is the map "
                 "HIERARCHY: areas are the main nodes, locations nest inside them (an area is just a "
                 "location with children). Leave area blank to detach it back to the top level.",
        keywords=["area", "region", "zone", "district", "wing", "inside", "part of", "belongs to",
                  "within", "group under", "nest", "contained in", "sub-location"],
        params={"id": "id of the location to place",
                "area": "id of the area/location it sits inside (blank to detach to top level)"})
def set_location_area(doc, *, _id, id, area=None):
    loc = _require(_by_id(doc.get("locations"), id), f"no location with id {id!r}")
    if not area:
        loc["parent"] = ""
        return
    if str(area) == str(id):
        raise ValueError("a location can't sit inside itself")
    _require(_by_id(doc.get("locations"), area), f"no area with id {area!r}")
    seen, cur = set(), str(area)          # walk up from the parent; reaching `id` would form a cycle
    while cur and cur not in seen:
        if cur == str(id):
            raise ValueError("that would nest the area inside its own descendant")
        seen.add(cur)
        up = _by_id(doc.get("locations"), cur)
        cur = str(up.get("parent") or "") if up else ""
    loc["parent"] = area


@script("set_location_field", describe="Set a field on an existing location.",
        keywords=["rename location", "change the place", "edit location", "set the location", "update place"],
        params={"id": "location id",
                "field": {"desc": "which field to set", "required": True,
                          "enum": ["name", "description", "background_prompt"]},
                "value": "new text"})
def set_location_field(doc, *, _id, id, field, value=""):
    _require(_by_id(doc.get("locations"), id), f"no location with id {id!r}")[field] = value


@script("remove_location", describe="Delete a location.",
        keywords=["remove location", "delete place", "drop the location", "cut the place"],
        params={"id": "id of the location to delete"})
def remove_location(doc, *, _id, id):
    _require(_by_id(doc.get("locations"), id), f"no location with id {id!r} to delete")
    doc["locations"] = [l for l in doc.get("locations", []) if str(l.get("id")) != str(id)]


@script("set_start", describe="Set which location the story opens in.",
        keywords=["start location", "opening location", "begins at", "starts in", "set start"],
        params={"id": "id of the starting location"})
def set_start(doc, *, _id, id):
    _require(_by_id(doc.get("locations"), id), f"no location with id {id!r} to start at")
    doc["start"] = id


# ── Cast (a {cast:[…]} artifact — the wizard's characters step) ───────────────────

@script("add_character", describe="Add a character to the cast (or to a draft queue). For a DRAFT "
        "harness the name may be blank — give the role plus the psychology (temperament, want, lie, "
        "wound, secret); name comes at commit. For a committed cast, prefer the richer create_character.",
        keywords=["add a character", "new character", "another character", "add npc", "new npc",
                  "add a cast member", "introduce a character"],
        params={"name": "the character's name (optional — blank for a draft harness)",
                "role": "their role in the story (e.g. mentor, rival)",
                "persona": "a DESCRIPTION of who they are — appearance, personality, voice, wants, in "
                           "prose (a paragraph). Never a quote or a line of spoken dialogue — describe "
                           "them, don't voice them (optional)",
                "temperament": "psychology in a phrase — Big Five markers, attachment style, defenses (optional)",
                "want": "what they consciously pursue (optional)",
                "lie": "the false belief they act on (optional)",
                "wound": "the formative hurt under the lie (optional)",
                "secret": "what they hide from the others (optional)",
                "group": "the social group/clique they belong to (e.g. 'the crew') — members of one "
                         "group are friends by default (optional)"})
def add_character(doc, *, _id, name="", role="", persona="", temperament="",
                  want="", lie="", wound="", secret="", group=""):
    entry = {"id": _id, "name": name, "role": role, "persona": persona,
             "appearance": "", "base_prompt": "", "primary": False}
    for k, v in (("temperament", temperament), ("want", want), ("lie", lie),
                 ("wound", wound), ("secret", secret), ("group", group)):
        if v:
            entry[k] = v
    doc.setdefault("cast", []).append(entry)


@script("set_character_field", describe="Set a field on an existing character (or draft harness).",
        keywords=["rename character", "change the character", "edit character", "set the character",
                  "update character", "change their role", "rewrite the persona", "their want",
                  "their lie", "their wound", "their secret", "temperament"],
        params={"id": "character id",
                "field": {"desc": "which field to set", "required": True,
                          "enum": ["name", "role", "persona", "appearance", "base_prompt",
                                   "temperament", "want", "lie", "wound", "secret", "group"]},
                "value": "new text"})
def set_character_field(doc, *, _id, id, field, value=""):
    _require(_by_id(doc.get("cast"), id), f"no character with id {id!r}")[field] = value


@script("remove_character", describe="Remove a character from the cast (never the ★ main character).",
        keywords=["remove character", "delete character", "drop the character", "cut the character",
                  "remove npc", "kill off"],
        params={"id": "id of the character to remove"})
def remove_character(doc, *, _id, id):
    _require(_by_id(doc.get("cast"), id), f"no character with id {id!r} to remove")
    doc["cast"] = [c for c in doc.get("cast", []) if str(c.get("id")) != str(id)]


@script("set_world_field", describe="Set one field of the story's WORLD frame (the stage: genre/tone/"
        "setting/situation). Use when the writer asks to change the world, setting, era, genre, or tone.",
        keywords=["the world", "the setting", "the genre", "the tone", "change the setting", "set the world",
                  "make the world", "the era", "rewrite the setting", "different setting", "shift the era"],
        params={"field": {"desc": "which world field", "required": True,
                          "enum": ["genre", "tone", "setting", "situation"]},
                "value": {"desc": "the new text for that field", "required": True}})
def set_world_field(doc, *, _id, field, value=""):
    field = str(field or "").strip().lower()
    if field not in ("genre", "tone", "setting", "situation"):
        raise ValueError(f"unknown world field {field!r}")
    w = doc.get("world")
    if not isinstance(w, dict):
        w = {}
        doc["world"] = w
    w[field] = str(value or "").strip()


# ── Relationships (the cast's web: doc["relationships"]:[…]) ──────────────────────
# Authored at build time (Character Agent) AND drift-able at runtime — the same shape the
# world-state engine tracks (entities[name].relationships[target]); `value` seeds that.

def _resolve_actor(doc, ref):
    """Map a relationship endpoint to a cast id: exact id wins; else match by name or role (the draft
    queue). Returns `ref` unchanged when there's no cast or no match (committed-story keys pass through)."""
    cast = doc.get("cast") or []
    if any(str(c.get("id")) == str(ref) for c in cast if isinstance(c, dict)):
        return ref
    low = str(ref).strip().lower()
    if low:
        for c in cast:
            if isinstance(c, dict) and low in (str(c.get("name", "")).strip().lower(),
                                               str(c.get("role", "")).strip().lower()):
                return str(c.get("id"))
    return ref


@script("set_relationship",
        describe="Create or update how one character relates to another (rival, mentor, lover…). "
                 "Describe the bond in PROSE — never a number.",
        keywords=["relationship", "rival", "ally", "mentor", "lover", "enemy", "friend", "sibling",
                  "history with", "knows", "connect them", "feels about", "their dynamic"],
        params={"source": "id or name of the character who holds the feeling",
                "target": "id or name of the other character",
                "nature": "the KIND of bond (e.g. rival, mentor, lover, sibling, estranged)",
                "dynamic": "2-3 WORDS for how SOURCE feels about target right now — terse and "
                           "evocative, never a sentence (e.g. 'protective, smothering', 'old grudge', "
                           "'wary respect', 'quiet devotion')",
                "stance": {"desc": "coarse feeling SOURCE→target, for the graph colour only",
                           "enum": ["devoted", "warm", "neutral", "strained", "hostile"]},
                "target_dynamic": "2-3 WORDS for how the TARGET feels back toward source (a bond reads "
                                  "BOTH WAYS and the two sides may DIFFER — unrequited, one-sided trust). "
                                  "Omit if it's symmetric (same as source).",
                "target_stance": {"desc": "coarse feeling TARGET→source; omit if symmetric",
                                  "enum": ["devoted", "warm", "neutral", "strained", "hostile"]},
                "note": "optional extra history",
                "potential": "the story SEED — the hidden COMMON CORE they share beneath their "
                             "surface-different backgrounds, the point they can plausibly build on "
                             "(love or enmity) (optional)",
                "trajectory": "where the bond could TRAVEL + how it FEELS — a from→to with a tone "
                              "('wary strangers → a slow, sweet first love, strawberry-milk gentle') (optional)"})
def set_relationship(doc, *, _id, source, target, nature="", dynamic="", stance="", note="",
                     potential="", trajectory="", target_dynamic="", target_stance=""):
    # Resolve a name/role to a cast id when the doc carries a cast (the draft queue, where the agent
    # creates + relates in one turn so it refers to actors by NAME, not the yet-unassigned id). A no-op
    # for a committed story (endpoints are already character keys; ids match, names don't resolve).
    source, target = _resolve_actor(doc, source), _resolve_actor(doc, target)
    if str(source) == str(target):
        raise ValueError("a character can't have a relationship with themselves")
    rels = doc.setdefault("relationships", [])
    cur = next((r for r in rels if r.get("source") == source and r.get("target") == target), None)
    if cur is None:
        cur = {"id": _id, "source": source, "target": target, "nature": "",
               "dynamic": "", "stance": "neutral", "note": "", "potential": "", "trajectory": ""}
        rels.append(cur)
    if dynamic:
        dynamic = " ".join(str(dynamic).split()[:6])   # 2-3 words; backstop a sentence
    if target_dynamic:
        target_dynamic = " ".join(str(target_dynamic).split()[:6])
    for k, v in (("nature", nature), ("dynamic", dynamic), ("note", note),
                 ("potential", potential), ("trajectory", trajectory),
                 ("target_dynamic", target_dynamic)):
        if v:
            cur[k] = v
    if stance in ("devoted", "warm", "neutral", "strained", "hostile"):
        cur["stance"] = stance
    if target_stance in ("devoted", "warm", "neutral", "strained", "hostile"):
        cur["target_stance"] = target_stance


@script("remove_relationship", describe="Remove the relationship between two characters.",
        keywords=["remove relationship", "delete relationship", "they don't know", "no relationship",
                  "sever", "unrelated"],
        params={"source": "id or name of the first character",
                "target": "id or name of the other character"})
def remove_relationship(doc, *, _id, source, target):
    rels = doc.get("relationships")
    if isinstance(rels, list):
        kept = [r for r in rels if not (r.get("source") == source and r.get("target") == target)]
        if len(kept) != len(rels):
            doc["relationships"] = kept
            return
    raise ValueError(f"no relationship between {source!r} and {target!r}")


# ── Scene connections (the map: doc["connections"]:[…]) ──────────────────────────
# Link scenes/places into a navigable graph (source -> target). Ids come from the doc's
# locations/places/scenes when present (validated then); otherwise recorded as given.

def _place_ids(doc) -> set:
    """Every location/scene id in the story (for connect_scenes validation). Scenes live on
    locations now (the old `places` collection is gone)."""
    ids = set()
    for key in ("locations", "scenes"):
        for it in doc.get(key) or []:
            if isinstance(it, dict) and it.get("id"):
                ids.add(str(it["id"]))
                for sc in it.get("scenes") or []:
                    if isinstance(sc, dict) and sc.get("id"):
                        ids.add(str(sc["id"]))
    return ids


@script("connect_scenes", describe="Connect one scene/place to another (a way to move between them).",
        keywords=["connect", "leads to", "path", "exit", "door", "passage", "from here", "go to",
                  "links to", "adjacent", "route"],
        params={"source": "id of the scene/place you move FROM",
                "target": "id of the scene/place it leads TO",
                "label": "how the move reads (e.g. 'through the gate', 'upstairs') (optional)",
                "kind": "the type of transition (e.g. door, path, stairs, portal, secret) (optional)"})
def connect_scenes(doc, *, _id, source, target, label="", kind=""):
    if str(source) == str(target):
        raise ValueError("a scene can't connect to itself")
    known = _place_ids(doc)
    if known:
        for x in (source, target):
            if str(x) not in known:
                raise ValueError(f"no scene/place with id {x!r}")
    conns = doc.setdefault("connections", [])
    cur = next((c for c in conns if c.get("source") == source and c.get("target") == target), None)
    if cur is None:
        cur = {"id": _id, "source": source, "target": target, "label": "", "kind": ""}
        conns.append(cur)
    if label:
        cur["label"] = label
    if kind:
        cur["kind"] = kind


@script("disconnect_scenes", describe="Remove the connection from one scene/place to another.",
        keywords=["disconnect", "unlink", "no path", "block", "remove exit", "seal", "can't get to"],
        params={"source": "id of the scene/place the link starts from",
                "target": "id of the scene/place it currently leads to"})
def disconnect_scenes(doc, *, _id, source, target):
    conns = doc.get("connections")
    if isinstance(conns, list):
        kept = [c for c in conns if not (c.get("source") == source and c.get("target") == target)]
        if len(kept) != len(conns):
            doc["connections"] = kept
            return
    raise ValueError(f"no connection {source!r} -> {target!r}")

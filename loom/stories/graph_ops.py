"""Data-driven graph functions — story "function books".

A *function book* is an ordinary lorebook whose entries DEFINE operations on the
development graph (the JSON `{logline,wound,lie,truth,nodes:[…]}` shown beside the
workshop chat). Each function entry carries, in its `content`, a JSON spec:

    {
      "fn": "add_beat",
      "describe": "Add a new beat to the arc of change.",
      "params": { "title": "the beat title", "after": "id of the beat it follows (optional)" },
      "ops": [
        { "op": "add",    "path": "/nodes/-",            "value": {"id":"{{id}}","title":"{{title}}","next":[]} },
        { "op": "append", "path": "/nodes/#{{after}}/next", "value": "{{id}}" }
      ]
    }

The entry's `keywords` are the TRIGGER TERMS. At a turn, functions whose keywords
appear in the recent transcript are OFFERED to the model (surfaced in the prompt);
the model then DECIDES which to call, emitting `graph_ops:[{fn,params}]`. The backend
interpolates each call's params into the function's `ops` and applies them to the graph.

The op vocabulary is a tiny, generic JSON-patch dialect — the *mutation logic lives in
the data* (the book), not in hardcoded Python, so new functions are authored as lore.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphFunction:
    name: str
    describe: str
    params: dict           # {param_name: human description}
    ops: list              # the patch template
    keywords: list[str] = field(default_factory=list)
    writes: str = "graph"  # the State-doc LEVEL this script mutates (default: the dev graph)
    reads: list[str] = field(default_factory=list)  # levels it reads (advisory; for context assembly)
    impl: Any = None       # a registered code function (stories/scripts.py); when set it RUNS
                           # instead of the `ops` template — the standard tool-calling path.

    def to_prompt(self) -> str:
        ps = ", ".join(f"{k} ({v})" for k, v in self.params.items()) or "(no params)"
        return f"- {self.name}: {self.describe}\n    params: {ps}"


# ── Parsing function-book entries ────────────────────────────────────────────────

def parse_functions(entries: list) -> list[GraphFunction]:
    """Turn the function-shaped entries of a book into GraphFunctions. Two flavors:
      • a registered CODE script — the entry just names it (`{"fn": "add_beat"}`) and
        carries trigger keywords; logic/params/describe come from stories/scripts.py.
      • a legacy inline op-spec (`{fn, ops}`) — the homegrown DSL, still honored as a
        fallback for any function not (yet) in the registry.
    A registered name ALWAYS runs its code impl, even if the entry also carries `ops`
    (so old seeded entries upgrade to code automatically). Plain data entries are ignored."""
    from . import scripts as _S

    out: list[GraphFunction] = []
    for e in entries or []:
        if not getattr(e, "enabled", True):
            continue
        spec = _parse_spec(getattr(e, "content", "") or "")
        if spec is None:
            continue
        name = (spec.get("fn") or getattr(e, "title", "") or "").strip()
        if not name:
            continue
        reg = _S.get(name)
        ops = spec.get("ops") if isinstance(spec.get("ops"), list) else None
        if reg is None and ops is None:
            continue   # unknown function with no inline ops — nothing to run
        kws = [str(k) for k in (getattr(e, "keywords", []) or [])]
        if reg is not None:
            # Registered code script: CODE is canonical for logic/contract. The lorebook
            # entry only contributes trigger keywords (and falls back to the code defaults).
            describe, params, writes, impl = reg.describe, reg.params, reg.writes, reg.impl
        else:
            # Legacy inline op-spec (the DSL fallback): everything comes from the entry.
            describe = (spec.get("describe") or getattr(e, "title", "") or name).strip()
            params = spec.get("params") if isinstance(spec.get("params"), dict) else {}
            writes, impl = (spec.get("writes") or "graph").strip() or "graph", None
        out.append(GraphFunction(
            name=name, describe=describe, params=params, ops=ops or [],
            keywords=kws or (reg.keywords if reg else []),
            writes=writes or "graph",
            reads=[str(r) for r in (spec.get("reads") or []) if r],
            impl=impl,
        ))
    return out


def is_function_entry(entry) -> bool:
    """True if this lore entry is a FUNCTION (a graph-op function OR a pipeline-stage
    function), vs a plain data entry. Used to keep function specs OUT of the data-lore
    injection — they're behaviors, not world facts."""
    return _parse_spec(getattr(entry, "content", "") or "") is not None


def _parse_spec(content: str) -> dict | None:
    """A function spec is a JSON object that either carries graph `ops`, declares
    `kind:"stage"` (a pipeline-stage function — see stage_spec), or simply names a
    registered code script (`{"fn": "<registered name>"}` — the trigger-only form)."""
    content = content.strip()
    if not content.startswith("{"):
        return None
    try:
        spec = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(spec, dict):
        return None
    if "ops" in spec or spec.get("kind") == "stage":
        return spec
    fn = spec.get("fn")
    if fn:
        from . import scripts as _S
        if _S.get(str(fn)) is not None:
            return spec
    return None


# ── Stage functions (pipeline behavior expressed as lore) ────────────────────────
# A STAGE function is a lorebook entry whose content is `{kind:"stage", fn, schema?,
# image_workflow?}`. It names a pipeline stage (storyboard/characters/wardrobe/…); the
# stage's MODEL + SYSTEM come from the book's bound preset (Function→Lorebook→Preset).
# Unlike graph-op functions these are resolved by NAME (linear pipeline), not keyword-
# triggered. The output schema stays in code (structural); the spec only names which one.

@dataclass
class StageFunction:
    fn: str                       # the stage id (e.g. "characters")
    describe: str = ""
    schema: str = ""              # names a code-side output schema (optional)
    image_workflow: str = ""      # for stages that render (optional)
    writes: str = ""              # the State-doc level a stage writes (e.g. "canon"/"graph"); "" = caller decides
    reads: list[str] = field(default_factory=list)  # levels it reads (advisory)


def stage_spec(content: str) -> dict | None:
    """The stage spec of an entry's content, or None if it isn't a stage function."""
    spec = _parse_spec(content)
    if spec and spec.get("kind") == "stage" and str(spec.get("fn") or "").strip():
        return spec
    return None


def parse_stage_function(entry) -> StageFunction | None:
    """Turn a stage-shaped lore entry into a StageFunction (or None)."""
    spec = stage_spec(getattr(entry, "content", "") or "")
    if spec is None:
        return None
    return StageFunction(
        fn=str(spec["fn"]).strip(),
        describe=str(spec.get("describe") or getattr(entry, "title", "") or "").strip(),
        schema=str(spec.get("schema") or "").strip(),
        image_workflow=str(spec.get("image_workflow") or "").strip(),
        writes=str(spec.get("writes") or "").strip(),
        reads=[str(r) for r in (spec.get("reads") or []) if r],
    )


# ── Trigger offering (transcript → which functions are available this turn) ──────

def offered(functions: list[GraphFunction], transcript: str, cap: int = 12) -> list[GraphFunction]:
    """The functions to surface to the model this turn. Trigger keywords PRIORITISE (a
    keyword hit, or no keywords, sorts first); the rest of an attached book's functions
    still follow up to `cap` — attaching the book is itself the "available here" signal, and
    the model ultimately decides which to call. (Keywords also gate which survive the cap
    for large books, and feed the future deterministic trigger→op path.)"""
    text = (transcript or "").lower()
    matched: list[GraphFunction] = []
    rest: list[GraphFunction] = []
    for fn in functions:
        hit = (not fn.keywords) or any(
            re.search(rf"\b{re.escape(k.lower())}\b", text) for k in fn.keywords if k)
        (matched if hit else rest).append(fn)
    return (matched + rest)[:cap]


def functions_prompt(fns: list[GraphFunction]) -> str:
    return "AVAILABLE GRAPH FUNCTIONS (call by name via graph_ops):\n" + "\n".join(f.to_prompt() for f in fns)


# The structured output the model returns to call functions. `params` is a JSON STRING
# (not a nested object): strict structured-output modes (OpenAI/OpenRouter) can't represent
# a free-form object with arbitrary keys, so the model writes the params as a JSON string and
# we parse it. additionalProperties:false everywhere keeps strict mode happy.
GRAPH_OPS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "graph_ops": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "fn": {"type": "string", "description": "the exact function name to call"},
                    "params": {"type": "string",
                               "description": "a JSON object string of the function's params, "
                                              "e.g. {\"title\": \"First crack\", \"after\": \"b0\"}"},
                },
                "required": ["fn", "params"],
            },
        }
    },
    "required": ["graph_ops"],
}


def ops_schema(functions: list[GraphFunction]) -> dict:
    """Build the structured-output schema with `params` as a concrete object whose properties
    are the UNION of the offered functions' param names. Free-form objects break strict mode
    (OpenAI/OpenRouter return `{}`); concrete named props work everywhere. Strict mode also
    requires every property in `required`, so optional params are typed nullable — the model
    fills the relevant ones and nulls the rest."""
    props: dict = {}
    for fn in functions:
        for k in fn.params:
            if k == "order":
                props[k] = {"type": ["array", "null"], "items": {"type": "string"}}
            else:
                props.setdefault(k, {"type": ["string", "null"]})
    params_schema = {"type": "object", "additionalProperties": False,
                     "properties": props, "required": list(props)}
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "graph_ops": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"fn": {"type": "string", "description": "the exact function name"},
                               "params": params_schema},
                "required": ["fn", "params"]}}},
        "required": ["graph_ops"],
    }


def _call_params(call: dict) -> dict:
    """Read a call's params — an object (drop null/blank values) or a JSON string (legacy)."""
    p = call.get("params")
    if isinstance(p, str) and p.strip():
        try: p = json.loads(p)
        except (ValueError, TypeError): p = {}
    if not isinstance(p, dict):
        return {}
    return {k: v for k, v in p.items() if v is not None and v != ""}


# ── Applying ops (the generic JSON-patch dialect) ────────────────────────────────

def apply_calls(state: dict, calls: list, functions: list[GraphFunction]) -> tuple[dict, list[dict]]:
    """Apply the model's function CALLS to a State doc (level-aware). Each call's ops are
    applied to the level the function declares via `writes` (default "graph"), through the
    one unified delta engine in `state_doc`. Returns (new_state, log). Never raises — a bad
    call is skipped and logged."""
    import copy

    from . import state_doc as _SD   # lazy: state_doc imports this module (avoid a cycle)

    st = _SD.normalize(copy.deepcopy(state))
    by_name = {f.name: f for f in functions}
    log: list[dict] = []

    for call in calls or []:
        if not isinstance(call, dict):
            continue
        fn = by_name.get(str(call.get("fn", "")))
        raw = _call_params(call)
        if fn is None:
            log.append({"fn": call.get("fn"), "ok": False, "error": "unknown function"})
            continue
        level = (fn.writes or "graph").strip() or "graph"
        if fn.impl is not None:
            # CODE path (tool-calling): run the registered function on the level's doc.
            root = st["levels"]
            if not isinstance(root.get(level), (dict, list)):
                root[level] = {}
            kw = {k: v for k, v in raw.items() if k in (fn.params or {})}
            try:
                fn.impl(root[level], _id=_new_id(), **kw)
                st["revision"] = int(st.get("revision") or 0) + 1
                log.append({"fn": fn.name, "ok": True, "applied": 1, "params": kw, "level": level})
            except Exception as exc:  # noqa: BLE001 — one bad call never sinks the batch
                log.append({"fn": fn.name, "ok": False, "error": str(exc)})
        else:
            # Legacy DSL path: interpolate + apply the inline op-spec template.
            params = {**raw, "id": raw.get("id") or _new_id()}
            st, sub = _SD.apply_ops(st, fn.ops, level=level, params=params)
            applied = sum(1 for s in sub if s.get("ok") and s.get("changed"))
            log.append({"fn": fn.name, "ok": True, "applied": applied, "params": params, "level": level})
            log.extend(s for s in sub if not s.get("ok"))   # surface any failed sub-ops
    return st, log


def apply_ops(graph: dict, calls: list, functions: list[GraphFunction]) -> tuple[dict, list[dict]]:
    """Back-compat shim for the workshop dev-graph endpoint: apply calls to a BARE graph
    dict. Wraps the graph as the "graph" level of a State doc, delegates to apply_calls,
    then unwraps. Returns (new_graph, log)."""
    import copy

    from . import state_doc as _SD

    state = {"levels": {"graph": copy.deepcopy(graph or {})}, "revision": 0}
    new_state, log = apply_calls(state, calls, functions)
    return _SD.get_level(new_state, "graph", {}), log


def _new_id() -> str:
    return "n" + uuid.uuid4().hex[:6]


def _interp(value: Any, params: dict) -> Any:
    """Recursively replace {{param}} placeholders. A string that is EXACTLY "{{param}}"
    yields the raw param value (preserving lists/dicts/numbers — e.g. a reorder's id list);
    otherwise placeholders are substituted as text."""
    if isinstance(value, str):
        whole = re.fullmatch(r"\{\{\s*([\w]+)\s*\}\}", value)
        if whole and whole.group(1) in params:
            return params[whole.group(1)]
        return re.sub(r"\{\{\s*([\w]+)\s*\}\}", lambda m: str(params.get(m.group(1).strip(), "")), value)
    if isinstance(value, dict):
        return {k: _interp(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_interp(v, params) for v in value]
    return value


def _apply_one(graph: dict, op: dict) -> bool:
    """Apply a single interpolated patch op. Returns True if it changed the graph.
    Ops: set/replace, add (append with /-), append (push to list), remove, pull."""
    kind = (op.get("op") or "").lower()
    path = op.get("path") or ""
    value = op.get("value")
    parent, key = _navigate(graph, path)
    if parent is None:
        return False   # unresolved path (e.g. an optional {{after}} that wasn't provided)

    if kind in ("set", "replace"):
        parent[key] = value
        return True
    if kind == "add":
        if key == "-" and isinstance(parent, list):
            parent.append(value); return True
        parent[key] = value; return True
    if kind == "append":
        target = parent[key] if (isinstance(parent, dict) and key in parent) or \
            (isinstance(parent, list) and isinstance(key, int) and key < len(parent)) else None
        if isinstance(target, list):
            target.append(value); return True
        return False
    if kind == "pull":
        target = parent[key] if key in parent else None
        if isinstance(target, list) and value in target:
            target.remove(value); return True
        return False
    if kind == "remove":
        if isinstance(parent, list) and isinstance(key, int):
            del parent[key]; return True
        if isinstance(parent, dict) and key in parent:
            del parent[key]; return True
        return False
    if kind == "move":
        # Relocate the list element whose id == `value` to just after the element whose
        # id == op["after"] (or to the end when `after` is missing/blank).
        target = parent[key] if (isinstance(parent, dict) and isinstance(parent.get(key), list)) else None
        if not isinstance(target, list):
            return False
        i = _index_of(target, value)
        if i is None:
            return False
        el = target.pop(i)
        after = op.get("after")
        j = _index_of(target, after) if after else None
        target.insert((j + 1) if j is not None else len(target), el)
        return True
    if kind == "reorder":
        # Reorder the list at `path` so its elements follow the id order in `value`
        # (ids not listed keep their original relative order, appended after).
        target = parent[key] if (isinstance(parent, dict) and isinstance(parent.get(key), list)) else None
        if not isinstance(target, list) or not isinstance(value, list):
            return False
        order = [str(x) for x in value]
        ranked = sorted(range(len(target)),
                        key=lambda i: (order.index(str(target[i].get("id"))) if isinstance(target[i], dict)
                                       and str(target[i].get("id")) in order else len(order) + i))
        parent[key] = [target[i] for i in ranked]
        return True
    return False


def _navigate(obj: Any, path: str):
    """Walk a path like '/nodes/#b2/next' and return (parent_container, last_key).
    Tokens: '-' (append slot), '#<id>' (list element by its `id`), <int> (index), or a key.
    Returns (None, None) when a token can't be resolved (so optional ops no-op)."""
    tokens = [t for t in path.split("/") if t != ""]
    if not tokens:
        return None, None
    cur = obj
    for tok in tokens[:-1]:
        nxt = _step(cur, tok)
        if nxt is None:
            return None, None
        cur = nxt
    return cur, _last_key(cur, tokens[-1])


def _step(container: Any, tok: str):
    if tok.startswith("#"):
        return _by_id(container, tok[1:])
    if isinstance(container, list):
        if tok.lstrip("-").isdigit():
            i = int(tok)
            return container[i] if -len(container) <= i < len(container) else None
        return None
    if isinstance(container, dict):
        return container.get(tok)
    return None


def _last_key(container: Any, tok: str):
    if tok == "-":
        return "-"
    if tok.startswith("#"):
        idx = _index_of(container, tok[1:])
        return idx if idx is not None else "-"   # unresolved → harmless append slot
    if isinstance(container, list) and tok.lstrip("-").isdigit():
        return int(tok)
    return tok


def _by_id(container: Any, node_id: str):
    idx = _index_of(container, node_id)
    return container[idx] if idx is not None else None


def _index_of(container: Any, node_id: str):
    if not isinstance(container, list) or not node_id:
        return None
    for i, el in enumerate(container):
        if isinstance(el, dict) and str(el.get("id")) == str(node_id):
            return i
    return None

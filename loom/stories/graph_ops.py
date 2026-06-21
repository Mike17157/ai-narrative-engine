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

    def to_prompt(self) -> str:
        ps = ", ".join(f"{k} ({v})" for k, v in self.params.items()) or "(no params)"
        return f"- {self.name}: {self.describe}\n    params: {ps}"


# ── Parsing function-book entries ────────────────────────────────────────────────

def parse_functions(entries: list) -> list[GraphFunction]:
    """Turn the function-shaped entries of a book into GraphFunctions. A plain
    (data) entry — whose content isn't a function spec — is ignored here."""
    out: list[GraphFunction] = []
    for e in entries or []:
        if not getattr(e, "enabled", True):
            continue
        spec = _parse_spec(getattr(e, "content", "") or "")
        if spec is None:
            continue
        name = (spec.get("fn") or getattr(e, "title", "") or "").strip()
        if not name or not isinstance(spec.get("ops"), list):
            continue
        out.append(GraphFunction(
            name=name,
            describe=(spec.get("describe") or getattr(e, "title", "") or name).strip(),
            params=spec.get("params") if isinstance(spec.get("params"), dict) else {},
            ops=spec["ops"],
            keywords=[str(k) for k in (getattr(e, "keywords", []) or [])],
        ))
    return out


def is_function_entry(entry) -> bool:
    """True if this lore entry is a graph function (vs a plain data entry). Used to keep
    function specs OUT of the data-lore injection — they're functions, not world facts."""
    return _parse_spec(getattr(entry, "content", "") or "") is not None


def _parse_spec(content: str) -> dict | None:
    content = content.strip()
    if not content.startswith("{"):
        return None
    try:
        spec = json.loads(content)
    except (ValueError, TypeError):
        return None
    return spec if isinstance(spec, dict) and "ops" in spec else None


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

def apply_ops(graph: dict, calls: list, functions: list[GraphFunction]) -> tuple[dict, list[dict]]:
    """Apply the model's `graph_ops` calls to a COPY of the graph. Returns
    (new_graph, log) where log records each call's outcome. Never raises — a bad call
    is skipped and logged."""
    import copy

    g = copy.deepcopy(graph or {})
    by_name = {f.name: f for f in functions}
    log: list[dict] = []

    for call in calls or []:
        if not isinstance(call, dict):
            continue
        fn = by_name.get(str(call.get("fn", "")))
        params = _call_params(call)
        if fn is None:
            log.append({"fn": call.get("fn"), "ok": False, "error": "unknown function"})
            continue
        params = {**params, "id": params.get("id") or _new_id()}
        applied = 0
        for op in fn.ops:
            try:
                if _apply_one(g, _interp(op, params)):
                    applied += 1
            except Exception as exc:  # noqa: BLE001 — one bad sub-op never sinks the call
                log.append({"fn": fn.name, "op": op.get("op"), "ok": False, "error": str(exc)})
        log.append({"fn": fn.name, "ok": True, "applied": applied, "params": params})
    return g, log


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

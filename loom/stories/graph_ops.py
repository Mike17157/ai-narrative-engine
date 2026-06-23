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
    """A model-callable function, resolved from the registry (stories/scripts.py). It pairs
    the registered code `impl` with this deployment's trigger `keywords` (from the lorebook
    entry). The model calls it by `name`; `impl` runs against the `writes` level's doc."""
    name: str
    describe: str
    params: dict           # {param_name: human description} — the model schema
    keywords: list[str] = field(default_factory=list)
    writes: str = "graph"  # the State-doc LEVEL this script mutates (default: the dev graph)
    reads: list[str] = field(default_factory=list)  # levels it reads (advisory; for context assembly)
    impl: Any = None       # the registered code function (def impl(doc, *, _id, **params))

    def to_prompt(self) -> str:
        ps = ", ".join(f"{k} ({v})" for k, v in self.params.items()) or "(no params)"
        return f"- {self.name}: {self.describe}\n    params: {ps}"


# ── Parsing function-book entries ────────────────────────────────────────────────

def parse_functions(entries: list) -> list[GraphFunction]:
    """Resolve the function-book entries into callable GraphFunctions. An entry names a
    registered code script (`{"fn": "add_beat"}`) and carries the trigger keywords; the
    logic/params/describe are CANONICAL in code (stories/scripts.py). An entry whose `fn`
    isn't registered (or a plain data entry) is ignored — there's no inline-code path."""
    from . import scripts as _S

    out: list[GraphFunction] = []
    for e in entries or []:
        if not getattr(e, "enabled", True):
            continue
        spec = _parse_spec(getattr(e, "content", "") or "")
        if spec is None:
            continue
        name = (spec.get("fn") or getattr(e, "title", "") or "").strip()
        reg = _S.get(name)
        if reg is None:
            continue   # not a registered script — nothing to run
        kws = [str(k) for k in (getattr(e, "keywords", []) or [])]
        out.append(GraphFunction(
            name=name, describe=reg.describe, params=reg.params,
            keywords=kws or reg.keywords, writes=reg.writes,
            reads=[str(r) for r in (spec.get("reads") or []) if r],
            impl=reg.impl,
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


# ── Applying calls (run the registered code impl on the target level) ────────────

def _new_id() -> str:
    return "n" + uuid.uuid4().hex[:6]


def apply_calls(state: dict, calls: list, functions: list[GraphFunction]) -> tuple[dict, list[dict]]:
    """Apply the model's function CALLS to a State doc (level-aware): each call runs its
    registered code `impl` against the doc at the function's `writes` level. Returns
    (new_state, log). Never raises — a bad call is skipped and logged."""
    import copy

    from . import state_doc as _SD

    st = _SD.normalize(copy.deepcopy(state))
    by_name = {f.name: f for f in functions}
    log: list[dict] = []

    for call in calls or []:
        if not isinstance(call, dict):
            continue
        fn = by_name.get(str(call.get("fn", "")))
        if fn is None or fn.impl is None:
            log.append({"fn": call.get("fn"), "ok": False, "error": "unknown function"})
            continue
        level = (fn.writes or "graph").strip() or "graph"
        root = st["levels"]
        if not isinstance(root.get(level), (dict, list)):
            root[level] = {}
        kw = {k: v for k, v in _call_params(call).items() if k in (fn.params or {})}
        try:
            fn.impl(root[level], _id=_new_id(), **kw)
            st["revision"] = int(st.get("revision") or 0) + 1
            log.append({"fn": fn.name, "ok": True, "applied": 1, "params": kw, "level": level})
        except Exception as exc:  # noqa: BLE001 — one bad call never sinks the batch
            log.append({"fn": fn.name, "ok": False, "error": str(exc)})
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

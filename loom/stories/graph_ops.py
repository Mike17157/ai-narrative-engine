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
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphFunction:
    """A model-callable function, resolved from the registry (stories/scripts.py). It pairs
    the registered code `impl` with this deployment's trigger `keywords` (from the lorebook
    entry). The model calls it by `name`; `impl` runs against the `writes` level's doc."""
    name: str
    describe: str
    params: dict           # {param_name: rich spec} — the model schema
    keywords: list[str] = field(default_factory=list)
    writes: str = "graph"  # the State-doc LEVEL this script mutates (default: the dev graph)
    reads: list[str] = field(default_factory=list)  # levels it reads (advisory; for context assembly)
    impl: Any = None       # the code function. kind "doc": impl(doc, *, _id, **params) mutates the
                           # doc. kind "action": impl(ctx, body) runs with ctx + returns an artifact.
    kind: str = "doc"      # "doc" (mutates the artifact) | "action" (runs w/ ctx, yields an artifact)


# ── Parsing function-book entries ────────────────────────────────────────────────

def parse_functions(entries: list) -> list[GraphFunction]:
    """Resolve the function-book entries into callable GraphFunctions. An entry names a
    registered code script (`{"fn": "add_beat"}`) and carries the trigger keywords; the
    logic/params/describe are CANONICAL in code (stories/scripts.py). An entry whose `fn`
    isn't registered (or a plain data entry) is ignored — there's no inline-code path."""
    from . import scripts as _S
    from . import stage_tools as _ST

    out: list[GraphFunction] = []
    for e in entries or []:
        if not getattr(e, "enabled", True):
            continue
        spec = _parse_spec(getattr(e, "content", "") or "")
        if spec is None:
            continue
        name = (spec.get("fn") or getattr(e, "title", "") or "").strip()
        kws = [str(k) for k in (getattr(e, "keywords", []) or [])]
        reads = [str(r) for r in (spec.get("reads") or []) if r]
        reg = _S.get(name)
        if reg is not None:                       # a graph SCRIPT — mutates the doc
            out.append(GraphFunction(
                name=name, describe=reg.describe, params=reg.params,
                keywords=kws or reg.keywords, writes=reg.writes, reads=reads,
                impl=reg.impl, kind="doc"))
            continue
        act = _ST.get(name)                        # a STAGE/ACTION tool — runs w/ ctx, yields an artifact
        if act is not None:
            out.append(GraphFunction(
                name=name, describe=act.describe, params=act.params,
                keywords=kws or act.keywords, reads=reads,
                impl=act.run, kind="action"))
            continue
        # neither registered — nothing to run
    return out


def resolve_functions(names: list[str]) -> list[GraphFunction]:
    """Resolve registered function NAMES straight to callable GraphFunctions — the direct path for
    the chat agent, whose modes list their functions in story_agent.json instead of going through a
    lorebook (the code registry in scripts.py / stage_tools.py is canonical). Unknown/dup names are
    skipped. Books are still used by the pipeline + for preset/model binding, just not for this."""
    from . import scripts as _S
    from . import stage_tools as _ST

    out: list[GraphFunction] = []
    seen: set = set()
    for raw in names or []:
        name = str(raw).strip()
        if not name or name in seen:
            continue
        reg = _S.get(name)
        if reg is not None:
            out.append(GraphFunction(name=name, describe=reg.describe, params=reg.params,
                                     keywords=reg.keywords, writes=reg.writes, impl=reg.impl, kind="doc"))
            seen.add(name)
            continue
        act = _ST.get(name)
        if act is not None:
            out.append(GraphFunction(name=name, describe=act.describe, params=act.params,
                                     keywords=act.keywords, impl=act.run, kind="action"))
            seen.add(name)
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


def tools_spec(functions: list[GraphFunction]) -> list[dict]:
    """Native tool specs (one per offered function) for the provider `tools=` API — the
    standard LLM tool-calling shape: each tool gets its OWN parameter schema, so the model
    calls them by name with a typed argument object. (This replaces the old `ops_schema`
    union-of-all-params blob, which strict structured-output forced into one flat nullable
    object.) Each param's rich spec (from scripts.py: {desc,type,enum,required}) drives the
    schema — `enum` constrains a fixed-choice param so the model can't pass an invalid value;
    only genuinely-required params land in `required`. Tolerates a bare-string param (legacy)."""
    out: list[dict] = []
    for fn in functions:
        props: dict = {}
        required: list[str] = []
        for k, spec in fn.params.items():
            if not isinstance(spec, dict):   # legacy: bare description string
                spec = {"desc": str(spec), "type": "string", "enum": None,
                        "required": "optional" not in str(spec).lower()}
            if spec.get("type") == "array":
                p = {"type": "array", "items": {"type": "string"}, "description": spec.get("desc", "")}
            else:
                p = {"type": spec.get("type") or "string", "description": spec.get("desc", "")}
            if spec.get("enum"):
                p["enum"] = list(spec["enum"])
            props[k] = p
            if spec.get("required", True):
                required.append(k)
        out.append({
            "name": fn.name,
            "description": fn.describe,
            "parameters": {"type": "object", "additionalProperties": False,
                           "properties": props, "required": required},
        })
    return out


# ── CLI-string tool protocol ────────────────────────────────────────────────────
# The model emits tool calls as CLI COMMAND STRINGS instead of native function-calling. Why:
# models have vast training exposure to CLI syntax, and the comparison harness showed they emit
# correct field paths (e.g. `fields.role`) from a CLI signature where they guessed wrong from a
# looser structured schema. The runner parses each line with shlex and dispatches to the SAME
# canonical scripts.invoke — no subprocess, no shell, no escaping/injection surface. The "CLI" is
# a string PROTOCOL the model speaks; Python parses and dispatches it.

# Map agent tool name -> (cli_subcommand, flag_for_param). Derived from cli_story.py's signatures
# so the vocabulary stays identical across the human CLI and the agent protocol.
_CLI_FLAG = {  # agent_param -> --flag
    "question": "-q", "name": "-n", "description": "-d", "value": "-v",
    "source": "--source", "target": "--target", "nature": "--nature",
    "dynamic": "--dynamic", "note": "--note", "field": "--field",
}


def cli_reference(functions: list[GraphFunction]) -> str:
    """Render the offered tools as a CLI reference block for the system prompt. Each tool is one
    example line showing the exact flag spelling, so the model emits well-formed commands. Replaces
    the native `tools=` schema presentation."""
    lines = ["TOOLS — call any of these by emitting one `tool <name> <args>` line per call. "
             "Args use the flags shown; quote prose values. Emit ONLY tool lines (no prose, no "
             "code fence) when you want to call a tool; otherwise reply in prose to the writer."]
    for fn in functions:
        parts = [f"tool {fn.name}"]
        for k, spec in fn.params.items():
            if not isinstance(spec, dict):
                spec = {"desc": str(spec)}
            flag = _CLI_FLAG.get(k, f"--{k}")
            req = "" if (spec.get("required", True) if isinstance(spec, dict) else True) else " (optional)"
            parts.append(f"{flag} <{k}>{req}")
        lines.append("  " + " ".join(parts))
    lines.append("Example: tool set_relationship --source eli --target mara --nature mentor "
                 "--dynamic \"wary respect\" --note \"old debt\"")
    return "\n".join(lines)


def parse_cli_calls(text: str, functions: list[GraphFunction]) -> list[dict]:
    """Parse the model's CLI-string reply into call dicts: [{fn, params}].

    Each line beginning with `tool ` is parsed by shlex (handles quoted prose safely); the first
    token after `tool` is the function name; remaining tokens are `--flag value` pairs, mapped back
    through `_CLI_FLAG` (reversed) to the canonical param names. Non-tool lines are ignored (the
    model may interleave prose; the caller extracts it separately). Malformed lines are skipped
    (logged by the caller via apply_ops) — one bad line never sinks the batch."""
    import shlex
    # reverse map: --flag -> agent_param
    flag_to_param = {v: k for k, v in _CLI_FLAG.items()}
    known = {f.name for f in functions}
    calls: list[dict] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("tool "):
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue   # unmatched quote — skip this line
        if len(tokens) < 2:
            continue
        fn_name = tokens[1]
        if fn_name not in known:
            continue
        params: dict = {}
        i = 2
        while i < len(tokens):
            tok = tokens[i]
            if tok.startswith("-"):
                flag = tok
                # support both --flag and -f; also --flag=value form
                if "=" in flag and flag.index("=") > 0:
                    flag, val = flag.split("=", 1)
                    param = flag_to_param.get(flag) or flag.lstrip("-")
                    params[param] = val
                    i += 1
                    continue
                param = flag_to_param.get(flag) or flag.lstrip("-")
                # Every flag in this protocol takes a value, so consume the next token even if it
                # was quoted and starts with '-' (e.g. --note "-lipstick on the collar"). shlex has
                # already stripped the quotes, so a leading dash here is content, not a new flag.
                if i + 1 < len(tokens):
                    params[param] = tokens[i + 1]
                    i += 2
                else:
                    params[param] = True   # trailing flag with no value
                    i += 1
            else:
                i += 1   # stray positional; skip
        calls.append({"fn": fn_name, "params": params})
    return calls


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

def apply_calls(state: dict, calls: list, functions: list[GraphFunction]) -> tuple[dict, list[dict]]:
    """Apply the model's function CALLS to a State doc (level-aware): each call runs its
    registered code script (via scripts.invoke) against the doc at the function's `writes`
    level. Returns (new_state, log). Never raises — a bad call is skipped and logged."""
    import copy

    from . import scripts as _S
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
        try:
            _S.invoke(fn.name, root[level], _call_params(call))
            st["revision"] = int(st.get("revision") or 0) + 1
            log.append({"fn": fn.name, "ok": True, "applied": 1, "level": level})
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

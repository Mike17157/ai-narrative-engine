"""Story-authoring agent runtime.

Owns agent modes and configuration, prompt assembly, direct turns, and graph-based
multi-step execution. This is the single canonical module for story-agent behavior.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic_graph import GraphBuilder, StepContext

from ..records import graph as GO
from ..authoring import scripts as _S
from ..authoring import stages as _ST

"""Legacy compatibility shim for the story chat agent's modes.

The agent definitions (persona + tools + triggers + craft section + injects) now live entirely in
`configs/story_agent.json` under `agents`. This module exists ONLY to translate the old internal
mode keys (used in persisted `active_behavior` signatures) to the new readable keys, so a session
saved before the rename still resolves its active agent after an upgrade.

Old key              → New key
  _smith_tools      → cast
  _location_fns     → locations
  _scene_fns        → scenes
  _wardrobe_fns     → wardrobe
  _story_tools      → story

This map and module can be deleted once no persisted sessions reference the old keys.
"""

LEGACY_KEY_MAP: dict[str, str] = {
    "_smith_tools": "cast",
    "_location_fns": "locations",
    "_scene_fns": "scenes",
    "_wardrobe_fns": "wardrobe",
    "_story_tools": "story",
}


def translate_key(key: str) -> str:
    """Map a (possibly old) agent key to its current name. Old keys translate; new/unknown keys
    pass through unchanged so the lookup either way is safe."""
    return LEGACY_KEY_MAP.get(key, key)


# --- configuration and mode selection ---

"""Loader for the story chat agent's declarative config (`configs/story_agent.json`) — the single
source for the agent's full definition: base system prompt, each agent's persona + tools + triggers +
craft section + injects, the conditional directives (world/draft/propose), craft anchors, grounding
rules, tool policy. Deep-merged over `_DEFAULTS` so the app runs (degraded) if the file is missing.

The agent DEFINITION is data (this file); the tool IMPLEMENTATIONS are code (scripts.py /
stage_tools.py). `load_config` VALIDATES every tool name against the code registry at load time and
raises loudly if a name doesn't resolve — so drift between the JSON and the registry is caught at
startup, never silently dropped. No heavy imports → both `agent.py` and `grounding.py` use it.
"""

import json
import math
import re
from pathlib import Path


# Minimal safety-net defaults. The RICH config lives in configs/story_agent.json (the editable source);
# these only keep the agent functional if that file is deleted.
_DEFAULTS: dict = {
    "system": "You are the writer's story collaborator. Discuss the story, and when the writer asks "
              "for a change, make it by calling the right tool. Always reply with something.",
    "tool_rules": "Fill every tool param using exact ids from the document; set all fields when adding.",
    "story_context_fields": ["title", "premise", "tone", "themes", "logline", "heart",
                             "arcs", "cast"],
    "tool_policy": {"all_tools_cap": 40, "attached_cap": 12},
    "routing": {"semantic_floor": 0.55},
    "agents": {},
    "directives": {},
}

_cache: dict[str, dict] = {}
_vec_cache: dict[str, dict] = {}  # str(root) -> {agent_id: passage_vector}; same lifecycle as _cache


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _registered_tool_names() -> set[str]:
    """Every tool name the code registry knows (scripts.py + stage_tools.py). Used to VALIDATE the
    `tools` lists in story_agent.json at load — a typo there now fails loudly instead of silently
    dropping the tool from the agent's menu."""
    names: set[str] = set()
    try:
        from ..authoring import scripts as _S
        names.update(getattr(_S, "REGISTRY", {}).keys())
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..authoring import stages as _ST
        names.update(getattr(_ST, "TOOLS", {}).keys())
    except Exception:  # noqa: BLE001
        pass
    return names


def _validate_agent_tools(agents: dict) -> None:
    """Raise if any agent lists a tool name the registry doesn't know. Catches JSON/registry drift
    at load time — the alternative (silent skip in resolve_functions) would leave an agent quietly
    missing a tool and the writer wondering why a command does nothing."""
    valid = _registered_tool_names()
    if not valid:   # registry not importable in this context (e.g. a unit test) — skip validation
        return
    for aid, a in (agents or {}).items():
        for name in (a.get("tools") or []):
            if name not in valid:
                raise ValueError(
                    f"story_agent.json: agent '{aid}' lists tool '{name}' which is not registered. "
                    f"Valid tools: {', '.join(sorted(valid))}")



def _join_prose(cfg: dict) -> None:
    """Normalize array-valued prose fields (persona/example/directives) back to single strings.
    The JSON stores these as arrays of paragraphs for human readability; the assembler expects
    strings. Joins paragraphs with a blank line between them — byte-identical to the original
    pre-array form."""
    def join(v):
        return "\n\n".join(v) if isinstance(v, list) else v
    for a in (cfg.get("agents") or {}).values():
        if isinstance(a, dict):
            for k in ("persona", "example"):
                if k in a:
                    a[k] = join(a[k])
    d = cfg.get("directives") or {}
    for k in ("world", "propose"):
        if k in d:
            d[k] = join(d[k])
    draft = d.get("draft") or {}
    for k, v in list(draft.items()):
        draft[k] = join(v)


def load_config(root: Path, *, fresh: bool = False) -> dict:
    """The merged agent config (cached per root). `fresh=True` re-reads the file (after an edit).
    Validates tool names against the registry and raises on drift."""
    key = str(root)
    if fresh:
        _vec_cache.pop(key, None)
    if not fresh and key in _cache:
        return _cache[key]
    cfg = dict(_DEFAULTS)
    p = Path(root) / "configs" / "story_agent.json"
    if p.is_file():
        try:
            data = {k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items()
                    if not k.startswith("_")}
            cfg = _deep_merge(_DEFAULTS, data)
        except Exception:  # noqa: BLE001 — a malformed file falls back to defaults, never crashes
            cfg = dict(_DEFAULTS)
    # The full agent definition (persona + tools + triggers + craft + inject) comes from JSON now.
    # Validate every tool name resolves against the code registry — fail loud on drift.
    _validate_agent_tools(cfg.get("agents") or {})
    cfg.setdefault("agents", {})
    cfg.setdefault("directives", {})
    _join_prose(cfg)
    _cache[key] = cfg
    return cfg


def modes_list(root: Path) -> list[dict]:
    """Selectable agents for the UI dropdown: [{id, label}] from the config."""
    return [{"id": aid, "label": a.get("label", aid)}
            for aid, a in (load_config(root).get("agents") or {}).items()]


def _keyword_modes(root: Path, text: str) -> list[str]:
    """Agent ids whose trigger keywords fire on `text` (word-boundary), in config order."""
    t = (text or "").lower()
    return [aid for aid, a in (load_config(root).get("agents") or {}).items()
            if any(re.search(rf"\b{re.escape(str(k).lower())}\b", t) for k in (a.get("triggers") or []) if k)]


def _cos(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def _mode_vectors(root: Path, modes: dict) -> dict:
    """Passage embedding per mode, cached per root. {} if embeddings are unavailable — caller then
    has nothing to match and the keyword result stands.

    We embed the TRIGGER terms only, not the persona prose: measured on bge-small, the abstract
    persona text ('character', 'story', 'world', 'concrete') is shared across modes and collapses the
    cosine separation; the concrete trigger nouns are the discriminative signal."""
    key = str(root)
    if key in _vec_cache:
        return _vec_cache[key]
    try:
        from ...server.services import embeddings as _emb
    except Exception:  # noqa: BLE001
        return {}
    ids, texts = [], []
    for mid, m in modes.items():
        ids.append(mid)
        texts.append(", ".join(m.get("triggers") or []))
    vecs = _emb.embed_passages(texts) if texts else None
    out = dict(zip(ids, vecs)) if vecs else {}
    if out:
        _vec_cache[key] = out
    return out


def _semantic_mode(root: Path, text: str) -> list[str]:
    """Best agent by cosine similarity of `text` to each agent, if it clears `semantic_floor`; else []."""
    cfg = load_config(root)
    agents = cfg.get("agents") or {}
    if not agents or not (text or "").strip():
        return []
    try:
        from ...server.services import embeddings as _emb
    except Exception:  # noqa: BLE001
        return []
    qv = _emb.embed_query(text)
    if qv is None:
        return []
    mvs = _mode_vectors(root, agents)
    if not mvs:
        return []
    score, mid = max(((_cos(qv, v), m) for m, v in mvs.items()), default=(0.0, ""))
    floor = (cfg.get("routing") or {}).get("semantic_floor", 0.55)
    return [mid] if mid and score >= floor else []


def match_modes(root: Path, text: str) -> list[str]:
    """Route the request to mode(s): exact keyword triggers first (precise, deterministic); if none
    fire, fall back to semantic similarity (recall for phrasing the triggers didn't anticipate).
    Returns [] when nothing is confident enough — the agent then runs as the general collaborator."""
    return _keyword_modes(root, text) or _semantic_mode(root, text)


def demo() -> None:
    # _cos: the only non-trivial math here. (keyword routing is regex, exercised live.)
    assert _cos([1, 0], [1, 0]) == 1.0
    assert _cos([1, 0], [0, 1]) == 0.0
    assert round(_cos([1, 0], [-1, 0]), 6) == -1.0
    assert _cos([], []) == 0.0 and _cos([0, 0], [1, 1]) == 0.0  # zero-vector guard
    # keyword path still works against the live config; semantic only if fastembed is installed.
    root = Path(__file__).resolve().parents[2]
    assert "wardrobe" in _keyword_modes(root, "design her outfit")
    try:
        from ...server.services import embeddings as _emb
        sem_ok = _emb.available()
    except Exception:  # noqa: BLE001
        sem_ok = False
    if sem_ok:
        # phrasing with NO trigger word should still reach wardrobe via semantics
        hit = match_modes(root, "what should she put on for the gala")
        print("semantic routing live — 'what should she put on…' ->", hit)
    else:
        print("semantic routing unavailable (no fastembed) — keyword path only, graceful fallback ok")
    print("agent_config demo ok")


if __name__ == "__main__":
    demo()


# --- direct agent turns ---

"""The story chat agent — the fixed STRUCTURE that runs a turn. All authored text/rules come from
`configs/story_agent.json` (see agent_config.py); the lorebooks supply the TOOLS (function books)
and the injected GUIDANCE (the adaptation basis + the literary-minimalist stance). This was extracted out of the
fat `story_story_graph` router endpoint — the router is now a thin wrapper. See [[agents-and-scripts]].
"""

import json
import re



def _story_context(ctx, skey: str, fields: list) -> str:
    """A compact STORY CONTEXT block so the agent can converse about premise/ending/arcs/cast — not
    just the relationship/location graph it's handed. Fields are selected by the config."""
    skey = (skey or "").strip()
    st = ctx.base_settings.stories.get(skey) if skey else None
    if st is None:
        return ""
    sd = st.model_dump()
    sb = sd.get("storyboard") or {}
    cast_names = [getattr(ctx.base_settings.characters.get(m.get("character")), "name", m.get("character"))
                 for m in (sd.get("cast") or []) if m.get("character")]
    vals = {
        "title": sd.get("name"), "premise": sd.get("premise"), "tone": sd.get("tone"),
        "themes": ", ".join(sd.get("themes") or []), "logline": sb.get("logline"), "heart": sb.get("heart"),
        "arcs": "; ".join(a.get("name", "") for a in (sd.get("arcs") or []) if a.get("name")),
        "cast": ", ".join(n for n in cast_names if n),
    }
    labels = {"title": "Title", "premise": "Premise", "tone": "Tone", "themes": "Themes",
              "logline": "Logline", "heart": "Heart",
              "arcs": "Arcs", "cast": "Cast"}
    block = "\n".join(f"{labels.get(k, k)}: {vals[k]}" for k in (fields or []) if vals.get(k))

    # The relationship MAP — always fed so the builder agent (and the human conversing with it) can
    # reason about scenes from the cast's web. We supply the map; the user selects the scene. No
    # auto-weighting — the human drives. See loom/stories/GENESIS.md.
    rels = sd.get("relationships") or []
    if rels:
        kname = {m.get("character"): getattr(ctx.base_settings.characters.get(m.get("character")),
                                              "name", m.get("character"))
                 for m in (sd.get("cast") or [])}
        lines = []
        for r in rels:
            s = kname.get(r.get("source"), r.get("source"))
            t = kname.get(r.get("target"), r.get("target"))
            nat = f" ({r['nature']})" if r.get("nature") else ""
            dyn = r.get("dynamic") or r.get("stance") or ""
            lines.append(f"- {s} → {t}{nat}: {dyn}".rstrip(": ").rstrip())
        block = (block + "\n" if block else "") + "Relationship map:\n" + "\n".join(lines)
    return block


def assemble_system_prompt(*, cfg: dict, graph: dict, adopted: str, story_ctx: str,
                          craft_block: str, char_ground: str, label: str,
                          draft: bool = False, propose: bool = False,
                          adopted_examples: str = "") -> str:
    """The fully-assembled system prompt — base + adopted mode (+ its worked example) + story context +
    craft + grounding + graph + tool rules, then the conditional WORLD / DRAFT / PROPOSE directives.
    Extracted from run_turn so it's inspectable WITHOUT running the model (the dump endpoint calls
    this). All static prose comes from `cfg` (story_agent.json); only the dynamic bits (story_ctx,
    craft_block, char_ground, graph prose, the world BRIEF) are passed in. Pure: no provider, no IO.
    """
    system = "\n".join(p for p in [
        cfg.get("system", ""),
        (f"\nADOPT THIS BEHAVIOUR for the current request:\n{adopted}" if adopted else ""),
        (f"\nWORKED EXAMPLE (the quality bar — match the SPECIFICITY, not the content):\n{adopted_examples}"
         if adopted_examples else ""),
        (f"\nSTORY CONTEXT:\n{story_ctx}" if story_ctx else ""),
        (f"\n{craft_block}" if craft_block else ""),
        (f"\n{char_ground}" if char_ground else ""),
        f"\nCURRENT {label} (the editable document — reference entries by their [id]):\n" + _graph_prose(graph),
        (f"\n{cfg.get('tool_rules', '')}" if cfg.get("tool_rules") else ""),
    ] if p)

    # ESTABLISHED WORLD directive — the framing prose is config; the world BRIEF is rendered live.
    from ..world.creation import world_brief as _world_brief
    _wbrief = _world_brief(graph.get("world")) if isinstance(graph, dict) else ""
    if _wbrief:
        system += "\n\n" + (cfg.get("directives") or {}).get("world", "") + "\n" + _wbrief

    # DRAFT directive (cast-queue mode) — config prose. The draft guidance is split into
    # independently-editable keys (framing, persona quality, cast cohesion, tone, anti-archetype,
    # relationships); they're joined here in reading order. Edit any one without touching the others.
    if draft:
        _draft = (cfg.get("directives") or {}).get("draft") or {}
        _draft_parts = [_draft.get(k, "") for k in (
            "framing", "persona_quality", "cast_cohesion",
            "tone_compliance", "anti_archetype", "relationships")]
        system += "\n\n" + "\n\n".join(p for p in _draft_parts if p)

    # PROPOSE directive (Structure creation, suggest→approve) — config prose.
    if propose:
        system += "\n\n" + (cfg.get("directives") or {}).get("propose", "")

    return system


def _graph_prose(graph: dict) -> str:
    """The editable doc rendered as labeled PROSE, never raw JSON — models read information
    context far better as an outline. Ids stay inline ([c3]) so the model's ops can still
    reference them. Unknown sections fall back to YAML (readable, lossless)."""
    if not isinstance(graph, dict) or not graph:
        return "(empty)"
    import yaml as _yaml
    out: list[str] = []
    known = {"title", "name", "premise", "tone", "themes", "cast", "relationships",
             "locations", "world"}
    for k in ("title", "name"):
        if graph.get(k):
            out.append(f"TITLE: {graph[k]}")
            break
    if graph.get("premise"):
        out.append(f"PREMISE: {graph['premise']}")
    if graph.get("tone"):
        out.append(f"TONE: {graph['tone']}")
    if graph.get("themes"):
        out.append("THEMES: " + ", ".join(str(t) for t in graph["themes"]))
    cast = [c for c in (graph.get("cast") or []) if isinstance(c, dict)]
    if cast:
        out.append("CAST:")
        for c in cast:
            head = f"- [{c.get('id', '?')}] {c.get('name') or '(unnamed)'}"
            if c.get("role"):
                head += f" — {c['role']}"
            out.append(head)
            for f in ("persona", "temperament", "want", "lie", "wound", "secret",
                      "good_memory", "appearance"):
                if c.get(f):
                    out.append(f"    {f}: {c[f]}")
    rels = [r for r in (graph.get("relationships") or []) if isinstance(r, dict)]
    if rels:
        nm = {c.get("id"): (c.get("name") or c.get("id")) for c in cast}
        out.append("RELATIONSHIPS:")
        for r in rels:
            bits = " / ".join(str(r[k]) for k in ("nature", "dynamic", "stance", "note") if r.get(k))
            out.append(f"- [{r.get('id', '?')}] {nm.get(r.get('source'), r.get('source'))} → "
                       f"{nm.get(r.get('target'), r.get('target'))}: {bits}")
    locs = [l for l in (graph.get("locations") or []) if isinstance(l, dict)]
    if locs:
        out.append("LOCATIONS:")
        for l in locs:
            out.append(f"- [{l.get('id', '?')}] {l.get('name', '')}"
                       + (f" (in {l['parent']})" if l.get("parent") else "")
                       + (f": {l['description']}" if l.get("description") else ""))
    # `world` is rendered separately as the ESTABLISHED WORLD brief — skipped here (no dup).
    rest = {k: v for k, v in graph.items() if k not in known and v not in (None, "", [], {})}
    if rest:
        out.append("OTHER:\n" + _yaml.safe_dump(rest, allow_unicode=True, sort_keys=False).strip())
    return "\n".join(out) or "(empty)"


def _propose_label(fn: str, params: dict, graph: dict | None = None) -> str:
    """A short title for an option/approval card (the action + its key nouns). Resolves id-valued
    params (e.g. remove_character's `id`, a relationship's source/target) to the entity's name via
    `graph`, so a removal reads 'remove character: Leo Vance', not a bare verb or an opaque id."""
    verb = fn.replace("_", " ")
    names: dict[str, str] = {}
    if isinstance(graph, dict):
        for c in graph.get("cast") or []:
            if c.get("id"):
                names[str(c["id"])] = str(c.get("name") or c.get("role") or c["id"])
        for l in graph.get("locations") or []:
            if l.get("id"):
                names[str(l["id"])] = str(l.get("name") or l["id"])
    show = lambda v: names.get(str(v), str(v))
    parts = [show(params[k]) for k in ("name", "title", "value", "nature", "role", "id", "source", "target")
             if params.get(k)]
    return (verb + (": " + " · ".join(parts[:3]) if parts else ""))[:160]


def _propose_detail(params: dict) -> str:
    """A CONCISE gist for an option card — the first sentence or two, so a row of options is scannable
    at a glance (the full persona is applied on approve and shown in the character card). A wall of
    prose 'tells you nothing' when you're comparing five cards; the hook does."""
    for k in ("persona", "premise", "description", "note", "value"):
        v = params.get(k)
        if isinstance(v, str) and v.strip():
            s = v.strip()
            sents = re.findall(r".*?[.!?](?:\s|$)", s)   # split on sentence enders, keep them
            gist = "".join(sents[:2]).strip() or s        # first 1-2 sentences = the hook
            return gist[:240]
    return ""


def run_turn(ctx, body: dict) -> dict:
    """One chat turn: offer the lorebook tools, adopt the matching mode persona, assemble the system
    prompt from config + grounding, call the model, apply tool calls, persist, return the result.
    Returns the response dict; a `_status` key (popped by the router) signals a non-200."""
    from ...server.services import lorebook_store as _LS
    from ...server.services import presets as _P
    from ..records import graph as GO
    from ..pipeline import grounding as _G

    body = body or {}
    root = ctx.root
    cfg = load_config(root)
    graph = body.get("graph") if isinstance(body.get("graph"), dict) else {}
    messages = body.get("messages") or []
    req_text = next((str(m.get("content", "")) for m in reversed(messages)
                     if isinstance(m, dict) and m.get("role") == "user"), "")
    transcript = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))

    # ── Agent / persona — resolve FIRST (triggers or explicit agent) so the tool menu can be scoped
    # to the active persona. The full agent definition (persona + tools + triggers) comes from the
    # JSON config. An explicit `mode` from the client may use the OLD internal key — translate it. ──
    modes = cfg.get("agents") or {}
    explicit = translate_key((body.get("mode") or "").strip())
    active_ids = [explicit] if (explicit and explicit in modes) else match_modes(root, req_text)
    adopted = "\n\n".join(modes[m]["persona"] for m in active_ids if modes.get(m, {}).get("persona"))
    adopted_examples = "\n\n".join(modes[m]["example"] for m in active_ids if modes.get(m, {}).get("example"))
    behavior_sig = explicit if (explicit and explicit in modes) else ",".join(sorted(set(active_ids)))
    primary = modes.get(active_ids[0], {}) if active_ids else {}

    # ── Context mutation: cut at the current request on a persona switch (clean context). ──
    context_cut = False
    if behavior_sig and behavior_sig != (body.get("active_behavior") or "") and len(messages) > 1:
        li = max((i for i, m in enumerate(messages)
                  if isinstance(m, dict) and m.get("role") == "user"), default=None)
        if li is not None and li > 0:
            messages = messages[li:]
            transcript = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
            context_cut = True

    # ── Tools: scope the menu to the active mode's `functions` so persona and tools AGREE (a wardrobe
    # turn isn't handed set_story_title). Resolved straight from the code registry (no lorebook hop).
    # No confident mode → the union of every mode's functions, so the model can still route. The
    # attached-books path (non-unified pipeline) still goes through books. ──
    if body.get("all_tools"):
        names = list(dict.fromkeys(n for mid in active_ids for n in (modes.get(mid, {}).get("tools") or [])))
        if not names:                         # general chat: offer every tool any agent declares
            names = list(dict.fromkeys(n for m in modes.values() for n in (m.get("tools") or [])))
        fns = GO.resolve_functions(names)
        preset_books = []   # the unified chat's model is the builder/workshop model — NOT resolved
                            # from function-book bindings (the chat no longer routes through books).
    else:
        book_ids = [re.sub(r"[^\w\-]+", "_", str(b)) for b in (body.get("lorebooks") or [])]
        preset_books = book_ids
        fns, seen = [], set()
        for bid in book_ids:                  # dedup by tool name (generate_image lives in several books)
            for f in GO.parse_functions(_LS.load_lorebook(root, bid)):
                if f.name not in seen:
                    fns.append(f); seen.add(f.name)
    pol = cfg.get("tool_policy") or {}
    cap = pol.get("all_tools_cap", 40) if body.get("all_tools") else pol.get("attached_cap", 12)
    off = fns if (body.get("offer_all") or body.get("all_tools")) else GO.offered(fns, transcript, cap=cap)

    # ── Edit TARGET: where approved doc-ops land + how they persist. The universal suggest→approve
    # loop is artifact-agnostic; `target` is the only per-surface knob. See [[two-agent-model]].
    #   • "draft"            → mutate the CLIENT-HELD doc only; persist nothing (a pre-commit queue).
    #   • "character:<key>"  → write the single edited character's fields back to its YAML.
    #   • "story" (default)  → the original story-field persist.
    # A draft also drops ACTION tools (no disk writes / image-gen on a doc the client still owns). ──
    target = (body.get("target") or "story").strip()
    draft = (target == "draft") or (body.get("commit", True) is False)
    if draft:
        off = [f for f in off if f.kind != "action"]
    if not off:
        return {"ok": True, "graph": graph, "applied": [], "offered": []}

    # ── Model/connection from a single chat preset (NOT a persona). An explicit per-request `model`
    # (e.g. the genesis flow's model toggle) OVERRIDES the preset → routes through builder_ctx's override. ──
    preset = None if (body.get("model") or "").strip() else _P.preset_for_books(root, preset_books)
    if preset is not None:
        provider = ctx.text_provider_for((preset.get("model") or "").strip() or None,
                                         preset.get("params") or {}, connection=preset.get("connection") or None)
    else:
        provider, _ = ctx.builder_ctx(body, body.get("script") or "workshop")
    if provider is None or not hasattr(provider, "generate_text"):
        return {"ok": False, "error": "no chat connection — connect a chat model first", "_status": 400}

    label = (body.get("artifact_label") or "DOCUMENT").strip()

    # ── Grounding: story context + craft (by the mode's section) + the mode's extra injects. ──
    story_ctx = _story_context(ctx, body.get("story"), cfg.get("story_context_fields") or [])
    _q = req_text or transcript
    craft_block = _G.MINIMALISM       # the literary-minimalist stance (replaced the _craft lorebook)
    inject = primary.get("inject") or []
    ground = []
    if "concreteness" in inject:
        ground.append(_G.CONCRETENESS)
    if "psyche" in inject:
        ground.append(_G.ADAPTATION)      # the wound→lie→coping adaptation basis (replaced Big Five)
    char_ground = "\n\n".join(p for p in ground if p)

    # ── System prompt — assembled by assemble_system_prompt (also used by the dump endpoint).
    # All static prose lives in cfg (story_agent.json); only dynamic bits are passed in. ──
    system = assemble_system_prompt(cfg=cfg, graph=graph, adopted=adopted, story_ctx=story_ctx,
                                    craft_block=craft_block, char_ground=char_ground, label=label,
                                    draft=draft, propose=bool(body.get("propose")),
                                    adopted_examples=adopted_examples)
    prompt = transcript or f"Apply the appropriate tools to the {label.lower()}."

    # Two opt-in flows gate tool application for a suggest→approve UX (the client sets the flag):
    #   • apply_calls=[…]  → skip the model and APPLY exactly these (a proposal the user approved).
    #   • propose=true     → run the model but DON'T apply; return the calls as `proposed` cards.
    # Neither flag (Overview/Cast) = the original auto-apply behaviour, untouched.
    reply_text = ""
    approved = body.get("apply_calls")
    if approved is not None:
        calls = [c for c in approved if isinstance(c, dict)]
    else:
        # CLI-string tool protocol: the model emits `tool <name> <flags>` lines instead of native
        # function-calling. The reference block (in the system prompt) shows the exact flag spelling;
        # parse_cli_calls turns the reply back into {fn, params} dicts dispatched to the same canonical
        # scripts.invoke. See story_graph.cli_reference / parse_cli_calls. No subprocess, no shell.
        system_with_tools = system + "\n\n" + GO.cli_reference(off) if off else system
        try:
            res = provider.generate_text(system=system_with_tools, prompt=prompt)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"graph-ops failed: {exc}", "_status": 500}
        raw_reply = (getattr(res, "text", "") or (res.data or {}).get("reply", "")
                     if hasattr(res, "data") else "")
        calls = GO.parse_cli_calls(raw_reply, off)
        # The model's PROSE reply is the non-tool lines (anything not a `tool ` command).
        reply_text = "\n".join(ln for ln in (raw_reply or "").splitlines()
                               if not ln.strip().lower().startswith("tool ")).strip()
        if body.get("propose"):
            specs = {f.name for f in off}
            # In a DRAFT the relationships are described IN PROSE and formalized on ratify — never
            # surfaced as their own cards (the writer tracks them grouped under each character).
            rel_fns = {"set_relationship", "remove_relationship"} if draft else set()
            proposed = []
            for c in calls:
                if not isinstance(c, dict) or str(c.get("fn", "")) not in specs:
                    continue
                fn = str(c.get("fn", ""))
                if fn in rel_fns:
                    continue
                pp = GO._call_params(c)
                proposed.append({"fn": fn, "params": pp,
                                 "label": _propose_label(fn, pp, graph), "detail": _propose_detail(pp)})
            # Co-author voice: when the model tool-called but emitted NO prose (common with OpenAI-style
            # tool calling — content is null when tool_calls are present), do ONE cheap text-only pass so a
            # card-dump still arrives with a thinking note + a forward-looking question, never silent. Skipped
            # when the model already spoke or proposed nothing — zero cost on the discuss path.
            if proposed and not reply_text:
                sketch = "; ".join(p["label"] for p in proposed)
                try:
                    note = provider.generate_text(
                        system="You are the writer's CO-AUTHOR. You just proposed these as approval cards: "
                               + sketch + ". Reply with ONE short sentence reacting — that's ALL. NEVER ask a "
                               "question or end with a question mark; the UI already shows the writer what's "
                               "next. Do NOT restate, list, or summarize the cards; no preamble, no "
                               "'Proceeding to…'.",
                        prompt="Write the one-sentence co-author note.")
                    reply_text = (getattr(note, "text", "") or "").strip()
                except Exception:  # noqa: BLE001 — the note is a nicety; never sink the proposal
                    pass
            return {"ok": True, "graph": graph, "proposed": proposed, "reply": reply_text,
                    "offered": [f.name for f in off], "active_behavior": behavior_sig,
                    "context_cut": context_cut}

    # ── Apply: DOC tools mutate the artifact; ACTION tools run with ctx and yield artifacts. ──
    action = {f.name: f for f in off if f.kind == "action"}
    doc_fns = [f for f in off if f.kind != "action"]
    doc_calls = [c for c in calls if isinstance(c, dict) and str(c.get("fn")) not in action]
    new_graph, log = GO.apply_ops(graph, doc_calls, doc_fns)

    artifacts: list = []
    preset_id = (preset or {}).get("id") if isinstance(preset, dict) else None
    for c in calls:
        f = action.get(str(c.get("fn", ""))) if isinstance(c, dict) else None
        if f is None:
            continue
        abody = {**GO._call_params(c), "character": body.get("character"),
                 "preset": preset_id, "graph": graph, "story": body.get("story")}
        try:
            result = f.impl(ctx, abody) or {}
            artifacts.append({"fn": f.name, **result})
            log.append({"fn": f.name, "ok": True, "artifact": True})
        except Exception as exc:  # noqa: BLE001 — one bad action never sinks the batch
            log.append({"fn": f.name, "ok": False, "error": str(exc)})

    # ── Persist by TARGET (draft persists nothing — the client owns the returned doc). ──
    if draft:
        pass
    elif target.startswith("character:"):
        ckey = target.split(":", 1)[1].strip()
        try:
            ctx.persist_character_entry(ckey, new_graph)
            log.append({"fn": "·persist", "ok": True, "saved": [f"character:{ckey}"]})
        except Exception as exc:  # noqa: BLE001 — a save failure never sinks the response
            log.append({"fn": "·persist", "ok": False, "error": str(exc)})
    else:
        # Story-field-shaped doc keys straight to the story YAML (same as the frontend PUT).
        skey = (body.get("story") or "").strip()
        if skey and ctx.base_settings.stories.get(skey) is not None:
            _PERSIST = ("locations", "start", "relationships", "connections")
            fields = {k: new_graph[k] for k in _PERSIST if k in new_graph}
            if isinstance(new_graph.get("cast"), list):
                fields["cast"] = ctx.cast_doc_to_members(new_graph["cast"])
            if fields:
                try:
                    ctx.update_story_fields(skey, fields)
                    log.append({"fn": "·persist", "ok": True, "saved": sorted(fields)})
                except Exception as exc:  # noqa: BLE001 — a save failure never sinks the response
                    log.append({"fn": "·persist", "ok": False, "error": str(exc)})

    out = {"ok": True, "graph": new_graph, "applied": log, "artifacts": artifacts,
           "offered": [f.name for f in off], "reply": reply_text,
           "active_behavior": behavior_sig, "context_cut": context_cut}
    if not calls and not out["reply"]:
        out["warning"] = ("the model returned neither a reply nor a tool call — it may not support "
                          "tool-calling; bind a tool-capable model to this preset")
    return out


if __name__ == "__main__":   # CLI dump — print the assembled system prompt without a model call.
    import sys, json
    from pathlib import Path
    # Minimal standalone ctx: assemble_system_prompt only needs root + base_settings.stories/.characters
    # (for _story_context). For a quick dump we pass a bare body and let story_ctx be empty.
    msg = " ".join(sys.argv[1:]) or "add a rival for the protagonist"
    root = Path(__file__).resolve().parents[2]
    cfg = load_config(root, fresh=True)
    agents = cfg.get("agents") or {}
    active_ids = match_modes(root, msg)
    adopted = "\n\n".join(agents[a]["persona"] for a in active_ids if agents.get(a, {}).get("persona"))
    adopted_examples = "\n\n".join(agents[a]["example"] for a in active_ids if agents.get(a, {}).get("example"))
    graph = {"title": "(sample)", "premise": "(sample premise)", "cast": [], "relationships": []}
    system = assemble_system_prompt(cfg=cfg, graph=graph, adopted=adopted, story_ctx="",
                                    craft_block="", char_ground="", label="DOCUMENT",
                                    adopted_examples=adopted_examples)
    print(f"=== ACTIVE MODES: {active_ids or ['(none — general chat)']} ===\n")
    print(system)
    print(f"\n=== {len(system)} chars ===")


# --- graph-driven agent turns ---

"""The STORY agent as a state graph — a plan/act/observe/reflect loop for complex story-structure
work (arc design, storyboard building, spine restructuring).

Unlike the single-shot chat agent (loom/stories/agent.py), this graph lets the model build ONE
beat at a time, observe it, check it against a craft rubric, and revise before committing. That
loop is what a human developmental editor runs; single-shot can't do it because it commits to a
whole spine+beats sequence in one call with no chance to verify the chain.

Topology::

    start → PLAN → ACT → OBSERVE → REFLECT (decision)
                                     ├─ match(revise/continue) → ACT   (loop back)
                                     └─ match(done)              → COMMIT → end

The control surface (configs/story_agent.json) configures this:
  • PLAN reads the story persona + the story's specifics (spine, hidden truth) as context
  • ACT uses the story agent's tool list (the same GraphFunction registry as the chat agent)
  • REFLECT checks against the story agent's `rubric` (reflection questions derived from the persona)
  • COMMIT persists via the same ctx.update_story_fields path as run_turn

Built on pydantic_graph (the same dependency genesis_graph.py uses) — no new deps, no MCP.
The single-shot path stays intact for the other 4 agents + simple edits; this graph is opt-in.
"""

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from pydantic_graph import GraphBuilder, StepContext

from ..records import graph as GO
from ..authoring import scripts as _S
from ..authoring import stages as _ST


def _noop_event(_: dict) -> None: ...
def _never_cancel() -> bool: return False

MAX_STEPS = 4  # cap the ACT↔REFLECT loop — each step is a model round-trip (~10-30s each on
               # DeepSeek v4 Pro), so 4 steps = ~2-4 min total. The sweet spot for arc design:
               # enough iterations to set the spine + build/check 2-3 beats, not so many the
               # writer waits forever. Simple tasks route to single-shot anyway.


# ── State + Deps ─────────────────────────────────────────────────────────────── #

@dataclass
class StoryAgentState:
    """Everything that flows across the plan/act/observe/reflect loop."""
    # Input
    story_key: str = ""
    task: str = ""                       # the writer's latest request
    messages: list = field(default_factory=list)   # conversation history
    # Working (mutates as the graph runs)
    graph_doc: dict = field(default_factory=dict)  # the editable story document
    plan: str = ""                       # the model's reasoning about how to approach the task
    step_count: int = 0                  # ACT visits — capped at MAX_STEPS
    tool_log: list = field(default_factory=list)   # [{fn, params, result, ok}]
    next_call: dict = field(default_factory=dict)  # the pending tool call for the next ACT
    reflect_verdict: str = ""            # "continue" | "revise" | "done" — set by REFLECT
    # Output
    reply: str = ""
    committed: bool = False


@dataclass
class StoryAgentDeps:
    """I/O the steps need, injected by the endpoint — keeps the graph framework-agnostic."""
    ctx: Any                             # AppContext — for persistence + providers
    body: dict = field(default_factory=dict)        # the original request body
    cfg: dict = field(default_factory=dict)         # loaded story_agent.json config
    provider: Any = None                 # the resolved text provider (has generate_text)
    on_event: Callable[[dict], None] = _noop_event  # structured progress → SSE (optional)


# ── Helpers ──────────────────────────────────────────────────────────────────── #

async def _thread(fn, *a, **kw):
    """Run a sync function off the event loop (providers are sync)."""
    return await asyncio.to_thread(lambda: fn(*a, **kw))


def _emit(ctx, node: str, status: str, **extra) -> None:
    ctx.deps.on_event({"type": "node", "node": node, "status": status, **extra})


def _story_tools(cfg: dict):
    """Resolve the story agent's tool list to callable GraphFunctions (same registry as chat)."""
    agent = (cfg.get("agents") or {}).get("story") or {}
    names = agent.get("tools") or []
    return GO.resolve_functions(names)


def _rubric(cfg: dict) -> list[str]:
    """The reflection questions for the story agent (from configs/story_agent.json)."""
    agent = (cfg.get("agents") or {}).get("story") or {}
    r = agent.get("rubric") or []
    return r if isinstance(r, list) else []


# ── Nodes ────────────────────────────────────────────────────────────────────── #

async def step_plan(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> str:
    """PLAN — the model reasons about the task and emits its FIRST tool call + a plan note.
    No execution here; this is reasoning. Stores the plan, sets next_call for ACT."""
    s, d = ctx.state, ctx.deps
    _emit(ctx, "plan", "start")
    agent = (d.cfg.get("agents") or {}).get("story") or {}
    system = assemble_system_prompt(
        cfg=d.cfg, graph=s.graph_doc, adopted=agent.get("persona", ""),
        story_ctx=_story_context(d.ctx, s.story_key, d.cfg.get("story_context_fields") or []),
        craft_block="", char_ground="", label="STORY",
        adopted_examples=agent.get("example", ""))
    system += ("\n\nYou are in PLANNING mode. Reason about the task, then emit your FIRST tool "
               "call — exactly ONE call, not a batch. You will build the structure ONE step at a "
               "time (one tool call per step), observing and revising between steps. State your "
               "plan in `plan`, then make the single tool call that starts it.\n\n"
               "Available tools (emit EXACTLY one of these per step):\n"
               + "\n".join(f"  • {f.name}({', '.join(f.params.keys()) if f.params else ''}) — {f.describe}"
                           for f in _story_tools(d.cfg))
               + "\n\nTool guidance:\n"
               "  • set_spine(field, value) — sets ONE spine field at a time. field ∈ "
               "{logline, wound, lie, truth}. To set all four, you'll call this four times across "
               "four steps — NOT storyboard.\n"
               "  • storyboard(premise) — runs the FULL generation pipeline (overwrites everything). "
               "Use ONLY when building from scratch, never for editing individual fields.\n"
               "  • add_beat(title, after) — adds one beat to the storyboard chain.\n"
               "Do NOT invent tools like 'batched' or 'get_storyboard' — only the names listed above.")
    schema = {"type": "object", "additionalProperties": False, "required": ["plan", "call"],
              "properties": {
                  "plan": {"type": "string", "description": "your reasoning: which tools, in what "
                            "order, what you'll check at each step (you'll be evaluated against the "
                            "rubric: " + "; ".join(_rubric(d.cfg)) + ")"},
                  "call": {"type": "object", "description": "your FIRST tool call — REQUIRED. Pick "
                           "one of the available tools and fill its params.",
                           "additionalProperties": False,
                           "required": ["fn"],
                           "properties": {"fn": {"type": "string", "description": "the tool name"},
                                          "params": {"type": "object", "additionalProperties": True}}}}}
    res = await _thread(d.provider.generate_text, system=system,
                        prompt=s.task or "Plan the work.", emits=schema)
    data = (res.data if hasattr(res, "data") else res) or {}
    s.plan = (data.get("plan") or "").strip()
    s.next_call = data.get("call") or {}
    _emit(ctx, "plan", "done", plan=s.plan[:160])
    return "plan_done"


async def step_act(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> str:
    """ACT — execute ONE tool call against the story doc. Doc tools mutate graph_doc in place;
    action tools run with ctx. Logs the result."""
    s, d = ctx.state, ctx.deps
    if s.step_count >= MAX_STEPS:
        s.reflect_verdict = "done"      # force commit at the cap
        return "capped"
    call = s.next_call or {}
    fn_name = (call.get("fn") or "").strip()
    params = call.get("params") or {}
    if not fn_name:
        s.reflect_verdict = "done"      # nothing to do → commit
        return "no_call"
    _emit(ctx, "act", "start", fn=fn_name, step=s.step_count + 1)
    # Resolve the tool and execute it
    fns = _story_tools(d.cfg)
    fn = next((f for f in fns if f.name == fn_name), None)
    entry = {"fn": fn_name, "params": params, "ok": False}
    if fn is None:
        entry["error"] = f"tool '{fn_name}' not in story agent's tool list"
    elif fn.kind == "doc":
        try:
            _S.invoke(fn_name, s.graph_doc, params)   # invoke takes the registered NAME
            entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
    elif fn.kind == "action":
        try:
            abody = {**params, "graph": s.graph_doc, "story": s.story_key}
            entry["result"] = await _thread(fn.impl, d.ctx, abody)
            entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
    s.tool_log.append(entry)
    s.step_count += 1
    s.next_call = {}                    # consumed
    _emit(ctx, "act", "done", ok=entry["ok"], step=s.step_count)
    return "act_done"


async def step_observe(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> str:
    """OBSERVE — render what the story doc looks like now + the relevant rubric, for REFLECT."""
    s, d = ctx.state, ctx.deps
    last = s.tool_log[-1] if s.tool_log else {}
    # The observation = the current graph prose + what the last tool did + the rubric to check.
    rubric = _rubric(d.cfg)
    s._observation = (              # stash for REFLECT (not part of the persisted state shape)
        f"LAST ACTION: {last.get('fn','?')} — {'ok' if last.get('ok') else 'FAILED: '+str(last.get('error',''))}\n"
        f"CURRENT STORY DOC:\n{_graph_prose(s.graph_doc)}\n"
        f"RUBRIC TO CHECK against what you just did:\n- " + "\n- ".join(rubric))
    return "observed"


async def step_reflect(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> str:
    """REFLECT (the branch) — the model evaluates the last step against the rubric and decides:
    continue (next planned step), revise (undo + retry), or done (commit). Sets reflect_verdict."""
    s, d = ctx.state, ctx.deps
    if s.step_count >= MAX_STEPS:
        s.reflect_verdict = "done"; return "done"     # hard cap
    if not s.tool_log:                                  # nothing to reflect on
        s.reflect_verdict = "done"; return "done"
    _emit(ctx, "reflect", "start", step=s.step_count)
    tools = _story_tools(d.cfg)
    tool_list = "\n".join(f"  • {f.name}({', '.join(f.params.keys()) if f.params else ''}) — {f.describe}"
                          for f in tools)
    valid_names = [f.name for f in tools]
    system = ("You are the REFLECTION step of a story-structure agent. Given the plan, the last "
              "action, and the current story doc, evaluate against the rubric and decide:\n"
              "• continue — the step was good; emit the NEXT tool call to proceed\n"
              "• revise — the step failed the rubric; emit a corrective tool call (e.g. set_beat_field "
              "to fix what's wrong, or delete_beat + add_beat to redo it)\n"
              "• done — the task is complete and satisfies the rubric; commit\n"
              "Be honest: if a beat doesn't cost something or doesn't cause the next, say revise.\n\n"
              "Available tools (use ONLY these names, with EXACTLY these params):\n" + tool_list)
    schema = {"type": "object", "additionalProperties": False, "required": ["verdict"],
              "properties": {
                  "verdict": {"type": "string", "enum": ["continue", "revise", "done"]},
                  "reason": {"type": "string", "description": "one sentence: why this verdict"},
                  "call": {"type": "object", "description": "the next tool call (required for continue/revise)",
                           "additionalProperties": False, "required": ["fn", "params"],
                           "properties": {"fn": {"type": "string", "enum": valid_names},
                                          "params": {"type": "object", "additionalProperties": True}}}}}
    res = await _thread(d.provider.generate_text, system=system,
                        prompt=getattr(s, "_observation", s.plan or s.task), emits=schema)
    data = (res.data if hasattr(res, "data") else res) or {}
    s.reflect_verdict = (data.get("verdict") or "done").strip().lower()
    if s.reflect_verdict in ("continue", "revise"):
        s.next_call = data.get("call") or {}     # ACT will execute it
    _emit(ctx, "reflect", "done", verdict=s.reflect_verdict,
          reason=(data.get("reason") or "")[:120])
    return s.reflect_verdict


async def step_commit(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> dict:
    """COMMIT — persist the mutated story doc + generate a terse reply."""
    s, d = ctx.state, ctx.deps
    _emit(ctx, "commit", "start")
    target = (d.body.get("target") or "story").strip()
    applied = [f["fn"] for f in s.tool_log if f.get("ok")]
    if target != "draft":
        try:
            # Persist the fields the story agent's tools mutate: the storyboard structure
            # (beats, spine-ish fields) + the top-level spine fields (logline/wound/lie/truth)
            # + arcs. set_spine writes to doc[field] top-level; add_beat/set_beat_field write
            # to storyboard.beats. Include all so nothing the agent built gets dropped.
            persist_fields = {k: s.graph_doc.get(k) for k in
                              ("arcs", "storyboard", "logline", "wound", "lie", "truth")
                              if s.graph_doc.get(k) is not None}
            if persist_fields:
                d.ctx.update_story_fields(s.story_key, persist_fields)
        except Exception:  # noqa: BLE001 — a persist failure never sinks the response
            pass
    # Terse reply summarizing what landed
    if applied:
        s.reply = f"Built {len(applied)} step(s): {', '.join(applied[:4])}."
    else:
        s.reply = "Couldn't apply anything — try rephrasing."
    s.committed = True
    _emit(ctx, "commit", "done", steps=s.step_count, applied=len(applied))
    return {"ok": True, "graph": s.graph_doc, "applied": s.tool_log, "reply": s.reply,
            "active_behavior": "story", "offered": [f.name for f in _story_tools(d.cfg)]}


# ── Graph construction ───────────────────────────────────────────────────────── #

def _build_story_agent_graph():
    """Build the plan → act → observe → reflect → {act (loop) | commit} → end graph.
    REFLECT returns the verdict string; a decision node branches it: continue/revise loops
    back to ACT, done goes forward to COMMIT."""
    from pydantic_graph.graph_builder import DecisionBranch
    gb = GraphBuilder(state_type=StoryAgentState, deps_type=StoryAgentDeps, output_type=dict)
    plan, act, observe, reflect = (gb.step(step_plan), gb.step(step_act),
                                   gb.step(step_observe), gb.step(step_reflect))
    commit = gb.step(step_commit)
    # Linear: start → plan → act → observe → reflect
    gb.add_edge(gb.start_node, plan)
    gb.add_edge(plan, act)
    gb.add_edge(act, observe)
    gb.add_edge(observe, reflect)
    # Branch at reflect: continue/revise → act (loop); done → commit
    loop_path = gb.edge_from(reflect).to(act)
    done_path = gb.edge_from(reflect).to(commit)
    loop_branch = DecisionBranch(source=str, matches=lambda v: v in ("continue", "revise"),
                                 path=loop_path.path, destinations=[act])
    done_branch = DecisionBranch(source=str, matches=lambda v: v == "done",
                                 path=done_path.path, destinations=[commit])
    decision = gb.decision(note="reflect_branch").branch(loop_branch).branch(done_branch)
    gb.add_edge(reflect, decision)
    gb.add_edge(commit, gb.end_node)
    return gb.build()


STORY_AGENT_GRAPH = _build_story_agent_graph()


async def run_story_agent(ctx, body: dict, cfg: dict, provider, *, on_event=None) -> dict:
    """Run the story-structure agent graph. Same return shape as agent.run_turn so the endpoint
    and frontend don't change. `provider` is the resolved text provider (has generate_text).
    `on_event` (optional) receives {type:'node', node, status} per step for SSE streaming."""
    messages = body.get("messages") or []
    task = next((str(m.get("content", "")) for m in reversed(messages)
                 if isinstance(m, dict) and m.get("role") == "user"), "")
    # Load the current story doc as the working graph_doc
    try:
        graph_doc = ctx._read_story_data(body.get("story") or "")
    except Exception:  # noqa: BLE001
        graph_doc = body.get("graph") or {}
    state = StoryAgentState(
        story_key=body.get("story") or "", task=task, messages=messages, graph_doc=graph_doc)
    deps = StoryAgentDeps(ctx=ctx, body=body, cfg=cfg, provider=provider,
                          on_event=on_event or _noop_event)
    result = await STORY_AGENT_GRAPH.run(state=state, deps=deps, inputs=None)
    return result if isinstance(result, dict) else {}


# ── Routing ──────────────────────────────────────────────────────────────────── #

# Tasks that benefit from the plan/act/reflect loop (vs. single-shot)
# Tasks that genuinely benefit from the plan/act/reflect loop — where intermediate state
# changes the plan (full restructuring, rebuilding from scratch, multi-beat redesign).
# Simple tasks (set a spine field, add a beat, rename) stay on single-shot: they're one-call
# work where the model sees full context at once, which is faster AND more consistent (no drift
# between steps). The graph loop earns its cost only when single-shot demonstrably fails.
_COMPLEX_KEYWORDS = ("restructure", "rework the", "rebuild the", "redesign the",
                     "fix the whole", "overhaul the", "start over",
                     "design the full arc", "design arc", "build the full storyboard")


def select_workflow(body: dict, cfg: dict) -> str:
    """Choose an explicit authoring workflow without asking an LLM to route.

    Clients may request ``workflow: 'structure'`` or ``'single'``.  ``auto``
    keeps the conservative compatibility fallback until every caller exposes a
    workflow selector; it never changes canonical state by itself.
    """
    requested = str((body or {}).get("workflow") or "auto").strip().lower()
    if requested in {"structure", "single"}:
        return requested
    return "structure" if should_use_graph(body or {}, cfg) else "single"


def should_use_graph(body: dict, cfg: dict) -> bool:
    """True ONLY for genuine multi-step restructuring — full arc design, storyboard rebuild,
    beat-chain overhaul. Single-field edits, single-beat adds, spine setting all stay single-shot
    (one-call, full context, no drift, ~10x faster). The graph loop is reserved for tasks where
    intermediate state genuinely changes the plan."""
    agents = cfg.get("agents") or {}
    explicit = translate_key((body.get("mode") or "").strip())
    active = explicit if (explicit and explicit in agents) else None
    if active is None:
        req = next((str(m.get("content", "")) for m in reversed(body.get("messages") or [])
                    if isinstance(m, dict) and m.get("role") == "user"), "")
        active = "story" if any(t in req.lower() for t in
                                ("storyboard", "title", "premise", "theme", "arc ", "plot",
                                 "beats", "outline", "ending", "climax")) else None
    if active != "story":
        return False
    req = next((str(m.get("content", "")) for m in reversed(body.get("messages") or [])
                if isinstance(m, dict) and m.get("role") == "user"), "")
    return any(k in req.lower() for k in _COMPLEX_KEYWORDS)


if __name__ == "__main__":   # topology self-check
    # The graph builds and has the expected nodes + the reflect branch
    g = STORY_AGENT_GRAPH
    print("story-agent graph built:", bool(g))
    print("topology: start → plan → act → observe → reflect → {act (loop) | commit} → end")
    print("MAX_STEPS cap:", MAX_STEPS)
    # Routing sanity — tightened: only genuine restructuring goes to the graph
    cfg_dummy = {"agents": {"story": {"tools": [], "rubric": ["test"]}}}
    # Graph: genuine multi-step restructuring
    assert should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "redesign the full arc from scratch"}]}, cfg_dummy)
    assert should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "rebuild the storyboard"}]}, cfg_dummy)
    # Single-shot: simple edits, spine, single beats, other agents
    assert not should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "set the logline spine field"}]}, cfg_dummy)
    assert not should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "add a beat where Eli finds the journal"}]}, cfg_dummy)
    assert not should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "design arc 3"}]}, cfg_dummy)  # just "design arc" isn't enough — needs "full"/"restructure"
    assert not should_use_graph({"mode": "cast", "messages": [{"role": "user", "content": "add a rival"}]}, cfg_dummy)
    print("routing: redesign full arc → graph ✓ | rebuild storyboard → graph ✓")
    print("         set spine → single-shot ✓ | add beat → single-shot ✓ | design arc → single-shot ✓")
    print("ok — story_agent_graph: topology + routing")

"""The story chat agent — the fixed STRUCTURE that runs a turn. All authored text/rules come from
`configs/story_agent.json` (see agent_config.py); the lorebooks supply the TOOLS (function books)
and the injected GUIDANCE (the adaptation basis + the literary-minimalist stance). This was extracted out of the
fat `story_graph_ops` router endpoint — the router is now a thin wrapper. See [[agents-and-scripts]].
"""
from __future__ import annotations

import json
import re

from . import agent_config as _AC


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
    from .genesis import world_brief as _world_brief
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
    from ..server.services import lorebook_store as _LS
    from ..server.services import presets as _P
    from . import graph_ops as GO
    from .pipeline import grounding as _G

    body = body or {}
    root = ctx.root
    cfg = _AC.load_config(root)
    graph = body.get("graph") if isinstance(body.get("graph"), dict) else {}
    messages = body.get("messages") or []
    req_text = next((str(m.get("content", "")) for m in reversed(messages)
                     if isinstance(m, dict) and m.get("role") == "user"), "")
    transcript = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))

    # ── Agent / persona — resolve FIRST (triggers or explicit agent) so the tool menu can be scoped
    # to the active persona. The full agent definition (persona + tools + triggers) comes from the
    # JSON config. An explicit `mode` from the client may use the OLD internal key — translate it. ──
    from .agent_modes import translate_key as _translate_key
    modes = cfg.get("agents") or {}
    explicit = _translate_key((body.get("mode") or "").strip())
    active_ids = [explicit] if (explicit and explicit in modes) else _AC.match_modes(root, req_text)
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
        # scripts.invoke. See graph_ops.cli_reference / parse_cli_calls. No subprocess, no shell.
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
    cfg = _AC.load_config(root, fresh=True)
    agents = cfg.get("agents") or {}
    active_ids = _AC.match_modes(root, msg)
    adopted = "\n\n".join(agents[a]["persona"] for a in active_ids if agents.get(a, {}).get("persona"))
    adopted_examples = "\n\n".join(agents[a]["example"] for a in active_ids if agents.get(a, {}).get("example"))
    graph = {"title": "(sample)", "premise": "(sample premise)", "cast": [], "relationships": []}
    system = assemble_system_prompt(cfg=cfg, graph=graph, adopted=adopted, story_ctx="",
                                    craft_block="", char_ground="", label="DOCUMENT",
                                    adopted_examples=adopted_examples)
    print(f"=== ACTIVE MODES: {active_ids or ['(none — general chat)']} ===\n")
    print(system)
    print(f"\n=== {len(system)} chars ===")

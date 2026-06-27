"""The story chat agent — the fixed STRUCTURE that runs a turn. All authored text/rules come from
`configs/story_agent.json` (see agent_config.py); the lorebooks supply the TOOLS (function books)
and the retrieved KNOWLEDGE (_craft principles, _psyche facets). This was extracted out of the
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
        "intended_ending": sd.get("intended_ending"),
        "arcs": "; ".join(a.get("name", "") for a in (sd.get("arcs") or []) if a.get("name")),
        "cast": ", ".join(n for n in cast_names if n),
    }
    labels = {"title": "Title", "premise": "Premise", "tone": "Tone", "themes": "Themes",
              "logline": "Logline", "heart": "Heart", "intended_ending": "Intended ending",
              "arcs": "Arcs", "cast": "Cast"}
    return "\n".join(f"{labels.get(k, k)}: {vals[k]}" for k in (fields or []) if vals.get(k))


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

    # ── Mode / persona — resolve FIRST (triggers or explicit mode) so the tool menu can be scoped
    # to the active persona. From the JSON config, not _behavior rows. ──
    modes = cfg.get("modes") or {}
    explicit = (body.get("mode") or "").strip()
    active_ids = [explicit] if (explicit and explicit in modes) else _AC.match_modes(root, req_text)
    adopted = "\n\n".join(modes[m]["persona"] for m in active_ids if modes.get(m, {}).get("persona"))
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
        names = list(dict.fromkeys(n for mid in active_ids for n in (modes.get(mid, {}).get("functions") or [])))
        if not names:                         # general chat: offer every function any mode declares
            names = list(dict.fromkeys(n for m in modes.values() for n in (m.get("functions") or [])))
        fns = GO.resolve_functions(names)
        preset_books = [b["id"] for b in _LS.list_books(root) if b.get("category") == "function"]
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
    if not off:
        return {"ok": True, "graph": graph, "applied": [], "offered": []}

    # ── Model/connection from a single chat preset (NOT a persona). ──
    preset = _P.preset_for_books(root, preset_books)
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
    craft_block = _G.craft_notes(root, _q, k=(cfg.get("craft") or {}).get("k", 5),
                                 section=(primary.get("craft_section") or ""))
    inject = primary.get("inject") or []
    ground = []
    if "concreteness" in inject:
        ground.append(_G.CONCRETENESS)
    if "psyche" in inject:
        ground.append(_G.psyche_notes(root, _q, k=(cfg.get("psyche") or {}).get("k", 4)))
    char_ground = "\n\n".join(p for p in ground if p)

    system = "\n".join(p for p in [
        cfg.get("system", ""),
        (f"\nADOPT THIS BEHAVIOUR for the current request:\n{adopted}" if adopted else ""),
        (f"\nSTORY CONTEXT:\n{story_ctx}" if story_ctx else ""),
        (f"\n{craft_block}" if craft_block else ""),
        (f"\n{char_ground}" if char_ground else ""),
        f"\nCURRENT {label} (the editable graph):\n" + json.dumps(graph, ensure_ascii=False),
        (f"\n{cfg.get('tool_rules', '')}" if cfg.get("tool_rules") else ""),
    ] if p)
    prompt = transcript or f"Apply the appropriate tools to the {label.lower()}."
    try:
        res = provider.generate_text(system=system, prompt=prompt, tools=GO.tools_spec(off))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"graph-ops failed: {exc}", "_status": 500}

    # ── Apply: DOC tools mutate the artifact; ACTION tools run with ctx and yield artifacts. ──
    calls = res.tool_calls or []
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

    # ── Persist story-field-shaped doc keys straight to the story YAML (same as the frontend PUT). ──
    skey = (body.get("story") or "").strip()
    if skey and ctx.base_settings.stories.get(skey) is not None:
        _PERSIST = ("locations", "places", "start", "relationships", "connections")
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
           "offered": [f.name for f in off], "reply": (getattr(res, "text", "") or "").strip(),
           "active_behavior": behavior_sig, "context_cut": context_cut}
    if not calls and not out["reply"]:
        out["warning"] = ("the model returned neither a reply nor a tool call — it may not support "
                          "tool-calling; bind a tool-capable model to this preset")
    return out

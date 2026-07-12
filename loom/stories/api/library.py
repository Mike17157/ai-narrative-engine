"""Story library HTTP endpoints.

Public paths are retained for backwards compatibility.
"""

from __future__ import annotations

from __future__ import annotations

import base64
import json
import re

import yaml
from fastapi.responses import FileResponse, JSONResponse

from ...config.schema import ModelDef
from ...server.services import config_files
from ...server.services.config_files import STORY_BUILDER_DEFAULT
from ...server.services.images import _clean_reference_png, _randomize_seeds, _render
from ...server.services.jobs_util import _start_stream_job
from ...server.services.prompts import FEATURES_SCHEMA, PLAY_SCHEMA, _assemble_base_prompt
from ..pipeline import apply_manifest as _apply_manifest, plan_and_apply as _plan_and_apply
# The story pipeline runs on pydantic-graph state machines (see graph_pipeline.py).
from ..authoring.pipeline_graph import StoryState, StoryDeps, run_turn, run_draft

def register(app, ctx):
    @app.get("/api/stories")
    def list_stories() -> list:
        out = []
        for k, st in ctx.base_settings.stories.items():
            f = ctx._story_file(k)                 # folder form or legacy flat
            mtime = f.stat().st_mtime if f else 0.0
            out.append({"key": k, "name": st.name, "premise": st.premise, "tone": st.tone,
                        "themes": st.themes, "locations": len(st.locations), "start": st.start,
                        "cast": [m.character for m in st.cast], "_mtime": mtime})
        out.sort(key=lambda s: s["_mtime"], reverse=True)  # newest first
        for s in out:
            s.pop("_mtime", None)
        return out

    @app.get("/api/stories/{key}")
    def get_story(key: str):
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        return {**st.model_dump(), "key": key}

    @app.put("/api/stories/{key}")
    def update_story(key: str, body: dict):
        """Edit a saved story in place (iterate). Updates only the fields sent; cast members are
        existing character keys, so no NPCs are re-created. Routed through update_story_fields so it
        lands in the story's DB (or legacy YAML) + validates."""
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        fields = {f: body[f] for f in (
            "name", "type", "premise", "tone", "themes", "art_style", "premise_parts", "conditions",
            "storyboard", "cast", "lorebook", "locations", "start", "background", "fields",
            "arcs", "chapters", "scenes", "features", "start_scene", "world",
            "relationships", "connections", "default_personas", "recent_window")
            if f in (body or {})}
        try:
            ctx.update_story_fields(key, fields)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "key": key}

    @app.post("/api/stories/{key}/weave-bonds")
    def weave_bonds(key: str, body: dict):
        """PROPOSE the relationship web (nothing saved — the roster reviews and accepts).
        The register is daylight-over-depth: every bond gets an innocent, specific surface
        read per side AND a hidden undercurrent rooted in the characters' wounds/lies/secrets,
        plus a trajectory for when the truth surfaces. Existing bonds are respected (only new
        pairs are proposed). Returns {bonds: [Relationship-shaped dicts]}."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        sd = st.model_dump()
        cast_keys = [m.get("character") for m in sd.get("cast") or [] if m.get("character")]
        if len(cast_keys) < 2:
            return JSONResponse({"error": "need at least two cast members"}, status_code=400)
        loc_names = {l.get("id"): l.get("name") or l.get("id") for l in sd.get("locations") or []}
        homes = {m.get("character"): loc_names.get(m.get("home"), "") for m in sd.get("cast") or []}
        lines = []
        for ck in cast_keys:
            ch = ctx.base_settings.characters.get(ck)
            f = (getattr(ch, "fields", None) or {}) if ch else {}
            nm = getattr(ch, "name", ck) or ck
            bits = [f"{ck} ({nm})"]
            for fk in ("role", "want", "lie", "contradiction", "wound", "secret", "temperament"):
                if (f.get(fk) or "").strip():
                    bits.append(f"  {fk}: {str(f[fk]).strip()[:220]}")
            if homes.get(ck):
                bits.append(f"  lives at: {homes[ck]}")
            lines.append("\n".join(bits))
        existing = {(r.get("source"), r.get("target")) for r in sd.get("relationships") or []}
        existing |= {(t, s) for (s, t) in existing}
        parts = sd.get("premise_parts") or {}
        ctx_text = "\n".join(filter(None, [
            f"Premise: {sd.get('premise') or ''}",
            f"Tone: {sd.get('tone') or ''}",
            "\n".join(f"{k}: {v}" for k, v in parts.items() if (v or '').strip()),
            "\nCAST (their hidden harnesses — the undercurrents grow FROM these):",
            "\n".join(lines),
            f"\nBonds that already exist (do NOT re-propose these pairs): "
            f"{', '.join(f'{s}-{t}' for s, t in sorted(existing)) or '(none)'}",
        ]))
        stances = ["devoted", "warm", "neutral", "strained", "hostile"]
        schema = {"type": "object", "additionalProperties": False, "required": ["bonds"],
                  "properties": {"bonds": {"type": "array", "maxItems": 8, "items": {
                      "type": "object", "additionalProperties": False,
                      "required": ["source", "target", "nature", "stance", "dynamic",
                                   "target_stance", "target_dynamic", "potential", "trajectory"],
                      "properties": {
                          "source": {"type": "string", "enum": cast_keys},
                          "target": {"type": "string", "enum": cast_keys},
                          "nature": {"type": "string", "maxLength": 40,
                                     "description": "PLAIN mundane label, 1-4 words: 'landlady', 'childhood friend', 'rival herbalist'. No poetry."},
                          "stance": {"type": "string", "enum": stances},
                          "dynamic": {"type": "string",
                                      "description": "ONE observable daylight HABIT of source toward target, one short sentence — a thing a bystander could watch: 'steals her pens, denies it badly'. Behavior only, no analysis."},
                          "target_stance": {"type": "string", "enum": stances},
                          "target_dynamic": {"type": "string",
                                             "description": "target's observable habit toward source, one short sentence, same rules"},
                          "potential": {"type": "string",
                                        "description": "the UNDERCURRENT: what is secretly true between them RIGHT NOW, grown from a named wound/lie — a fact, not a prediction. 1-2 sentences."},
                          "trajectory": {"type": "string", "description": "from → to: how the bond turns when the hidden thing surfaces (1 sentence; predictions live HERE, not in potential)"},
                      }}}}}
        system = (
            "You weave the RELATIONSHIP WEB for a character-driven story. The register is daylight "
            "innocence over hidden depth: the surface is light and CONCRETE — running jokes, petty "
            "thefts, borrowed things never returned, dumb shared rituals — while underneath, every "
            "bond carries something secretly true, grown from the characters' named wounds and lies.\n"
            "Field discipline:\n"
            "- nature = a label a census would record. dynamic = an observable habit, filmable.\n"
            "- potential = a present-tense hidden FACT (who knows what, who is really what, what "
            "actually happened between them). NOT a prediction.\n"
            "- trajectory = the prediction: from → to when the hidden fact surfaces.\n"
            "The best undercurrents make the innocent surface RE-READ as something else entirely "
            "once known. Asymmetry is good: the two sides may misread each other. Not every bond is "
            "dark. Propose only bonds that matter; skip pairs with nothing real between them.")
        # Reasoning ON for construction quality; fall back to non-thinking if the reasoning
        # channel breaks structured output.
        out = {}
        for effort in ("high", "none"):
            provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": effort})
            if provider is None or not hasattr(provider, "generate_text"):
                return JSONResponse({"error": "no text model available"}, status_code=400)
            try:
                out = (provider.generate_text(system=system, prompt=ctx_text, emits=schema).data) or {}
                break
            except Exception as exc:  # noqa: BLE001
                if effort == "none":
                    return JSONResponse({"error": f"weave failed: {exc}"}, status_code=500)
        bonds = []
        for b in out.get("bonds") or []:
            s, t = b.get("source"), b.get("target")
            if not s or not t or s == t or (s, t) in existing:
                continue
            existing.add((s, t)); existing.add((t, s))   # dedupe within the proposal too
            bonds.append({"id": f"r-{s}-{t}", **b})
        if bonds:   # pipe into the work queue: proposals survive navigation until reviewed
            from ..records.cards import set_pending
            try:
                ctx.update_story_fields(key, {"fields": set_pending(_story_fields(key), "bonds", bonds)})
            except FileNotFoundError:
                pass   # draft story (genesis, not committed) — review stays in-page only
        return {"bonds": bonds}

    @app.post("/api/stories/{key}/conditions/generate")
    def conditions_generate(key: str, body: dict):
        """PROPOSE the setting's recurring STAGES — the modes this world moves through (seasons,
        event-states, place-states) that will visibly change daily life and switch on situational
        character content. Reasoned from the premise/tone/philosophy + the existing geography.
        Not saved — the Map tab reviews and keeps them. Returns {conditions: [Condition-shaped]}."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        sd = st.model_dump()
        parts = sd.get("premise_parts") or {}
        locs = ", ".join(l.get("name") or l.get("id") for l in (sd.get("locations") or [])) or "(none yet)"
        have = [(c.get("name") or "").strip() for c in (sd.get("conditions") or []) if (c.get("name") or "").strip()]
        ctx_text = "\n".join(filter(None, [
            f"PREMISE: {sd.get('premise') or ''}",
            f"TONE: {sd.get('tone') or ''}",
            f"PHILOSOPHY: {parts.get('philosophy') or ''}",
            f"PLACES: {locs}",
            f"ALREADY HAVE (don't repeat): {', '.join(have)}" if have else "",
        ]))
        schema = {"type": "object", "additionalProperties": False, "required": ["conditions"],
                  "properties": {"conditions": {"type": "array", "minItems": 3, "maxItems": 6, "items": {
                      "type": "object", "additionalProperties": False,
                      "required": ["name", "kind", "description", "effect"],
                      "properties": {
                          "name": {"type": "string", "description": "the stage, plainly named — 'The flood season', 'The deep snows'"},
                          "kind": {"type": "string", "enum": ["seasonal", "event", "place"]},
                          "description": {"type": "string", "description": "what it IS — the objective world-change, 1-2 sentences"},
                          "effect": {"type": "string", "description": "how it bends DAILY LIFE: what stops, what becomes dangerous or possible, what ordinary people do differently while it holds"},
                      }}}}}
        system = (
            "You define the recurring STAGES a story-world moves through — the modes it enters and "
            "leaves that reshape ordinary life while they last. Think seasons (deep snow, flood, "
            "drought), event-states (a siege, a festival, a plague), and place-states (a dungeon "
            "opens beneath the city, the tide exposes a causeway). Each must: recur or toggle (a "
            "persistent MODE, not a one-off plot beat), visibly change what people can and can't do, "
            "and grow from THIS world's specifics — its geography, its central pressure. Concrete "
            "and lived, never generic 'the weather changes'. These are the stages that will bring out "
            "different sides of the cast, so make each one a genuinely different way to live.")
        provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "high"})
        if provider is None or not hasattr(provider, "generate_text"):
            provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no text model available"}, status_code=400)
        try:
            out = (provider.generate_text(system=system, prompt=ctx_text, emits=schema).data) or {}
        except Exception:  # noqa: BLE001 — reasoning channel can break structured output
            try:
                provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
                out = (provider.generate_text(system=system, prompt=ctx_text, emits=schema).data) or {}
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"condition gen failed: {exc}"}, status_code=500)
        conds, seen = [], {c.lower() for c in have}
        for c in out.get("conditions") or []:
            nm = (c.get("name") or "").strip()
            if not nm or nm.lower() in seen:
                continue
            seen.add(nm.lower())
            conds.append({"id": re.sub(r"[^\w]+", "_", nm.lower()).strip("_") or f"cond{len(conds)}",
                          "name": nm, "kind": (c.get("kind") or "").strip(),
                          "description": (c.get("description") or "").strip(),
                          "effect": (c.get("effect") or "").strip()})
        if conds:   # pipe into the work queue: proposals survive navigation until reviewed
            from ..records.cards import set_pending
            try:
                ctx.update_story_fields(key, {"fields": set_pending(_story_fields(key), "conditions", conds)})
            except FileNotFoundError:
                pass   # draft story (genesis, not committed) — review stays in-page only
        return {"conditions": conds}

    @app.get("/api/stories/{key}/conditions/usage")
    def conditions_usage(key: str):
        """LINT the setting stages: how many cast exemplars each stage would activate
        (`when:<id>` tags across the cast's banks), plus ORPHANS — when: ids bound to no
        existing stage (a renamed/deleted condition silently kills its content otherwise).
        Returns {usage: {cond_id: count}, orphans: {when_id: count}}."""
        from ...server.services import lorebook_store as _LS
        from ..pipeline.character_scaffold import entry_when
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        known = {c.id for c in (st.conditions or []) if c.id}
        usage = {cid: 0 for cid in known}
        orphans: dict[str, int] = {}
        for m in st.cast:
            scope = re.sub(r"[^\w\-]+", "_", str(m.character))
            for e in _LS.load_lorebook(ctx.root, scope):
                w = entry_when(e)
                if not w:
                    continue
                if w in known:
                    usage[w] += 1
                else:
                    orphans[w] = orphans.get(w, 0) + 1
        return {"usage": usage, "orphans": orphans}

    # ── The WORK QUEUE — pending approvals + card todos as one ordered, non-locking list ──
    def _story_fields(key: str) -> dict:
        return dict((ctx._read_story_data(key).get("fields")) or {})

    @app.get("/api/stories/{key}/pending")
    def pending_get(key: str):
        """This story's pending approvals ({kind: {items}}) — generation output awaiting
        a human decision, persisted so review survives navigation. See stories/queue.py."""
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        return {"pending": _story_fields(key).get("pending") or {}}

    @app.put("/api/stories/{key}/pending/{kind}")
    def pending_put(key: str, kind: str, body: dict):
        """Set one kind's pending items (the review surfaces call this as the user keeps or
        dismisses proposals; an empty list clears the kind and its queue entry)."""
        from ..records.cards import PENDING_KINDS, set_pending
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if kind not in PENDING_KINDS:
            return JSONResponse({"error": f"unknown pending kind '{kind}'"}, status_code=400)
        fields = set_pending(_story_fields(key), kind, (body or {}).get("items") or [])
        ctx.update_story_fields(key, {"fields": fields})
        return {"pending": fields["pending"]}

    @app.get("/api/stories/{key}/queue")
    def story_queue(key: str):
        """The ordered work queue: for each card layer (overview → cast), pending approvals
        first, then the layer's todos. Advisory order — every item deep-links to its tab."""
        from ..records.cards import build_card
        from ..records.cards import build_queue
        from ...server.services.prompts import style_anchor
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        return {"items": build_queue(card, _story_fields(key).get("pending"))}

    @app.get("/api/stories/{key}/card")
    def story_card(key: str):
        """The story's CONTEXT CARD — the layered spine every generator reads (see
        stories/card.py). One layer per tab (overview → cast), each with content +
        the step's `todo` checklist. The cast layer's per-sprite deep zoom is
        POST /api/characters/{key}/sprite-stack."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        from ..records.cards import build_card
        from ...server.services.prompts import style_anchor
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        card["story"] = key
        return card

    @app.post("/api/stories/{key}/card/{layer}")
    def story_card_patch(key: str, layer: str, body: dict):
        """Mutate ONE card layer — the chokepoint narrative functions (plot dialogue,
        play consolidation) route through. The patch is filtered to the layer's
        whitelisted story fields (card.LAYER_FIELDS) and lands via the validated
        update path. Returns the rebuilt layer so callers can re-check the step."""
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        from ..records.cards import build_card, layer_patch_fields
        try:
            fields = layer_patch_fields(layer, body or {})
        except KeyError:
            return JSONResponse({"error": f"layer '{layer}' has no patchable story fields"},
                                status_code=400)
        if not fields:
            return JSONResponse({"error": "nothing patchable for this layer in the body"},
                                status_code=400)
        try:
            ctx.update_story_fields(key, fields)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        st = ctx.base_settings.stories.get(key)
        from ...server.services.prompts import style_anchor
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        lay = next((l for l in card["layers"] if l["id"] == layer), None)
        return {"ok": True, "layer": lay}

    def _rebuilt_layer(key: str, layer: str):
        st = ctx.base_settings.stories.get(key)
        from ..records.cards import build_card
        from ...server.services.prompts import style_anchor
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        return next((l for l in card["layers"] if l["id"] == layer), None)

    _SECTION_BRIEF = {
        "overview": "the WORLD at its highest level — designed top-down, not enumerated. Two things carry "
                    "it: the PRINCIPLE (premise_parts/root — the one law this world runs on, its defining "
                    "trait: 'reality runs on a spendable life-energy', 'the gods are dead and magic answers "
                    "to whoever takes it') and the CONFLICT (premise_parts/question — the unresolvable "
                    "dilemma that principle forces, with two defensible sides). The premise/tone/themes "
                    "follow from those. Edit with merge on premise_parts (key = root or question). When the "
                    "writer RESHAPES the world (a new principle, a different premise), update "
                    "premise_parts/root, premise_parts/question, AND premise together in the SAME turn so "
                    "they stay consistent — never rewrite the premise prose while leaving root or question "
                    "describing the old world. 'Change the principle' means edit premise_parts/root (and "
                    "re-derive the question), not the premise text. Do NOT "
                    "enumerate factions, traditions, or populations here — those particulars belong to the "
                    "cast, locations, and scenes, made when the story needs them. Never good-vs-evil; both "
                    "sides of the conflict must be righteous.",
        "map": "the WORLD — locations (each an item with an id) and the recurring setting conditions/stages.",
        "relationships": "the CAST & fixed BONDS — relationship items (each with source/target/nature and the "
                         "hidden potential/trajectory). Warmth drifts in play; you set the fixed structure.",
        "plot": "the PROGRESSION — arcs and the storyboard beats (the staged plan).",
    }

    @app.post("/api/stories/{key}/card/{layer}/chat")
    async def story_card_chat(key: str, layer: str, body: dict):
        """The SECTION COLLABORATOR — a thinking partner AND editor for ONE section, using
        HASH-ANCHORED (hashline) ops in the OhMyPi style.

        The model is shown an ANCHORED view of the editable nodes (path + #hash + preview).
        To change something it returns `ops`: each points at a node by slash-path AND cites
        the #hash it saw there. We re-read the live story, recompute each node's hash, and
        REJECT the op if the node drifted since the model read it — a stale read can no
        longer silently clobber a field edited elsewhere. Stale ops come back in
        `rejected` for the client to surface; the others apply through the existing
        whole-Story-validated write path.

        Returns {reply, applied:[{path,op}], rejected:[{path,reason, current_hash?}],
                 before:{top_field:old}, layer?}. `before` holds the pre-edit top-level
        fields for the client's Undo. Body {messages:[{role,text}]}."""
        import asyncio
        import threading

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from ...server.services import config_files as _cf
        from ..records.cards import LAYER_FIELDS
        from ..records.anchors import anchored_view, apply_ops, any_stale_rejections, merge_results, partial_reply
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if layer not in LAYER_FIELDS:
            return JSONResponse({"error": f"layer '{layer}' isn't editable by chat"}, status_code=400)
        messages = [m for m in ((body or {}).get("messages") or []) if isinstance(m, dict) and m.get("text")]
        if not messages:
            return JSONResponse({"error": "say something"}, status_code=400)
        # EFFICIENT ROUTING: hand the editor ONLY this layer's raw editable fields — the actual
        # arcs/bonds/conditions/premise_parts with their REAL ids/values, each tagged with a
        # content-hash anchor. The model edits by pointing at these anchors, so it never has to
        # re-send a whole list to change one item. Nothing else from the story is loaded.
        allowed = list(LAYER_FIELDS[layer])
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        # The anchored view is the model's map of what it may touch. Built from the LIVE
        # story so the anchors match what `apply_ops` will verify against right after.
        view = anchored_view(raw, allowed)
        # Structured `ops` (a real array — not a string-encoded patch). Each op names its
        # target by slash-path and cites the #hash it saw; merge/set/remove cover every
        # edit at field, item, and sub-field granularity under one vocabulary.
        schema = {"type": "object", "additionalProperties": False, "required": ["reply", "ops", "suggestions"],
                  "properties": {
                      "reply": {"type": "string", "description": "your conversational turn to the writer — "
                                "an ANSWER if they asked a question, a brief note if you made a change. Keep "
                                "it SHORT; put the concrete options in `suggestions`, not a wall of prose"},
                      "suggestions": {"type": "array", "description": "a short list (2–5) of concrete things "
                                "the writer could ADDRESS OR DEVELOP next — forks, gaps, open threads, or "
                                "directions to pursue. Each is a brief actionable phrase the writer can pick "
                                "to run with. In INTERVIEW MODE these are the forks. EMPTY only if truly "
                                "nothing is open.",
                                "items": {"type": "string"}},
                      "ops": {"type": "array", "description": "SURGICAL edits — one entry per node you "
                              "change. EMPTY if you're only discussing. Each entry: "
                              "{path, anchor, op, value}.",
                              "items": {"type": "object", "additionalProperties": False,
                                        "required": ["path", "op"],
                                        "properties": {
                                            "path": {"type": "string", "description": "slash-path of the "
                                                      "node, exactly as shown in the ANCHORED SECTION"},
                                            "anchor": {"type": "string", "description": "the #hash shown "
                                                       "beside that path (copy it). OMIT only when CREATING "
                                                       "a brand-new node that isn't in the view yet."},
                                            "op": {"type": "string", "enum": ["set", "merge", "remove"],
                                                   "description": "set=replace the node's value; "
                                                   "merge=deep-merge an object into a dict node; "
                                                   "remove=delete the node"},
                                            "value": {"description": "the new value for set/merge "
                                                       "(string, object, list…). OMIT for remove."}}}}}}
        # A blank story hasn't found its core question yet — the chat runs an INTERVIEW (below)
        # instead of waiting for edit commands. "Thin" = no premise and no premise_parts.question.
        _pp = (raw.get("premise_parts") or {}) if isinstance(raw, dict) else {}
        _thin = not (raw.get("premise") or "").strip() and not (_pp.get("question") or "").strip()
        system = (
            "You are the writer's COLLABORATOR on ONE section of their story bible — both a thinking "
            "partner and an editor. Choose your mode from the writer's LATEST message:\n"
            "• DISCUSSION — they ask a question, want your read, want to brainstorm, or ask you to weigh "
            "in: ANSWER substantively and specifically in `reply`, like a sharp co-writer who knows this "
            "story. Set `ops` to []. Do NOT edit just because you're talking. Questions ('what's the "
            "tension?', 'is this premise strong?', 'who is X?') get an answer, never an edit.\n"
            "• CHANGE — they explicitly ask you to change / add / remove / rewrite something: emit `ops`, "
            "one per node you change. Each op POINTS at its target by the slash-path from the ANCHORED "
            "SECTION and cites the #hash shown beside it. Three ops cover everything:\n"
            "   - set: replace the node's value (a string, a list, a whole object…).\n"
            "   - merge: deep-merge a JSON OBJECT into a dict node (use this to update ONE key of "
            "premise_parts without disturbing the others — value:{root:'…'}).\n"
            "   - remove: delete the node (a list item by id, a dict key).\n"
            "ANCHOR RULE: copy the #hash exactly as shown. OMIT `anchor` ONLY when creating a node that "
            "isn't in the view (a brand-new premise_parts key, a new condition). If your anchor is stale "
            "the edit is rejected — the writer will be told which paths changed, and can ask you again.\n"
            "You may edit at ANY granularity: a top-level field (premise), one dict key "
            "(premise_parts/root), one list item by id (arcs/arc-2), or one sub-field of an item "
            "(arcs/arc-2/premise). Prefer the SMALLEST change — set the one sub-field, not the whole item.\n"
            "REPLY MUST MATCH OPS — never say in `reply` that you changed, updated, added, or removed "
            "something unless you emitted an op for it THIS turn. If you only rewrote the premise, do not "
            "claim you also updated the principle or the question. Saying 'Done' while the ops are empty "
            "(or don't cover what you claim) is a hard failure — the writer trusts the reply.\n"
            "Only these top-level fields are editable: " + ", ".join(allowed) + ". Keep prose concrete and "
            "in the story's voice. NAMES: every faction, creed, religion, order or organization is ONE "
            "coined word — never two words, never 'The <Adjective> <Noun>' (Crownsworn, Unbound, "
            "Emberwake — NOT 'Harvest Binding').\n"
            "ALWAYS, in BOTH modes: keep `reply` short and fill `suggestions` with 2–5 concrete things "
            "the writer could address or develop next — the open threads, gaps, or directions that follow "
            "from where the story is now. These are optional picks the writer can run with, not commands. "
            "Leave `suggestions` empty only when there is genuinely nothing open.\n"
            f"SECTION: {_SECTION_BRIEF.get(layer, layer)}")
        if _thin and layer in ("overview", "map"):
            system += (
                "\n\nINTERVIEW MODE — the story is blank. The objective is to help the writer build a "
                "rich story from nothing by offering FORKS — concrete directions the story could take — "
                "and developing whichever one the writer picks. Lead; do not wait for edit commands.\n\n"
                "FORKS — a story gets rich by accumulating specifics, lorebook-style, not by nailing one "
                "dramatic spine. Each turn, put a few forks on the table, then develop the one the writer "
                "chooses and offer the next. A fork can be any of: a defining trait or law of the world; a "
                "central tension or question the story turns on; a character with a want and a secret; a "
                "place with a history; a relationship under strain; a recurring texture or motif. A central "
                "dilemma (the axioms below) is ONE fork among these, never the required destination — many "
                "good stories are cozy, exploratory, or character-driven and never pose one. Follow the "
                "writer; do not funnel every story toward a moral choice.\n"
                "PITCH WORLD FORKS AS KERNELS — one compressed sentence about HOW THE WORLD IS MADE: the "
                "foundational condition that constitutes it. VARY THE KIND across the set. At least one "
                "must be FULLY GROUNDED with no speculative or magical element whatsoever — a real "
                "material condition of the world (a climate, an economy, a technology, a political order), "
                "e.g. 'the world is dying: warming is desertifying the land, the cities flood, the old "
                "currencies collapsed and everything trades in crypto'. Spread the rest across the other "
                "registers — social, spiritual, mysterious, metaphysical — so magic is one option among "
                "many, never the default. A kernel names the constituting condition and STOPS: it does "
                "NOT enumerate society, customs, or factions — those are uncovered after a pick. The "
                "strongest kernels hold a mystery: something beyond the surface.\n\n"
                "DEFINITIONS\n"
                "World root: HOW THE WORLD IS MADE — the foundational condition that constitutes it and "
                "from which everything grows, stated as a standing fact about the world, not an event. It "
                "need NOT be magical: it can be ecological, economic, technological, social, spiritual, "
                "mysterious, or metaphysical — whatever makes this world's substrate unlike ours. A range "
                "of kinds — Rewrite (metaphysical): the world is alive and incarnates its own life-energy "
                "as familiars. The Wandering Inn (systemic + mystery): the world runs on Diablo-style "
                "leveling — Classes, Levels, Skills — but magic is something older, beyond the system. "
                "Alien Stage (social): human voices are farmed as entertainment by an alien overclass. "
                "Fully grounded (ecological + economic): the world is dying — warming is desertifying the "
                "land, the great cities are flooding, the old currencies collapsed and everything now "
                "trades in crypto. A "
                "mere circumstance ('a failing harvest') is an event IN a world; a root is the property of "
                "the world that generates such events. In grounded fiction the root is a systemic condition "
                "(a company town, an occupation), not a metaphysics — but still a standing trait, not an "
                "incident.\n"
                "Core question: the unresolvable dilemma the world root forces a specific person to answer "
                "under pressure. It is not a theme. A theme ('forgiveness', 'freedom') is a category; a "
                "core question is a forced choice between two defensible but incompatible answers.\n"
                "Concrete situation: a specific circumstance — particular place, time, and people — where "
                "the root makes the choice unavoidable. An abstract formulation ('freedom vs. security') "
                "is never a situation; it is a seminar topic.\n\n"
                "AXIOMS — WHEN the writer takes the central-tension fork, these sharpen it into something "
                "that lands. They do NOT apply to the other forks; skip them entirely if the story has no "
                "dilemma.\n"
                "1. SYMMETRY OF SIDES. A core question must have two defensible sides. A decent person "
                "could choose either, and suffer for it. If one side is obviously correct, the question "
                "is under-derived: continue until you can name the person who would take the losing side "
                "and construct the argument for why they are not wrong to. Operation: when the writer "
                "states a one-sided position, ask who is destroyed by it and who would oppose them on "
                "defensible grounds.\n"
                "2. THE ANSWER COSTS THE WINNER. The choice must cost the person who makes it — not in "
                "what they lose, but in what they become by making it. A costless resolution is excluded. "
                "Operation: for any position the writer commits to, ask what the protagonist becomes by "
                "holding it, and what that transformation costs over time.\n"
                "3. ACCRUAL OVER DETONATION. Profundity compounds through sustained pressure, not a "
                "single crisis. Operation: ask what carrying the question for years does to a person; "
                "prefer slow erosion over a detonating event.\n"
                "4. CONDITIONAL ANTAGONISM. Where the genre permits, the opposing force is an indifferent "
                "condition (a process, an ecology, a law), not an agent with intent. An antagonist that "
                "can be bargained with or defeated is weaker than one that cannot, because indifference "
                "resists no argument. Operation: when the writer proposes a villain, test whether the "
                "same pressure could be exerted by a non-agentive condition, and prefer it where it can.\n"
                "5. THE DIGNITY GAP. The most load-bearing character is often the one the diegetic world "
                "has devalued or misclassified — and whose actual interiority contradicts that "
                "classification. The gap between assigned status and real personhood is the structural "
                "source of the reader's re-evaluation. Operation: identify which character the writer's "
                "world underestimates, and ask what it would take for them to be correctly seen.\n"
                "6. THE LIE OVER THE WOUND. A character's false belief (the adaptive distortion produced "
                "by past harm) is structurally more useful than the harm itself, because the belief "
                "generates daily behavior whereas the wound is inert backstory. Operation: given a "
                "character's wound, derive the false belief it installed, the behavior it dictates, and "
                "the event that would falsify it.\n\n"
                "HARD CONSTRAINTS\n"
                "C1. Stay concrete. Instantiate every fork in specifics — a particular world, place, "
                "person, or thing — never an abstract label. For a tension: 'the last free city falls "
                "unless it adopts the methods it is fighting', not 'freedom vs. security'.\n"
                "C2. Never use capitalized abstract-noun oppositions, 'X versus Y' framings, or "
                "theme-word labels. These are the exact failure signature of unoriginal output.\n"
                "C3. One fork or question per turn. Then stop and wait.\n"
                "C4. Do not answer for the writer. You offer forks and develop their pick; they decide.\n"
                "C5. Keep `ops` empty while exploring. Commit only once the writer settles on something.\n\n"
                "PUT THE FORKS IN `suggestions` — the forks you offer are the `suggestions` list, one "
                "fork per item, each a short concrete phrase the writer can pick. `reply` is just the "
                "one-line framing around them, never a long enumeration.\n"
                "OPENING MOVE — if the writer has provided no material yet: greet in one line in `reply`, "
                "then put three to four DISTINCT forks in `suggestions`, MIXED in kind — e.g. a world with "
                "a defining trait, a character with a want and a secret, a place with a history, a central "
                "tension. Each fork is ONE kernel sentence — the principle, not its worked-out "
                "consequences. Range widely: some speculative, some grounded, some dramatic, some quiet. Do "
                "not offer bare theme-words or abstract oppositions (C1, C2), and do not make every option "
                "a dilemma.\n\n"
                "PROCEDURE\n"
                "When the writer picks or supplies a fork, develop it into something specific in `reply` "
                "(one or two lines), then put the NEXT forks in `suggestions`. If the fork is a central "
                "tension, sharpen it with the axioms — who is destroyed by it (A1), what it costs the "
                "chooser (A2), is the antagonist conditional (A4), what false belief it implies (A6). For "
                "any other fork, add a concrete detail and a reason it matters.\n"
                "AGREEMENT SIGNAL: when the writer accepts a formulation, says it's right, or picks an "
                "option, treat it as SETTLED and COMMIT it. Continuing to interrogate past agreement is a "
                "failure mode — the interview must produce committed material, not talk indefinitely.\n\n"
                "COMMIT — on agreement, write what the writer settled on to the fields you own (merge, so "
                "you touch only what changed): premise = a sentence on what the story is; premise_parts/root "
                "= the world's defining trait, IF the story has one; premise_parts/question = a central "
                "tension, ONLY IF the writer took that fork — never invent one. Commit only what's actually "
                "settled and leave the rest blank; a cozy story may commit a premise and nothing else. Cast, "
                "places, and plot are built later by their own editors. Do not commit on the first message; "
                "offer forks first, then commit each piece as the writer agrees.")
        convo = "\n".join(f"{'Writer' if m.get('role') == 'user' else 'You'}: {m['text']}" for m in messages)
        _roles = _cf.load_text_roles(ctx.root)
        _PROVIDER_ROLE = _roles.get("director") or _roles.get("narrator")

        # SSE plumbing — the model streams its `reply` text so the writer watches it form
        # in the chat window; the ops apply/persist happens after and lands in a final event.
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def _emit_reply(text: str):
            loop.call_soon_threadsafe(q.put_nowait, {"type": "reply", "text": text})

        def _run(view, note="", stream=False):
            """Run the editor with a given anchored view. `note` appends a freshness
            instruction; `stream=True` pushes reply-so-far deltas to the SSE queue."""
            prompt = (f"ANCHORED SECTION (path  #hash  preview):\n{view}\n\n"
                      f"EDITABLE FIELDS: {', '.join(allowed)}\n\nCONVERSATION:\n{convo}\n\n"
                      f"{note}Answer or edit per the writer's LATEST message.")
            # Effort ladder. `reasoning_effort: high` on this model reliably produces near-EMPTY /
            # malformed STRUCTURED output (a 48-char reply, no suggestions, sometimes nothing) — the
            # reasoning channel eats the answer. Measured: high → ~empty; low → fast (~5s) + clean;
            # none → works but slow/verbose. So lead with `low`, then fall back to the non-reasoning
            # path a couple times before giving up.
            for effort in ("low", "none", "none", "none"):
                p = ctx.text_provider_for(_PROVIDER_ROLE, {"reasoning_effort": effort})
                if p is None:
                    return None
                on_delta = None
                if stream:
                    acc: list[str] = []           # per-attempt (a retry restreams cleanly, replacing)
                    def on_delta(t, acc=acc):
                        acc.append(t)
                        _emit_reply(partial_reply("".join(acc)))
                try:
                    out = (p.generate_text(system=system, prompt=prompt, emits=schema,
                                           on_delta=on_delta).data) or {}
                except Exception:  # noqa: BLE001 — reasoning channel can break structured output
                    out = {}
                if out.get("reply") or out.get("ops") or out.get("suggestions"):
                    return out
            return {}

        def compute():
            """The blocking model→apply→persist path (run off the event loop). Returns the
            final result dict the client applies; reply text has already streamed live."""
            out = _run(view, stream=True)
            if out is None:
                return {"error": "no editor model configured", "_status": 400}
            reply = (out.get("reply") or "").strip()
            suggestions = [s.strip() for s in (out.get("suggestions") or [])
                           if isinstance(s, str) and s.strip()]
            ops = out.get("ops") if isinstance(out.get("ops"), list) else []
            # Re-read the LIVE story right before applying, so anchors are checked against the
            # freshest state (not the snapshot the model read). apply_ops mutates `live` in place
            # and returns {applied, rejected, before} — `before` holds the OLD top-level field
            # values for the client's Undo; `live` now holds the NEW merged values to persist.
            try:
                live = ctx._read_story_data(key)
            except FileNotFoundError:
                live = raw
            result = apply_ops(live, ops)

            # ── Stale-anchor recovery (one retry) ─────────────────────────────────
            # If any op failed PURELY due to staleness (the node drifted since the model read it
            # — a concurrent edit), re-show the model the FRESH anchored view and ask it to
            # re-emit just those ops with the updated anchors. Structural failures (a path that's
            # gone, a merge on a non-object) are NOT retried — they'd loop. We persist once
            # (after the retry) so the writer sees a single coherent apply. `live` already holds
            # any first-pass applied mutations in memory (nothing persisted yet) — the retry
            # applies ON TOP of that state, so nothing is lost.
            if any_stale_rejections(result["rejected"]):
                stale_paths = [r["path"] for r in result["rejected"] if any_stale_rejections([r])]
                fresh_view = anchored_view(live, allowed)
                note = (f"NOTE: your prior edit(s) to {', '.join(stale_paths)} were STALE — those "
                        "nodes changed since you read them. The fresh anchored view above has the "
                        "CURRENT #hashes. Re-emit ONLY the op(s) for those path(s) with the updated "
                        "anchors, or reply that you can't.\n\n")
                r2 = _run(fresh_view, note=note)
                if r2:
                    ops2 = r2.get("ops") if isinstance(r2.get("ops"), list) else []
                    if ops2:
                        retry_result = apply_ops(live, ops2)   # on the already-mutated state
                        result = merge_results(result, retry_result)
                        if r2.get("reply") and not reply:
                            reply = r2["reply"].strip()

            applied, rejected, before = result["applied"], result["rejected"], result["before"]
            if not applied:
                # Nothing landed — reply only. Surface rejections so the client can show why.
                return {"reply": reply, "suggestions": suggestions, "applied": [], "rejected": rejected, "before": {}}
            # Persist the NEW values (live was mutated by apply_ops) through the validated write
            # path — `before` (the old values) goes back to the client for Undo.
            try:
                ctx.update_story_fields(key, {f: live.get(f) for f in before})
            except Exception as exc:  # noqa: BLE001 — validation rejected the merged story → don't corrupt
                return {"reply": reply, "suggestions": suggestions, "applied": [], "rejected": rejected,
                        "before": {}, "error": f"couldn't apply: {exc}"}
            return {"reply": reply, "suggestions": suggestions, "applied": applied, "rejected": rejected,
                    "before": before, "layer": _rebuilt_layer(key, layer)}

        # Drive compute() off the event loop; stream reply deltas, then the final result.
        async def run():
            try:
                data = await run_in_threadpool(compute)
                loop.call_soon_threadsafe(q.put_nowait, {"type": "result", "data": data})
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(q.put_nowait, None)

        asyncio.create_task(run())

        async def events():
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev)}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.delete("/api/stories/{key}")
    def delete_story(key: str):
        import shutil
        from ...server.services import story_store as _SS
        from ..records.store import delete_db
        safe = re.sub(r"[^\w\-]+", "", key)
        if not _SS.story_exists(ctx.root, safe):
            # fall back to legacy on-disk forms (pre-migration yaml/json/folder)
            yaml_p = ctx.story_dir() / f"{safe}.yaml"
            legacy_json = ctx.story_dir() / f"{safe}.json"
            folder = ctx.story_dir() / safe
            if not yaml_p.is_file() and not legacy_json.is_file() and not (folder / "story.json").is_file():
                return JSONResponse({"error": "no such story"}, status_code=404)
        # delete from the relational store (the source of truth)
        _SS.delete_story(ctx.root, safe)
        # also sweep any lingering legacy on-disk forms + the assets folder
        yaml_p = ctx.story_dir() / f"{safe}.yaml"
        legacy_json = ctx.story_dir() / f"{safe}.json"
        folder = ctx.story_dir() / safe
        if yaml_p.is_file():
            yaml_p.unlink()
        delete_db(legacy_json)                           # legacy flat (+ any <safe>.db)
        shutil.rmtree(folder, ignore_errors=True)        # folder form: story's embedded chars' assets
        ctx.reload_settings()
        removed = ctx.prune_orphan_characters()  # cascade: any pre-migration global-pool leftovers
        return {"ok": True, "removed_characters": removed}

    @app.post("/api/stories/{key}/regenerate-cast")
    async def regenerate_cast(key: str, body: dict):
        """DESTRUCTIVE: re-derive the whole cast from the story's storyboard, STREAMED live as a
        job (roster pass + each character). Step 1 distils the source card into ONE clean BASE
        CHARACTER CARD; step 2 re-extracts supporting NPCs in that structure. Old story-bound
        characters (+ portraits) are deleted and the cast rewritten; the source card is untouched.
        Returns {job} — consume /api/jobs/<id>/stream to watch + know when it's done."""
        import shutil

        from ..pipeline import extract_characters, extract_protagonist

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        provider, systems = ctx.builder_ctx(body or {}, "characters")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        # Resolve the protagonist source: the imported source card if it still exists, else fall
        # back to the story's current primary cast member.
        source = (st.fields or {}).get("source_character")
        prot_key = source if (source and source in ctx.base_settings.characters) else None
        if prot_key is None:
            prot_key = next((m.character for m in st.cast if m.primary), None) \
                or (st.cast[0].character if st.cast else None)
        prot = ctx.base_settings.characters.get(prot_key) if prot_key else None
        board = {"logline": st.storyboard.logline, "premise": st.premise, "tone": st.tone,
                 "beats": [b.model_dump() for b in st.storyboard.beats]}

        def work(emit, cancelled):
            prot_data = None
            if prot is not None:
                prot_data = extract_protagonist(
                    provider, name=prot.name, persona=prot.system or "",
                    extras=ctx.card_extras(prot, prot_key),
                    systems=systems, on_event=emit)
            out = extract_characters(
                provider, name=(prot_data["name"] if prot_data else st.name),
                persona=(prot_data["persona"] if prot_data else ""),
                board=board, extras=ctx.card_extras(prot, prot_key) if prot else {},
                systems=systems,
                reference_card=(prot_data["persona"] if prot_data else ""), on_event=emit)
            npcs = out.get("npcs", [])
            if cancelled():
                return {"cancelled": True}

            # Compose the RICH base prompt for EVERYONE via the shared ✨ pipeline (single
            # appearance authority) — in parallel — so the regenerated cast matches the manual ✨
            # button (skin-tone/eye-demeanor/cooccur/etc.) with no extra step.
            from concurrent.futures import ThreadPoolExecutor
            people = ([("__prot__", prot_data)] if prot_data else []) \
                + [(str(i), n) for i, n in enumerate(npcs)]

            from ..pipeline import compose_base_prompt as _compose_base_prompt
            _bp_cfg = ctx.load_story_builder()
            _bp_prov = ctx.stage_provider("base_image")
            _bp_sys = (_bp_cfg.get("systems") or {})

            def _bp(item):
                pid, p = item
                if cancelled():
                    return (pid, "")
                emit({"type": "phase", "label": f"Rendering appearance — {p.get('name', '?')}"})
                try:
                    r = _compose_base_prompt(_bp_prov, p.get("name", ""), p.get("persona", ""),
                                             p.get("appearance", ""), p.get("role", ""),
                                             systems=_bp_sys)
                except Exception as exc:  # noqa: BLE001 — one character must not sink the whole regen
                    emit({"type": "phase", "label": f"{p.get('name', '?')}: appearance failed ({exc})"})
                    return (pid, "")
                bp = r.get("prompt", "") if isinstance(r, dict) else ""
                if isinstance(r, dict):
                    h = (r.get("features") or {}).get("height_cm")
                    if h:
                        p["height_cm"] = h        # same dict write_npc persists -> fields.height_cm
                if bp:
                    emit({"type": "item", "name": p.get("name", "?"), "text": bp})
                return (pid, bp)

            bps = {}
            if people:
                with ThreadPoolExecutor(max_workers=min(len(people), 6)) as ex:
                    bps = dict(ex.map(_bp, people))

            emit({"type": "phase", "label": "Saving the cast…"})
            # Build the whole new cast, commit the story yaml, THEN delete the old members —
            # transactional: a failed write throws before the story is touched.
            created: list[str] = []
            cast = []
            if prot_data is not None:
                # No ref_from: the protagonist's base image is GENERATED like everyone else (the
                # source card stays the STYLE anchor via source_character, not the literal image).
                pkey = ctx.write_npc(prot_data, story_key=key, base_prompt=bps.get("__prot__", ""))
                cast.append({"character": pkey, "primary": True}); created.append(pkey)
            for i, npc in enumerate(npcs):
                nk = ctx.write_npc(npc, story_key=key, base_prompt=bps.get(str(i), ""))
                cast.append({"character": nk, "primary": False}); created.append(nk)
            ctx.update_story_fields(key, {"cast": cast})   # DB-backed or YAML — routed + validated
            keep = {source, *created}
            cdir = ctx.char_dir()
            # Sweep EVERY character bound to THIS story that isn't part of the new cast — not just the
            # previous st.cast — so duplicate/orphan members left by earlier or cancelled regenerations
            # (e.g. a stale 'kaia_nakumura' beside the new 'kaia_nakumura_2') are cleared automatically.
            for ck, ch in list(ctx.base_settings.characters.items()):
                if ck in keep or (ch.fields or {}).get("story") != key:
                    continue
                safe = re.sub(r"[^\w\-]+", "", ck)
                for fn in (f"{safe}.yaml", f"{safe}.png", f"{safe}.ref.png"):
                    f = cdir / fn
                    if f.is_file():
                        f.unlink()
                shutil.rmtree(ctx.portrait_dir(ck), ignore_errors=True)
            ctx.reload_settings()

            # Auto-plan wardrobes using scene-based reasoning — one call per location.
            emit({"type": "phase", "label": "Planning scene wardrobes…"})
            try:
                w_prov, w_sys = ctx.builder_ctx({}, "wardrobe")
                if w_prov is not None:
                    from ..pipeline import plan_story_wardrobe as _plan_story_wardrobe
                    from ..pipeline.wardrobe import compose_outfit_prompt as _cop
                    from concurrent.futures import ThreadPoolExecutor as _TPE
                    full_story = st.model_dump()
                    name_to_key_regen = {}
                    cast_details_regen = []
                    for ckey in created:
                        ch_r = ctx.base_settings.characters.get(ckey)
                        if ch_r is None:
                            continue
                        name_to_key_regen[ch_r.name.lower()] = ckey
                        cast_details_regen.append({
                            "name": ch_r.name,
                            "persona": ch_r.system or "",
                            "appearance": (ch_r.fields or {}).get("appearance", ""),
                            "key": ckey,
                        })
                    scene_plans = _plan_story_wardrobe(
                        w_prov, story=full_story, cast=cast_details_regen,
                        systems=w_sys, on_event=emit)
                    all_w: list[dict] = []
                    for scene in scene_plans:
                        for o in scene.get("outfits") or []:
                            cn = (o.get("character") or "").lower()
                            ck = name_to_key_regen.get(cn) or next(
                                (k for n, k in name_to_key_regen.items()
                                 if cn and (cn in n or n in cn)), None)
                            if not ck:
                                continue
                            ch_r = ctx.base_settings.characters.get(ck)
                            all_w.append({
                                **o,
                                "name": o.get("outfit_name") or o.get("name") or "Outfit",
                                "_char_key": ck,
                                "_persona": (ch_r.system or "") if ch_r else "",
                                "_appearance": ((ch_r.fields or {}).get("appearance", "")) if ch_r else "",
                            })
                    def _ref(o):
                        try:
                            r = _cop(w_prov, o["_persona"], o["_appearance"],
                                     o.get("name", ""), o.get("concept") or "")
                            if r.get("attire"):
                                o["attire_prompt"] = r["attire"]
                                o["unified"] = r.get("unified", False)
                        except Exception:  # noqa: BLE001
                            pass
                        return o
                    if all_w:
                        with _TPE(max_workers=min(len(all_w), 8)) as _ex:
                            all_w = list(_ex.map(_ref, all_w))
                    by_char_regen: dict[str, list] = {}
                    for o in all_w:
                        by_char_regen.setdefault(o["_char_key"], []).append(o)
                    for ckey, woutfits in by_char_regen.items():
                        if cancelled():
                            break
                        ch_r = ctx.base_settings.characters.get(ckey)
                        emit({"type": "phase", "label": f"Applying {ch_r.name if ch_r else ckey}'s wardrobe"})
                        _apply_manifest(ctx, ckey, {"outfits": woutfits, "replace": True},
                                        provider=w_prov)
            except Exception as exc:  # noqa: BLE001
                emit({"type": "phase", "label": f"Wardrobe planning skipped ({exc})"})

            emit({"type": "phase", "label": f"Done — {len(cast)} cast members"})
            return {"ok": True, "cast": [m["character"] for m in cast], "created": created}

        job = _start_stream_job("cast", "Regenerate cast", st.name, f"stories/{key}/cast", work)
        return {"job": job.id}


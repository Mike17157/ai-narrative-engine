from __future__ import annotations

import base64
import json
import re

import yaml
from fastapi.responses import FileResponse, JSONResponse

from ..config.schema import ModelDef
from ..server.services import config_files
from ..server.services.config_files import STORY_BUILDER_DEFAULT
from ..server.services.images import _clean_reference_png, _randomize_seeds, _render
from ..server.services.jobs_util import _start_stream_job
from ..server.services.prompts import FEATURES_SCHEMA, PLAY_SCHEMA, _assemble_base_prompt
from .pipeline import apply_manifest as _apply_manifest, plan_and_apply as _plan_and_apply
# The story pipeline runs on pydantic-graph state machines (see graph_pipeline.py).
from .graph_pipeline import StoryState, StoryDeps, run_turn, run_draft


def register(app, ctx):
    @app.get("/api/story-builder")
    def get_story_builder() -> dict:
        return config_files.load_story_builder(ctx.root)

    @app.post("/api/story-builder")
    def set_story_builder(body: dict):
        path = ctx.root / "configs" / "story_builder.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**STORY_BUILDER_DEFAULT, **(body or {})}, indent=2), encoding="utf-8")
        return {"ok": True}

    # The build is a controlled, stepped procedure — one LLM call per endpoint,
    # reviewed in the UI before the next. Nothing is written until /api/stories.
    @app.post("/api/stories/storyboard")
    async def story_storyboard(body: dict):
        """Stage 1 — STREAM the storyboard live (LOGLINE/PREMISE/TONE/THEMES/BEATS)
        so it can be watched. Emits `delta` text events + a final `board` event.
        If the client disconnects (cancel), upstream generation is stopped."""
        import asyncio
        import threading

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from .pipeline import parse_storyboard, storyboard_inputs

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, systems = ctx.builder_ctx(body, "storyboard")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        spine = body.get("spine") or {}
        from .pipeline import grounding as _G
        _prem = (body.get("premise") or "").strip()
        system, prompt = storyboard_inputs(name=ch.name, persona=ch.system,
                                           extras=ctx.card_extras(ch, body["character"]),
                                           systems=systems,
                                           premise=_prem,
                                           spine=spine,
                                           craft=_G.craft_notes(ctx.root, f"{_prem} {ch.system or ''}"[:600], k=6, section="character"))

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()

        def on_delta(t: str):
            loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": t})

        async def run():
            try:
                res = await run_in_threadpool(lambda: provider.generate_text(
                    system=system, prompt=prompt, on_delta=on_delta,
                    cancel=cancel_evt.is_set))
                if not cancel_evt.is_set():
                    board = parse_storyboard(res.text or "")
                    loop.call_soon_threadsafe(q.put_nowait, {"type": "board", "board": board})
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(q.put_nowait, None)

        asyncio.create_task(run())

        async def events():
            try:
                while True:
                    ev = await q.get()
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                cancel_evt.set()  # client disconnected / cancelled → stop upstream
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/stories/expand-graph")
    async def story_expand_graph(body: dict):
        """Faithfully expand a DEVELOPMENT GRAPH into a chapter draft — one chapter per
        node, preserving ids and branches. The conversation's graph becomes the draft;
        nothing is re-derived from a premise string.

        Body: { character, graph: {logline, wound, lie, truth, nodes[]}, model? }
        Streams `delta` text + a final `graph` event with the enriched graph.
        """
        import asyncio
        import threading

        from fastapi.responses import StreamingResponse

        from .pipeline._helpers import _card_context

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, systems = ctx.builder_ctx(body, "storyboard")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        graph = body.get("graph") or {}
        nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")]
        if not nodes:
            return JSONResponse({"error": "no graph nodes to expand"}, status_code=400)

        card = _card_context(ch.name, ch.system, ctx.card_extras(ch, body["character"]))

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()

        # Drive the draft graph (expand → revise). Steps emit a `graph` event via on_event.
        deps = StoryDeps(
            provider=provider,
            on_event=lambda e: loop.call_soon_threadsafe(q.put_nowait, e),
            cancel=cancel_evt.is_set,
            do_revise=bool(body.get("revise")),
        )
        state = StoryState(card=card, working_graph=graph)

        async def run():
            try:
                await run_draft(state, deps)
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(q.put_nowait, None)

        asyncio.create_task(run())

        async def events():
            try:
                while True:
                    ev = await q.get()
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                cancel_evt.set()
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    # ── Character simulation (emergent narrative) ─────────────────────────────
    # Design an info-isolated cast (Sonnet, one pass), then simulate them scene by
    # scene (GLM actors + DeepSeek director). The transcript feeds the graph later.

    @app.post("/api/stories/simulate/design")
    def sim_design(body: dict):
        """Design a cast for simulation — each with goals + a private secret. Sonnet, one pass."""
        from .simulation import design_cast
        body = body or {}
        cfg = ctx.load_story_builder()
        prov = ctx.stage_provider("characters")
        if prov is None:
            return JSONResponse({"error": "no character-design model configured"}, status_code=400)
        premise = (body.get("premise") or "").strip()
        if not premise:
            return JSONResponse({"error": "premise required"}, status_code=400)
        n = max(2, min(int(body.get("n") or 3), 6))
        try:
            cast = design_cast(prov, premise, n)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        if not cast:
            return JSONResponse({"error": "no characters produced (model may not support structured output)"}, status_code=502)
        return {"characters": cast}

    @app.post("/api/stories/simulate/scene")
    async def sim_scene(body: dict):
        """Run ONE autonomous scene burst — streams turns, returns the updated sim_state."""
        import asyncio
        import threading

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from .simulation import run_scene_burst

        body = body or {}
        cfg = ctx.load_story_builder()
        director = ctx.stage_provider("sim_director")
        actor = ctx.stage_provider("sim_actor")
        if director is None or actor is None:
            return JSONResponse({"error": "simulation models not configured (sim_director / sim_actor)"}, status_code=400)
        # The sim lives in the thread's State doc `sim` level. Prefer the client-sent
        # sim_state (contract unchanged); fall back to the stored level when a sid is given.
        sid = (body.get("sid") or "").strip()
        sim_state = body.get("sim_state") or {}
        if not sim_state.get("characters") and sid:
            from ..server.services.story_sessions import load_session
            from .simulation import sim_of
            sim_state = sim_of((load_session(ctx.root, sid) or {}).get("state"))
        if not sim_state.get("characters"):
            return JSONResponse({"error": "no characters to simulate"}, status_code=400)
        steer = (body.get("steer") or "").strip()
        max_turns = max(2, min(int(body.get("max_turns") or 8), 16))

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        holder: dict = {}

        def emit(e: dict):
            loop.call_soon_threadsafe(q.put_nowait, e)

        async def run():
            try:
                holder["state"] = await run_in_threadpool(lambda: run_scene_burst(
                    director_prov=director, actor_prov=actor, sim_state=sim_state,
                    max_turns=max_turns, steer=steer, on_event=emit))
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
            if holder.get("state") is not None:
                # Persist into the thread's State doc `sim` level when a sid was given
                # (the client still receives the sim_state, so its contract is unchanged).
                if sid:
                    try:
                        from ..server.services.story_sessions import load_session, save_session
                        from .simulation import with_sim
                        _sess = load_session(ctx.root, sid) or {}
                        save_session(ctx.root, sid,
                                     {**_sess, "state": with_sim(_sess.get("state"), holder["state"])})
                    except Exception:  # noqa: BLE001 — persistence is best-effort
                        pass
                yield f'data: {json.dumps({"type": "state", "sim_state": holder["state"]})}\n\n'
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")


    # ── Character psyche scaffold — build ONE character through a conversation ──────
    # Exemplars (life/saying/reaction) accrete into the character's own lorebook
    # (scope = character key) as the interview agrees on them. See character_scaffold.py.

    def _char_scope(key: str) -> str:
        return re.sub(r"[^\w\-]+", "_", str(key or ""))

    def _facet_cards(scope: str) -> list[dict]:
        from ..server.services import lorebook_store as LS
        from .pipeline.character_scaffold import FACET_TYPES
        out = []
        for e in LS.load_lorebook(ctx.root, scope):
            # An exemplar is anything typed as a facet — regardless of which tool wrote it
            # (interview, deepen, harvest, contrast). Filtering by source hid deepen's bank.
            if (e.facet or "") not in FACET_TYPES:
                continue
            out.append({"id": e.id, "type": e.facet or "life", "title": e.title,
                        "keywords": list(e.keywords or []), "content": e.content,
                        "source": e.source or ""})
        return out

    @app.post("/api/stories/character/{key}/interview")
    def character_interview(key: str, body: dict):
        """One conversational turn developing a character. Returns the agent's `reply` and
        commits any newly-agreed exemplars to the character lorebook.
        Body: { messages: [{role, content}], model?: str }"""
        from ..server.services import lorebook_store as LS
        from .pipeline.character_scaffold import (
            INTERVIEW_SCHEMA, INTERVIEW_SYSTEM, facet_to_entry, interview_prompt)

        body = body or {}
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, _systems = ctx.builder_ctx(body, "character")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)

        scope = _char_scope(key)
        entries = LS.load_lorebook(ctx.root, scope)
        prompt = interview_prompt(ch.name, ch.system, entries, body.get("messages") or [])
        try:
            res = provider.generate_text(system=INTERVIEW_SYSTEM, prompt=prompt, emits=INTERVIEW_SCHEMA)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"interview failed: {exc}"}, status_code=500)

        data = res.data or {}
        saved = []
        for f in (data.get("facets") or []):
            e = facet_to_entry(f)
            if e is not None:
                LS.upsert_entry(ctx.root, scope, e)
                saved.append(e.id)
        return {"ok": True, "reply": (data.get("reply") or "").strip(),
                "saved": saved, "facets": _facet_cards(scope)}

    @app.post("/api/stories/character/{key}/deepen")
    def character_deepen(key: str, body: dict):
        """TWO passes: (1) a REASONED psychological portrait — the model actually thinks the
        person through (mechanism, contradictions, the defense's daily cost), free prose, no
        slot structure; (2) the exemplar bank written FROM that portrait, the model choosing
        which moments this person needs captured. The portrait persists to fields.psychology
        (working material for later steps); exemplars go to the character's lorebook, which
        play retrieves per scene. Returns {saved, facets, portrait}."""
        from ..server.services import lorebook_store as LS
        from .pipeline.character_scaffold import (
            DEEPEN_SYSTEM, PORTRAIT_SYSTEM, deepen_facets_schema, deepen_prompt,
            facet_to_entry, portrait_prompt)
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        f = ch.fields or {}
        harness = {k: f.get(k) or "" for k in
                   ("role", "temperament", "want", "lie", "wound", "secret", "good_memory")}
        if not any(harness.values()):
            return JSONResponse({"error": "character has no harness (want/lie/wound…) to deepen from"},
                                status_code=400)
        # The owning story supplies the frame: premise as world, philosophy as the central question,
        # and its SETTING STAGES so the bank can carry per-stage (situational) reactions/secrets.
        world, philosophy, conds = "", "", []
        owner = ctx._char_owner(key)
        st = ctx.base_settings.stories.get(owner) if owner else None
        if st is not None:
            world = (st.premise or "")[:500]
            philosophy = ((st.premise_parts or {}).get("philosophy") or "")[:500]
            conds = [c.model_dump() for c in (st.conditions or [])]

        # Phase 1 — think the person through (REASONING ON, plain prose).
        thinker = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "high"})
        if thinker is None or not hasattr(thinker, "generate_text"):
            return JSONResponse({"error": "no text model available"}, status_code=400)
        try:
            portrait = (thinker.generate_text(
                system=PORTRAIT_SYSTEM,
                prompt=portrait_prompt(ch.name, ch.system, harness, world=world, philosophy=philosophy),
            ).text or "").strip()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"portrait failed: {exc}"}, status_code=500)
        if not portrait:
            return JSONResponse({"error": "portrait came back empty"}, status_code=500)
        # Persist the portrait — working material for later steps (contrast, weave, play prompts).
        try:
            data_c = ctx._read_character_data(key) or {}
            data_c.setdefault("fields", {})["psychology"] = portrait
            ctx._write_character_data(key, data_c)
        except Exception:  # noqa: BLE001 — the bank still lands; portrait persistence is best-effort
            pass

        # Phase 2 — the bank, FROM the portrait (structured; reasoning off for schema reliability).
        scope = _char_scope(key)
        entries = LS.load_lorebook(ctx.root, scope)
        writer = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
        try:
            data = (writer.generate_text(
                system=DEEPEN_SYSTEM,
                prompt=deepen_prompt(ch.name, portrait, entries, conditions=conds),
                emits=deepen_facets_schema([c.get("id") for c in conds])).data) or {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"exemplar write failed: {exc}"}, status_code=500)
        saved = []
        for fc in (data.get("facets") or []):
            e = facet_to_entry(fc, source="deepen")
            if e is not None:
                LS.upsert_entry(ctx.root, scope, e)
                saved.append(e.id)
        return {"ok": True, "saved": saved, "portrait": portrait, "facets": _facet_cards(scope)}

    @app.get("/api/stories/character/{key}/facets")
    def character_facets(key: str):
        """All exemplar cards established for a character (read from its lorebook)."""
        if ctx.base_settings.characters.get(key) is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        return {"ok": True, "facets": _facet_cards(_char_scope(key))}

    @app.post("/api/stories/character/{key}/facets/{entry_id}/refine")
    def character_refine_facet(key: str, entry_id: str, body: dict):
        """Regenerate ONE exemplar given a writer instruction (the iterate primitive)."""
        from ..server.services import lorebook_store as LS
        from .pipeline.character_scaffold import REFINE_SCHEMA, FACET_TYPES

        body = body or {}
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        scope = _char_scope(key)
        entry = next((e for e in LS.load_lorebook(ctx.root, scope) if e.id == entry_id), None)
        if entry is None:
            return JSONResponse({"error": "no such facet"}, status_code=404)
        provider, _systems = ctx.builder_ctx(body, "character")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)

        instruction = (body.get("instruction") or "").strip() or "Sharpen and make it more specific."
        ftype = entry.facet or "life"
        guide = {"life": "a vivid 1-3 sentence scene from their past",
                 "saying": "an actual line in their voice",
                 "reaction": "a 'When <situation>, they <do/say>' pattern"}.get(ftype, "")
        prompt = (
            f"CHARACTER: {ch.name}\nPERSONA:\n{ch.system or '(thin)'}\n\n"
            f"CURRENT {ftype.upper()} EXEMPLAR — \"{entry.title}\":\n{entry.content}\n\n"
            f"WRITER'S INSTRUCTION: {instruction}\n\n"
            f"Rewrite this {ftype} exemplar as {guide}. Keep it true to the character. "
            "Output structured JSON only.")
        try:
            res = provider.generate_text(
                system=f"You refine one character exemplar. Type: {ftype}. Concrete, specific, "
                       "in-character. No abstract trait talk.", prompt=prompt, emits=REFINE_SCHEMA)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"refine failed: {exc}"}, status_code=500)
        d = res.data or {}
        if (d.get("content") or "").strip():
            entry.title = (d.get("title") or entry.title).strip()
            entry.content = d["content"].strip()
            if d.get("keywords"):
                entry.keywords = [str(k).strip() for k in d["keywords"] if str(k).strip()]
            LS.upsert_entry(ctx.root, scope, entry)
        return {"ok": True, "facets": _facet_cards(scope)}

    @app.delete("/api/stories/character/{key}/facets/{entry_id}")
    def character_delete_facet(key: str, entry_id: str):
        from ..server.services import lorebook_store as LS
        LS.delete_entry(ctx.root, _char_scope(key), entry_id)
        return {"ok": True, "facets": _facet_cards(_char_scope(key))}

    @app.post("/api/stories/improv")
    def character_improv(body: dict):
        """Put 2-3 characters in a scene and let them bounce off each other (reveals
        idiosyncrasy through behaviour). Returns the transcript — nothing is committed.
        Body: { characters: [key], situation: str, rounds?: int }"""
        from ..server.services import lorebook_store as LS
        from .pipeline.character_scaffold import facet_digest, run_improv

        body = body or {}
        keys = [k for k in (body.get("characters") or []) if k]
        if len(keys) < 2:
            return JSONResponse({"error": "pick at least two characters"}, status_code=400)
        cast = []
        for k in keys:
            ch = ctx.base_settings.characters.get(k)
            if ch is None:
                return JSONResponse({"error": f"no such character: {k}"}, status_code=404)
            digest = facet_digest(LS.load_lorebook(ctx.root, _char_scope(k)))
            cast.append({"key": k, "name": ch.name, "persona": ch.system, "digest": digest})
        provider, _systems = ctx.builder_ctx(body, "character")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)
        situation = (body.get("situation") or "").strip() or \
            f"{cast[0]['name']} and {cast[1]['name']} cross paths and end up talking."
        try:
            rounds = int(body.get("rounds") or 2)
        except (TypeError, ValueError):
            rounds = 2
        try:
            transcript = run_improv(provider, situation=situation, cast=cast, rounds=max(1, min(rounds, 3)))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"improv failed: {exc}"}, status_code=500)
        return {"ok": True, "situation": situation, "transcript": transcript}

    @app.post("/api/stories/improv/harvest")
    def character_improv_harvest(body: dict):
        """Distil an improv transcript into new exemplars, committed per character.
        Body: { characters: [key], transcript: [{speaker, text}] }"""
        from ..server.services import lorebook_store as LS
        from .pipeline.character_scaffold import (
            FACETS_SCHEMA, HARVEST_SYSTEM, facet_to_entry, harvest_prompt)

        body = body or {}
        keys = [k for k in (body.get("characters") or []) if k]
        transcript = body.get("transcript") or []
        if not keys or not transcript:
            return JSONResponse({"error": "need characters and a transcript"}, status_code=400)
        provider, _systems = ctx.builder_ctx(body, "character")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)

        added = {}
        for k in keys:
            ch = ctx.base_settings.characters.get(k)
            if ch is None:
                continue
            scope = _char_scope(k)
            existing = LS.load_lorebook(ctx.root, scope)
            try:
                res = provider.generate_text(
                    system=HARVEST_SYSTEM,
                    prompt=harvest_prompt(ch.name, transcript, existing), emits=FACETS_SCHEMA)
            except Exception:  # noqa: BLE001
                continue
            ids = []
            for f in ((res.data or {}).get("facets") or []):
                e = facet_to_entry(f)
                if e is not None:
                    LS.upsert_entry(ctx.root, scope, e)
                    ids.append(e.id)
            added[k] = {"added": ids, "facets": _facet_cards(scope)}
        return {"ok": True, "characters": added}

    @app.post("/api/stories/contrast")
    def character_contrast(body: dict):
        """For each character, invent exemplars that make them DISTINCT from the others in the
        selection (keeps an ensemble from feeling same-y). Body: { characters: [key] }"""
        from ..server.services import lorebook_store as LS
        from .pipeline.character_scaffold import (
            CONTRAST_SYSTEM, FACETS_SCHEMA, contrast_prompt, facet_digest, facet_to_entry)

        body = body or {}
        keys = [k for k in (body.get("characters") or []) if k]
        if len(keys) < 2:
            return JSONResponse({"error": "pick at least two characters to contrast"}, status_code=400)
        provider, _systems = ctx.builder_ctx(body, "character")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)

        # Pre-load every selected character's persona + established digest.
        cast = {}
        for k in keys:
            ch = ctx.base_settings.characters.get(k)
            if ch is None:
                return JSONResponse({"error": f"no such character: {k}"}, status_code=404)
            entries = LS.load_lorebook(ctx.root, _char_scope(k))
            cast[k] = {"name": ch.name, "persona": ch.system, "entries": entries,
                       "digest": facet_digest(entries)}

        out = {}
        for k in keys:
            me = cast[k]
            others = [{"name": cast[o]["name"], "persona": cast[o]["persona"], "digest": cast[o]["digest"]}
                      for o in keys if o != k]
            scope = _char_scope(k)
            try:
                res = provider.generate_text(
                    system=CONTRAST_SYSTEM,
                    prompt=contrast_prompt(me["name"], me["persona"], me["entries"], others),
                    emits=FACETS_SCHEMA)
            except Exception:  # noqa: BLE001
                continue
            ids = []
            for f in ((res.data or {}).get("facets") or []):
                e = facet_to_entry(f)
                if e is not None:
                    LS.upsert_entry(ctx.root, scope, e)
                    ids.append(e.id)
            out[k] = {"added": ids, "facets": _facet_cards(scope)}
        return {"ok": True, "characters": out}

    @app.post("/api/stories/arcs/weave")
    def arcs_weave(body: dict):
        """Weave THEMED ARCS from a developed cast's exemplars (Phase 2: drama from character,
        not from a story spine). Returns arcs; nothing is persisted.
        Body: { characters: [key], premise?: str }"""
        from ..server.services import lorebook_store as LS
        from .pipeline.arc_weave import ARCS_SCHEMA, ARCS_SYSTEM, arcs_prompt, digest_for

        body = body or {}
        keys = [k for k in (body.get("characters") or []) if k]
        if len(keys) < 1:
            return JSONResponse({"error": "pick at least one character"}, status_code=400)
        cast = []
        for k in keys:
            ch = ctx.base_settings.characters.get(k)
            if ch is None:
                return JSONResponse({"error": f"no such character: {k}"}, status_code=404)
            cast.append({"key": k, "name": ch.name, "persona": ch.system,
                         "digest": digest_for(LS.load_lorebook(ctx.root, _char_scope(k)))})
        # Arc reasoning is heavier than per-facet calls — use the "arc" stage (falls back to the
        # active author model unless a stage_arc preset pins it elsewhere).
        provider, _systems = ctx.builder_ctx(body, "arc")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)
        from .pipeline import grounding as _G
        _arc_craft = _G.craft_notes(ctx.root, f"theme arc structure climax ending {body.get('premise') or ''}"[:400], k=6, section="arc")
        try:
            res = provider.generate_text(system=ARCS_SYSTEM + (("\n\n" + _arc_craft) if _arc_craft else ""),
                                         prompt=arcs_prompt(cast, body.get("premise") or ""),
                                         emits=ARCS_SCHEMA)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"arc weave failed: {exc}"}, status_code=500)
        arcs = (res.data or {}).get("arcs") or []
        return {"ok": True, "arcs": arcs, "cast": [c["name"] for c in cast]}

    @app.post("/api/stories/from-cast")
    def story_from_cast(body: dict):
        """Persist a character-first build: a Story that REFERENCES the existing developed
        characters (their exemplar lorebooks light up automatically in play) + the woven themed
        arcs. No story spine, no NPC duplication. Body: { name, premise?, characters:[key], arcs:[...] }"""
        from ..config.schema import Arc as ArcModel

        body = body or {}
        keys = [k for k in (body.get("characters") or []) if k in ctx.base_settings.characters]
        if not keys:
            return JSONResponse({"error": "no valid characters"}, status_code=400)

        name = (body.get("name") or ctx.base_settings.characters[keys[0]].name or "Story").strip()
        cast = [{"character": k, "primary": (idx == 0)} for idx, k in enumerate(keys)]
        name_to_key = {ctx.base_settings.characters[k].name.lower(): k for k in keys}

        arcs_validated = []
        for idx, a in enumerate(body.get("arcs") or []):
            if not isinstance(a, dict):
                continue
            spotlight = [name_to_key[str(n).lower()] for n in (a.get("spotlight") or [])
                         if str(n).lower() in name_to_key]
            arc = {"id": f"arc-{idx + 1}", "name": a.get("name") or f"Arc {idx + 1}",
                   "themes": a.get("themes") or [], "premise": a.get("premise") or "",
                   "dramatic_function": a.get("turn") or "", "cast": spotlight, "order": idx}
            try:
                arcs_validated.append(ArcModel(**arc).model_dump())
            except Exception:  # noqa: BLE001
                arcs_validated.append(arc)

        locations = []
        for l in (body.get("locations") or []):
            if not isinstance(l, dict) or not l.get("id"):
                continue
            locations.append({"id": l["id"], "name": l.get("name", l["id"]),
                              "description": l.get("description", ""),
                              "background_prompt": l.get("background_prompt", ""),
                              "background": l.get("background")})
        loc_ids = {l["id"] for l in locations}
        start = body.get("start") if body.get("start") in loc_ids else (locations[0]["id"] if locations else None)

        try:
            skey = ctx.create_story(name, {
                "premise": body.get("premise", ""), "cast": cast, "arcs": arcs_validated,
                "fields": {"source_character": keys[0]}, "locations": locations, "start": start,
            }, character_keys=keys, type_=body.get("type", "novel"))   # one self-contained <skey>.db
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save story: {exc}"}, status_code=400)
        return {"ok": True, "key": skey}

    @app.post("/api/stories/builder/prompt")
    def builder_prompt(body: dict):
        """Preview/inspect a builder STAGE's prompt before generating. Returns the
        editable `base` system prompt, the `default` (for reset), and the effective
        `system` (the effective system prompt). For the storyboard stage it also
        composes the user-message `prompt` for the given character. Stages:
        storyboard · locations · characters · wardrobe."""
        from .pipeline import NEEDS_IMAGE, DEFAULT_SYSTEMS, _sys, storyboard_inputs

        body = body or {}
        stage = body.get("stage", "storyboard")
        if stage not in DEFAULT_SYSTEMS:
            return JSONResponse({"error": f"unknown stage '{stage}'"}, status_code=400)
        cfg = config_files.load_story_builder(ctx.root)
        systems = cfg.get("systems") or {}
        out = {
            "stage": stage,
            "base": systems.get(stage) or DEFAULT_SYSTEMS[stage],
            "default": DEFAULT_SYSTEMS[stage],
            "system": _sys(systems, stage),
            "model": config_files._stage_model(cfg, stage, body.get("model")),
            "requires_image": stage in NEEDS_IMAGE,
        }
        ch = ctx.base_settings.characters.get(body.get("character"))
        if stage == "storyboard" and ch is not None:
            _, out["prompt"] = storyboard_inputs(name=ch.name, persona=ch.system,
                                                 extras=ctx.card_extras(ch, body["character"]),
                                                 systems=systems)
        return out

    @app.post("/api/stories/builder/test")
    async def builder_test(body: dict):
        """Test ONE builder stage end-to-end for a chosen character, using that stage's
        (possibly unsaved) model + system prompt. Prerequisite stages run
        with their SAVED config. Returns a readable {summary, output} to preview before
        committing the config."""
        from fastapi.concurrency import run_in_threadpool

        from . import pipeline as B
        from .pipeline import extract_characters, extract_locations, plan_wardrobe

        body = body or {}
        stage = body.get("stage", "storyboard")
        if stage not in B.DEFAULT_SYSTEMS:
            return JSONResponse({"error": f"unknown stage '{stage}'"}, status_code=400)
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "pick a character to test with"}, status_code=400)
        cfg = config_files.load_story_builder(ctx.root)
        saved = cfg.get("systems") or {}
        systems = dict(saved)
        if body.get("system"):           # the unsaved edit for the target stage
            systems[stage] = body["system"]
        t_prov = ctx.stage_provider(stage, body.get("model"))
        if t_prov is None:
            return JSONResponse({"error": "no author model configured"}, status_code=400)
        extras = ctx.card_extras(ch, body.get("character"))
        fields = ch.fields or {}

        def gen_board(target: bool):
            sysd = systems if (target and stage == "storyboard") else saved
            prov = t_prov if (target and stage == "storyboard") else ctx.stage_provider("storyboard")
            s, p = B.storyboard_inputs(name=ch.name, persona=ch.system, extras=extras, systems=sysd)
            return B.parse_storyboard(prov.generate_text(system=s, prompt=p).text or "")

        def run():
            if stage == "storyboard":
                bd = gen_board(True)
                out = "\n".join([f"LOGLINE: {bd['logline']}", f"TONE: {bd['tone']}", "",
                                 f"{len(bd['beats'])} chapters:"] +
                                [f"{i}. {b.get('title') or '(untitled)'}  —  @{b.get('location') or '?'}"
                                 for i, b in enumerate(bd['beats'], 1)])
                return f"{len(bd['beats'])} chapters", out
            if stage == "base_image":
                ctx_text = "\n\n".join(s for s in [
                    f"NAME: {ch.name}", f"PERSONA:\n{ch.system}" if ch.system else "",
                    f"APPEARANCE NOTES: {fields.get('appearance')}" if fields.get("appearance") else "",
                    f"ROLE: {fields.get('role')}" if fields.get("role") else ""] if s)
                sysb = systems.get("base_image") or B.DEFAULT_SYSTEMS["base_image"]
                imgs = []
                ref = ctx.reference_path(body["character"])
                if ref:
                    imgs = ["data:image/png;base64," + base64.b64encode(ref.read_bytes()).decode()]
                    ctx_text = "Describe the CHARACTER IN THE IMAGE.\n\n" + ctx_text
                feats = t_prov.generate_text(system=sysb, prompt=ctx_text, emits=FEATURES_SCHEMA, images=imgs).data or {}
                return "feature schema filled" + (" (from reference image)" if imgs else ""), _assemble_base_prompt(feats)
            bd = gen_board(False)
            if stage == "locations":
                locs = extract_locations(t_prov, board=bd, systems=systems).get("locations", [])
                return f"{len(locs)} locations", "\n".join(
                    f"• {l.get('name')} ({l.get('id')})\n  {l.get('background_prompt') or l.get('description','')}" for l in locs)
            if stage == "characters":
                npcs = extract_characters(t_prov, name=ch.name, persona=ch.system, board=bd,
                                          extras=extras, systems=systems).get("npcs", [])
                return f"{len(npcs)} characters", "\n\n".join(
                    f"• {n.get('name')} — {n.get('role','')}\n  {n.get('appearance','')}" for n in npcs) or "(no supporting cast in this storyboard)"
            if stage == "wardrobe":
                story = {"premise": bd.get("premise", ""), "tone": bd.get("tone", ""),
                         "storyboard": {"logline": bd.get("logline", ""), "beats": bd.get("beats", [])}}
                plan = plan_wardrobe(t_prov, char_name=ch.name, persona=ch.system,
                                     appearance=fields.get("appearance", ""), story=story,
                                     systems=systems)
                outs = "\n".join(f"• {o['name']}: {o.get('concept') or o.get('attire_prompt', '')}" for o in plan.get("outfits", []))
                exprs = "\n".join(f"• {k}: {v}" for k, v in (plan.get("expressions") or {}).items())
                return (f"{len(plan.get('outfits', []))} outfits, {len(plan.get('expressions') or {})} expressions",
                        f"OUTFITS\n{outs}\n\nEXPRESSIONS\n{exprs}")
            return "", ""

        try:
            summary, output = await run_in_threadpool(run)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"test failed: {exc}"}, status_code=500)
        return {"stage": stage, "summary": summary, "output": output}

    @app.post("/api/stories/premise-from-character")
    def premise_from_character(body: dict):
        """One-shot: invent a STORY PREMISE seeded by a reference character card (or several).
        Body: { character?: str, characters?: [key], premise?: str (a hint to lean toward) }.
        Returns { premise }. Routes through the builder chokepoint (falls back to the author
        model when no `premise` stage preset exists)."""
        from .pipeline._helpers import _card_context

        body = body or {}
        keys = body.get("characters") or ([body["character"]] if body.get("character") else [])
        cards = [(k, ctx.base_settings.characters.get(k)) for k in keys]
        cards = [(k, c) for k, c in cards if c is not None]
        if not cards:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, _systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)
        blocks = [_card_context(c.name, c.system, ctx.card_extras(c, k)) for k, c in cards]
        cards_text = "\n\n---\n\n".join(blocks)
        hint = (body.get("premise") or "").strip()
        system = ("You are a story architect. From the character card(s) below, invent ONE "
                  "compelling story PREMISE: one or two sentences naming who it's about, the "
                  "situation, and the central tension — concrete, grounded, and true to these "
                  "characters. Output ONLY the premise prose: no title, no preamble, no quotes.")
        prompt = f"CHARACTER CARD(S):\n{cards_text}"
        if hint:
            prompt += f"\n\nLEAN TOWARD THIS IDEA: {hint}"
        try:
            res = provider.generate_text(system=system, prompt=prompt)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"generation failed: {exc}"}, status_code=500)
        premise = (res.text or "").strip().strip('"').strip()
        if not premise:
            return JSONResponse({"error": "model returned nothing"}, status_code=500)
        return {"premise": premise}

    # The premise's core components — what a strong premise must address. Replaces the old scripted
    # "premise interview"; the overview checks these against the story instead (premise-coverage).
    _PREMISE_COMPONENTS = [
        ("philosophy", "Overarching philosophy",
         "the argument the story interrogates — a real question with two DEFENSIBLE sides, which "
         "characters embody through their lies and choices (not a moral, an open contest)"),
        ("protagonist", "Protagonist", "who the story is about — a specific person, not a type"),
        ("lie", "The lie they live by", "the false belief / self-deception the story will test"),
        ("inciting", "Inciting incident", "what breaks the calm and sets the story in motion"),
        ("opposition", "Opposition", "who or what pushes back against the protagonist"),
        ("stakes", "Stakes", "what is at risk — what is lost if they fail"),
        ("texture", "Tone & texture", "the mood, genre and sensory feel of the world"),
    ]

    @app.post("/api/stories/{key}/premise-parts/draft")
    def premise_parts_draft(key: str, body: dict):
        """AI-DRAFT premise components (protagonist / lie / inciting / opposition / stakes /
        texture). Body {component: id} drafts that ONE (even if already filled — a redraft);
        empty body drafts every empty component. Drafts from the premise + tone + themes + cast
        + the parts already written, so components stay consistent with each other. Returns
        {parts: {id: text}} — NOT saved; the overview's fields are the editing surface and the
        normal story PUT persists them."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        sd = st.model_dump()
        parts = {k: v for k, v in (sd.get("premise_parts") or {}).items() if (v or "").strip()}
        only = ((body or {}).get("component") or "").strip()
        want = ([c for c in _PREMISE_COMPONENTS if c[0] == only] if only
                else [c for c in _PREMISE_COMPONENTS if c[0] not in parts])
        if not want:
            return JSONResponse({"error": "unknown component" if only else "nothing to draft"},
                                status_code=400)
        cast = ", ".join(getattr(ctx.base_settings.characters.get(m.get("character")), "name", m.get("character"))
                         for m in (sd.get("cast") or []) if m.get("character")) or "(none yet)"
        written = "\n".join(f"- {cid}: {parts[cid]}" for cid, _l, _d in _PREMISE_COMPONENTS if cid in parts)
        ctx_text = "\n".join([
            f"Premise: {sd.get('premise') or '(empty)'}",
            f"Logline: {(sd.get('storyboard') or {}).get('logline') or ''}",
            f"Tone: {sd.get('tone') or ''}",
            f"Themes: {', '.join(sd.get('themes') or [])}",
            f"Cast: {cast}",
            f"Components already written:\n{written}" if written else "",
        ])
        comp_lines = "\n".join(f"- {cid} ({label}): {desc}" for cid, label, desc in want)
        # v4pro with REASONING for construction quality (fallback to non-thinking below if the
        # reasoning channel breaks structured output).
        provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "high"})
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no text model available"}, status_code=400)
        schema = {"type": "object", "additionalProperties": False, "required": ["parts"],
                  "properties": {"parts": {"type": "array", "items": {
                      "type": "object", "additionalProperties": False, "required": ["id", "text"],
                      "properties": {"id": {"type": "string", "enum": [c[0] for c in want]},
                                     "text": {"type": "string", "description": "1-2 concrete sentences"}}}}}}
        system = ("You draft the missing COMPONENTS of a story premise. Write each as 1-2 concrete, "
                  "specific sentences grounded in the material given — name names, pick particulars, "
                  "no vague archetypes. Stay consistent with the components already written.")
        prompt = f"STORY SO FAR:\n{ctx_text}\n\nDRAFT THESE COMPONENTS:\n{comp_lines}"
        try:
            out = (provider.generate_text(system=system, prompt=prompt, emits=schema).data) or {}
        except Exception:  # noqa: BLE001 — reasoning channel can break structured output; retry plain
            try:
                provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
                out = (provider.generate_text(system=system, prompt=prompt, emits=schema).data) or {}
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"draft failed: {exc}"}, status_code=500)
        drafted = {p["id"]: p["text"].strip() for p in (out.get("parts") or [])
                   if isinstance(p, dict) and p.get("id") and (p.get("text") or "").strip()}
        return {"parts": drafted}

    # ── Relationship-first genesis (harnesses → web → derived stories) ────────────
    # See loom/stories/GENESIS.md. Premise is an OUTPUT: design unnamed harnesses, weave
    # the tension web, derive candidate stories, commit one (names the cast + writes the
    # Story). Steps 1-3 are stateless structured passes over client-held draft state; only
    # commit persists. All route through the `premise` builder chokepoint.

    @app.post("/api/stories/genesis/world")
    def genesis_world(body: dict):
        """Step 0 — author the WORLD frame (the stage, not the plot) from a one-line idea.
        Body: { seed?, model? } → { genre, tone, setting, situation }. See genesis.design_world."""
        from .genesis import design_world
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return design_world(provider, seed=body.get("seed", ""))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"world design failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/world/field")
    def genesis_world_field(body: dict):
        """Regenerate ONE field of the world frame (inline ↻). Body: { field, world?, seed?, model? }
        → { value }. Honors the per-request `model` override like every genesis step."""
        from .genesis import regen_world_field
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return {"value": regen_world_field(provider, body.get("world") or {},
                                               (body.get("field") or "").strip(), seed=body.get("seed", ""))}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"regen failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/harnesses")
    def genesis_harnesses(body: dict):
        """Step 1 — design N unnamed character harnesses around an optional `seed`, who BELONG to the
        authored `world`. Body: { seed?, n?, world?, model? } → { harnesses: [{id, role, …}] }."""
        from .genesis import design_harnesses
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        seed = body.get("seed", "")
        # Ground the cast in the SAME modern-psych scaffold the workshop uses (_psyche book) — so
        # genesis characters have real depth, not random traits. See GENESIS.md §6.
        from .pipeline import grounding as _G
        try:
            psyche = _G.psyche_notes(ctx.root, seed or "character personality behaviour", k=6)
        except Exception:  # noqa: BLE001 — grounding is best-effort; never block generation
            psyche = ""
        try:
            hs = design_harnesses(provider, seed=seed, n=body.get("n", 4), grounding=psyche,
                                  world=body.get("world") or "")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"harness design failed: {exc}"}, status_code=500)
        if not hs:
            return JSONResponse({"error": "the model returned no characters — it likely declined the "
                                 "prompt or doesn't support structured output. Pick a different model "
                                 "in the ⚙ picker (or your usual chat model) and try again."},
                                status_code=502)
        return {"harnesses": hs}

    @app.post("/api/stories/genesis/roles")
    def genesis_roles(body: dict):
        """Function-first cast — generate ONE focused harness per Truby dramatic role (protagonist / ally /
        opponent / false-ally / mirror), each a separate model run in context of the cast so far. See
        GENESIS.md §6. Body: { seed?, world?, model? } → { harnesses: [{id, function, role, …}] }."""
        from .genesis import design_by_role
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        seed = body.get("seed", "")
        from .pipeline import grounding as _G
        try:
            psyche = _G.psyche_notes(ctx.root, seed or "character personality behaviour", k=6)
        except Exception:  # noqa: BLE001 — grounding is best-effort; never block generation
            psyche = ""
        try:
            hs = design_by_role(provider, seed=seed, grounding=psyche, world=body.get("world") or "")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"role design failed: {exc}"}, status_code=500)
        if not hs:
            return JSONResponse({"error": "the model returned no characters — try a different model in "
                                 "the ⚙ picker."}, status_code=502)
        return {"harnesses": hs}

    @app.post("/api/stories/genesis/worldgen")
    def genesis_worldgen(body: dict):
        """BOTTOM-UP world gen: accrete N radically distinct, procedurally-ruled SYSTEMS that interlock
        (à la The Wandering Inn's faerie magic beside the [System]); then let a concrete opening EMERGE
        from their sharpest collision. Body: { seed?, n?, systems?, model? } → { systems, scenario }."""
        from .worldgen import accrete_systems, scenario_from_systems, gen_particulars
        body = body or {}
        provider, systems_err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems_err}, status_code=400)
        seed = body.get("seed", "")
        try:
            if body.get("mode") == "particulars":   # anti-slop: lived fragments, systems implicit
                sub = body.get("substrate") if isinstance(body.get("substrate"), dict) else None
                parts = gen_particulars(provider, seed, int(body.get("n") or 6), substrate=sub)
                if not parts:
                    return JSONResponse({"error": "the model returned nothing — try a different model."}, status_code=502)
                return {"particulars": parts}
            systems = body.get("systems") if isinstance(body.get("systems"), list) else accrete_systems(provider, seed, int(body.get("n") or 4))
            scenario = scenario_from_systems(provider, systems, seed) if systems else {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"worldgen failed: {exc}"}, status_code=500)
        if not systems:
            return JSONResponse({"error": "the model returned no systems — try a different model."}, status_code=502)
        return {"systems": systems, "scenario": scenario}

    @app.post("/api/stories/genesis/premise")
    def genesis_premise(body: dict):
        """Targeted PREMISE — a concrete dramatic situation (want/obstacle/stakes/spark), NOT a theme.
        Body: { seed?, model? } → { premise }."""
        from .worldgen import build_premise
        body = body or {}
        provider, err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": err}, status_code=400)
        try:
            p = build_premise(provider, body.get("seed", ""))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"premise failed: {exc}"}, status_code=500)
        if not p:
            return JSONResponse({"error": "the model returned no premise — try a different model."}, status_code=502)
        return {"premise": p}

    @app.post("/api/stories/genesis/substrate")
    def genesis_substrate(body: dict):
        """The invisible SUBSTRATE — the world's ache + a few real traditions + place + people (the skeleton
        the author knows, never shows). Body: { seed?, model? } → { substrate }."""
        from .worldgen import build_substrate
        body = body or {}
        provider, err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": err}, status_code=400)
        try:
            sub = build_substrate(provider, body.get("seed", ""))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"substrate failed: {exc}"}, status_code=500)
        if not sub:
            return JSONResponse({"error": "the model returned no substrate — try a different model."}, status_code=502)
        return {"substrate": sub}

    @app.post("/api/stories/genesis/scene-loop")
    def genesis_scene_loop(body: dict):
        """Loop-based scene gen — draft → critique → revise, N rounds, converging to the bar (no culling).
        Model-agnostic: tests whether looping lifts a WEAK model to strong-model quality. Body: { brief,
        rounds?, model?, critic_model?, } → { scene, trace, rounds_used }."""
        from .worldgen import loop_scene
        body = body or {}
        provider, systems_err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems_err}, status_code=400)
        critic = provider
        cm = (body.get("critic_model") or "").strip()
        if cm:
            try:
                critic = ctx.text_provider_for(cm, {}, None) or provider
            except Exception:  # noqa: BLE001
                critic = provider
        try:
            out = loop_scene(provider, body.get("brief") or "", int(body.get("rounds") or 3), critic_provider=critic)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"scene-loop failed: {exc}"}, status_code=500)
        if not out.get("scene"):
            return JSONResponse({"error": "the model returned no scene — try a different model."}, status_code=502)
        return out

    @app.post("/api/stories/genesis/generate")
    async def genesis_generate(body: dict):
        """Grow a WHOLE story in one streamed pydantic-graph run — premise → substrate → particulars →
        cast → weave → scene, each a focused node. Returns {job}; GenStream renders per-node progress
        (events: {type:'node', node, status}). The per-step endpoints above stay for manual editing.
        Body: { seed?, world?, rounds?, model?, critic_model? }."""
        from .genesis_graph import (run_genesis, GenesisState, GenesisDeps, genesis_state_from,
                                     save_run, load_run)
        import uuid as _uuid
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        critic = provider
        cm = (body.get("critic_model") or "").strip()
        if cm:
            try:
                critic = ctx.text_provider_for(cm, {}, None) or provider
            except Exception:  # noqa: BLE001
                critic = provider
        seed = body.get("seed", "")
        # RESUME: a run is keyed by run_id; its checkpoint (the accumulated graph state) is stored as
        # configs/genesis_runs/<run_id>.json. Reload it → completed nodes short-circuit, rest re-run.
        run_id = (body.get("run_id") or "").strip() or _uuid.uuid4().hex[:12]
        saved = load_run(ctx.root, run_id) if body.get("run_id") else None
        if isinstance(saved, dict) and saved:
            state = genesis_state_from(saved)
        else:
            # Ground the cast in the modern-psych scaffold (same _psyche book as genesis_roles).
            from .pipeline import grounding as _G
            try:
                psyche = _G.psyche_notes(ctx.root, seed or "character personality behaviour", k=6)
            except Exception:  # noqa: BLE001 — grounding is best-effort
                psyche = ""
            state = GenesisState(seed=seed, world=body.get("world") or "", grounding=psyche,
                                 rounds=int(body.get("rounds") or 2))

        def work(emit, cancelled):
            import asyncio as _aio
            emit({"type": "run", "run_id": run_id})    # client keeps this to resume an interrupted run
            def checkpoint(state_dict):
                save_run(ctx.root, run_id, state_dict)
            deps = GenesisDeps(provider=provider, root=ctx.root, critic_provider=critic,
                               on_event=emit, cancel=cancelled, on_checkpoint=checkpoint)
            return _aio.run(run_genesis(state, deps))

        job = _start_stream_job("genesis", "Grow story", (seed[:60] or "story"), "stories/genesis", work)
        return {"job": job.id}

    @app.post("/api/stories/genesis/systems")
    async def genesis_systems(body: dict):
        """Bottom-up WORLD gen as a streamed pydantic-graph run — accrete interlocking systems →
        scenario. Returns {job}; GenStream renders per-node progress. Body: { seed?, n?, model? }.
        (The sync /genesis/worldgen stays for the particulars mode + non-streamed callers.)"""
        from .genesis_graph import run_systems, SystemsState, GenesisDeps
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        state = SystemsState(seed=body.get("seed", ""), n=int(body.get("n") or 4))

        def work(emit, cancelled):
            import asyncio as _aio
            deps = GenesisDeps(provider=provider, on_event=emit, cancel=cancelled)
            return _aio.run(run_systems(state, deps))

        job = _start_stream_job("genesis", "World systems", (body.get("seed", "")[:60] or "world"),
                                "stories/genesis", work)
        return {"job": job.id}

    @app.post("/api/stories/genesis/weave")
    def genesis_weave(body: dict):
        """Step 2 — wire the tension web between harnesses.
        Body: { harnesses:[…], model? } → { relationships:[…] }."""
        from .genesis import weave_relationships
        body = body or {}
        harnesses = body.get("harnesses") or []
        if len(harnesses) < 2:
            return JSONResponse({"error": "need at least 2 harnesses"}, status_code=400)
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return {"relationships": weave_relationships(provider, harnesses)}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"weave failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/formalize")
    def genesis_formalize(body: dict):
        """Formalize a RATIFIED character's prose into structure + its relationships (the draft cast
        queue ratify step). Body: { persona, role?, others?:[names], model? }
        → { temperament, want, lie, wound, secret, relationships:[{target, nature, dynamic, stance, note}] }."""
        from .genesis import formalize_harness
        body = body or {}
        blurb = (body.get("persona") or body.get("blurb") or "").strip()
        if not blurb:
            return JSONResponse({"error": "no character text to formalize"}, status_code=400)
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return formalize_harness(provider, blurb, role=body.get("role", ""),
                                     others=body.get("others") or [], world=body.get("world") or "")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"formalize failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/potentials")
    def genesis_potentials(body: dict):
        """Suggest relationship POTENTIALS between two characters (the story seed) — each the hidden
        COMMON CORE + a wanted TRAJECTORY/tone. Body: { a, b, n? } (a/b are character dicts:
        name/persona/want/lie/wound) → { potentials: [{common, trajectory, nature, stance}] }."""
        from .genesis import suggest_potentials
        body = body or {}
        a, b = body.get("a") or {}, body.get("b") or {}
        if not a or not b:
            return JSONResponse({"error": "need two characters"}, status_code=400)
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return {"potentials": suggest_potentials(provider, a, b, n=body.get("n", 3))}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"potentials failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/face")
    async def genesis_face(body: dict):
        """Best-effort FACE PORTRAIT for a draft (keyless) genesis character → a node avatar.
        Body: { name, persona|background, appearance?, model?, image_model? } → { image: dataURI,
        appearance } or { error }. Drafts live in browser state (no key/disk), so nothing is saved;
        the frontend stores the data URI on the harness and clips it into the graph node."""
        body = body or {}
        persona = (body.get("persona") or body.get("background") or "").strip()
        name = (body.get("name") or "").strip()
        appearance = (body.get("appearance") or "").strip()
        if not persona and not appearance:
            return JSONResponse({"error": "need a persona or appearance"}, status_code=400)
        # 1) Booru identity tags (FACE-focused). Use an explicit appearance if given, else infer the
        #    persistent face/identity from the persona (Illustrious wants tags, not prose).
        if not appearance:
            author = ctx.author_provider(body.get("model"))
            if author is None:
                return JSONResponse({"error": "no author model configured"}, status_code=400)
            from .pipeline._helpers import _TAG_RULE
            system = ("You output a short Danbooru tag list describing ONLY a single character's FACE "
                      "and persistent identity for an Illustrious/SDXL anime portrait: sex + honest age, "
                      "hair (colour/length/style), eyes (colour/shape), skin tone, and 1-2 distinguishing "
                      "facial hooks (freckles, a mole, glasses, a scar). NO clothing, NO background, NO "
                      "pose, NO expression. Output ONLY the comma-separated tags, nothing else.\n\n" + _TAG_RULE)
            prompt = f"CHARACTER: {name}\n\n{persona}" if name else persona
            try:
                appearance = (author.generate_text(system=system, prompt=prompt).text or "").strip()
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"appearance failed: {exc}"}, status_code=500)
        appearance = appearance.strip().strip('"').strip()
        if not appearance:
            return JSONResponse({"error": "no appearance tags"}, status_code=500)
        # 2) Render a TIGHT face close-up (the node crop is a small circle — the face must fill it, not
        #    the torso). A SQUARE latent at the model's NATIVE SDXL resolution (1024²) — NOT a small ad-hoc
        #    size, which under-resolves the face; square so the circular crop has no bias. Face-focus tags
        #    keep the head centred.
        from ..server.services.poses import ASPECT_DIMS
        prompt = (appearance + ", solo, portrait, close-up, face focus, looking at viewer, "
                  "detailed face, head shot, simple background")
        provider, model_id = ctx.role_image_provider("base", body.get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)
        try:
            png = await _render(provider, prompt, latent=ASPECT_DIMS["square"])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode(), "appearance": appearance}

    @app.post("/api/stories/genesis/derive")
    def genesis_derive(body: dict):
        """Step 3 — derive candidate stories from the web (premise as output).
        Body: { harnesses:[…], relationships:[…], steer?, model? } → { candidates:[…] }."""
        from .genesis import derive_stories
        body = body or {}
        harnesses = body.get("harnesses") or []
        if not harnesses:
            return JSONResponse({"error": "no harnesses"}, status_code=400)
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            cands = derive_stories(provider, harnesses, body.get("relationships") or [],
                                   steer=body.get("steer", ""))
            return {"candidates": cands}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"derive failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/commit")
    def genesis_commit(body: dict):
        """Step 4 — commit a chosen candidate: name the anchor harnesses into real characters,
        rewrite edge ids → character keys, persist the Story. Body: { candidate, harnesses,
        relationships, name?, type? } → { ok, key }."""
        from . import story_db as _SDB
        from .genesis import name_cast, persona_from_harness

        body = body or {}
        cand = body.get("candidate") or {}
        by_id = {h.get("id"): h for h in (body.get("harnesses") or []) if h.get("id")}
        rels = body.get("relationships") or []
        if not cand or not by_id:
            return JSONResponse({"error": "need candidate + harnesses"}, status_code=400)

        anchors = [a for a in (cand.get("anchors") or []) if a in by_id]
        prot = cand.get("protagonist") if cand.get("protagonist") in by_id else (anchors[0] if anchors else None)
        if prot is None:
            return JSONResponse({"error": "candidate has no valid protagonist"}, status_code=400)
        if prot not in anchors:
            anchors = [prot] + anchors

        # New stories are born as ONE self-contained <skey>.db with characters EMBEDDED. Compute the
        # key FIRST + create the DB so write_npc can embed each anchor into it. See story_db.py.
        ctx.story_dir().mkdir(parents=True, exist_ok=True)
        name = (body.get("name") or cand.get("title") or "Story").strip()
        existing_names = {st.name for st in ctx.base_settings.stories.values()}
        base_name, j = name, 2
        while name in existing_names:
            name, j = f"{base_name} ({j})", j + 1
        skey_base = re.sub(r"[^\w\-]+", "_", name.lower()).strip("_") or "story"
        skey, i = skey_base, 2
        while (ctx.story_dir() / f"{skey}.yaml").is_file() or (ctx.story_dir() / f"{skey}.db").is_file():
            skey, i = f"{skey_base}_{i}", i + 1
        stype = body.get("type") if body.get("type") in ("novel", "vn") else "novel"
        db_path = ctx.story_dir() / f"{skey}.db"
        _SDB.save_story(db_path, {
            "name": name, "type": stype, "premise": cand.get("premise", ""),
            "tone": cand.get("tone", ""), "themes": cand.get("themes") or [],
            "storyboard": {"logline": cand.get("logline", "")},
            "fields": {"source": "genesis", "dramatic_question": cand.get("dramatic_question", "")},
        }, {})

        # Name the anchors (commit is the first time harnesses get names) + embed them into the DB.
        nprov, _systems = ctx.builder_ctx(body, "characters")
        names = name_cast(nprov, [by_id[a] for a in anchors]) if nprov is not None else {}
        id_to_key: dict[str, str] = {}
        for idx, hid in enumerate(anchors):
            h = by_id[hid]
            nm = (h.get("name") or "").strip() or (names.get(hid) or {}).get("name") \
                or h.get("role") or f"Character {idx + 1}"
            id_to_key[hid] = ctx.write_npc({
                "name": nm, "persona": persona_from_harness(h),
                "appearance": (names.get(hid) or {}).get("appearance", ""), "role": h.get("role", ""),
                "want": h.get("want", ""), "lie": h.get("lie", ""),
                "wound": h.get("wound", ""), "secret": h.get("secret", ""),
            }, story_key=skey)                            # embeds into <skey>.db

        cast = [{"character": id_to_key[hid], "primary": (hid == prot)} for hid in anchors]
        out_rels = []
        for i, e in enumerate(rels):
            s, t = id_to_key.get(e.get("source")), id_to_key.get(e.get("target"))
            if not s or not t or s == t:
                continue  # edge touches a harness that didn't make the cast — drop it
            out_rels.append({"id": f"r{i + 1}", "source": s, "target": t,
                             "nature": e.get("nature", ""), "dynamic": e.get("dynamic", ""),
                             "stance": e.get("stance", "neutral"), "note": e.get("note", "")})
        try:
            ctx.update_story_fields(skey, {"cast": cast, "relationships": out_rels})
        except Exception as exc:  # noqa: BLE001
            _SDB.delete_db(db_path)                        # rollback the half-created story
            return JSONResponse({"error": f"could not save story: {exc}"}, status_code=400)
        return {"ok": True, "key": skey}

    @app.post("/api/stories/{key}/genesis/add")
    def genesis_add(key: str, body: dict):
        """ADD generated characters to an EXISTING story (the in-Structure generate tools): name the
        confirmed harnesses, create characters, append to the cast + relationships. Same naming/
        write_npc path as commit, but onto a live story. Body: { harnesses, relationships? } → { ok }."""
        from .genesis import name_cast, persona_from_harness

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        by_id = {h.get("id"): h for h in (body.get("harnesses") or []) if h.get("id")}
        if not by_id:
            return JSONResponse({"error": "no harnesses"}, status_code=400)

        nprov, _systems = ctx.builder_ctx(body, "characters")
        names = name_cast(nprov, list(by_id.values())) if nprov is not None else {}
        id_to_key: dict[str, str] = {}
        for idx, (hid, h) in enumerate(by_id.items()):
            nm = (h.get("name") or "").strip() or (names.get(hid) or {}).get("name") \
                or h.get("role") or f"Character {idx + 1}"
            id_to_key[hid] = ctx.write_npc({
                "name": nm, "persona": persona_from_harness(h),
                "appearance": (names.get(hid) or {}).get("appearance", ""), "role": h.get("role", ""),
                "want": h.get("want", ""), "lie": h.get("lie", ""),
                "wound": h.get("wound", ""), "secret": h.get("secret", ""),
            }, story_key=key)

        sd = st.model_dump()
        cast = (sd.get("cast") or []) + [{"character": k, "primary": False} for k in id_to_key.values()]
        rels = list(sd.get("relationships") or [])
        base = len(rels)
        for i, e in enumerate(body.get("relationships") or []):
            s, t = id_to_key.get(e.get("source")), id_to_key.get(e.get("target"))
            if not s or not t or s == t:
                continue  # only edges among the newly-added characters (drafts use harness ids)
            rels.append({"id": f"r{base + i + 1}", "source": s, "target": t,
                         "nature": e.get("nature", ""), "dynamic": e.get("dynamic", ""),
                         "stance": e.get("stance", "neutral"), "note": e.get("note", "")})
        try:
            ctx.update_story_fields(key, {"cast": cast, "relationships": rels})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not add to cast: {exc}"}, status_code=400)
        return {"ok": True, "added": list(id_to_key.values())}

    def _chapter_ctx(sd: dict, idx: int) -> str:
        """Running context for drafting/consolidating a novel chapter: premise/tone/cast + the prior
        chapters' recaps (what's carried forward). This is the serial memory — never the whole book."""
        chapters = sd.get("chapters") or []
        cast = ", ".join(getattr(ctx.base_settings.characters.get(m.get("character")), "name", m.get("character"))
                         for m in (sd.get("cast") or []) if m.get("character")) or "(unspecified)"
        prior = "\n".join(
            f"Ch.{i + 1} {chapters[i].get('title', '') or ''}: {chapters[i].get('recap', '') or '(no recap)'}"
            for i in range(idx)) or "(this is the first chapter)"
        return (f"STORY: {sd.get('name')}\nPREMISE: {sd.get('premise', '')}\nTONE: {sd.get('tone', '')}\n"
                f"CAST: {cast}\n\nWHAT HAS HAPPENED SO FAR:\n{prior}")

    def _find_chapter(key: str, cid: str):
        """(story_dict, chapters_list, index) for a novel chapter, or (None, None, -1)."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return None, None, -1
        sd = st.model_dump()
        chapters = sd.get("chapters") or []
        idx = next((i for i, c in enumerate(chapters) if c.get("id") == cid), -1)
        return sd, chapters, idx

    @app.post("/api/stories/{key}/chapter/{cid}/draft")
    def chapter_draft(key: str, cid: str, body: dict):
        """Draft ONE novel chapter's prose (Narrative voice) from its harness + the running context.
        Serial-gated: the previous chapter must be `drafted`, so the book is never generated at once."""
        sd, chapters, idx = _find_chapter(key, cid)
        if idx < 0:
            return JSONResponse({"error": "no such chapter"}, status_code=404)
        if idx > 0 and chapters[idx - 1].get("status") != "drafted":
            return JSONResponse({"error": f"draft chapter {idx} first — chapters generate in order"},
                                status_code=409)
        provider, _systems = ctx.builder_ctx(body or {}, "chapter")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)
        ch = chapters[idx]
        beats = "; ".join(ch.get("beats") or []) or (ch.get("purpose") or "")
        system = ("You are the NARRATIVE voice writing a novel chapter by chapter. Render THIS chapter "
                  "as prose — vivid, in-scene, consistent with what came before. Don't summarize or skip "
                  "ahead, don't write headings or meta; just the chapter's prose.")
        prompt = (f"{_chapter_ctx(sd, idx)}\n\nCHAPTER {idx + 1} — {ch.get('title', '')}\n"
                  f"POV: {ch.get('pov', '')}\nSETTING: {ch.get('setting', '')}\n"
                  f"PURPOSE: {ch.get('purpose', '')}\nBEATS: {beats}\n\nWrite chapter {idx + 1} now.")
        try:
            res = provider.generate_text(system=system, prompt=prompt)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"draft failed: {exc}"}, status_code=500)
        draft = (res.text or "").strip()
        if not draft:
            return JSONResponse({"error": "model returned nothing"}, status_code=500)
        ch["draft"] = draft
        ch["status"] = "drafted"
        ctx.update_story_fields(key, {"chapters": chapters})
        return {"ok": True, "id": cid, "status": "drafted", "words": len(draft.split()), "draft": draft}

    @app.post("/api/stories/{key}/chapter/{cid}/consolidate")
    def chapter_consolidate(key: str, cid: str, body: dict):
        """Storymaster: distill a drafted chapter into a tight carry-forward `recap` (what changed,
        what now matters) — the input the NEXT chapter draws on."""
        sd, chapters, idx = _find_chapter(key, cid)
        if idx < 0:
            return JSONResponse({"error": "no such chapter"}, status_code=404)
        ch = chapters[idx]
        if ch.get("status") != "drafted" or not (ch.get("draft") or "").strip():
            return JSONResponse({"error": "draft the chapter before consolidating"}, status_code=409)
        provider, _systems = ctx.builder_ctx(body or {}, "consolidate")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)
        system = ("You are the Storymaster. Distill this chapter into a tight RECAP that the next "
                  "chapter will rely on: what changed, for whom, and what now matters going forward. "
                  "2-4 concrete sentences. No preamble.")
        try:
            res = provider.generate_text(system=system, prompt=(ch.get("draft") or "")[:8000])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"consolidate failed: {exc}"}, status_code=500)
        recap = (res.text or "").strip()
        if not recap:
            return JSONResponse({"error": "model returned nothing"}, status_code=500)
        ch["recap"] = recap
        ctx.update_story_fields(key, {"chapters": chapters})
        return {"ok": True, "id": cid, "recap": recap}

    import operator as _operator
    _COND_OPS = {">=": _operator.ge, "<=": _operator.le, "==": _operator.eq,
                 "!=": _operator.ne, ">": _operator.gt, "<": _operator.lt}
    _COND_RE = re.compile(r"^\s*(\w+)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

    def _cond_met(cond: str, state: dict) -> bool:
        """Cheap emergent-condition check — '<feature> <op> <number>' against the play state. This is
        the DETECT step; it's free (no model), so the runtime can watch every turn."""
        m = _COND_RE.match(cond or "")
        if not m:
            return False
        var, op, num = m.group(1), m.group(2), float(m.group(3))
        try:
            return _COND_OPS[op](float(state.get(var, 0) or 0), num)
        except Exception:  # noqa: BLE001
            return False

    @app.post("/api/stories/{key}/scene/{sid}/evaluate")
    def scene_evaluate(key: str, sid: str, body: dict):
        """The detect→ask→act decision for an AI-played VN scene (the same shape as the sleep/death
        detectors, generalized to scene-switching). DETECT which divergence triggers are armed: a
        `choice` trigger fires on the player's pick (deterministic); `emergent` triggers fire when a
        tracked feature crosses its threshold — and then we ASK the tool whether it's dramatically
        time to switch. Body: { state:{feature:num}, choice?:str, last?:str }.
        Returns { transition, branch, why, armed:[conditions] }."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        scene = next((s for s in (st.model_dump().get("scenes") or []) if s.get("id") == sid), None)
        if scene is None:
            return JSONResponse({"error": "no such scene"}, status_code=404)
        body = body or {}
        state = body.get("state") or {}
        choice = (body.get("choice") or "").strip().lower()
        last = (body.get("last") or "").strip()
        triggers = scene.get("triggers") or []

        # A player CHOICE fires deterministically — they decided.
        if choice:
            hit = next((t for t in triggers if t.get("kind") == "choice" and t.get("condition")
                        and (choice in t["condition"].strip().lower() or t["condition"].strip().lower() in choice)), None)
            if hit:
                return {"transition": True, "branch": hit.get("branch"), "why": "player choice",
                        "armed": [hit.get("condition")]}

        # EMERGENT conditions: detect (free), then ASK the tool whether it's time.
        armed = [t for t in triggers if t.get("kind") == "emergent" and _cond_met(t.get("condition"), state)]
        if not armed:
            return {"transition": False, "branch": None, "armed": []}
        provider, _systems = ctx.builder_ctx(body, "director")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)
        opts = "\n".join(f'- branch "{t.get("branch")}" (triggered by {t.get("condition")}): {t.get("intent")}'
                         for t in armed)
        schema = {"type": "object", "additionalProperties": False, "required": ["transition", "branch", "why"],
                  "properties": {"transition": {"type": "boolean"}, "branch": {"type": "string"},
                                 "why": {"type": "string"}}}
        system = ("You are the Dungeon Master deciding whether an AI-played scene should TRANSITION now. "
                  "One or more divergence conditions have triggered. Switch only if it is dramatically "
                  "earned given the scene's goal and what just happened — otherwise stay in the scene "
                  "and let it breathe. Pick from the triggered branches only.")
        prompt = (f"SCENE goal: {scene.get('goal', '')}\nTONE: {scene.get('tone', '')}\n"
                  f"WHAT JUST HAPPENED: {last or '(unspecified)'}\n\nTRIGGERED BRANCHES:\n{opts}\n\n"
                  "Decide whether to transition now, and to which branch.")
        try:
            out = (provider.generate_text(system=system, prompt=prompt, emits=schema).data) or {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"evaluate failed: {exc}"}, status_code=500)
        armed_branches = {t.get("branch") for t in armed}
        branch = out.get("branch") if out.get("branch") in armed_branches else armed[0].get("branch")
        go = bool(out.get("transition"))
        return {"transition": go, "branch": branch if go else None,
                "why": str(out.get("why") or "").strip(), "armed": [t.get("condition") for t in armed]}

    @app.post("/api/stories/extract-scenes")
    def story_extract_scenes(body: dict):
        """Stage 2 — extract neutral locations (pure backgrounds) from the beats."""
        from .pipeline import extract_locations

        provider, systems = ctx.builder_ctx(body or {}, "locations")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        body = body or {}
        board = body.get("board") or {}
        premise = (body.get("premise") or "").strip()
        try:
            return extract_locations(provider, board=board, premise=premise, systems=systems)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/stories/extract-characters")
    def story_extract_characters(body: dict):
        """Stage 3 — build the cast in TWO steps: (1) distill the source card into the main
        character, then (2) flesh the supporting NPCs from the beats in that SAME structure.
        Returns { cast: [...] } — ONE uniform list with the protagonist first, flagged `primary`
        (no separate "main character card")."""
        from .pipeline import extract_characters, extract_protagonist

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, systems = ctx.builder_ctx(body, "characters")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        extras = ctx.card_extras(ch, body["character"])
        from .pipeline import grounding as _G
        _craft = _G.craft_notes(ctx.root, (ch.system or ch.name or "")[:600], k=6, section="character")
        try:
            base = extract_protagonist(provider, name=ch.name, persona=ch.system or "",
                                       extras=extras, systems=systems, craft=_craft)
            out = extract_characters(provider, name=base["name"], persona=base["persona"],
                                     board=body.get("board") or {}, extras=extras,
                                     systems=systems,
                                     reference_card=base["persona"], craft=_craft)
            npcs = out.get("npcs", [])
            # ONE uniform cast list — protagonist first, flagged `primary`. Compose the SUPERIOR ✨
            # image description (_compose_base_prompt: FEATURES_SCHEMA → Danbooru co-occurrence) for
            # EVERY member IN PARALLEL, stored on `base_prompt` (what the renderer uses), so the
            # wizard shows/saves the rich description, not the basic tags. The basic `appearance` is
            # kept as the seed fed into the composer.
            cast = [{**base, "primary": True}] + [{**n, "primary": False} for n in npcs]
            from concurrent.futures import ThreadPoolExecutor
            from .pipeline import (compose_base_prompt as _compose_base_prompt,
                                   compose_expressions as _compose_expressions,
                                   compose_affect_range as _compose_affect_range)
            _bp_cfg = ctx.load_story_builder()
            _bp_prov = ctx.stage_provider("base_image")
            _emo_prov = ctx.stage_provider("emotion")
            _bp_sys = (_bp_cfg.get("systems") or {})

            def _enrich(item):
                idx, p = item
                persona = p.get("persona", "")
                base_prompt, height_cm = "", None
                expressions: dict = {}
                affect: dict = {}
                try:
                    r = _compose_base_prompt(_bp_prov, p.get("name", ""), persona,
                                             p.get("appearance", ""), p.get("role", ""),
                                             systems=_bp_sys)
                    if isinstance(r, dict):
                        base_prompt = r.get("prompt", "")
                        height_cm = (r.get("features") or {}).get("height_cm")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    expressions = _compose_expressions(_emo_prov or _bp_prov, persona)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    affect = _compose_affect_range(_emo_prov or _bp_prov, persona,
                                                   systems=_bp_sys)
                except Exception:  # noqa: BLE001
                    pass
                return (idx, base_prompt, height_cm, expressions, affect)

            with ThreadPoolExecutor(max_workers=min(len(cast), 6)) as ex:
                enriched = {idx: (bp, h, exprs, aff)
                            for idx, bp, h, exprs, aff in ex.map(_enrich, list(enumerate(cast)))}
            for idx, member in enumerate(cast):
                bp, h, exprs, aff = enriched.get(idx, ("", None, {}, {}))
                if bp:
                    member["base_prompt"] = bp
                if h:
                    member["height_cm"] = h
                if exprs:
                    member["expressions"] = exprs
                if isinstance(aff, dict) and aff.get("range"):
                    member["affect"] = aff
            return {"cast": cast}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/stories/workshop")
    async def story_workshop(body: dict):
        """Collaborative story-planning chat. Streams a conversational response from a
        story-architect persona that knows the character and helps the user develop a premise
        before committing to a full storyboard generation.

        Body: { character: str, messages: [{role: str, content: str}], premise?: str }
        Streams `delta` text events + a final `done` event (same SSE pattern as storyboard).
        """
        import asyncio
        import threading

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)

        provider, systems = ctx.builder_ctx(body, "workshop")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        # Build character context for the workshop system prompt.
        extras = ctx.card_extras(ch, body["character"])
        from .pipeline._helpers import _card_context
        card = _card_context(ch.name, ch.system, extras)

        system = (
            f"You are a story consultant — a developmental editor working with a writer to find "
            f"the story that wants to be told from this character. You think the way working "
            f"modern craft writers think (Will Storr, Lisa Cron, K.M. Weiland, John Truby, George "
            f"Saunders, Donald Maass, Charles Baxter, Brandon Sanderson, Shawn Coyne, Jane Alison, "
            f"Matthew Salesses), not in the vocabulary of academic literary theory. You will be "
            f"given CRAFT NOTES below, drawn from these writers and selected for the current "
            f"exchange — lean on them, but speak like an editor in the room, not a lecturer.\n\n"

            f"You know {ch.name} from the character card below. Read it the way a developmental "
            f"editor reads: find the flawed theory of control the character lives by, the misbelief "
            f"and the concrete past moment that planted it, the gap between what they want and what "
            f"they actually need, and the contradiction that makes them worth reading about. The "
            f"plot is just the machine that tests all of this.\n\n"

            f"The writer wants stories with real interiority, tension, melancholy, and earned "
            f"growth — characters who circle the truth, flinch, retreat, and change only at cost. "
            f"Aim for that depth; resist tidy or shallow premises.\n\n"

            f"HOW YOU WORK:\n"
            f"- This conversation is about CHARACTER DEVELOPMENT, not plot. You map how this person "
            f"CHANGES — the wound, the misbelief/lie they live by, the want/need gap, and the "
            f"sequence of internal inflections that carry them (or fail to carry them) from the lie "
            f"toward the truth. Events exist only as the LEVERS that force those inflections; never "
            f"discuss plot for its own sake.\n"
            f"- DIAGNOSTIC first. Open with what you SEE — the wound, the misbelief, the shape of "
            f"change latent in them — then invite the writer to react. Don't open with questions.\n"
            f"- OPINIONATED. If an idea dodges the character's real developmental potential, say so "
            f"and propose the harder, truer arc of change. Think in growth cycles: encounter the "
            f"truth, flinch, retreat into the lie, pay a cost, circle back — what finally breaks "
            f"the pattern?\n"
            f"- CONCRETE. Ground every craft principle in THIS character. When you name an event, "
            f"name the internal shift it is there to force.\n"
            f"- Conversational but substantive: 2-4 paragraphs of prose. No bullet lists.\n"
            f"- When the wound, misbelief, want/need gap, emotional register, and the sequence of "
            f"inflections are clear, say so and invite the writer to generate the draft.\n\n"

            f"A DEVELOPMENT GRAPH of the character's arc of change is shown beside the chat and "
            f"kept in sync automatically by the system — you do NOT write it out yourself. Just "
            f"keep your prose anchored to that arc: for each beat you discuss, name the internal "
            f"inflection it forces and the event that serves as its lever. Treat any edits the "
            f"writer has made to the working graph (shown below) as authoritative and build on "
            f"them.\n\n"

            f"CHARACTER CARD:\n{card}"
        )

        # Build the conversation transcript as a single prompt string.
        messages = list(body.get("messages") or [])
        premise = (body.get("premise") or "").strip()
        if not messages and premise:
            messages = [{"role": "user", "content": f"Here's the premise I have in mind: {premise}\n\nGive me your read of whether this plays to {ch.name}'s real dramatic potential, or whether there's a truer story here."}]
        elif not messages:
            messages = [{"role": "user", "content": f"Read {ch.name}'s character card and give me your opening read. What's the wound? What misbelief are they living by? What kind of change — or refusal to change — does their nature pull toward?"}]

        # Retrieve craft principles + world lore (libSQL/Turso store, FTS5 bm25).
        from ..server.services import lorebook_store as _LS
        from ..server.services.lorebook import format_lore_block
        query_text = " ".join(str(m.get("content", "")) for m in messages)

        # Craft lorebook (_craft): modern storytelling theory the consultant
        # reasons with. Surface what the exchange calls for; always keep a
        # foundational floor so the AI is never without a craft lens.
        craft_hits = _LS.retrieve(ctx.root, query_text, ["_craft"], top_k=7)
        if not craft_hits:
            craft_hits = _LS.top_by_priority(ctx.root, "_craft", 4)
        if craft_hits:
            system = system + "\n\n" + format_lore_block(
                craft_hits,
                header=(
                    "CRAFT NOTES — modern storytelling principles relevant to this "
                    "exchange. Reason with these and name the thinker when it sharpens "
                    "a point, but apply them to THIS character; never lecture:"
                ),
            )

        # World lore (setting, history, established facts). The console may assign
        # specific scopes via body["lorebooks"]; default to character + global.
        world_scopes = body.get("lorebooks")
        if not isinstance(world_scopes, list) or not world_scopes:
            world_scopes = [body["character"], "_global"]
        world_scopes = [re.sub(r"[^\w\-]+", "_", str(s)) for s in world_scopes]
        world_scopes = [s for s in world_scopes if s and s != "_craft"]
        world_hits = _LS.retrieve(ctx.root, query_text, world_scopes, top_k=5,
                                  allow_nsfw=ctx.allow_nsfw()) if world_scopes else []
        # Function-book entries are functions, not world facts — never inject their specs as lore.
        from . import graph_ops as _GO
        world_hits = [e for e in world_hits if not _GO.is_function_entry(e)]
        if world_hits:
            system = system + "\n\n" + format_lore_block(world_hits)

        retrieved_lore = craft_hits + world_hits

        # The writer may have hand-edited the working spine in the graph pane — feed
        # it back so the model builds on their changes rather than its own last draft.
        working_spine = body.get("spine") or body.get("graph")
        if isinstance(working_spine, dict) and working_spine:
            from .graph_pipeline import spine_prose as _spine_prose
            system = system + (
                "\n\nCURRENT WORKING STORY GRAPH (the writer may have edited this in the graph "
                "pane; treat their edits as authoritative and continue from them):\n"
                + _spine_prose(working_spine)
            )

        # Serialize the conversation history as a readable transcript for the prompt.
        transcript_parts = []
        for msg in messages:
            role = (msg.get("role") or "user").strip()
            content = (msg.get("content") or "").strip()
            label = "User" if role == "user" else "Assistant"
            if content:
                transcript_parts.append(f"{label}: {content}")
        # The model sees the full history and must reply to the last user turn.
        prompt = "\n\n".join(transcript_parts) if transcript_parts else "User: Help me develop a story for this character."

        # (Stage/action tools — storyboard, generate_image — are no longer requested via a text
        # sentinel here. They ride the SAME native tool-calling pass as graph tools: the console's
        # graph-ops call offers every tool and the model calls them with params. See graph_ops.py.)

        # Context-budget breakdown for the console's usage dial (estimate ~4 chars/token).
        from ..server.services.lorebook import estimate_tokens
        craft_text = format_lore_block(craft_hits, header="x") if craft_hits else ""
        world_text = format_lore_block(world_hits) if world_hits else ""
        craft_tok = estimate_tokens(craft_text)
        world_tok = estimate_tokens(world_text)
        hist_tok = estimate_tokens(prompt)
        base_tok = max(0, estimate_tokens(system) - craft_tok - world_tok)
        usage = {
            "type": "usage",
            "parts": {"base": base_tok, "craft": craft_tok, "world": world_tok, "history": hist_tok},
            "tokens": base_tok + craft_tok + world_tok + hist_tok,
            "window": 64000,  # DeepSeek V3 context window
        }

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()

        # Drive the turn graph (consult → extract). consult streams prose via on_delta;
        # extract emits the updated development graph via on_event (a `spine` event).
        deps = StoryDeps(
            provider=provider,
            on_delta=lambda t: loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": t}),
            on_event=lambda e: loop.call_soon_threadsafe(q.put_nowait, e),
            cancel=cancel_evt.is_set,
        )
        state = StoryState(
            card=card, system=system, prompt=prompt,
            working_graph=working_spine if isinstance(working_spine, dict) else None,
        )

        async def run():
            try:
                await run_turn(state, deps)
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(q.put_nowait, None)

        asyncio.create_task(run())

        async def events():
            yield f"data: {json.dumps(usage)}\n\n"
            try:
                while True:
                    ev = await q.get()
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                cancel_evt.set()
            lore_meta = [{"id": e.id, "title": e.title, "facet": e.facet} for e in retrieved_lore]
            yield f'data: {json.dumps({"type": "lore", "entries": lore_meta})}\n\n'
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/agent/modes")
    def agent_modes():
        """The selectable agent MODES — defined in configs/story_agent.json. The chat can force one
        explicitly (reliable) instead of relying on keyword trigger detection."""
        from . import agent_config as _AC
        return {"modes": _AC.modes_list(ctx.root)}

    @app.post("/api/stories/graph-ops")
    def story_graph_ops(body: dict):
        """Data-driven story chat agent. Function-book TOOLS + a JSON-config persona/grounding
        (loom/stories/agent.py + configs/story_agent.json). Thin wrapper — the brain lives in agent.py."""
        from . import agent as _AG
        out = _AG.run_turn(ctx, body or {})
        st = out.pop("_status", None)
        return JSONResponse(out, status_code=st) if st else out

    @app.get("/api/stages")
    def list_stage_tools():
        """The catalog of callable STAGE TOOLS — what an agent can trigger in a story surface
        (used by the Scripts panel + the post-history tool protocol)."""
        from . import stage_tools as ST
        return {"stages": ST.catalog()}

    @app.get("/api/tools")
    def list_tools():
        """Catalog of model-callable TOOLS for the Library ▸ Tools tab. Two kinds, both code
        (read-only): GRAPH scripts (scripts.py) and pipeline STAGE tools (stage_tools.py). Each
        tool's `agents` is the chat MODE(s) that offer it (from configs/story_agent.json — the real
        chat menu), or 'Pipeline' if only a function book wires it. `used_by` lists the function
        books that reference it (the pipeline wiring)."""
        import inspect

        from ..server.services import lorebook_store as LS
        from . import agent_config as AC
        from . import graph_ops as GO
        from . import scripts as S
        from . import stage_tools as ST

        def _src(fn) -> str:
            """The tool's actual implementation source — the truest 'how it works'."""
            try:
                return inspect.getsource(fn)
            except (OSError, TypeError):
                return ""

        # Which function books reference each tool — the PIPELINE wiring (a stage step attaches a
        # function book). Still real for the pipeline; the chat agent no longer routes tools this way.
        used: dict[str, list[str]] = {}
        for b in LS.list_books(ctx.root):
            for e in LS.load_lorebook(ctx.root, b["id"]):
                spec = GO._parse_spec(getattr(e, "content", "") or "")
                fn = (spec or {}).get("fn")
                if fn:
                    used.setdefault(str(fn), [])
                    if b["id"] not in used[str(fn)]:
                        used[str(fn)].append(b["id"])

        # The chat's REAL menu: which story_agent.json mode(s) offer each tool (its `functions` list).
        # This is the source of truth now — grouping by it shows what the agent can actually call.
        modes = (AC.load_config(ctx.root).get("modes") or {})
        mode_fns = {mid: set(m.get("functions") or []) for mid, m in modes.items()}
        mode_label = {mid: (m.get("label") or mid) for mid, m in modes.items()}

        def _agents(fn: str) -> list[dict]:
            """Where a tool is offered: the chat MODES that list it; else 'Pipeline' if a function
            book wires it (pipeline-only, not chat-callable); else nothing (Unbound)."""
            out = [{"id": mid, "name": mode_label[mid], "group": "Modes"}
                   for mid, fns in mode_fns.items() if fn in fns]
            if not out and used.get(fn):
                out.append({"id": "_pipeline", "name": "Pipeline", "group": "Pipeline"})
            return out

        def _pdisplay(params: dict) -> dict:
            # Flatten the rich param specs to {name: description} for the catalog UI; enum/type
            # live in the model-facing schema (tools_spec), not this human view.
            out = {}
            for k, v in (params or {}).items():
                desc = v.get("desc", "") if isinstance(v, dict) else str(v)
                if isinstance(v, dict) and v.get("enum"):
                    desc = (desc + " — one of: " + ", ".join(map(str, v["enum"]))).strip(" —")
                out[k] = desc
            return out

        graph = [{"fn": sd.name, "kind": "graph", "describe": sd.describe,
                  "params": _pdisplay(sd.params),
                  "keywords": sd.keywords, "writes": sd.writes, "used_by": used.get(sd.name, []),
                  "agents": _agents(sd.name), "source": _src(sd.impl)}
                 for sd in S.REGISTRY.values()]
        stage = [{**t, "used_by": used.get(t["fn"], []), "agents": _agents(t["fn"]),
                  "params": _pdisplay(ST.TOOLS[t["fn"]].params) if t["fn"] in ST.TOOLS else {},
                  "source": _src(ST.TOOLS[t["fn"]].run) if t["fn"] in ST.TOOLS else ""}
                 for t in ST.catalog()]
        return {"graph": graph, "stage": stage}

    @app.post("/api/tools/{fn}")
    def edit_tool(fn: str, body: dict):
        """Validate — and optionally save — an edited tool's source. Tools are code; saving
        splices the new function text into its .py file after an ast.parse check (snippet AND
        whole-file), then a backend restart applies it to the running registry.
        body: { source: str, validate_only?: bool }."""
        import ast
        import inspect
        from pathlib import Path as _Path

        from . import scripts as S
        from . import stage_tools as ST

        body = body or {}
        source = body.get("source") or ""
        sd = S.REGISTRY.get(fn)
        target = sd.impl if sd else (ST.TOOLS[fn].run if fn in ST.TOOLS else None)
        if target is None:
            return JSONResponse({"error": f"no such tool: {fn!r}"}, status_code=404)

        # 1) the edited snippet must parse on its own
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return JSONResponse({"error": exc.msg, "line": exc.lineno}, status_code=400)
        if body.get("validate_only"):
            return {"ok": True}

        # 2) splice into the file and re-validate the WHOLE module before writing
        try:
            path = inspect.getsourcefile(target)
            old = inspect.getsource(target)
        except (OSError, TypeError):
            return JSONResponse({"error": "cannot locate tool source"}, status_code=500)
        text = _Path(path).read_text(encoding="utf-8")
        if old not in text:
            return JSONResponse({"error": "source drifted on disk — reload and retry"}, status_code=409)
        new_text = text.replace(old, source if source.endswith("\n") else source + "\n", 1)
        try:
            ast.parse(new_text)
        except SyntaxError as exc:
            return JSONResponse({"error": f"file would not parse: {exc.msg}", "line": exc.lineno},
                                status_code=400)
        _Path(path).write_text(new_text, encoding="utf-8")
        return {"ok": True, "note": "saved — restart the backend to apply"}

    @app.post("/api/stories/run-stage")
    def story_run_stage(body: dict):
        """Execute a STAGE TOOL by name and return its structured artifact. This is the
        execution path an agent's tool-call triggers in the workshop/play surfaces — the
        runner reuses the same pipeline units the dedicated stage endpoints use.
        Body: { stage: str, character: str, premise?: str, spine?: dict }."""
        from . import stage_tools as ST
        body = body or {}
        stage = (body.get("stage") or "").strip()
        if ST.get(stage) is None:
            return JSONResponse({"error": f"unknown stage tool: {stage!r}",
                                 "available": [t["fn"] for t in ST.catalog()]}, status_code=400)
        try:
            result = ST.run_stage(ctx, stage, body)
        except ValueError as exc:           # bad/missing character
            return JSONResponse({"error": str(exc)}, status_code=404)
        except Exception as exc:            # noqa: BLE001 — surface stage failures cleanly
            return JSONResponse({"error": f"stage '{stage}' failed: {exc}"}, status_code=500)
        return {"ok": True, "stage": stage, **result}

    # ── Story session checkpoints (server-side) ───────────────────────────────

    @app.get("/api/stories/session/{sid}")
    def get_story_session(sid: str):
        from ..server.services.story_sessions import load_session
        return load_session(ctx.root, sid) or {}

    @app.put("/api/stories/session/{sid}")
    def put_story_session(sid: str, body: dict):
        from ..server.services.story_sessions import save_session
        return {"ok": True, "session": save_session(ctx.root, sid, body or {})}

    @app.delete("/api/stories/session/{sid}")
    def delete_story_session(sid: str):
        from ..server.services.story_sessions import delete_session
        delete_session(ctx.root, sid)
        return {"ok": True}

    # ── World-state engine (per-thread mutable state) ──────────────────────────

    @app.get("/api/stories/{key}/state")
    def get_world_state(key: str, sid: str | None = None):
        from ..server.services.story_sessions import load_session
        from ..server.services import lorebook_store as _LS
        from . import state_engine as _SE
        from . import state_doc as _SD
        sid = sid or f"play-{key}"
        doc = _SD.normalize((load_session(ctx.root, sid) or {}).get("state"))
        ws = _SE.world_of(doc)
        # The full leveled view for the State viewer: graph/world/sim sizes from the doc,
        # plus the `facts` level (its own libSQL scope) counted from the store.
        levels = _SD.level_summary(doc)
        try:
            facts_n = len(_LS.load_lorebook(ctx.root, _SE.facts_scope(sid)) or [])
            if facts_n:
                levels["facts"] = {"size": facts_n}
        except Exception:  # noqa: BLE001
            pass
        return {"sid": sid, "state": ws, "revision": doc.get("revision", 0), "levels": levels}

    @app.put("/api/stories/{key}/state")
    def put_world_state(key: str, body: dict):
        """Manual override of the world level (the state panel's edits). Body:
        { sid?, state } — replaces the stored world level with the normalized payload."""
        from ..server.services.story_sessions import load_session, save_session
        from . import state_engine as _SE
        body = body or {}
        sid = body.get("sid") or f"play-{key}"
        sess = load_session(ctx.root, sid) or {}
        ws = _SE.normalize(body.get("state") or {})
        save_session(ctx.root, sid, {**sess, "state": _SE.with_world(sess.get("state"), ws)})
        return {"ok": True, "state": ws}

    @app.post("/api/stories/{key}/state/reset")
    def reset_world_state(key: str, body: dict | None = None):
        from ..server.services.story_sessions import load_session, save_session
        from . import state_engine as _SE
        sid = (body or {}).get("sid") or f"play-{key}"
        sess = load_session(ctx.root, sid) or {}
        save_session(ctx.root, sid, {**sess, "state": _SE.with_world(sess.get("state"), _SE.empty_state())})
        return {"ok": True, "state": _SE.empty_state()}

    # ── Text-model roles (narrator / scribe / refusal fallback) ────────────────

    @app.get("/api/text-roles")
    def get_text_roles():
        from ..server.services import config_files as _cf
        roles = _cf.load_text_roles(ctx.root)
        # Offer the selectable text models/connections so the UI can build pickers.
        items = [{"value": "", "label": "Active text connection"}]
        items += [{"value": k, "label": k} for k, m in ctx.effective_settings().models.items()
                  if m.kind == "text"]
        return {"roles": roles, "models": items}

    @app.put("/api/text-roles")
    def put_text_roles(body: dict):
        from ..server.services import config_files as _cf
        return {"ok": True, "roles": _cf.save_text_roles(ctx.root, body or {})}

    # ── Lorebook CRUD ─────────────────────────────────────────────────────────

    @app.get("/api/lorebook/{scope}")
    def get_lorebook(scope: str):
        from ..server.services import lorebook_store as _LS
        safe = re.sub(r"[^\w\-]+", "_", scope)
        return {"entries": [e.model_dump() for e in _LS.load_lorebook(ctx.root, safe)]}

    @app.put("/api/lorebook/{scope}")
    def put_lore_entry(scope: str, body: dict):
        from ..server.services import lorebook_store as _LS
        from ..config.schema import LoreEntry
        import uuid
        safe = re.sub(r"[^\w\-]+", "_", scope)
        entry_data = dict(body or {})
        if not entry_data.get("id"):
            entry_data["id"] = str(uuid.uuid4())[:8]
        new_entry = LoreEntry(**entry_data)
        _LS.upsert_entry(ctx.root, safe, new_entry)   # dynamic insert-or-update
        return {"ok": True, "entry": new_entry.model_dump()}

    @app.delete("/api/lorebook/{scope}/{entry_id}")
    def delete_lore_entry(scope: str, entry_id: str):
        from ..server.services import lorebook_store as _LS
        safe = re.sub(r"[^\w\-]+", "_", scope)
        _LS.delete_entry(ctx.root, safe, entry_id)
        return {"ok": True}

    @app.post("/api/lorebook/import/{scope}")
    def import_lorebook(scope: str, body: dict):
        """Import a SillyTavern world-info export into a scope. Body: {entries:[...]} or a
        raw array. Safety filters reject prompt-injection entries and strip override lines."""
        from ..server.services.lorebook_import import import_sillytavern
        safe = re.sub(r"[^\w\-]+", "_", scope)
        entries = (body or {}).get("entries") if isinstance(body, dict) else body
        if not isinstance(entries, list):
            return JSONResponse({"error": "expected a list of entries (or {entries:[...]})"}, status_code=400)
        facet = (body or {}).get("facet", "") if isinstance(body, dict) else ""
        return import_sillytavern(ctx.root, safe, entries, default_facet=facet)

    ARC_SUGGESTION_SCHEMA = {
        "type": "object", "additionalProperties": False, "required": ["arcs"],
        "properties": {
            "arcs": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "name", "mini_ending", "dramatic_function", "cast"],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "mini_ending": {"type": "string"},
                        "dramatic_function": {"type": "string"},
                        "cast": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                    }
                }
            }
        }
    }

    @app.post("/api/stories/workshop/arcs")
    async def story_workshop_arcs(body: dict):
        """Suggest arc structure from the workshop conversation + intended ending.
        NOT streaming — fast structured output. Returns {arcs: [...]}.

        Body: { character: str, messages: [{role, content}], intended_ending: str, story_cast: [str] }
        """
        from fastapi.concurrency import run_in_threadpool

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)

        provider, systems = ctx.builder_ctx(body, "storyboard")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        intended_ending = (body.get("intended_ending") or "").strip()
        story_cast = list(body.get("story_cast") or [])

        # Summarize the workshop conversation as a brief premise transcript.
        messages = list(body.get("messages") or [])
        transcript_parts = []
        for msg in messages:
            role = (msg.get("role") or "user").strip()
            content = (msg.get("content") or "").strip()
            label = "User" if role == "user" else "Assistant"
            if content:
                transcript_parts.append(f"{label}: {content}")
        transcript = "\n\n".join(transcript_parts) if transcript_parts else "(no premise conversation yet)"

        cast_list = ", ".join(story_cast) if story_cast else "(none specified)"

        system = (
            "You are a story architect designing the ARC STRUCTURE for a book.\n\n"
            "You have been given:\n"
            "- The story premise (from the workshop conversation)\n"
            "- The intended ending (what the book closes on)\n"
            "- The available cast of characters\n\n"
            "Design 2-4 arcs that form the dramatic spine from premise to ending. Each arc is a "
            "complete mini-story — it has a beginning, a rising tension, and a turn that leaves "
            "the protagonist changed and sets up the next arc.\n\n"
            "For each arc provide:\n"
            "- `id`: lowercase-hyphen slug (arc-1, arc-2, etc.)\n"
            "- `name`: a short evocative title (e.g. \"First Meeting\", \"The Fracture\", \"Coming Home\")\n"
            "- `mini_ending`: one sentence — what this arc leaves the protagonist with; how does its closing scene feel?\n"
            "- `dramatic_function`: which Story Circle steps this arc covers (e.g. \"You + Need + Go\", "
            "\"Search + Find + Take\", \"Return + Change\")\n"
            "- `cast`: which character keys from the available cast appear in this arc (protagonist is always included)\n"
            "- `rationale`: one sentence explaining why this arc exists in the structure\n\n"
            "The arcs together must tell the full story from the premise to the intended ending. "
            "The final arc's mini_ending must match the intended ending."
        )

        from .pipeline import grounding as _G
        _arc_craft = _G.craft_notes(
            ctx.root, f"{intended_ending} arc structure change"[:400], k=6, section="arc")
        prompt = (
            (f"{_arc_craft}\n\n" if _arc_craft else "")
            + f"PREMISE CONVERSATION:\n{transcript}\n\n"
            f"INTENDED ENDING: {intended_ending or '(not specified)'}\n\n"
            f"AVAILABLE CAST: {cast_list}\n\n"
            "Design the arc structure now."
        )

        try:
            result = await run_in_threadpool(
                lambda: provider.generate_text(system=system, prompt=prompt, emits=ARC_SUGGESTION_SCHEMA)
            )
            data = result.data or {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

        if not data:
            return JSONResponse(
                {"error": "arc suggestion returned no structured data (model may not support it)"},
                status_code=500
            )
        # Pin the protagonist key into every arc's cast — they are always present.
        protagonist_key = body.get("character", "")
        for arc in data.get("arcs") or []:
            cast = arc.get("cast") or []
            if protagonist_key and protagonist_key not in cast:
                cast.insert(0, protagonist_key)
            arc["cast"] = cast
        return data

    @app.patch("/api/stories/{key}/arc/{arc_id}")
    def patch_arc(key: str, arc_id: str, body: dict):
        """Patch mutable arc fields: name, mini_ending, dramatic_function, cast."""
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        data = ctx._read_story_data(key)
        arcs = data.get("arcs") or []
        arc_entry = next((a for a in arcs if a.get("id") == arc_id), None)
        if arc_entry is None:
            return JSONResponse({"error": f"no arc '{arc_id}'"}, status_code=404)
        body = body or {}
        for field in ("name", "mini_ending", "dramatic_function", "cast"):
            if field in body:
                arc_entry[field] = body[field]
        data["arcs"] = arcs
        ctx._write_story_data(key, data)
        return {"ok": True}

    @app.post("/api/stories/{key}/arc/{arc_id}/expand")
    async def arc_expand(key: str, arc_id: str, body: dict):
        """Expand ONE arc into chapters (ArcBeat nodes). Streams delta events + a final arc event.

        Body: { instruction?: str }
        Emits SSE: delta text events while streaming, then an `arc` event with
        {arc_id, nodes: {id: ArcBeat}, start: first_chapter_id}, then done.
        """
        import asyncio
        import threading

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from .pipeline import parse_storyboard
        from ..config.schema import ArcBeat

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)

        # Find the arc by arc_id.
        arc = next((a for a in st.arcs if a.id == arc_id), None)
        if arc is None:
            return JSONResponse({"error": f"no arc '{arc_id}' in story '{key}'"}, status_code=404)

        provider, systems = ctx.builder_ctx(body or {}, "storyboard")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        instruction = ((body or {}).get("instruction") or "").strip()

        # Collect context: heart, preceding arcs' mini_endings, arc cast details. NO story-level ending
        # — each arc owns its OWN resolution (mini_ending), generated from the arc's premise + owner lie.
        heart = st.storyboard.heart or ""

        # Preceding arcs sorted by order.
        sorted_arcs = sorted(st.arcs, key=lambda a: a.order)
        arc_idx = next((i for i, a in enumerate(sorted_arcs) if a.id == arc_id), 0)
        preceding = sorted_arcs[:arc_idx]

        prev_endings = "\n".join(
            f"Arc {i + 1} ({a.name}): {a.mini_ending}" for i, a in enumerate(preceding)
        ) or "(this is the first arc)"

        # Cast details for this arc's cast members.
        # Always include the story's primary character (protagonist) even if missing from arc.cast.
        primary_key = next((m.character for m in st.cast if m.primary), None) or \
                      (st.cast[0].character if st.cast else None)
        arc_cast_keys = list(arc.cast)
        if primary_key and primary_key not in arc_cast_keys:
            arc_cast_keys.insert(0, primary_key)
        arc_cast_details = []
        for ck in arc_cast_keys:
            ch = ctx.base_settings.characters.get(ck)
            arc_cast_details.append(ch.name if ch else ck)
        cast_line = ", ".join(arc_cast_details) or "(unspecified)"

        # Truby/ending craft so the arc (esp. its final chapter) lands a self-revelation whose
        # consequence binds the world's fate to the hero's choice — never a generic "greater good".
        from .pipeline import grounding as _G
        _exp_craft = _G.craft_notes(
            ctx.root, f"{arc.name} {arc.mini_ending or ''} {arc.premise or ''} climax self-revelation"[:400],
            k=6, section="chapters")

        system = (
            "You are writing the CHAPTERS for ONE ARC of a book.\n\n"
            "You will receive:\n"
            "- The book's overall heart and intended ending\n"
            "- What previous arcs established (their mini-endings, in order)\n"
            "- This arc's name, dramatic function, and mini-ending (where THIS arc must land)\n"
            "- The cast active in this arc\n\n"
            "Generate 3-6 chapters that form this arc. Each chapter is a node in the arc's graph.\n\n"
            "Output as a CHAPTERS block, one chapter per line, 7 pipe-delimited fields:\n"
            "id | Title | Narrative: what happens | Emotional: what shifts | "
            "Hook: tension into next | Location | Characters (comma-separated) | "
            "Scene: visual background prompt\n\n"
            f"The id should be {arc_id}-ch1, {arc_id}-ch2, etc. (e.g. {arc_id}-ch1).\n"
            "The final chapter must land on this arc's mini_ending — as the protagonist's "
            "self-revelation and the CHOICE it forces, with the outer stakes bound to that choice "
            "(never a generic 'greater good'). Apply the CRAFT NOTES below.\n"
            "Characters in each chapter must be a subset of this arc's cast — no one else."
        )

        prompt = (
            (f"{_exp_craft}\n\n" if _exp_craft else "")
            + f"BOOK HEART: {heart or '(not set)'}\n\n"
            f"PREVIOUS ARCS (their mini-endings — this arc builds on them):\n{prev_endings}\n\n"
            f"THIS ARC:\n"
            f"  Name: {arc.name}\n"
            f"  Dramatic function: {arc.dramatic_function or '(not set)'}\n"
            f"  Mini-ending: {arc.mini_ending or '(not set)'}\n"
            f"  Cast: {cast_line}\n\n"
            + (f"INSTRUCTION: {instruction}\n\n" if instruction else "")
            + "Write the chapters for this arc now.\n\nCHAPTERS:"
        )

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()
        full_text: list[str] = []

        def on_delta(t: str):
            full_text.append(t)
            loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": t})

        def _parse_arc_chapters(raw: str) -> tuple[dict, str]:
            """Parse the 7-field chapter lines into ArcBeat nodes dict + start id."""
            # Wrap in a fake storyboard block so parse_storyboard can parse it.
            fake = f"CHAPTERS:\n{raw}"
            parsed = parse_storyboard(fake)
            beats = parsed.get("beats") or []
            nodes: dict = {}
            start_id = ""
            for i, b in enumerate(beats):
                # Use the id from the first field if the chapter line starts with an id field,
                # otherwise generate one from the arc_id + index.
                # parse_storyboard returns title/summary/etc — we need to map them.
                # The 7-field format puts id in position 0 (before Title), but parse_storyboard
                # doesn't know that. We re-parse from full_text to extract the id field.
                beat_id = f"{arc_id}-ch{i + 1}"
                node = ArcBeat(
                    id=beat_id,
                    title=b.get("title", ""),
                    summary=b.get("summary", ""),
                    emotional_core=b.get("emotional_core", ""),
                    hook=b.get("hook", ""),
                    location=b.get("location", ""),
                    scene_prompt=b.get("scene_prompt", ""),
                    characters=b.get("characters", []),
                    next=[f"{arc_id}-ch{i + 2}"] if i < len(beats) - 1 else [],
                )
                nodes[beat_id] = node
                if i == 0:
                    start_id = beat_id
            return nodes, start_id

        async def run():
            try:
                await run_in_threadpool(lambda: provider.generate_text(
                    system=system, prompt=prompt, on_delta=on_delta,
                    cancel=cancel_evt.is_set))
                if not cancel_evt.is_set():
                    raw = "".join(full_text).strip()
                    nodes, start_id = _parse_arc_chapters(raw)

                    # Persist the expanded chapters to the story (DB-backed or YAML — routed).
                    try:
                        data = ctx._read_story_data(key)
                        arcs_data = data.get("arcs") or []
                        updated = False
                        for arc_entry in arcs_data:
                            if arc_entry.get("id") == arc_id:
                                arc_entry["nodes"] = {
                                    nid: n.model_dump() for nid, n in nodes.items()
                                }
                                arc_entry["start"] = start_id
                                updated = True
                                break
                        if updated:
                            data["arcs"] = arcs_data
                            ctx._write_story_data(key, data)
                    except Exception:  # noqa: BLE001 — disk write failure is non-fatal for the stream
                        pass

                    arc_event = {
                        "type": "arc",
                        "arc_id": arc_id,
                        "nodes": {nid: n.model_dump() for nid, n in nodes.items()},
                        "start": start_id,
                    }
                    loop.call_soon_threadsafe(q.put_nowait, arc_event)
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(q.put_nowait, None)

        asyncio.create_task(run())

        async def events():
            try:
                while True:
                    ev = await q.get()
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                cancel_evt.set()
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    TIMELINE_DERIVE_SCHEMA = {
        "type": "object", "additionalProperties": False, "required": ["axis", "timelines"],
        "properties": {
            "axis": {"type": "string"},
            "timelines": {
                "type": "array", "minItems": 2, "maxItems": 3,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "name", "premise"],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "premise": {"type": "string"},
                    }
                }
            }
        }
    }

    TRANSITION_SCHEMA = {
        "type": "object", "additionalProperties": False, "required": ["transitions"],
        "properties": {
            "transitions": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["from_timeline", "from_node", "to_timeline", "to_node", "condition"],
                    "properties": {
                        "from_timeline": {"type": "string"},
                        "from_node": {"type": "string"},
                        "to_timeline": {"type": "string"},
                        "to_node": {"type": "string"},
                        "condition": {"type": "string"},
                        "direction": {"type": "string"},
                    }
                }
            }
        }
    }

    @app.post("/api/stories/{key}/arc/{arc_id}/timelines")
    async def arc_generate_timelines(key: str, arc_id: str, body: dict):
        """Generate parallel timelines for an arc in three phases (streamed):
        1. Derive 2-3 timeline premises from the persona's core tension.
        2. Expand each timeline's chapters in parallel (one thread per timeline).
        3. Auto-discover transition edges by comparing chapter states across timelines.
        Saves the result to the story YAML and emits a final `result` event.

        SSE events: phase · timeline_start · delta · timeline_done · transitions · result · done
        """
        import asyncio
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from .pipeline import parse_storyboard
        from ..config.schema import ArcBeat, ArcTimeline, ArcTransition

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        arc = next((a for a in st.arcs if a.id == arc_id), None)
        if arc is None:
            return JSONResponse({"error": f"no arc '{arc_id}'"}, status_code=404)

        provider, systems = ctx.builder_ctx(body or {}, "storyboard")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        # Protagonist + cast details.
        primary_key = next((m.character for m in st.cast if m.primary), None) or \
                      (st.cast[0].character if st.cast else None)
        arc_cast_keys = list(arc.cast)
        if primary_key and primary_key not in arc_cast_keys:
            arc_cast_keys.insert(0, primary_key)
        cast_details = []
        prot_persona = ""
        for ck in arc_cast_keys:
            ch = ctx.base_settings.characters.get(ck)
            if ch:
                cast_details.append(f"{ch.name}: {(ch.system or '').splitlines()[0][:120]}")
                if ck == primary_key:
                    prot_persona = ch.system or ""
            else:
                cast_details.append(ck)
        cast_block = "\n".join(cast_details) or "(unspecified)"

        # Arc context.
        sorted_arcs = sorted(st.arcs, key=lambda a: a.order)
        arc_idx = next((i for i, a in enumerate(sorted_arcs) if a.id == arc_id), 0)
        preceding = sorted_arcs[:arc_idx]
        prev_endings = "\n".join(
            f"Arc {i + 1} ({a.name}): {a.mini_ending}" for i, a in enumerate(preceding)
        ) or "(this is the first arc)"

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()

        def emit(ev: dict):
            loop.call_soon_threadsafe(q.put_nowait, ev)

        def _parse_chapters(raw: str, tl_id: str) -> tuple[dict, str]:
            fake = f"CHAPTERS:\n{raw}"
            parsed = parse_storyboard(fake)
            beats = parsed.get("beats") or []
            nodes: dict = {}
            start_id = ""
            for i, b in enumerate(beats):
                nid = f"{arc_id}-{tl_id}-ch{i + 1}"
                node = ArcBeat(
                    id=nid,
                    title=b.get("title", ""),
                    summary=b.get("summary", ""),
                    emotional_core=b.get("emotional_core", ""),
                    hook=b.get("hook", ""),
                    location=b.get("location", ""),
                    scene_prompt=b.get("scene_prompt", ""),
                    characters=b.get("characters", []),
                    next=[f"{arc_id}-{tl_id}-ch{i + 2}"] if i < len(beats) - 1 else [],
                )
                nodes[nid] = node
                if i == 0:
                    start_id = nid
            return nodes, start_id

        async def run():
            try:
                # ── Phase 1: Derive timelines ──────────────────────────────
                emit({"type": "phase", "label": "Deriving timelines from persona…"})
                derive_system = (
                    "You are a narrative architect. Analyse the protagonist's persona and the arc's "
                    "dramatic function to identify the core DIVERGENCE AXIS — the fundamental tension "
                    "within this character that could pull them toward different paths.\n\n"
                    "Design 2-3 parallel TIMELINES for this arc. Each timeline is a complete, coherent "
                    "path through the arc where the protagonist expresses a different aspect of themselves. "
                    "The timelines should feel genuinely different in tone and outcome, not just slight variations.\n\n"
                    "Rules:\n"
                    "- `axis`: one phrase naming the divergence dimension (e.g. 'trust vs. control')\n"
                    "- Each timeline `id`: short slug (tl-a, tl-b, tl-c)\n"
                    "- Each timeline `name`: 2-4 words, evocative (e.g. 'The Opened Door')\n"
                    "- Each timeline `premise`: one sentence — what choice or stance defines this path through the arc"
                )
                derive_prompt = (
                    f"PROTAGONIST PERSONA:\n{prot_persona[:800] or '(not set)'}\n\n"
                    f"ARC: {arc.name}\n"
                    f"  Dramatic function: {arc.dramatic_function or '(unset)'}\n"
                    f"  Mini-ending: {arc.mini_ending or '(unset)'}\n\n"
                    f"CAST:\n{cast_block}\n\n"
                    f"STORY HEART: {st.storyboard.heart or '(unset)'}\n\n"
                    "Derive the divergence axis from the persona, and 2-3 timeline premises now."
                )
                derive_result = await run_in_threadpool(
                    lambda: provider.generate_text(system=derive_system, prompt=derive_prompt,
                                                   emits=TIMELINE_DERIVE_SCHEMA)
                )
                derive_data = derive_result.data or {}
                axis = derive_data.get("axis", "")
                tl_defs = derive_data.get("timelines") or []
                if not tl_defs:
                    emit({"type": "error", "error": "could not derive timelines"})
                    return
                emit({"type": "timelines_derived", "axis": axis, "timelines": tl_defs})

                # ── Phase 2: Expand each timeline's chapters in parallel ───
                emit({"type": "phase", "label": f"Expanding {len(tl_defs)} timelines…"})

                chapter_system = (
                    "You are writing the CHAPTERS for ONE TIMELINE of an arc. This timeline represents "
                    "one specific path the protagonist could take through this act — shaped by the "
                    "premise given.\n\n"
                    "Generate 3-5 chapters that form this timeline. Each chapter must feel distinctly "
                    "coloured by this path's premise.\n\n"
                    "Output as a CHAPTERS block, one chapter per line, pipe-delimited:\n"
                    "Title | Narrative: what happens | Emotional: what shifts | "
                    "Hook: tension into next | Location | Characters (comma-separated) | "
                    "Scene: visual background prompt\n\n"
                    "The final chapter must land on the arc's mini_ending as expressed through this timeline's lens."
                )

                def expand_timeline(tl_def):
                    tl_id = tl_def["id"]
                    tl_name = tl_def["name"]
                    tl_premise = tl_def["premise"]
                    emit({"type": "timeline_start", "timeline_id": tl_id, "name": tl_name})
                    full_text: list[str] = []

                    def on_delta(t: str):
                        full_text.append(t)
                        emit({"type": "delta", "timeline_id": tl_id, "text": t})

                    ch_prompt = (
                        f"ARC: {arc.name}\n"
                        f"  Dramatic function: {arc.dramatic_function or '(unset)'}\n"
                        f"  Mini-ending: {arc.mini_ending or '(unset)'}\n\n"
                        f"PREVIOUS ARCS:\n{prev_endings}\n\n"
                        f"TIMELINE: {tl_name}\n"
                        f"  Premise: {tl_premise}\n"
                        f"  Divergence axis: {axis}\n\n"
                        f"CAST:\n{cast_block}\n\n"
                        "Write the chapters for this timeline now.\n\nCHAPTERS:"
                    )
                    provider.generate_text(system=chapter_system, prompt=ch_prompt,
                                           on_delta=on_delta, cancel=cancel_evt.is_set)
                    raw = "".join(full_text).strip()
                    nodes, start_id = _parse_chapters(raw, tl_id)
                    emit({"type": "timeline_done", "timeline_id": tl_id,
                          "nodes": {k: v.model_dump() for k, v in nodes.items()},
                          "start": start_id})
                    return ArcTimeline(id=tl_id, name=tl_name, premise=tl_premise,
                                       nodes=nodes, start=start_id)

                with ThreadPoolExecutor(max_workers=len(tl_defs)) as ex:
                    timelines: list[ArcTimeline] = list(ex.map(expand_timeline, tl_defs))

                if cancel_evt.is_set():
                    return

                # ── Phase 3: Discover transitions ──────────────────────────
                emit({"type": "phase", "label": "Discovering transitions…"})

                # Build a chapter summary block for the LLM.
                tl_blocks = []
                for tl in timelines:
                    ordered = []
                    seen_ch: set = set()
                    cur_ch = tl.start
                    while cur_ch and cur_ch not in seen_ch:
                        seen_ch.add(cur_ch)
                        nd = tl.nodes.get(cur_ch)
                        if not nd:
                            break
                        ordered.append(nd)
                        cur_ch = nd.next[0] if nd.next else None
                    lines = [f"TIMELINE {tl.id}: {tl.name} ({tl.premise})"]
                    for nd in ordered:
                        lines.append(f"  {nd.id}: {nd.title} | {nd.emotional_core} | loc:{nd.location}")
                    tl_blocks.append("\n".join(lines))
                chapters_block = "\n\n".join(tl_blocks)

                # Build valid (timeline_id, node_id) pairs for the LLM to choose from.
                valid_pairs = []
                for tl in timelines:
                    for nid in tl.nodes:
                        valid_pairs.append(f"{tl.id}/{nid}")
                pairs_hint = ", ".join(valid_pairs[:40])

                tr_system = (
                    "You are a narrative editor. Given parallel timelines of an arc, identify natural "
                    "TRANSITION POINTS — chapters where a character could plausibly shift from one "
                    "path to another because their emotional or situational state aligns closely enough.\n\n"
                    "Rules:\n"
                    "- Transitions must feel narratively credible — the two chapters must be at a "
                    "  similar juncture (same location, similar tension, compatible emotional state)\n"
                    "- A transition should represent a decision or realisation that tips the character "
                    "  from one path toward the other\n"
                    "- `condition`: one sentence describing what tips the character across\n"
                    "- `direction`: 'up' if shifting toward a lighter/more redemptive path, 'down' otherwise\n"
                    "- Use EXACT node IDs from the chapter list — do not invent ids\n"
                    "- 2-4 transitions total; prefer mid-arc crossover points over start/end"
                )
                tr_prompt = (
                    f"ARC: {arc.name} (divergence axis: {axis})\n\n"
                    f"{chapters_block}\n\n"
                    f"Valid node references: {pairs_hint}\n\n"
                    "Identify transition points now."
                )
                tr_result = await run_in_threadpool(
                    lambda: provider.generate_text(system=tr_system, prompt=tr_prompt,
                                                   emits=TRANSITION_SCHEMA)
                )
                raw_trs = (tr_result.data or {}).get("transitions") or []

                # Validate: both ends must exist.
                tl_node_map = {(tl.id, nid) for tl in timelines for nid in tl.nodes}
                transitions: list[ArcTransition] = []
                for tr in raw_trs:
                    ft = tr.get("from_timeline", ""); fn = tr.get("from_node", "")
                    tt = tr.get("to_timeline", "");   tn = tr.get("to_node", "")
                    if (ft, fn) in tl_node_map and (tt, tn) in tl_node_map and ft != tt:
                        transitions.append(ArcTransition(
                            from_timeline=ft, from_node=fn, to_timeline=tt, to_node=tn,
                            condition=tr.get("condition", ""), direction=tr.get("direction", ""),
                        ))

                emit({"type": "transitions", "transitions": [t.model_dump() for t in transitions]})

                # ── Persist (DB-backed or YAML — routed) ──────────────────
                try:
                    data = ctx._read_story_data(key)
                    for arc_entry in (data.get("arcs") or []):
                        if arc_entry.get("id") == arc_id:
                            arc_entry["timelines"] = [
                                {
                                    "id": tl.id, "name": tl.name, "premise": tl.premise,
                                    "start": tl.start,
                                    "nodes": {k: v.model_dump() for k, v in tl.nodes.items()},
                                }
                                for tl in timelines
                            ]
                            arc_entry["transitions"] = [t.model_dump() for t in transitions]
                            arc_entry["divergence_axis"] = axis
                            break
                    ctx._write_story_data(key, data)
                except Exception:  # noqa: BLE001
                    pass

                emit({
                    "type": "result",
                    "arc_id": arc_id,
                    "axis": axis,
                    "timelines": [
                        {"id": tl.id, "name": tl.name, "premise": tl.premise, "start": tl.start,
                         "nodes": {k: v.model_dump() for k, v in tl.nodes.items()}}
                        for tl in timelines
                    ],
                    "transitions": [t.model_dump() for t in transitions],
                })
            except Exception as exc:  # noqa: BLE001
                emit({"type": "error", "error": str(exc)})
            emit(None)

        asyncio.create_task(run())

        async def events():
            try:
                while True:
                    ev = await q.get()
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                cancel_evt.set()
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/stories/chapter/regenerate")
    async def chapter_regenerate(body: dict):
        """Regenerate a single chapter of a storyboard in context of the full board.
        Streams `delta` events while the model writes, then emits a `chapter` event with
        the parsed beat dict on completion.

        Body: { character: str, board: dict, chapter_index: int, instruction?: str }
        """
        import asyncio
        import threading

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from .pipeline import parse_storyboard

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)

        board = body.get("board") or {}
        chapter_index = body.get("chapter_index")
        if chapter_index is None:
            return JSONResponse({"error": "chapter_index is required"}, status_code=400)
        try:
            chapter_index = int(chapter_index)
        except (TypeError, ValueError):
            return JSONResponse({"error": "chapter_index must be an integer"}, status_code=400)

        beats = board.get("beats") or []
        if chapter_index < 0 or chapter_index >= len(beats):
            return JSONResponse({"error": f"chapter_index {chapter_index} out of range (board has {len(beats)} chapters)"},
                                status_code=400)

        provider, systems = ctx.builder_ctx(body, "storyboard")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        current_beat = beats[chapter_index]
        instruction = (body.get("instruction") or "").strip()

        # Build context: logline + premise + adjacent chapters for continuity.
        logline = board.get("logline", "")
        premise = board.get("premise", "")
        prev_beat = beats[chapter_index - 1] if chapter_index > 0 else None
        next_beat = beats[chapter_index + 1] if chapter_index < len(beats) - 1 else None

        def _fmt_beat(b: dict, n: int) -> str:
            return (f"Chapter {n + 1}: {b.get('title', '(untitled)')}\n"
                    f"  Narrative: {b.get('summary', '')}\n"
                    f"  Emotional: {b.get('emotional_core', '')}\n"
                    f"  Hook: {b.get('hook', '')}\n"
                    f"  Location: {b.get('location', '')}")

        context_parts = []
        if logline:
            context_parts.append(f"LOGLINE: {logline}")
        if premise:
            context_parts.append(f"PREMISE: {premise}")
        if prev_beat:
            context_parts.append(f"PREVIOUS CHAPTER:\n{_fmt_beat(prev_beat, chapter_index - 1)}")
        context_parts.append(f"CURRENT CHAPTER (to rewrite):\n{_fmt_beat(current_beat, chapter_index)}")
        if next_beat:
            context_parts.append(f"NEXT CHAPTER:\n{_fmt_beat(next_beat, chapter_index + 1)}")

        system = (
            "You are rewriting ONE chapter of a story. Maintain the established tone, characters, "
            "and dramatic arc shown in the context. Output ONLY the single chapter line — no "
            "commentary, no numbering prefix, no markdown — in exactly this format:\n\n"
            "Title | Narrative: <what concretely happens> | Emotional: <what shifts internally> | "
            "Hook: <tension/question pulling into next chapter> | Location Name | "
            "Characters, comma-separated | Scene: <1-2 sentence visual background, empty environment, "
            "no people, painterly/evocative, matching this chapter's emotional tone>"
        )

        extras = ctx.card_extras(ch, body["character"])
        from .pipeline._helpers import _card_context
        card = _card_context(ch.name, ch.system, extras)

        user_prompt = (
            f"CHARACTER:\n{card}\n\n"
            + "\n\n".join(context_parts)
            + (f"\n\nINSTRUCTION: {instruction}" if instruction else "")
            + "\n\nRewrite the current chapter now."
        )

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()
        full_text: list[str] = []

        def on_delta(t: str):
            full_text.append(t)
            loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": t})

        async def run():
            try:
                await run_in_threadpool(lambda: provider.generate_text(
                    system=system, prompt=user_prompt, on_delta=on_delta,
                    cancel=cancel_evt.is_set))
                if not cancel_evt.is_set():
                    # Parse the single chapter line from the streamed output.
                    raw = "".join(full_text).strip()
                    # Wrap in a fake storyboard so parse_storyboard can extract it.
                    fake_board_text = f"CHAPTERS:\n1. {raw}"
                    parsed = parse_storyboard(fake_board_text)
                    beat = parsed["beats"][0] if parsed["beats"] else {}
                    loop.call_soon_threadsafe(q.put_nowait,
                                              {"type": "chapter", "index": chapter_index, "beat": beat})
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(q.put_nowait, None)

        asyncio.create_task(run())

        async def events():
            try:
                while True:
                    ev = await q.get()
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                cancel_evt.set()
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    # (The server-side wizard DRAFT store was removed — the genesis cast-queue draft is client-held,
    # and stories persist as one <key>.db each. See loom/stories/story_db.py + [[per-story-database]].)

    @app.get("/api/stories")
    def list_stories() -> list:
        out = []
        for k, st in ctx.base_settings.stories.items():
            f = ctx.story_dir() / f"{k}.db"
            mtime = f.stat().st_mtime if f.is_file() else 0.0
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
            "storyboard", "cast", "lorebook", "locations", "places", "start", "background", "fields",
            "arcs", "chapters", "scenes", "features", "start_scene",
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
            for fk in ("role", "want", "lie", "wound", "secret", "temperament"):
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
            from .queue import set_pending
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
            from .queue import set_pending
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
        from ..server.services import lorebook_store as _LS
        from .pipeline.character_scaffold import entry_when
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
        from .queue import PENDING_KINDS, set_pending
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
        from .card import build_card
        from .queue import build_queue
        from ..server.services.prompts import style_anchor
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
        from .card import build_card
        from ..server.services.prompts import style_anchor
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
        from .card import build_card, layer_patch_fields
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
        from ..server.services.prompts import style_anchor
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        lay = next((l for l in card["layers"] if l["id"] == layer), None)
        return {"ok": True, "layer": lay}

    def _rebuilt_layer(key: str, layer: str):
        st = ctx.base_settings.stories.get(key)
        from .card import build_card
        from ..server.services.prompts import style_anchor
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        return next((l for l in card["layers"] if l["id"] == layer), None)

    @app.post("/api/stories/{key}/card/{layer}/ops")
    def story_card_ops(key: str, layer: str, body: dict):
        """TARGETED partial edits to ONE layer — apply a list of ops that each touch a single part
        (set a field, merge one dict key, upsert/remove one list item by id) WITHOUT resending the
        whole section. Ops naming non-whitelisted fields are dropped. Body {ops:[...]}. Returns
        {ok, applied, layer}. This is the apply half of the section agent's suggest→approve loop."""
        from .card import apply_layer_ops, LAYER_FIELDS
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if layer not in LAYER_FIELDS:
            return JSONResponse({"error": f"layer '{layer}' has no editable fields"}, status_code=400)
        try:
            data = ctx._read_story_data(key)
        except FileNotFoundError:
            return JSONResponse({"error": "story not committed"}, status_code=400)
        new_data, applied, stale = apply_layer_ops(data, layer, (body or {}).get("ops") or [])
        if not applied:
            # a drifted anchor (someone edited underneath) → tell the caller to re-read, not clobber
            msg = "the section changed since these edits were proposed — re-open it" if stale else "no applicable ops"
            return JSONResponse({"error": msg, "stale": stale}, status_code=409 if stale else 400)
        # persist only the fields the ops touched
        touched = {op["field"] for op in applied if op.get("field")}
        try:
            ctx.update_story_fields(key, {f: new_data.get(f) for f in touched})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "applied": applied, "stale": stale, "layer": _rebuilt_layer(key, layer)}

    _SECTION_BRIEF = {
        "overview": "the PREMISE & THEME — premise, tone, themes, art style, and the premise components "
                    "(philosophy/protagonist/lie/inciting/opposition/stakes/texture). The controlling idea.",
        "map": "the WORLD — locations (each an item with an id) and the recurring setting conditions/stages.",
        "relationships": "the CAST & fixed BONDS — relationship items (each with source/target/nature and the "
                         "hidden potential/trajectory). Warmth drifts in play; you set the fixed structure.",
        "plot": "the PROGRESSION — arcs and the storyboard beats (the staged plan).",
    }

    @app.post("/api/stories/{key}/card/{layer}/chat")
    def story_card_chat(key: str, layer: str, body: dict):
        """The SECTION AGENT — converse about ONE section and propose TARGETED ops (never a whole-doc
        rewrite). Given the section's current JSON + the writer's message, returns {reply, ops} where
        each op edits one part (set/merge/upsert/remove). The client shows ops as approve cards and
        applies the kept ones via /card/{layer}/ops. Body {messages:[{role,text}]}."""
        from ..server.services import config_files as _cf
        from .card import LAYER_FIELDS, build_card
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if layer not in LAYER_FIELDS:
            return JSONResponse({"error": f"layer '{layer}' isn't editable by chat"}, status_code=400)
        messages = [m for m in ((body or {}).get("messages") or []) if isinstance(m, dict) and m.get("text")]
        if not messages:
            return JSONResponse({"error": "say something"}, status_code=400)
        from ..server.services.prompts import style_anchor
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        content = next((l["content"] for l in build_card(st.model_dump(), manifests,
                        global_style=style_anchor(ctx.root))["layers"] if l["id"] == layer), {})
        allowed = list(LAYER_FIELDS[layer])
        op_item = {"type": "object", "additionalProperties": False, "required": ["op", "field", "summary"],
                   "properties": {
                       "op": {"type": "string", "enum": ["set", "merge", "upsert", "remove"]},
                       "field": {"type": "string", "enum": allowed},
                       "key": {"type": "string", "description": "for merge: the dict key to set (e.g. a premise-component id)"},
                       "id": {"type": "string", "description": "for upsert/remove: the item's id"},
                       "value": {"type": "string", "description": "for set/merge: the new text value"},
                       "json": {"type": "string", "description": "for upsert: the item's fields as a JSON "
                                "object string, e.g. {\"source\":\"eli\",\"target\":\"mara\",\"nature\":\"rival\"}"},
                       "summary": {"type": "string", "description": "one short line describing this edit"}}}
        schema = {"type": "object", "additionalProperties": False, "required": ["reply", "ops"],
                  "properties": {"reply": {"type": "string", "description": "your conversational turn "
                                           "to the writer — brief, one idea at a time"},
                                 "ops": {"type": "array", "items": op_item}}}
        system = (
            "You are the writer's editor for ONE section of their story bible. Discuss it and propose "
            "TARGETED ops that edit only the affected part — never rewrite the whole section. Ops: set "
            "(a whole field), merge (one key of a dict like premise_parts — `key` is the component id, "
            "`value` the text), upsert (add/update one list item — `id` + `json` of the fields to set; "
            "existing keys are kept), remove (one list item by id). WHEN THE WRITER ASKS FOR A CHANGE "
            "OR APPROVES ONE, you MUST include the op(s) — do not merely describe them in `reply`. If "
            "you're only clarifying, return an empty ops list. Only the listed fields are editable. "
            f"Keep prose concrete and in the story's voice.\nSECTION: {_SECTION_BRIEF.get(layer, layer)}")
        convo = "\n".join(f"{'Writer' if m.get('role') == 'user' else 'You'}: {m['text']}" for m in messages)
        import json as _json
        prompt = (f"CURRENT SECTION JSON:\n{_json.dumps(content, ensure_ascii=False)[:6000]}\n\n"
                  f"EDITABLE FIELDS: {', '.join(allowed)}\n\nCONVERSATION:\n{convo}\n\n"
                  "Reply, and include ops for every change the writer asked for or approved.")
        _roles = _cf.load_text_roles(ctx.root)

        def _run(effort):
            p = ctx.text_provider_for(_roles.get("director") or _roles.get("narrator"),
                                      {"reasoning_effort": effort})
            if p is None:
                return None
            try:
                return (p.generate_text(system=system, prompt=prompt, emits=schema).data) or {}
            except Exception:  # noqa: BLE001 — reasoning channel can break structured output
                return {}
        out = _run("medium")
        if out is None:
            return JSONResponse({"error": "no editor model configured"}, status_code=400)
        if not out.get("reply") and not out.get("ops"):
            out = _run("none") or out          # non-thinking fallback for the empty-structured flake

        from .card import op_base_hash
        try:
            raw = ctx._read_story_data(key)      # anchor against the RAW story fields apply edits
        except FileNotFoundError:
            raw = {}
        ops = []
        for o in (out.get("ops") or []):
            if not isinstance(o, dict) or o.get("field") not in allowed:
                continue
            if o.get("op") == "upsert" and o.get("json") and "item" not in o:
                try:
                    o["item"] = _json.loads(o["json"])   # JSON-string → dict (robust structured output)
                except Exception:  # noqa: BLE001
                    continue
            o.pop("json", None)
            o["base"] = op_base_hash(raw, o)     # hash-anchor the edit to what it expects to change
            ops.append(o)
        return {"reply": (out.get("reply") or "").strip(), "ops": ops}

    @app.delete("/api/stories/{key}")
    def delete_story(key: str):
        from .story_db import delete_db
        safe = re.sub(r"[^\w\-]+", "", key)
        yaml_p = ctx.story_dir() / f"{safe}.yaml"
        db_p = ctx.story_dir() / f"{safe}.db"        # a DB-backed story (embeds its characters)
        if not yaml_p.is_file() and not db_p.is_file():
            return JSONResponse({"error": "no such story"}, status_code=404)
        if yaml_p.is_file():
            yaml_p.unlink()
        delete_db(db_p)                              # also drops the embedded characters
        ctx.reload_settings()
        removed = ctx.prune_orphan_characters()  # cascade: drop the now-storyless generated cast
        return {"ok": True, "removed_characters": removed}

    @app.post("/api/stories/{key}/regenerate-cast")
    async def regenerate_cast(key: str, body: dict):
        """DESTRUCTIVE: re-derive the whole cast from the story's storyboard, STREAMED live as a
        job (roster pass + each character). Step 1 distils the source card into ONE clean BASE
        CHARACTER CARD; step 2 re-extracts supporting NPCs in that structure. Old story-bound
        characters (+ portraits) are deleted and the cast rewritten; the source card is untouched.
        Returns {job} — consume /api/jobs/<id>/stream to watch + know when it's done."""
        import shutil

        from .pipeline import extract_characters, extract_protagonist

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

            from .pipeline import compose_base_prompt as _compose_base_prompt
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
                    from .pipeline import plan_story_wardrobe as _plan_story_wardrobe
                    from .pipeline.wardrobe import compose_outfit_prompt as _cop
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

    @app.post("/api/stories/{key}/play")
    def story_play(key: str, body: dict):
        """The runtime DIRECTOR: given the transcript (+ an optional location the
        player moved to), narrate the next turn AND report the scene state — current
        location, who's present, each one's emotion, and whether the moment invites
        moving to another location (so the UI can offer location choices)."""
        from ..providers.registry import build_provider

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}

        # This thread's durable state: a dedicated play session holds the mutable world
        # state; `thread-<sid>` is its write-back lorebook scope (engine-established facts).
        from ..server.services.story_sessions import load_session, save_session
        from ..server.services import lorebook_store as _LS0
        from . import state_engine as _SE0
        sid = body.get("sid") or f"play-{key}"
        _sess = load_session(ctx.root, sid) or {}
        # The thread's mutable record is a State doc; the world-state engine owns its
        # `world` level. Fall back to a client-seeded world_state only when the doc's
        # world level is empty (a brand-new thread).
        _doc_ws = _SE0.world_of(_sess.get("state"))
        if not any(_doc_ws.get(k) for k in ("entities", "flags", "inventory", "log", "location")):
            _doc_ws = _SE0.normalize(_sess.get("world_state") or body.get("world_state") or {})
        world_state = _doc_ws
        thread_scope = _SE0.facts_scope(sid)          # the `facts` level (engine write-back)
        story_scope = re.sub(r"[^\w\-]+", "_", f"story-{key}")

        # One-time migration: a story's legacy inline `lorebook` dict moves into the
        # unified libSQL store as the book `story-<key>` (all lore lives in one place).
        if _LS0.get_book(ctx.root, story_scope) is None and (st.lorebook or {}).get("entries"):
            from ..server.services import lorebook_import as _LI0
            _LI0.import_sillytavern(ctx.root, story_scope, st.lorebook["entries"])
            _LS0.upsert_book(ctx.root, story_scope, name=st.name, category="story",
                             rating="sfw", description=f"World lore for “{st.name}”.")

        tconn = ctx.store.active("text")
        if tconn is None:
            return JSONResponse({"error": "no chat connection"}, status_code=400)
        opts = {**tconn.to_model_options(), "max_tokens": 40000}
        if body.get("chat_model"):
            opts["model"] = body["chat_model"]
        if isinstance(body.get("chat_params"), dict):    # e.g. {"reasoning_effort": "none"} for GLM non-thinking
            opts.update(body["chat_params"])
        provider = build_provider(ModelDef(provider=tconn.provider, kind="text", options=opts))

        # THE TURN IS A GRAPH (play_graph.py): compile → prose → scribe → apply — the shipped
        # shape of the narrative harness (the bench exercises this same path). The endpoint
        # keeps only HTTP/session concerns; everything else lives in the nodes.
        import asyncio as _aio
        from ..server.services import config_files as _cf
        from .play_graph import run_play_turn, PlayState, PlayDeps
        _roles = _cf.load_text_roles(ctx.root)
        # The WRITER: an explicit body.chat_model wins; else the configured `narrator` role (ONE
        # chokepoint for "which model writes the prose" — without it play silently rides the
        # active connection's default). Prose is always NON-thinking on hybrid reasoners.
        if not body.get("chat_model") and _roles.get("narrator"):
            _np = ctx.text_provider_for(_roles["narrator"],
                                        {"max_tokens": 40000, "reasoning_effort": "none",
                                         **(body.get("chat_params") or {})})
            if _np is not None:
                provider = _np
        fallback = ctx.text_provider_for(_roles.get("fallback")) if _roles.get("fallback") else None
        # The scribe is a structured REPORTER — always non-thinking (cheap + fast; DS4-pro's big
        # context comfortably holds the state block + narration).
        scribe_prov = (ctx.text_provider_for(_roles.get("scribe"), {"reasoning_effort": "none"})
                       if _roles.get("scribe") else None) or provider
        # The director/consequence step REASONS out what happens — thinking ON (its whole value is
        # causal logic). Defaults to the writer if the `director` role is unset.
        director_prov = (ctx.text_provider_for(_roles.get("director"), {"reasoning_effort": "medium"})
                         if _roles.get("director") else None) or provider

        pstate = PlayState(body=body, world_state=world_state)
        pdeps = PlayDeps(ctx=ctx, st=st, key=key, sid=sid, sess=_sess,
                         provider=provider, scribe_provider=scribe_prov,
                         consequence_provider=director_prov, fallback=fallback,
                         story_scope=story_scope, thread_scope=thread_scope)
        _aio.run(run_play_turn(pstate, pdeps))
        if pstate.error:
            return JSONResponse({"error": pstate.error}, status_code=500)
        return pstate.result

    @app.post("/api/stories/{key}/prologue")
    def story_prologue(key: str, body: dict):
        """The novel's PROLOGUE — the protagonist's ordinary life, slow, in close third,
        establishing the world/people/pressure before the strange thing intrudes. A STORY
        CONSTANT, like a card's first message: written ONCE, identical for every playthrough
        — stored on the story itself (st.fields.prologue), never per-session. Body:
        { sid?, model?, regenerate? } → { sections:[{title,text}], cached }."""
        from .worldgen import generate_prologue
        from ..server.services.story_sessions import load_session, save_session

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        sid = body.get("sid") or f"play-{key}"
        stored = (st.fields or {}).get("prologue") or {}
        if stored.get("sections") and not body.get("regenerate"):
            return {"sections": stored["sections"], "cached": True}
        # Migration: a prologue already written under the old per-session cache gets PROMOTED
        # to the story instead of being regenerated (~80s saved).
        sess = load_session(ctx.root, sid) or {}
        if not body.get("regenerate") and sess.get("prologue", {}).get("sections"):
            pro = sess["prologue"]
            try:
                data = ctx._read_story_data(key)
                data.setdefault("fields", {})["prologue"] = pro
                ctx._write_story_data(key, data)
            except FileNotFoundError:
                pass                               # legacy YAML story: session copy stands
            return {"sections": pro["sections"], "cached": True}

        # The prologue is plain prose — non-thinking writer (thinking would be ~8x slower for the
        # four sections). Passed model wins, else the `narrator` role (the same model that will
        # narrate play — the prologue is its opening pages), else the premise-stage provider.
        from ..server.services import config_files as _cf
        _narr = _cf.load_text_roles(ctx.root).get("narrator")
        if (body.get("model") or "").strip():
            provider = ctx.text_provider_for(body["model"], {"reasoning_effort": "none"})
        elif _narr:
            provider = ctx.text_provider_for(_narr, {"reasoning_effort": "none"})
        else:
            provider, _ = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": "no provider for the prologue model"}, status_code=400)

        def _nm(k):
            c = ctx.base_settings.characters.get(k)
            return (c.name if c else k) or k

        def _life(k):
            c = ctx.base_settings.characters.get(k)
            return ((c.system or "").splitlines()[0] if c else "")[:180]

        prim = next((m.character for m in st.cast if m.primary), None) \
            or (st.cast[0].character if st.cast else None)
        loc = st.locations[0] if st.locations else None
        sheet = {
            "protagonist": {"name": _nm(prim) if prim else "the protagonist",
                            "life": _life(prim) if prim else "", "want": st.premise or ""},
            "place": {"name": (loc.name if loc else st.name), "era": st.tone or "",
                      "description": (loc.description if loc else "")},
            "pressure": st.premise or "",
            "people": [{"name": _nm(m.character), "life": _life(m.character), "want": ""}
                       for m in st.cast if m.character != prim][:2],
            "facts": [], "strange": (st.fields or {}).get("strange", "") or "",
        }
        try:
            pro = generate_prologue(provider, sheet)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"prologue failed: {exc}"}, status_code=500)
        if not pro.get("sections"):
            return JSONResponse({"error": "the model returned no prologue — try another model."},
                                status_code=502)
        # Persist on the STORY (the constant); legacy YAML stories fall back to the session.
        try:
            data = ctx._read_story_data(key)
            data.setdefault("fields", {})["prologue"] = pro
            ctx._write_story_data(key, data)
        except FileNotFoundError:
            save_session(ctx.root, sid, {**sess, "prologue": pro})
        return {"sections": pro["sections"], "cached": False}

    @app.get("/api/stories/{key}/arc")
    def story_arc_get(key: str, sid: str = ""):
        """The ACTIVE arc on this thread's world model (null if none planned)."""
        from ..server.services.story_sessions import load_session
        from . import state_engine as _SE
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        sess = load_session(ctx.root, sid or f"play-{key}") or {}
        return {"arc": _SE.world_of(sess.get("state")).get("arc") or None}

    @app.post("/api/stories/{key}/arc")
    def story_arc(key: str, body: dict):
        """Plan a story ARC — a staged progression (purpose / concrete milestone / small
        planned moments per stage) installed on the thread's world model. The one-line
        `request` IS the template ("a village romance: he draws up his courage…"). The
        per-scene director + consequence step steer toward the current stage; the scribe
        advances stages when milestones land. Body: { sid?, request } → { arc }."""
        from ..server.services.story_sessions import load_session, save_session
        from ..server.services import config_files as _cf
        from . import state_engine as _SE
        from .storymaster import generate_arc

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        request = (body.get("request") or "").strip()
        sid = body.get("sid") or f"play-{key}"
        sess = load_session(ctx.root, sid) or {}
        ws = _SE.world_of(sess.get("state"))
        # INSTALL mode: a full `arc` (from the collaborative designer) becomes THE active arc
        # on this thread's world model, stage 0; its pending draft clears from the work queue.
        if not request and isinstance(body.get("arc"), dict) and (body["arc"].get("stages")):
            arc = dict(body["arc"])
            arc["stage"] = 0
            known = {c.id for c in (st.conditions or []) if c.id}
            arc["conditions"] = [c for c in (arc.get("conditions") or []) if c in known]
            ws["arc"] = arc
            save_session(ctx.root, sid, {**sess, "state": _SE.with_world(sess.get("state"), ws)})
            from .queue import set_pending
            try:
                ctx.update_story_fields(key, {"fields": set_pending(_story_fields(key), "arc", [])})
            except FileNotFoundError:
                pass
            return {"arc": arc}
        # PATCH mode: no request + a `conditions` list = hand-edit the ACTIVE arc's setting
        # stages (validated against the story's stage-set). The next turn's sync flips the flags.
        if not request and isinstance(body.get("conditions"), list):
            arc = ws.get("arc") if isinstance(ws.get("arc"), dict) else None
            if not arc:
                return JSONResponse({"error": "no active arc to edit"}, status_code=400)
            known = {c.id for c in (st.conditions or []) if c.id}
            arc["conditions"] = [c for c in body["conditions"] if c in known]
            save_session(ctx.root, sid, {**sess, "state": _SE.with_world(sess.get("state"), ws)})
            return {"arc": arc}
        if not request:
            return JSONResponse({"error": "give the arc a one-line request"}, status_code=400)
        _roles = _cf.load_text_roles(ctx.root)
        prov = ctx.text_provider_for(_roles.get("director") or _roles.get("narrator"),
                                     {"reasoning_effort": "medium"})
        if prov is None:
            return JSONResponse({"error": "no director model configured"}, status_code=400)
        arc = generate_arc(prov, ctx, st, ws, request)
        if not arc:
            return JSONResponse({"error": "the model returned no arc"}, status_code=502)
        save_session(ctx.root, sid, {**sess, "state": _SE.with_world(sess.get("state"), ws)})
        return {"arc": arc}

    @app.post("/api/stories/{key}/arc/design")
    def story_arc_design(key: str, body: dict):
        """COLLABORATIVE arc design — one conversational turn. Body {messages: [{role,text}]};
        the current draft rides the story's pending store (fields.pending.arc), so design
        survives navigation and shows in the work queue until kept (POST /arc {sid, arc}).
        Returns {reply, arc}."""
        from ..server.services import config_files as _cf
        from .storymaster import design_arc
        from .queue import set_pending
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        if not messages:
            return JSONResponse({"error": "say something to start designing"}, status_code=400)
        draft = ((_story_fields(key).get("pending") or {}).get("arc") or {}).get("items") or []
        _roles = _cf.load_text_roles(ctx.root)
        prov = ctx.text_provider_for(_roles.get("director") or _roles.get("narrator"),
                                     {"reasoning_effort": "medium"})
        if prov is None:
            return JSONResponse({"error": "no director model configured"}, status_code=400)
        out = design_arc(prov, ctx, st, messages, draft[0] if draft else None)
        if not out.get("reply") and not out.get("arc"):
            return JSONResponse({"error": out.get("error")
                                 or "the designer returned nothing — say it again"}, status_code=502)
        if out.get("arc"):
            try:
                ctx.update_story_fields(
                    key, {"fields": set_pending(_story_fields(key), "arc", [out["arc"]])})
            except FileNotFoundError:
                pass
        return {"reply": out.get("reply", ""), "arc": out.get("arc")}

    @app.post("/api/stories/{key}/day/suggest")
    def story_day_suggest(key: str, body: dict):
        """The DAY's scene offers: 3 suggested scenes for the current slot (morning/evening/
        night), grounded in the arc + whereabouts + world stage. Body {sid?, advance?: bool} —
        advance moves to the NEXT slot first (night never advances here; sleeping in play is
        the only door out of night). Returns {day, slot, options}."""
        from ..server.services.story_sessions import load_session, save_session
        from ..server.services import config_files as _cf
        from . import state_engine as _SE
        from .storymaster import suggest_slot_scenes, advance_slot, day_of
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        sid = body.get("sid") or f"play-{key}"
        sess = load_session(ctx.root, sid) or {}
        ws = _SE.world_of(sess.get("state"))
        if body.get("advance"):
            if day_of(ws)["slot"] == "night":
                return JSONResponse({"error": "night ends by sleeping, not by moving on"},
                                    status_code=400)
            advance_slot(ws)
        else:
            day_of(ws)   # seed day 1 morning on first touch
        _roles = _cf.load_text_roles(ctx.root)
        prov = ctx.text_provider_for(_roles.get("director") or _roles.get("narrator"),
                                     {"reasoning_effort": "medium"})
        if prov is None:
            return JSONResponse({"error": "no director model configured"}, status_code=400)
        out = suggest_slot_scenes(prov, ctx, st, ws)
        save_session(ctx.root, sid, {**sess, "state": _SE.with_world(sess.get("state"), ws)})
        if out.get("error") and not out.get("options"):
            return JSONResponse({"error": out["error"]}, status_code=502)
        return {"day": out["day"], "slot": out["slot"], "options": out["options"]}

    @app.get("/api/stories/{key}/manuscript")
    def story_manuscript(key: str, sid: str = ""):
        """The MANUSCRIPT — this playthrough's prose as literature: the prologue plus the
        play narration grouped by SCENE (each page = one turn's text + its beat line). Sessions
        from before the manuscript existed fall back to the flat transcript as one scene."""
        from ..server.services.story_sessions import load_session
        from . import state_engine as _SE
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        sess = load_session(ctx.root, sid or f"play-{key}") or {}
        ws = _SE.world_of(sess.get("state"))
        scenes = ws.get("manuscript") or []
        if not scenes and ws.get("transcript"):
            scenes = [{"loc": "The story so far", "opened": 0,
                       "pages": [{"step": i, "text": t, "beat": "", "ti": i}
                                 for i, t in enumerate(ws["transcript"]) if (t or "").strip()]}]
        prologue = ((st.fields or {}).get("prologue") or {}).get("sections") or []
        return {"prologue": prologue, "scenes": scenes}

    @app.post("/api/stories/{key}/manuscript/edit")
    def story_manuscript_edit(key: str, body: dict):
        """Manually edit one paragraph-block of the manuscript. Body: either
        { prologue: <section index>, text } (edits the story-constant prologue) or
        { sid?, scene: i, page: j, text } (edits a play page + its transcript entry, so the
        narrator's own history window sees the corrected prose)."""
        from ..server.services.story_sessions import load_session, save_session
        from . import state_engine as _SE
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        text = (body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "empty text"}, status_code=400)
        if body.get("prologue") is not None:
            try:
                data = ctx._read_story_data(key)
                secs = ((data.get("fields") or {}).get("prologue") or {}).get("sections") or []
                secs[int(body["prologue"])]["text"] = text
                ctx._write_story_data(key, data)
                return {"ok": True}
            except (FileNotFoundError, IndexError, KeyError):
                return JSONResponse({"error": "no editable prologue"}, status_code=400)
        sid = body.get("sid") or f"play-{key}"
        sess = load_session(ctx.root, sid) or {}
        ws = _SE.world_of(sess.get("state"))
        try:
            scenes = ws.get("manuscript") or []
            if scenes:
                page = scenes[int(body["scene"])]["pages"][int(body["page"])]
            else:                                      # legacy flat-transcript session
                page = {"ti": int(body["page"])}
            ti = page.get("ti")
            if ti is not None and 0 <= int(ti) < len(ws.get("transcript") or []):
                ws["transcript"][int(ti)] = text
            page["text"] = text
        except (IndexError, KeyError, ValueError, TypeError):
            return JSONResponse({"error": "no such page"}, status_code=400)
        save_session(ctx.root, sid, {**sess, "state": _SE.with_world(sess.get("state"), ws)})
        return {"ok": True}

    @app.get("/api/stories/{key}/state-card")
    def story_state_card(key: str, sid: str = ""):
        """THE STATE CARD — the story's current state as one readable card (scene + its plan,
        arc, people by place, established/promised, hard state). A pure derived VIEW over the
        thread's delta-maintained world model: reading it is instant, and it is always current
        because the per-turn deltas already updated the model underneath (no LLM, no rewrite)."""
        from ..server.services.story_sessions import load_session
        from . import state_engine as _SE
        from .storymaster import state_card
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        sess = load_session(ctx.root, sid or f"play-{key}") or {}
        ws = _SE.world_of(sess.get("state"))
        return {"card": state_card(ws, st), "step": ws.get("step"), "revision": ws.get("revision")}

    @app.post("/api/stories/{key}/dream")
    def story_dream(key: str, body: dict):
        """The storymaster's FEVER-DREAM. Reads the on-stage cast's hidden psychology (want/lie/wound/
        secret) + bonds + recent events and returns ONE foreboding, oblique dream-portent (NOT options,
        NOT a recap). This is the DM's conflict signal delivered as a dream — fired primarily when the
        player SLEEPS (see consolidate_on_rest), exposed here for a deliberate rest/dream. Pure read.
        Body: { sid?, present?[keys], you? } → { dream }."""
        from ..server.services.story_sessions import load_session
        from . import state_engine as _SE
        from . import stage_tools as _ST

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        sid = body.get("sid") or f"play-{key}"
        ws = _SE.world_of((load_session(ctx.root, sid) or {}).get("state"))
        dream = _ST.generate_dream(ctx, key, ws, present=body.get("present"), you=(body.get("you") or "").strip())
        return {"dream": dream}

    @app.post("/api/stories/{key}/geography")
    def story_geography(key: str, body: dict):
        """Generate the story's LIVED GEOGRAPHY — areas → spots → who habitually occupies them
        (orbits) + travel notes — grounded in the cast's daily lives, weaving in existing
        locations. `apply` (default true) writes locations/places/fields.travel onto the story,
        which arms the whereabouts lines + the move rate-limiter in play (geography.py).
        Body: { model?, apply? } → { geography, applied, locations, places, travel }."""
        from .geography import gen_geography, apply_geography

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        provider, err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": err}, status_code=400)

        name_to_key, cast_lines = {}, []
        for m in st.cast:
            c = ctx.base_settings.characters.get(m.character)
            nm = (c.name if c else m.character) or m.character
            name_to_key[nm] = m.character
            cast_lines.append({"name": nm,
                               "about": ((c.system or "").splitlines()[0] if c else "")[:160]})
        try:
            geo = gen_geography(provider, premise=st.premise, tone=st.tone,
                                world_note=(st.storyboard.logline if st.storyboard else ""),
                                cast=cast_lines, existing=[l.name for l in st.locations],
                                root=ctx.root)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"geography generation failed: {exc}"}, status_code=500)
        if not geo:
            return JSONResponse({"error": "the model returned no geography — try another model."},
                                status_code=502)
        out = apply_geography(geo, st, name_to_key)
        applied = False
        if body.get("apply", True):
            fields = {**(st.fields or {}), "travel": out["travel"]}
            ctx.update_story_fields(key, {"locations": out["locations"],
                                          "places": out["places"], "fields": fields})
            applied = True
        return {"geography": geo, "applied": applied, **out}

    @app.post("/api/stories/{key}/plan-wardrobe")
    async def story_plan_wardrobe(key: str, body: dict):
        """Plan one cast character's wardrobe (outfits) + story-derived expression prompts,
        STREAMED live as a job. Returns {job}; the final `result` event carries the plan for
        review (persist via the portraits wardrobe endpoint). Renders nothing."""
        from .pipeline import plan_wardrobe

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        char_key = (body or {}).get("character")
        ch = ctx.base_settings.characters.get(char_key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, systems = ctx.builder_ctx(body or {}, "wardrobe")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        story = st.model_dump()
        appearance = (ch.fields or {}).get("appearance", "")

        def work(emit, cancelled):
            plan = plan_wardrobe(provider, char_name=ch.name, persona=ch.system,
                                 appearance=appearance, story=story,
                                 systems=systems, on_event=emit)
            # Pass 2 — refine EACH outfit into careful, consistent booru tags, in parallel (same
            # 2-step pipeline the base image gets).
            emit({"type": "phase", "label": "Refining each outfit — booru tags"})
            from .pipeline import refine_outfits as _refine_outfits
            plan["outfits"] = _refine_outfits(provider, plan.get("outfits"), ch.system, appearance, emit=emit)
            return {"character": char_key, **plan}

        job = _start_stream_job("wardrobe", "Plan wardrobe", ch.name,
                                f"stories/{key}/cast", work)
        return {"job": job.id}

    @app.post("/api/stories/{key}/regenerate-character")
    async def regenerate_character(key: str, body: dict):
        """DESTRUCTIVE, STREAMED: re-derive ONE cast member end-to-end, steered by an optional
        free-text `instruction`. Rewrites their persona + role + appearance (so the story overview
        updates), recomposes + saves the base-image prompt, re-renders the base image, and rebuilds
        the wardrobe (old outfits + sprites cleared). Returns {job}; GenStream watches it live.

        With `text_only: true` it runs the TEXT half only — rewrite + recompose the base prompt +
        refresh the persona-driven canonical expression prompts (and plan a wardrobe if none exists) —
        and SKIPS every image render, leaving existing outfits and rendered sprites untouched. The
        gated RegenModal uses this so the user can review each image stage before it renders."""
        from .pipeline import revise_character

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        char_key = body.get("character")
        ch = ctx.base_settings.characters.get(char_key)
        if ch is None or not any(m.character == char_key for m in st.cast):
            return JSONResponse({"error": "character is not in this story's cast"}, status_code=404)
        provider, systems = ctx.builder_ctx(body, "characters")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        instruction = (body.get("instruction") or "").strip()
        text_only = bool(body.get("text_only"))
        is_primary = any(m.character == char_key and m.primary for m in st.cast)
        story = st.model_dump()
        board = {"logline": (story.get("storyboard") or {}).get("logline", "")}
        cur_name, cur_persona = ch.name, ch.system or ""
        cur_role = (ch.fields or {}).get("role") or ("protagonist" if is_primary else "supporting")
        cur_appear = (ch.fields or {}).get("appearance", "")

        def work(emit, cancelled):
            # 1. Rewrite persona + role + appearance, steered by the instruction.
            revised = revise_character(provider, name=cur_name, persona=cur_persona, role=cur_role,
                                       appearance=cur_appear, instruction=instruction, board=board,
                                       systems=systems, on_event=emit)
            if cancelled():
                return {"cancelled": True}
            # 2. Compose the rich base-image prompt from the rewritten persona + appearance.
            emit({"type": "phase", "label": "Composing the base-image prompt"})
            from .pipeline import compose_base_prompt as _compose_base_prompt
            _bp_cfg = ctx.load_story_builder()
            _bp_prov = ctx.stage_provider("base_image")
            comp = _compose_base_prompt(_bp_prov, revised["name"], revised["persona"],
                                        revised["appearance"], revised["role"],
                                        systems=(_bp_cfg.get("systems") or {}))
            base_prompt = comp.get("prompt", "") if isinstance(comp, dict) else ""
            height_cm = (comp.get("features") or {}).get("height_cm") if isinstance(comp, dict) else None
            # 3. Persist the rewritten card (routes to the owning story DB if embedded, else global YAML).
            data = ctx._read_character_data(char_key) or {}
            data["name"] = revised["name"] or data.get("name") or char_key
            data["system"] = revised["persona"]
            data["fields"] = {**(data.get("fields") or {}), "role": revised["role"],
                              "appearance": revised["appearance"], "base_prompt": base_prompt}
            try:
                if height_cm:
                    data["fields"]["height_cm"] = int(height_cm)
            except (TypeError, ValueError):
                pass
            ctx._write_character_data(char_key, data)
            emit({"type": "item", "name": revised["name"], "text": base_prompt or revised["appearance"]})
            if cancelled():
                return {"cancelled": True}
            # 4. Re-render the base image from the new prompt and set it as the reference (best-effort).
            #    text_only skips every render — the gated modal renders the base later, with review.
            if not text_only:
                emit({"type": "phase", "label": "Rendering the new base image"})
                try:
                    iprov, _mid = ctx.role_image_provider("base")
                    if iprov is not None and base_prompt:
                        from ...comfy.server import get_server
                        get_server(iprov.base_url).ensure_up()
                        res = iprov.generate_image(prompt=base_prompt,
                                                   out_prefix=ctx.output_prefix_for(_mid, "base", char_key))
                        if res.images:
                            (ctx.char_dir() / f"{safe}.ref.png").write_bytes(_clean_reference_png(res.images[0]))
                except Exception as exc:  # noqa: BLE001 — a render failure must not sink the rewrite
                    emit({"type": "phase", "label": f"Base image skipped ({exc})"})
                if cancelled():
                    return {"cancelled": True}
            # 5. Refresh the wardrobe. Full path (replace=True) clears old outfits + sprite dirs and
            #    recomposes all persona-derived prompts fresh. text_only keeps existing outfits and
            #    rendered sprites intact but refreshes expression/pose/affect from the new persona;
            #    it plans a wardrobe only when there is none yet so the image stages have something
            #    to render.
            emit({"type": "phase", "label": "Refreshing prompts" if text_only else "Rebuilding the wardrobe"})
            try:
                w_prov, w_sys = ctx.builder_ctx({}, "wardrobe")
                fresh_ch = ctx.base_settings.characters[char_key]
                if text_only:
                    # Recompose persona-driven prompts from the revised persona and merge them into
                    # the manifest without touching the existing outfits or their sprites.
                    from .pipeline import (compose_expressions as _compose_expressions,
                                               compose_poses as _compose_poses,
                                               compose_affect_range as _compose_affect_range,
                                               plan_wardrobe as _plan_wardrobe,
                                               refine_outfits as _refine_outfits)
                    emit({"type": "phase", "label": "Composing expression + pose range"})
                    fresh_exprs = _compose_expressions(w_prov or provider, revised["persona"])
                    fresh_poses = _compose_poses(w_prov or provider, revised["persona"], ctx.load_pose_library())
                    emit({"type": "phase", "label": "Composing emotional expression range"})
                    _emo_cfg = ctx.load_story_builder()
                    _emo_prov = ctx.stage_provider("emotion")
                    fresh_affect = _compose_affect_range(_emo_prov, revised["persona"],
                                                        systems=(_emo_cfg.get("systems") or {}))
                    apply_body: dict = {"expressions": fresh_exprs, "poses": fresh_poses}
                    if isinstance(fresh_affect, dict) and fresh_affect.get("range"):
                        apply_body["affect"] = fresh_affect
                    if not (ctx.portrait_manifest(char_key).get("outfits") or []):
                        plan = _plan_wardrobe(w_prov or provider, char_name=revised["name"],
                                              persona=revised["persona"],
                                              appearance=revised["appearance"],
                                              story=story, systems=w_sys or systems, on_event=emit)
                        apply_body["outfits"] = _refine_outfits(
                            w_prov or provider, plan.get("outfits"),
                            revised["persona"], revised["appearance"], emit=emit)
                    _apply_manifest(ctx, char_key, apply_body)
                else:
                    # Full rebuild: replace=True clears outfits + persona caches; apply_manifest
                    # recomposes expressions/poses/affect fresh from the saved revised persona.
                    _plan_and_apply(ctx, w_prov or provider, char_key, fresh_ch,
                                    story, w_sys or systems, emit=emit, replace=True)
            except Exception as exc:  # noqa: BLE001
                emit({"type": "phase", "label": f"Wardrobe refresh skipped ({exc})"})
            emit({"type": "phase", "label": f"Done — {revised['name']} {'text refreshed' if text_only else 'regenerated'}"})
            return {"ok": True, "character": char_key, "name": revised["name"],
                    "role": revised["role"], "text_only": text_only}

        job = _start_stream_job("character", "Regenerate character", ch.name,
                                f"stories/{key}/cast", work)
        return {"job": job.id}

    @app.post("/api/stories/{key}/locations/{loc}/regen-prompt")
    def regen_location_prompt(key: str, body: dict):
        """Regenerate ONE location's background_prompt using the locations stage's
        configured model + system prompt (framing rules included), then save it to the
        story. Returns {prompt}."""
        from .pipeline import DEFAULT_SYSTEMS

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        location = next((l for l in st.locations if l.id == loc), None)
        if location is None:
            return JSONResponse({"error": "no such location"}, status_code=404)
        cfg = config_files.load_story_builder(ctx.root)
        provider = ctx.stage_provider("locations")
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no author model configured"}, status_code=400)
        locsys = (cfg.get("systems") or {}).get("locations") or DEFAULT_SYSTEMS["locations"]
        system = (locsys + "\n\nNOW: output ONLY the `background_prompt` for the SINGLE location "
                  "below — a flat comma-separated Danbooru tag list framing the place itself. No "
                  "id, no name, no description, no commentary, no quotes — just the tags.")
        ctx_text = f"NAME: {location.name}\nDESCRIPTION: {location.description or location.name}"
        try:
            res = provider.generate_text(system=system, prompt=ctx_text)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"regenerate failed: {exc}"}, status_code=500)
        prompt = (res.text or "").strip().strip("`").strip().strip('"').strip()
        if not prompt:
            return JSONResponse({"error": "model returned nothing"}, status_code=500)
        # Save onto the story (DB-backed or legacy YAML — routed).
        data = ctx._read_story_data(key)
        for l in data.get("locations", []):
            if l.get("id") == loc:
                l["background_prompt"] = prompt
        ctx._write_story_data(key, data)
        return {"prompt": prompt}

    @app.get("/api/stories/{key}/bg/{file}")
    def story_background(key: str, file: str):
        if not re.fullmatch(r"[\w\-]+\.png", file):
            return JSONResponse({"error": "bad path"}, status_code=404)
        p = (ctx.story_bg_dir(key) / file).resolve()
        if ctx.story_bg_dir(key).resolve() not in p.parents or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, media_type="image/png")

    @app.post("/api/stories/{key}/locations/{loc}/background/candidate")
    async def background_candidate(key: str, loc: str, body: dict):
        """Render ONE background CANDIDATE for a location (fresh seed each call, so a
        batch varies). Returns a data URI — not saved. The UI shows a few and lets
        you pick the winner via .../background/select. Workflow-driven: only the
        location description is injected into the `scene` workflow's {{image}}."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        location = next((l for l in st.locations if l.id == loc), None)
        if location is None:
            return JSONResponse({"error": "no such location"}, status_code=404)
        prompt = (location.background_prompt or location.description or "").strip()
        if not prompt:
            return JSONResponse({"error": "location has no background prompt"}, status_code=400)
        # L0 of the image card: the story's art style leads the scene prompt, so
        # locations and sprites come out of the same visual world.
        if (st.art_style or "").strip():
            prompt = f"{ctx.art_style(story_key=key)} {prompt}"
        # Go through the single role chokepoint so the active image preset's LoRA
        # look is injected, same as base/sprite renders.
        provider, model_id = ctx.role_image_provider("scene", (body or {}).get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)
        try:
            png = await _render(provider, prompt, out_prefix=ctx.output_prefix_for(model_id, "scene", loc))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode()}

    @app.post("/api/stories/{key}/locations/{loc}/background/select")
    def select_location_background(key: str, loc: str, body: dict):
        """Save a chosen candidate (base64 data URI) as the location's background."""
        st = ctx.base_settings.stories.get(key)
        if st is None or not any(l.id == loc for l in st.locations):
            return JSONResponse({"error": "no such story/location"}, status_code=404)
        uri = (body or {}).get("data", "")
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        try:
            png = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad image data"}, status_code=400)
        url = ctx.save_location_bg(key, loc, png)
        return {"ok": True, "url": url}

    @app.post("/api/stories/{key}/scene/{sid}/background/candidate")
    async def scene_background_candidate(key: str, sid: str, body: dict):
        """Render ONE background candidate for a Place's Scene (fresh seed each call).
        Returns a data URI — not saved. Mirrors the location background flow but the
        prompt comes from the scene's own background_prompt (the spot's empty plate)."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        scene = next((s for p in st.places for s in p.scenes if s.id == sid), None)
        if scene is None:
            return JSONResponse({"error": "no such scene"}, status_code=404)
        prompt = (scene.background_prompt or scene.name or "").strip()
        if not prompt:
            return JSONResponse({"error": "scene has no background prompt"}, status_code=400)
        if (st.art_style or "").strip():   # L0: same style layer as sprites + locations
            prompt = f"{ctx.art_style(story_key=key)} {prompt}"
        provider, model_id = ctx.role_image_provider("scene", (body or {}).get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)
        try:
            png = await _render(provider, prompt, out_prefix=ctx.output_prefix_for(model_id, "scene", sid))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode()}

    @app.post("/api/stories/{key}/scene/{sid}/background/select")
    def select_scene_background(key: str, sid: str, body: dict):
        """Save a chosen candidate (base64 data URI) as the scene's background, writing
        it back into the right scene inside the story's places."""
        st = ctx.base_settings.stories.get(key)
        if st is None or not any(s.id == sid for p in st.places for s in p.scenes):
            return JSONResponse({"error": "no such story/scene"}, status_code=404)
        uri = (body or {}).get("data", "")
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        try:
            png = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad image data"}, status_code=400)
        d = ctx.story_bg_dir(key); d.mkdir(parents=True, exist_ok=True)
        fname = f"scene_{re.sub(r'[^a-z0-9_]+', '', sid.lower())}.png"
        (d / fname).write_bytes(png)
        url = f"/api/stories/{key}/bg/{fname}"
        data = ctx._read_story_data(key)
        for p in data.get("places", []):
            for s in p.get("scenes", []):
                if s.get("id") == sid:
                    s["background"] = url
        ctx._write_story_data(key, data)
        return {"ok": True, "url": url}

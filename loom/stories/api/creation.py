"""Story builder HTTP endpoints.

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

        from ..pipeline import parse_storyboard, storyboard_inputs

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, systems = ctx.builder_ctx(body, "storyboard")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        spine = body.get("spine") or {}
        from ..pipeline import grounding as _G
        _prem = (body.get("premise") or "").strip()
        system, prompt = storyboard_inputs(name=ch.name, persona=ch.system,
                                           extras=ctx.card_extras(ch, body["character"]),
                                           systems=systems,
                                           premise=_prem,
                                           spine=spine,
                                           craft=_G.MINIMALISM)

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

        from ..pipeline._helpers import _card_context

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
        from ..runtime.simulation import design_cast
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

        from ..runtime.simulation import run_scene_burst

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
            from ..runtime.simulation import sim_of
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
                        from ..runtime.simulation import with_sim
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
        from ..pipeline.character_scaffold import FACET_TYPES
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
        from ..server.services import config_files as _cf
        from ..pipeline.character_scaffold import (
            INTERVIEW_SCHEMA, INTERVIEW_SYSTEM, facet_to_entry, interview_prompt)

        body = body or {}
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        # The character interview is the harder creative task (a real person through conversation),
        # so we run it with REASONING ON, then fall back to the structured channel if the reasoning
        # model flakes on structured output — the same pattern story_card_chat uses for its interview.
        # An explicit body.model still wins; otherwise the director/narrator role resolves the model.
        _roles = _cf.load_text_roles(ctx.root)
        _PROVIDER_ROLE = body.get("model") or _roles.get("director") or _roles.get("narrator")
        if not _PROVIDER_ROLE:
            return JSONResponse({"error": "no chat model configured"}, status_code=400)

        scope = _char_scope(key)
        entries = LS.load_lorebook(ctx.root, scope)
        prompt = interview_prompt(ch.name, ch.system, entries, body.get("messages") or [])
        data: dict = {}
        try:
            for effort in ("high", "none"):       # reasoning ON, then the structured flake fallback
                p = ctx.text_provider_for(_PROVIDER_ROLE, {"reasoning_effort": effort})
                if p is None:
                    break
                try:
                    out = (p.generate_text(system=INTERVIEW_SYSTEM, prompt=prompt,
                                           emits=INTERVIEW_SCHEMA).data) or {}
                except Exception:  # noqa: BLE001 — reasoning channel can break structured output
                    out = {}
                if out.get("reply") or out.get("facets"):
                    data = out
                    break
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"interview failed: {exc}"}, status_code=500)

        saved = []
        for f in (data.get("facets") or []):
            e = facet_to_entry(f)
            if e is not None:
                LS.upsert_entry(ctx.root, scope, e)
                saved.append(e.id)
        return {"ok": True, "reply": (data.get("reply") or "").strip(),
                "saved": saved, "facets": _facet_cards(scope)}

    @app.post("/api/stories/character/{key}/interview/seal")
    def character_interview_seal(key: str, body: dict):
        """SEAL the interview conversation: distil the FULL transcript into exemplars in one
        explicitly-triggered pass and commit them to the character's lorebook. The interview
        itself (POST .../interview) is pure conversation and commits nothing per turn; sealing is
        the separate process the writer invokes when they judge enough has been established — the
        same two-phase shape as harvest (scene) and deepen (portrait→bank). Conversation first,
        extraction second, never both in one call.
        Body: { messages: [{role, content|text}], model?: str }. Returns {ok, saved, facets}."""
        from ..server.services import lorebook_store as LS
        from ..server.services import config_files as _cf
        from ..pipeline.character_scaffold import (
            FACETS_SCHEMA, SEAL_SYSTEM, facet_to_entry, interview_seal_prompt)

        body = body or {}
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        messages = body.get("messages") or []
        if not messages:
            return JSONResponse({"error": "no interview transcript to seal"}, status_code=400)
        _roles = _cf.load_text_roles(ctx.root)
        _PROVIDER_ROLE = body.get("model") or _roles.get("director") or _roles.get("narrator")
        if not _PROVIDER_ROLE:
            return JSONResponse({"error": "no chat model configured"}, status_code=400)

        scope = _char_scope(key)
        entries = LS.load_lorebook(ctx.root, scope)
        prompt = interview_seal_prompt(ch.name, ch.system, messages, entries)
        data: dict = {}
        try:
            for effort in ("high", "none"):       # reasoning ON, then the structured flake fallback
                p = ctx.text_provider_for(_PROVIDER_ROLE, {"reasoning_effort": effort})
                if p is None:
                    break
                try:
                    data = (p.generate_text(system=SEAL_SYSTEM, prompt=prompt,
                                            emits=FACETS_SCHEMA).data) or {}
                except Exception:  # noqa: BLE001
                    data = {}
                if data.get("facets"):
                    break
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"seal failed: {exc}"}, status_code=500)

        saved = []
        for f in (data.get("facets") or []):
            e = facet_to_entry(f, source="interview-seal")
            if e is not None:
                LS.upsert_entry(ctx.root, scope, e)
                saved.append(e.id)
        return {"ok": True, "saved": saved, "facets": _facet_cards(scope)}

    @app.post("/api/stories/character/{key}/deepen")
    def character_deepen(key: str, body: dict):
        """TWO passes: (1) a REASONED psychological portrait — the model actually thinks the
        person through (mechanism, contradictions, the defense's daily cost), free prose, no
        slot structure; (2) the exemplar bank written FROM that portrait, the model choosing
        which moments this person needs captured. The portrait persists to fields.psychology
        (working material for later steps); exemplars go to the character's lorebook, which
        play retrieves per scene. Returns {saved, facets, portrait}."""
        from ..server.services import lorebook_store as LS
        from ..pipeline.character_scaffold import (
            DEEPEN_SYSTEM, PORTRAIT_SYSTEM, deepen_facets_schema, deepen_prompt,
            facet_to_entry, portrait_prompt)
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        f = ch.fields or {}
        harness = {k: f.get(k) or "" for k in
                   ("role", "temperament", "want", "lie", "contradiction", "wound", "secret", "good_memory")}
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
            _pp = st.premise_parts or {}
            philosophy = (_pp.get("question") or _pp.get("creeds") or _pp.get("philosophy") or "")[:500]
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
        from ..pipeline.character_scaffold import REFINE_SCHEMA, FACET_TYPES

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
        from ..pipeline.character_scaffold import facet_digest, run_improv

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
        from ..pipeline.character_scaffold import (
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
        from ..pipeline.character_scaffold import (
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
        from ..pipeline.arc_weave import ARCS_SCHEMA, ARCS_SYSTEM, arcs_prompt, digest_for

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
        from ..pipeline import grounding as _G
        _arc_craft = _G.MINIMALISM
        try:
            res = provider.generate_text(system=ARCS_SYSTEM + (("\n\n" + _arc_craft) if _arc_craft else ""),
                                         prompt=arcs_prompt(cast, body.get("premise") or ""),
                                         emits=ARCS_SCHEMA)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"arc weave failed: {exc}"}, status_code=500)
        arcs = (res.data or {}).get("arcs") or []
        return {"ok": True, "arcs": arcs, "cast": [c["name"] for c in cast]}

    @app.post("/api/stories/new")
    def story_new(body: dict):
        """Create an EMPTY story and return its key. This is the whole 'from scratch' path now —
        no wizard, no draft/commit: you get a blank story and build it by CONVERSATION in the editor
        (the chat, interviewer-flavoured while the story is thin, edits its world/premise/cast).
        Body: { name?, type? }."""
        body = body or {}
        name = (body.get("name") or "Untitled story").strip() or "Untitled story"
        type_ = body.get("type") if body.get("type") in ("novel", "vn") else "novel"
        key = ctx.create_story(name, {}, character_keys=[], type_=type_)
        return {"ok": True, "key": key}

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
        cast = [{"character": k} for k in keys]
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
            }, character_keys=keys, type_=body.get("type", "novel"))   # one self-contained <skey>.json
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
        from ..pipeline import NEEDS_IMAGE, DEFAULT_SYSTEMS, _sys, storyboard_inputs

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

        from .. import pipeline as B
        from ..pipeline import extract_characters, extract_locations, plan_wardrobe

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
        from ..pipeline._helpers import _card_context

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

    @app.post("/api/stories/seed-from-card")
    def seed_from_card(body: dict):
        """Card -> MATURE SEED: a full story foundation to roleplay from — the inferred archetype, a
        grounded world + quiet central question, a continuous backstory/trauma (on a prose model), psych,
        and an OPENING scene. Body: { character: <key> } OR { card: <raw text> }, plus optional
        { model, backstory_model }. Reuses loom.stories.story_seed.build_seed."""
        from ..pipeline._helpers import _card_context
        from ..world.creation import build_seed

        body = body or {}
        card_text = (body.get("card") or "").strip()
        if not card_text:
            key = body.get("character")
            c = ctx.base_settings.characters.get(key) if key else None
            if c is None:
                return JSONResponse({"error": "provide `card` text or a valid `character` key"}, status_code=400)
            card_text = _card_context(c.name, c.system, ctx.card_extras(c, key))
        prov = ctx.text_provider_for(body.get("model") or "minimax/minimax-m3", {"reasoning_effort": "low"})
        if prov is None:
            return JSONResponse({"error": "no text provider — connect a model first"}, status_code=400)
        # the backstory wants prose over structure: deepseek-v4-pro (no-JSON) is strong + cheap here
        bprov = ctx.text_provider_for(body.get("backstory_model") or "deepseek/deepseek-v4-pro",
                                      {"reasoning_effort": "low"})
        try:
            seed = build_seed(prov, card=card_text, backstory_provider=bprov)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"seed failed: {exc}"}, status_code=500)
        if not seed:
            return JSONResponse({"error": "the card yielded no usable seed"}, status_code=500)
        return {"seed": seed}

    # The premise as a CAUSAL ENGINE, not a flat checklist — the Rewrite structure: one root
    # pressure at the base, everything else DERIVED from it. A dying ecology forces a question
    # (is humanity worth its cost to the world?), the question splits into competing FAITHS that
    # each believe they're saving everyone, and the clash is TRAGIC because both are partly right.
    # Ordered so each component follows from the one above; the drafter builds down the chain.
    _PREMISE_COMPONENTS = [
        ("root", "The root pressure",
         "the ONE standing force the whole story grows from — a world-level condition, not an event "
         "(an ecology at its limit, a faith in decline, a power running out). Everything below derives "
         "from it. Rewrite's is a planet that can no longer afford humanity; Embergloom's is fading magic."),
        ("question", "The question it forces",
         "the unanswerable moral question the root pressure puts to everyone, with two GENUINELY "
         "defensible answers — a real contest, not a theme-word (is humanity worth its cost to the "
         "world? is a father's life worth a kingdom's?). Characters embody the sides through their choices."),
        ("creeds", "The competing creeds",
         "the organized answers — the factions / faiths / orders that each embody one side of the "
         "question and believe they are SAVING everyone. This is where religion and ideology enter as "
         "STRUCTURE, not decoration. Each is internally righteous; there are no villains, only sides."),
        ("tragedy", "The tragic bind",
         "why the creeds cannot both win and why each is sympathetic — the reason the conflict destroys "
         "good people instead of resolving cleanly. Both are partly right; any victory is also a loss."),
        ("protagonist", "The protagonist in the crossfire",
         "the specific person caught between the creeds (not a type), and the LIE they live by that the "
         "conflict will test — where they start, and what belief the story will break in them."),
        ("stakes", "Stakes",
         "what is concretely lost if it goes wrong — at the scale of the WORLD and of this one person."),
        ("texture", "Tone & texture", "the mood, genre and sensory feel through which the system is lived"),
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
        # WORLD-FIRST: premise & theme is DISTILLED from the defined world, not struck before it. The
        # persisted world is the foundation — root ← its pressure/ache, creeds ← its forces, the rest
        # earned from the whole world + cast. A thin/empty world means there's little to distil from.
        from ..world.creation import world_full_brief
        world_block = world_full_brief(sd.get("world"))
        ctx_text = "\n".join(filter(None, [
            "THE DEFINED WORLD (the foundation — distil every component FROM it):\n" + world_block
            if world_block else "THE WORLD IS NOT YET DEFINED — say so; premise & theme should be built "
                                "AFTER the world, not before it.",
            f"Premise (a working synopsis, if any): {sd.get('premise') or '(empty)'}",
            f"Logline: {(sd.get('storyboard') or {}).get('logline') or ''}",
            f"Tone: {sd.get('tone') or ''}",
            f"Themes: {', '.join(sd.get('themes') or [])}",
            f"Cast: {cast}",
            f"Components already written:\n{written}" if written else "",
        ]))
        comp_lines = "\n".join(f"- {cid} ({label}): {desc}" for cid, label, desc in want)
        # NON-THINKING writer (the codebase paradigm): the causal structure lives in the architect
        # system prompt, not in visible CoT. v4pro at high reasoning BLEEDS its chain-of-thought into
        # the text field ("I notice the request asks…") — the scaffold does the thinking, not the model.
        provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no text model available"}, status_code=400)
        schema = {"type": "object", "additionalProperties": False, "required": ["parts"],
                  "properties": {"parts": {"type": "array", "items": {
                      "type": "object", "additionalProperties": False, "required": ["id", "text"],
                      "properties": {"id": {"type": "string", "enum": [c[0] for c in want]},
                                     "text": {"type": "string", "description": "1-2 concrete sentences"}}}}}}
        system = ("You are a story's THEMATIC ARCHITECT. Premise & theme is DISTILLED FROM THE DEFINED "
                  "WORLD above — never invented before it, never striking at the tragedy before the world "
                  "earns it. Read the whole world (its pressure/ache, its forces, its traditions, people, "
                  "and lived fragments) and DERIVE the causal engine from it: the `root` IS the world's "
                  "pressure/ache in one clean line; the `creeds` ARE the world's forces (use their names + "
                  "stances); the `question` is what that pressure asks of everyone; the `tragedy` is why "
                  "those forces cannot both win; protagonist/stakes/texture follow from the world + cast. "
                  "Do not add factions the world doesn't have. Draft each requested component as 1-2 "
                  "concrete sentences that FOLLOW FROM the world and the components already written — name "
                  "the world's names, pick its particulars, no vague archetypes. The creeds must each be "
                  "sympathetic and internally righteous (no villains, only sides); the tragedy must come "
                  "from both sides being partly right. NAMES: "
                  "every faction, creed, religion, order or organization is ONE coined word — never two "
                  "words, never 'The <Adjective> <Noun>' (Crownsworn, Unbound, Emberwake — NOT 'Harvest "
                  "Binding', NOT 'Severance Witnesses'). Stay consistent with what's written.")
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
        from ..world.creation import design_world
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
        from ..world.creation import regen_world_field
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
        from ..world.creation import design_harnesses
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        seed = body.get("seed", "")
        # Ground the cast in the SAME adaptation basis the workshop uses (wound→lie→coping) — so
        # genesis characters have real depth, not random traits. See GENESIS.md §6.
        from ..pipeline import grounding as _G
        try:
            psyche = _G.ADAPTATION
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
        from ..world.creation import design_by_role
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        seed = body.get("seed", "")
        from ..pipeline import grounding as _G
        try:
            psyche = _G.ADAPTATION
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
        from ..world.creation import accrete_systems, scenario_from_systems, gen_particulars
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
        from ..world.creation import build_premise
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
        from ..world.creation import build_substrate
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
        from ..world.creation import loop_scene
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
        from ..world.creation import (run_genesis, GenesisState, GenesisDeps, genesis_state_from,
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
            # Ground the cast in the adaptation basis (wound→lie→coping, same as genesis_roles).
            from ..pipeline import grounding as _G
            try:
                psyche = _G.ADAPTATION
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
        from ..world.creation import run_systems, SystemsState, GenesisDeps
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
        from ..world.creation import weave_relationships
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
        from ..world.creation import formalize_harness
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
        from ..world.creation import suggest_potentials
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
            from ..pipeline._helpers import _TAG_RULE
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
        from ..world.creation import derive_stories
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
        from ..server.services import story_store as _SS
        from ..world.creation import name_cast, persona_from_harness, compose_world

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

        # New stories are born as ONE self-contained <skey>.json with characters EMBEDDED. Compute
        # the key FIRST + create the file so write_npc can embed each anchor into it. See story_db.py.
        ctx.story_dir().mkdir(parents=True, exist_ok=True)
        name = (body.get("name") or cand.get("title") or "Story").strip()
        existing_names = {st.name for st in ctx.base_settings.stories.values()}
        base_name, j = name, 2
        while name in existing_names:
            name, j = f"{base_name} ({j})", j + 1
        skey_base = re.sub(r"[^\w\-]+", "_", name.lower()).strip("_") or "story"
        skey, i = skey_base, 2
        while _SS.story_exists(ctx.root, skey):
            skey, i = f"{skey_base}_{i}", i + 1
        stype = body.get("type") if body.get("type") in ("novel", "vn") else "novel"
        # The DEFINED WORLD survives commit now (it used to be discarded) — the permanent foundation
        # premise & theme distils from. Composed from the genesis draft: frame + substrate + particulars.
        world = compose_world(body.get("world"), body.get("substrate"), body.get("particulars"))
        story_dict = {
            "name": name, "type": stype, "premise": cand.get("premise", ""),
            "tone": cand.get("tone", ""), "themes": cand.get("themes") or [],
            "storyboard": {"logline": cand.get("logline", "")}, "world": world,
            "fields": {"source": "genesis", "dramatic_question": cand.get("dramatic_question", "")},
        }
        from ..config.schema import Story
        Story(**story_dict)   # validate before writing — closes the genesis bypass
        ctx.story_dir().mkdir(parents=True, exist_ok=True)   # keep the folder for assets
        _SS.save_story(ctx.root, skey, story_dict, {})

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
                "contradiction": h.get("contradiction", ""),
                "wound": h.get("wound", ""), "secret": h.get("secret", ""),
            }, story_key=skey)                            # embeds into <skey>.json

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
            _SS.delete_story(ctx.root, skey)   # rollback: drop the half-written story from the store
            return JSONResponse({"error": f"could not save story: {exc}"}, status_code=400)
        return {"ok": True, "key": skey}

    @app.post("/api/stories/{key}/genesis/add")
    def genesis_add(key: str, body: dict):
        """ADD generated characters to an EXISTING story (the in-Structure generate tools): name the
        confirmed harnesses, create characters, append to the cast + relationships. Same naming/
        write_npc path as commit, but onto a live story. Body: { harnesses, relationships? } → { ok }."""
        from ..world.creation import name_cast, persona_from_harness

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
                "contradiction": h.get("contradiction", ""),
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
        from ..pipeline import extract_locations

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
        from ..pipeline import extract_characters, extract_protagonist

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, systems = ctx.builder_ctx(body, "characters")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        extras = ctx.card_extras(ch, body["character"])
        from ..pipeline import grounding as _G
        _craft = _G.MINIMALISM
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
            from ..pipeline import (compose_base_prompt as _compose_base_prompt,
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

        from ..pipeline import grounding as _G
        _arc_craft = _G.MINIMALISM
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

        from ..pipeline import parse_storyboard
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
        from ..pipeline import grounding as _G
        _exp_craft = _G.MINIMALISM

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
            "(never a generic 'greater good'). Apply the guidance below.\n"
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

        from ..pipeline import parse_storyboard
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

        from ..pipeline import parse_storyboard

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
        from ..pipeline._helpers import _card_context
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
    # and stories persist as one <key>.json each. See loom/stories/story_db.py.)


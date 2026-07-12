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
from ..graph_pipeline import StoryState, StoryDeps, run_turn, run_draft

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
        from ..server.services import config_files as _cf
        from .pipeline.character_scaffold import (
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
        from .pipeline.character_scaffold import (
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
        from .pipeline.character_scaffold import (
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

    @app.post("/api/stories/seed-from-card")
    def seed_from_card(body: dict):
        """Card -> MATURE SEED: a full story foundation to roleplay from — the inferred archetype, a
        grounded world + quiet central question, a continuous backstory/trauma (on a prose model), psych,
        and an OPENING scene. Body: { character: <key> } OR { card: <raw text> }, plus optional
        { model, backstory_model }. Reuses loom.stories.story_seed.build_seed."""
        from .pipeline._helpers import _card_context
        from .story_seed import build_seed

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

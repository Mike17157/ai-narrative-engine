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
        system, prompt = storyboard_inputs(name=ch.name, persona=ch.system,
                                           extras=ctx.card_extras(ch, body["character"]),
                                           systems=systems,
                                           premise=(body.get("premise") or "").strip(),
                                           spine=spine)

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

    @app.post("/api/stories/spine")
    async def story_spine(body: dict):
        """Generate the EMOTIONAL SPINE for a character — wound / lie / truth / beats.
        Streams delta text while the model writes, then emits a final `spine` event.

        Body: { character: str, premise?: str, intended_ending?: str }
        """
        import asyncio
        import threading

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from .pipeline._helpers import SPINE_SCHEMA, _card_context, _sys

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)

        provider, systems = ctx.builder_ctx(body, "spine")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        extras = ctx.card_extras(ch, body["character"])
        card = _card_context(ch.name, ch.system, extras)
        premise = (body.get("premise") or "").strip()
        intended_ending = (body.get("intended_ending") or "").strip()
        system = _sys(systems, "spine")

        parts = [f"CHARACTER CARD:\n{card}"]
        if premise:
            parts.append(f"STORY PREMISE:\n{premise}")
        if intended_ending:
            parts.append(f"INTENDED ENDING:\n{intended_ending}")
        parts.append(
            "Read this character deeply. Reveal the WOUND already present in who they are. "
            "Derive the LIE they tell themselves because of it. Find the TRUTH they must accept. "
            "Map the emotional beats — the psychological stations — that would take them from lie to truth. "
            "Output structured JSON only."
        )
        prompt = "\n\n".join(parts)

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()

        def on_delta(t: str):
            loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": t})

        async def run():
            try:
                res = await run_in_threadpool(lambda: provider.generate_text(
                    system=system, prompt=prompt, emits=SPINE_SCHEMA,
                    on_delta=on_delta, cancel=cancel_evt.is_set))
                if not cancel_evt.is_set():
                    spine = res.data or {}
                    if not spine:
                        loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": "no spine produced"})
                    else:
                        loop.call_soon_threadsafe(q.put_nowait, {"type": "spine", "spine": spine})
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

    @app.post("/api/stories/extract-scenes")
    def story_extract_scenes(body: dict):
        """Stage 2 — extract neutral locations (pure backgrounds) from the beats."""
        from .pipeline import extract_locations

        provider, systems = ctx.builder_ctx(body or {}, "locations")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        board = (body or {}).get("board") or {}
        try:
            return extract_locations(provider, board=board, systems=systems)
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
        try:
            base = extract_protagonist(provider, name=ch.name, persona=ch.system or "",
                                       extras=extras, systems=systems)
            out = extract_characters(provider, name=base["name"], persona=base["persona"],
                                     board=body.get("board") or {}, extras=extras,
                                     systems=systems,
                                     reference_card=base["persona"])
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
            system = system + (
                "\n\nCURRENT WORKING STORY GRAPH (the writer may have edited this in the graph "
                "pane; treat their edits as authoritative and continue from them):\n"
                + json.dumps(working_spine, ensure_ascii=False)
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

    @app.post("/api/stories/graph-ops")
    def story_graph_ops(body: dict):
        """Data-driven graph FUNCTIONS. Attached *function books* (lorebooks whose entries
        carry a function spec) define operations on the development graph. The transcript's
        trigger terms OFFER the relevant functions; the model DECIDES which to call (emits
        `graph_ops`); we apply them to the working graph and return it. See graph_ops.py."""
        from ..server.services import lorebook_store as _LS
        from . import graph_ops as GO

        body = body or {}
        graph = body.get("graph") if isinstance(body.get("graph"), dict) else {}
        messages = body.get("messages") or []
        transcript = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))

        # Collect function definitions from every attached book (function entries only).
        fns: list = []
        for b in (body.get("lorebooks") or []):
            scope = re.sub(r"[^\w\-]+", "_", str(b))
            fns += GO.parse_functions(_LS.load_lorebook(ctx.root, scope))
        off = GO.offered(fns, transcript)
        if not off:
            return {"ok": True, "graph": graph, "applied": [], "offered": []}

        # Provider follows the SAME chain as the chat turn: Function → Lorebook → Preset.
        # The attached function book binds a preset (model+params); that drives the ops call
        # — it never crosses over to the free-chat config. Falls back to a builder stage /
        # the workshop default only when no book is bound.
        from ..server.services import presets as _P
        script = body.get("script")
        preset = _P.preset_for_books(ctx.root, body.get("lorebooks"))
        if preset is not None:
            provider = ctx.text_provider_for((preset.get("model") or "").strip() or None,
                                             preset.get("params") or {},
                                             connection=preset.get("connection") or None)
        else:
            provider, _systems = ctx.builder_ctx(body, script or "workshop")
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no chat connection — connect a chat model first"}, status_code=400)

        label = (body.get("artifact_label") or "DOCUMENT").strip()
        system = (
            f"You edit a JSON {label} by calling functions, filling each function's params from "
            "the conversation.\n\n"
            + GO.functions_prompt(off)
            + f"\n\nCURRENT {label}:\n" + json.dumps(graph, ensure_ascii=False)
            + "\n\nEmit `graph_ops` — the function calls that apply the changes the writer asked "
            "for. Fill EVERY param each function needs (never leave a name/value blank) and use "
            "EXACT ids from the document above. When you ADD a new item, set ALL its fields in "
            "that same add call's params — a new item's id is assigned afterward, so you cannot "
            "reference it later in the same batch. Emit an empty list if no change is called for. "
            "Only call functions from the list above by their exact name; never invent names."
        )
        prompt = transcript or f"Apply the appropriate functions to the {label.lower()}."
        try:
            data = provider.generate_text(system=system, prompt=prompt, emits=GO.ops_schema(off)).data or {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"graph-ops failed: {exc}"}, status_code=500)
        new_graph, log = GO.apply_ops(graph, data.get("graph_ops") or [], fns)
        return {"ok": True, "graph": new_graph, "applied": log, "offered": [f.name for f in off]}

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

        prompt = (
            f"PREMISE CONVERSATION:\n{transcript}\n\n"
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
        import yaml as _yaml
        st_path = ctx.root / "configs" / "stories" / f"{key}.yaml"
        if not st_path.is_file():
            return JSONResponse({"error": "no such story"}, status_code=404)
        raw = _yaml.safe_load(st_path.read_text(encoding="utf-8")) or {}
        arcs = raw.get("arcs") or []
        arc_entry = next((a for a in arcs if a.get("id") == arc_id), None)
        if arc_entry is None:
            return JSONResponse({"error": f"no arc '{arc_id}'"}, status_code=404)
        body = body or {}
        for field in ("name", "mini_ending", "dramatic_function", "cast"):
            if field in body:
                arc_entry[field] = body[field]
        raw["arcs"] = arcs
        st_path.write_text(_yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        ctx.reload_settings()
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

        # Collect context: heart, intended_ending, preceding arcs' mini_endings, arc cast details.
        heart = st.storyboard.heart or ""
        intended_ending = st.intended_ending or ""

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
            "The final chapter must land on this arc's mini_ending.\n"
            "Characters in each chapter must be a subset of this arc's cast — no one else."
        )

        # Spine context — informs which emotional inflections this arc should force.
        spine = st.spine
        spine_block = ""
        if spine and (spine.wound or spine.beats):
            lines = ["EMOTIONAL SPINE (character psychology — shape chapters to force these inflections):"]
            if spine.wound:
                lines.append(f"  WOUND: {spine.wound}")
            if spine.lie:
                lines.append(f"  LIE: {spine.lie}")
            if spine.truth:
                lines.append(f"  TRUTH: {spine.truth}")
            if spine.beats:
                lines.append("  BEATS:")
                for b in spine.beats:
                    lines.append(f"    [{b.inflection}] {b.description}")
            spine_block = "\n".join(lines) + "\n\n"

        prompt = (
            f"BOOK HEART: {heart or '(not set)'}\n"
            f"INTENDED ENDING: {intended_ending or '(not set)'}\n\n"
            f"{spine_block}"
            f"PREVIOUS ARCS:\n{prev_endings}\n\n"
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

                    # Persist the expanded chapters to disk.
                    safe = re.sub(r"[^\w\-]+", "", key)
                    path = ctx.story_dir() / f"{safe}.yaml"
                    try:
                        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
                            path.write_text(
                                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                encoding="utf-8"
                            )
                            ctx.reload_settings()
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
                # Build spine block for timeline derivation if the story has one.
                spine = st.spine
                spine_tl_block = ""
                if spine and (spine.wound or spine.beats):
                    lines = ["EMOTIONAL SPINE (the axis timelines diverge along):"]
                    if spine.wound:
                        lines.append(f"  WOUND: {spine.wound}")
                    if spine.lie:
                        lines.append(f"  LIE: {spine.lie}")
                    if spine.truth:
                        lines.append(f"  TRUTH: {spine.truth}")
                    if spine.beats:
                        lines.append("  BEATS to force:")
                        for b in spine.beats:
                            lines.append(f"    [{b.inflection}] {b.description}")
                    spine_tl_block = "\n".join(lines) + "\n\n"

                derive_prompt = (
                    f"PROTAGONIST PERSONA:\n{prot_persona[:800] or '(not set)'}\n\n"
                    f"{spine_tl_block}"
                    f"ARC: {arc.name}\n"
                    f"  Dramatic function: {arc.dramatic_function or '(unset)'}\n"
                    f"  Mini-ending: {arc.mini_ending or '(unset)'}\n\n"
                    f"CAST:\n{cast_block}\n\n"
                    f"STORY HEART: {st.storyboard.heart or '(unset)'}\n\n"
                    "If an emotional spine is provided, use it as the divergence axis — timelines "
                    "should represent different ways the protagonist could face (or avoid) the "
                    "emotional beats. Otherwise derive the axis from the persona directly.\n\n"
                    "Derive the divergence axis and 2-3 timeline premises now."
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

                # ── Persist ───────────────────────────────────────────────
                safe = re.sub(r"[^\w\-]+", "", key)
                path = ctx.story_dir() / f"{safe}.yaml"
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
                    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
                    ctx.reload_settings()
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

    # ── Draft persistence ────────────────────────────────────────────────────── #
    # Drafts store the full wizard state server-side so in-progress stories
    # survive localStorage clearing and show up in the library.

    def _drafts_dir():
        return ctx.story_dir() / "drafts"

    @app.post("/api/stories/draft")
    def save_draft(body: dict):
        import uuid as _uuid
        from datetime import datetime, timezone
        d = body or {}
        raw_id = d.get("id") or f"draft_{_uuid.uuid4().hex[:8]}"
        safe_id = re.sub(r"[^\w\-]+", "", raw_id)
        drafts = _drafts_dir()
        drafts.mkdir(parents=True, exist_ok=True)
        d["id"] = safe_id
        d["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        (drafts / f"{safe_id}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "id": safe_id}

    @app.get("/api/stories/draft/{draft_id}")
    def get_draft(draft_id: str):
        safe_id = re.sub(r"[^\w\-]+", "", draft_id)
        p = _drafts_dir() / f"{safe_id}.json"
        if not p.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return json.loads(p.read_text(encoding="utf-8"))

    @app.delete("/api/stories/draft/{draft_id}")
    def delete_draft(draft_id: str):
        safe_id = re.sub(r"[^\w\-]+", "", draft_id)
        p = _drafts_dir() / f"{safe_id}.json"
        if p.exists():
            p.unlink()
        return {"ok": True}

    @app.post("/api/stories")
    def save_story(body: dict):
        """Materialize an accepted draft: create Character files for proposed NPCs,
        remap cast names→keys, link beats to locations, and write the Story."""
        from ..config.schema import Story

        draft = body or {}
        name = (draft.get("name") or "Story").strip()
        primary_key = draft.get("source_character") or draft.get("character")
        if primary_key and primary_key not in ctx.base_settings.characters:
            primary_key = None

        # If the caller provided an existing story key, overwrite that story in place
        # rather than deduplicating the name/key. This happens when re-generating an
        # existing story from the wizard (regenStory flow).
        existing_key = re.sub(r"[^\w\-]+", "", draft.get("existing_key") or "")
        overwriting = existing_key and (ctx.story_dir() / f"{existing_key}.yaml").is_file()

        if overwriting:
            skey = existing_key
            # Keep the existing story's name if the user didn't rename it.
            existing_st = ctx.base_settings.stories.get(skey)
            if existing_st and not draft.get("name"):
                name = existing_st.name
        else:
            # Ensure a UNIQUE display name so the story list isn't full of identically-named entries.
            existing_names = {st.name for st in ctx.base_settings.stories.values()}
            if name in existing_names:
                base_name, n = name, 2
                while name in existing_names:
                    name, n = f"{base_name} ({n})", n + 1

            # Decide the story key first so NPCs can be tagged with it.
            skey_base = re.sub(r"[^\w\-]+", "_", name.lower()).strip("_") or "story"
            skey, i = skey_base, 2
            while (ctx.story_dir() / f"{skey}.yaml").is_file():
                skey, i = f"{skey_base}_{i}", i + 1

        created: list[str] = []
        from .pipeline import extract_protagonist

        # UNIFIED CAST — ONE ordered list of members; the protagonist is simply the member flagged
        # `primary` (no separate "main character card" path). The wizard sends `cast`; older drafts
        # sent `primary_card` + `proposed_npcs`, still accepted for compatibility.
        cast_in = draft.get("cast")
        if cast_in is None:
            cast_in = ([{**draft["primary_card"], "primary": True}] if draft.get("primary_card") else []) \
                + [{**n, "primary": False} for n in (draft.get("proposed_npcs") or [])]
        cast_in = [dict(m) for m in cast_in if isinstance(m, dict)]

        # Always guarantee a protagonist: if a source character is set but no member is flagged
        # primary, distil one faithfully from the source card and prepend it. (The original library
        # card stays free; the protagonist is a fresh story-bound character like everyone else.)
        src = ctx.base_settings.characters.get(primary_key) if primary_key else None
        if src is not None and not any(m.get("primary") for m in cast_in):
            base_card = None
            try:
                provider, systems = ctx.builder_ctx(draft, "characters")
                if provider is not None:
                    base_card = extract_protagonist(
                        provider, name=src.name, persona=src.system or "",
                        extras=ctx.card_extras(src, primary_key), systems=systems)
            except Exception:  # noqa: BLE001
                base_card = None
            if not base_card:
                base_card = {"name": src.name, "persona": src.system or "",
                             "appearance": (src.fields or {}).get("appearance", ""),
                             "role": (src.fields or {}).get("role") or "protagonist"}
            cast_in.insert(0, {**base_card, "primary": True})

        # Compose the RICH base-image prompt via the shared ✨ pipeline (the single appearance
        # authority) for EVERY member IN PARALLEL — reuse one already on the entry, only compose the
        # missing ones. Same path as the ✨ button and regenerate_cast.
        from concurrent.futures import ThreadPoolExecutor
        from .pipeline import compose_base_prompt as _compose_base_prompt
        _bp_cfg = ctx.load_story_builder()
        _bp_prov = ctx.stage_provider("base_image")
        _bp_sys = (_bp_cfg.get("systems") or {})

        def _bp(item):
            idx, p = item
            existing = (p.get("base_prompt") or "").strip()
            if existing:
                return (idx, existing, p.get("height_cm"))
            try:
                r = _compose_base_prompt(_bp_prov, p.get("name", ""), p.get("persona", ""),
                                         p.get("appearance", ""), p.get("role", ""),
                                         systems=_bp_sys)
                if not isinstance(r, dict):
                    return (idx, "", p.get("height_cm"))
                return (idx, r.get("prompt", ""),
                        (r.get("features") or {}).get("height_cm") or p.get("height_cm"))
            except Exception:  # noqa: BLE001
                return (idx, "", p.get("height_cm"))

        bps: dict[int, str] = {}
        if cast_in:
            with ThreadPoolExecutor(max_workers=min(len(cast_in), 6)) as ex:
                for idx, pr, h in ex.map(_bp, list(enumerate(cast_in))):
                    bps[idx] = pr
                    if h:
                        cast_in[idx]["height_cm"] = h   # so write_npc persists it

        # Write EVERY member through the SAME _write_npc path — the protagonist differs ONLY by the
        # `primary` flag. Its base image is generated like everyone else's (no ref_from copy of the
        # source art); the source card remains the style anchor via story.fields.source_character.
        cast = []
        seen_primary = False
        for idx, member in enumerate(cast_in):
            is_primary = bool(member.get("primary")) and not seen_primary
            seen_primary = seen_primary or is_primary
            k = ctx.write_npc(member, story_key=skey, base_prompt=bps.get(idx, ""))
            created.append(k)
            cast.append({"character": k, "primary": is_primary})
            # Seed the portrait manifest with emotions generated during character extraction —
            # this runs before wardrobe planning so affect.range is ready for outfit-pose generation.
            seed: dict = {}
            if isinstance(member.get("expressions"), dict) and member["expressions"]:
                seed["expressions"] = member["expressions"]
            if isinstance(member.get("affect"), dict) and member["affect"].get("range"):
                seed["affect"] = member["affect"]
            if seed:
                try:
                    _apply_manifest(ctx, k, seed, compose_persona=False)
                except Exception:  # noqa: BLE001
                    pass

        # Locations are self-contained neutral places — no cast remapping needed.
        locations = []
        name_to_loc: dict[str, str] = {}
        for l in draft.get("locations", []) or []:
            lid = l.get("id")
            locations.append({
                "id": lid, "name": l.get("name", lid),
                "description": l.get("description", ""),
                "background_prompt": l.get("background_prompt", ""),
                "background": l.get("background"),
            })
            if l.get("name"):
                name_to_loc[l["name"].lower()] = lid

        # Persist the storyboard as the spine; link each beat's place to a location id.
        # New beats include emotional_core, hook, and scene_prompt from the enriched format.
        board = draft.get("storyboard") or {}
        beats = []
        for b in board.get("beats", []) or []:
            loc = (b.get("location") or "")
            beat: dict = {
                "title": b.get("title", ""), "summary": b.get("summary", ""),
                "location": name_to_loc.get(loc.lower(), loc),
                "characters": b.get("characters", []),
            }
            # Preserve enriched fields if present (new 7-field format).
            if b.get("emotional_core"):
                beat["emotional_core"] = b["emotional_core"]
            if b.get("hook"):
                beat["hook"] = b["hook"]
            if b.get("scene_prompt"):
                beat["scene_prompt"] = b["scene_prompt"]
            beats.append(beat)
        storyboard = {"heart": board.get("heart", ""), "logline": board.get("logline", ""), "beats": beats}

        # Validate arcs if provided.
        from ..config.schema import Arc as ArcModel
        arcs_raw = draft.get("arcs") or []
        arcs_validated = []
        for arc_dict in arcs_raw:
            if isinstance(arc_dict, dict):
                try:
                    arcs_validated.append(ArcModel(**arc_dict).model_dump())
                except Exception:  # noqa: BLE001
                    arcs_validated.append(arc_dict)

        story = {
            "name": name, "premise": draft.get("premise", ""), "tone": draft.get("tone", ""),
            "themes": draft.get("themes", []), "storyboard": storyboard, "cast": cast,
            "lorebook": draft.get("lorebook") or {}, "locations": locations,
            "start": draft.get("start"), "background": draft.get("background"),
            "fields": {"source_character": primary_key} if primary_key else {},
            "intended_ending": draft.get("intended_ending", ""),
            "arcs": arcs_validated,
            "spine": draft.get("spine") or None,
        }
        try:
            Story(**story)  # validate (start ∈ locations, cast ∈ characters)
            ctx.story_dir().mkdir(parents=True, exist_ok=True)
            (ctx.story_dir() / f"{skey}.yaml").write_text(
                yaml.safe_dump(story, allow_unicode=True, sort_keys=False), encoding="utf-8")
            ctx.reload_settings()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save story: {exc}"}, status_code=400)
        return {"ok": True, "key": skey, "created_characters": created}

    @app.get("/api/stories")
    def list_stories() -> list:
        out = []
        for k, st in ctx.base_settings.stories.items():
            f = ctx.story_dir() / f"{k}.yaml"
            mtime = f.stat().st_mtime if f.is_file() else 0.0
            out.append({"key": k, "name": st.name, "premise": st.premise, "tone": st.tone,
                        "themes": st.themes, "locations": len(st.locations), "start": st.start,
                        "cast": [m.character for m in st.cast], "_mtime": mtime})
        out.sort(key=lambda s: s["_mtime"], reverse=True)  # newest first
        for s in out:
            s.pop("_mtime", None)
        # Append in-progress drafts (newest first).
        drafts = _drafts_dir()
        if drafts.is_dir():
            draft_list = []
            for p in drafts.glob("*.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    draft_list.append({**d, "draft": True, "_mtime": p.stat().st_mtime})
                except Exception:
                    pass
            draft_list.sort(key=lambda x: x.get("_mtime", 0), reverse=True)
            for d in draft_list:
                d.pop("_mtime", None)
            out = draft_list + out
        return out

    @app.get("/api/stories/{key}")
    def get_story(key: str):
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        return {**st.model_dump(), "key": key}

    @app.put("/api/stories/{key}")
    def update_story(key: str, body: dict):
        """Edit a saved story in place (iterate). Updates only the fields sent;
        cast members are existing character keys, so no NPCs are re-created."""
        from ..config.schema import Story

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        data = st.model_dump()
        for f in ("name", "premise", "tone", "themes", "storyboard", "cast",
                  "lorebook", "locations", "start", "background", "fields",
                  "intended_ending", "arcs", "spine"):
            if f in (body or {}):
                data[f] = body[f]
        try:
            Story(**data)
            safe = re.sub(r"[^\w\-]+", "", key)
            (ctx.story_dir() / f"{safe}.yaml").write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            ctx.reload_settings()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "key": key}

    @app.delete("/api/stories/{key}")
    def delete_story(key: str):
        safe = re.sub(r"[^\w\-]+", "", key)
        p = ctx.story_dir() / f"{safe}.yaml"
        if not p.is_file():
            return JSONResponse({"error": "no such story"}, status_code=404)
        p.unlink()
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
            safe = re.sub(r"[^\w\-]+", "", key)
            path = ctx.story_dir() / f"{safe}.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data["cast"] = cast
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            ctx.reload_settings()
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
        opts = {**tconn.to_model_options(), "max_tokens": 1500}
        if body.get("chat_model"):
            opts["model"] = body["chat_model"]
        provider = build_provider(ModelDef(provider=tconn.provider, kind="text", options=opts))

        # Compose the director's brief from the story.
        from ..server.services.emotions import EMOTION_KEYS, NORMAL_KEYS

        def _char_emotion_keys(char_key: str) -> list[str]:
            """Return this character's affect.range keys (new or old manifest format)."""
            mf = ctx.portrait_manifest(char_key)
            raw = (mf.get("affect") or {}).get("range") if isinstance(mf.get("affect"), dict) else None
            if isinstance(raw, list) and raw:
                if isinstance(raw[0], dict):
                    return [e["emotion"] for e in raw if e.get("emotion") in EMOTION_KEYS]
                return [k for k in raw if k in EMOTION_KEYS]
            return list(NORMAL_KEYS)

        def cast_line(m):
            c = ctx.base_settings.characters.get(m.character)
            name = c.name if c else m.character
            desc = ((c.system or "").splitlines()[0] if c else "")[:140]
            keys = _char_emotion_keys(m.character)
            return f"- {name}: {desc}\n  emotions: {', '.join(keys)}"

        cast = "\n".join(cast_line(m) for m in st.cast) or "(none)"
        locs = "\n".join(f"- {l.id} | {l.name}: {l.description}" for l in st.locations) or "(none)"
        # World lore is no longer dumped here — it lives in the `story-<key>` book and is
        # retrieved (ranked) into the WORLD INFO block below, like every other lorebook.
        cur = body.get("location") or st.start or (st.locations[0].id if st.locations else "")
        # The protagonist. The frontend passes the active persona (who *you* are);
        # fall back to a generic "Player" so old callers still work. A named player
        # with a description lets the director narrate to a real identity instead of
        # a nameless "you", and the transcript reflects that name.
        player = body.get("player") or {}
        player_name = (player.get("name") or "Player").strip() or "Player"
        player_desc = (player.get("description") or "").strip()
        player_line = f"PLAYER: {player_name}" + (f" — {player_desc}" if player_desc else "")
        system = (
            f"You are the narrator and director of an interactive visual novel titled \"{st.name}\".\n"
            f"PREMISE: {st.premise}\nTONE: {st.tone}\n"
            + f"{player_line}\n"
            f"CAST (use these names):\n{cast}\n"
            f"LOCATIONS (the scene is in exactly one; use the id):\n{locs}\n\n"
            "Narrate the next moment in-world and in the established tone, responding to the player. "
            "Then report the scene state in your structured output:\n"
            "- reply: the narration (second person to the player, plus character action/dialogue). Vivid but concise.\n"
            "- location: the id of the location the scene is currently in (one of the listed ids).\n"
            "- present: ALWAYS list the names of EVERY cast character physically in the scene right now "
            "(anyone who speaks, acts, or is described as present) — never leave it empty if someone is there.\n"
            "- emotions: for each present character, pick the ONE emotion key from their listed emotions "
            "that best matches how they feel right now. Use the exact key string — it selects their "
            "portrait sprite directly. Judge honestly from the moment; default to 'neutral' if unsure.\n"
            "- movement: true ONLY when this moment invites the player to move to a different location "
            "(they suggest leaving, a path opens, the beat concludes) — otherwise false."
        )

        history = body.get("history") or []
        lines = []
        for m in history:
            who = player_name if m.get("role") == "user" else "Narrator"
            lines.append(f"{who}: {m.get('text', '')}")
        transcript = "\n".join(lines) or "(the story is just beginning)"

        # NSFW injection: when the recent transcript hits trigger keywords, prepend the
        # matching guidance — `_nsfw` (general intimacy style note) + `_nsfw_acts` (specific
        # act/position guides). Deterministic word-match; capped so the prompt stays lean.
        # All editable as normal lorebook entries (PUT /api/lorebook/_nsfw[_acts]).
        from ..server.services import lorebook_store as _LS
        _recent = " ".join(str(m.get("text", "")) for m in history[-3:]).lower()
        _inject: list[str] = []
        for _scope in ("_nsfw", "_nsfw_acts"):
            for _e in _LS.load_lorebook(ctx.root, _scope):
                if not (_e.enabled and _e.content):
                    continue
                if any(re.search(rf"\b{re.escape(k.lower())}\b", _recent) for k in _e.keywords if k):
                    _inject.append(_e.content)
                    if len(_inject) >= 4:
                        break
            if len(_inject) >= 4:
                break
        if _inject:
            system = "\n\n".join(_inject) + "\n\n" + system

        # Canon retrieval: attached lorebooks (world/RPG/story books pinned to this thread)
        # PLUS the thread's own established-facts scope (engine write-back). Inject what the
        # recent transcript calls for as a WORLD INFO block.
        from ..server.services.lorebook import format_lore_block
        attached = body.get("lorebooks")
        world_scopes = [re.sub(r"[^\w\-]+", "_", str(s)) for s in (attached or []) if s]
        world_scopes += [story_scope, thread_scope]   # the story's own book + this thread's facts
        hits = _LS.retrieve(ctx.root, _recent or transcript, world_scopes, top_k=6)
        if hits:
            system = system + "\n\n" + format_lore_block(hits)

        # WORLD STATE: the mutable working memory of this playthrough (read every turn).
        from . import state_engine as _SE
        _state_block = _SE.render_state(world_state)
        if _state_block:
            system = system + "\n\n" + _state_block
        system = system + (
            "\n\nAlso report `state_deltas`: a list of update objects for what changed THIS turn. "
            "Each object has an `op` and ONLY the fields that op needs; set unused fields to \"\" or []. "
            "`name` is always a CHARACTER's name (never the op word). Examples:\n"
            "- relationship shift toward the player: {\"op\":\"rel\",\"name\":\"Aria\",\"key\":\"you\",\"value\":\"-1\",\"title\":\"\",\"keywords\":[]}\n"
            "- a character moves: {\"op\":\"move\",\"name\":\"Aria\",\"key\":\"\",\"value\":\"the pier\",\"title\":\"\",\"keywords\":[]}\n"
            "- mood change: {\"op\":\"mood\",\"name\":\"Aria\",\"key\":\"\",\"value\":\"guarded\",\"title\":\"\",\"keywords\":[]}\n"
            "- player gains an item: {\"op\":\"item_add\",\"name\":\"\",\"key\":\"\",\"value\":\"brass key\",\"title\":\"\",\"keywords\":[]}\n"
            "- a real story variable: {\"op\":\"set_flag\",\"name\":\"\",\"key\":\"met_aria\",\"value\":\"true\",\"title\":\"\",\"keywords\":[]}\n"
            "- a new fact to remember as canon: {\"op\":\"fact\",\"name\":\"\",\"key\":\"\",\"value\":\"Aria distrusts strangers after being burned before.\",\"title\":\"Aria distrusts strangers\",\"keywords\":[\"Aria\",\"trust\"]}\n"
            "- a one-line beat summary: {\"op\":\"log\",\"name\":\"\",\"key\":\"\",\"value\":\"Daniel introduced himself; Aria sized him up warily.\",\"title\":\"\",\"keywords\":[]}\n"
            "Be thorough: for EVERY character present whose mood or stance toward the player shifted "
            "this turn, emit a `mood` and/or `rel` delta using their EXACT name from the CAST list "
            "above; emit `move` when someone changes location and `item_add`/`item_remove` when the "
            "player's belongings change. Always include exactly one `log` op summarizing the beat. Do "
            "NOT invent flags; only set_flag for genuine story variables. Empty list only if truly "
            "nothing changed.")

        moved = body.get("choice")
        directive = ""
        if moved:
            dest = next((l.name for l in st.locations if l.id == moved), moved)
            directive = f"\n\n[The player moves to: {dest}. Narrate the transition and arrival there; set location to '{moved}'.]"
        prompt = (f"CURRENT LOCATION: {cur}\n\nTRANSCRIPT:\n{transcript}{directive}\n\n"
                  f"Narrate the next turn and report the scene state.")

        # Director narrates AND reports world-state changes in one structured call
        # (PLAY_SCHEMA + state_deltas). The call is refusal-guarded: on a refusal /
        # error / invalid output it re-runs on the configured fallback model.
        import copy as _copy
        from .guards import generate_guarded
        from ..server.services import config_files as _cf
        play_schema = _copy.deepcopy(PLAY_SCHEMA)
        play_schema["properties"]["state_deltas"] = {"type": "array", "items": _SE.STATE_DELTA_ITEM}
        play_schema["required"] = list(play_schema["required"]) + ["state_deltas"]

        _fb_key = _cf.load_text_roles(ctx.root).get("fallback")
        fallback = ctx.text_provider_for(_fb_key) if _fb_key else None
        guarded = generate_guarded(provider, system=system, prompt=prompt, root=ctx.root,
                                   emits=play_schema, fallback=fallback)
        data = guarded["data"] or {}
        if not data:
            return JSONResponse({"error": guarded.get("error") or "director returned no structured data "
                                          "(model may not support structured output)"}, status_code=500)
        # map present/emotion names -> character keys for the UI's sprite lookup
        name_to_key = {(ctx.base_settings.characters[m.character].name if m.character in ctx.base_settings.characters
                        else m.character).lower(): m.character for m in st.cast}
        present_keys = [name_to_key.get((n or "").lower()) for n in data.get("present", [])]
        # Direct key resolution. The director names an emotion key from the character's range.
        # If the key is valid (in EMOTION_KEYS), use it. If it's unrecognised, fall back to neutral.
        emotions = {}
        for e in data.get("emotions", []):
            ck = name_to_key.get((e.get("character") or "").lower())
            if not ck:
                continue
            key = (e.get("emotion") or "neutral").strip().lower()
            if key not in EMOTION_KEYS:
                key = "neutral"
            emotions[ck] = key
        loc = data.get("location") if any(l.id == data.get("location") for l in st.locations) else cur

        # Evolve + persist the world state from this turn's deltas. `fact` deltas are
        # written back into the thread's lorebook scope (retrievable next turn).
        try:
            world_state = _SE.apply_deltas(world_state, data.get("state_deltas") or [],
                                           root=ctx.root, scope=thread_scope)
            world_state["location"] = loc or world_state.get("location") or ""
            save_session(ctx.root, sid, {**_sess, "state": _SE.with_world(_sess.get("state"), world_state)})
        except Exception:  # noqa: BLE001 — a state-write failure must not drop the turn
            pass

        return {
            "reply": data.get("reply", ""), "location": loc,
            "present": [k for k in present_keys if k],
            "emotions": emotions,
            "movement": bool(data.get("movement")),
            "state": _SE.summary(world_state),
            "guard": {"tripped": guarded.get("tripped"), "used_fallback": guarded.get("used_fallback")},
        }

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

    @app.post("/api/stories/{key}/plan-wardrobe-all")
    async def story_plan_wardrobe_all(key: str, body: dict):
        """Plan + SAVE (replace) wardrobe for ALL cast members via scene-based reasoning.

        One LLM call per location, covering all characters present. Outfits are only generated
        for major story events that genuinely warrant a costume change.
        Plans only — renders no sprites; the user renders those per-character afterward.
        Returns {job}."""
        from .pipeline import plan_story_wardrobe as _plan_story_wardrobe
        from .pipeline.wardrobe import compose_outfit_prompt as _compose_outfit_prompt

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        provider, systems = ctx.builder_ctx(body or {}, "wardrobe")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        story = st.model_dump()

        # Build name→key mapping and cast detail list for the scene planner.
        name_to_key: dict[str, str] = {}
        cast_details: list[dict] = []
        for m in st.cast:
            ch = ctx.base_settings.characters.get(m.character)
            if ch is None:
                continue
            name_to_key[ch.name.lower()] = m.character
            cast_details.append({
                "name": ch.name,
                "persona": ch.system or "",
                "appearance": (ch.fields or {}).get("appearance", ""),
                "key": m.character,
            })

        def work(emit, cancelled):
            emit({"type": "phase", "label": "Planning scene wardrobes…"})
            scene_plans = _plan_story_wardrobe(provider, story=story, cast=cast_details,
                                               systems=systems, on_event=emit)

            # Flatten all outfits and tag with resolved character key.
            all_outfits: list[dict] = []
            for scene in scene_plans:
                for o in scene.get("outfits") or []:
                    char_name = (o.get("character") or "").lower()
                    char_key = name_to_key.get(char_name)
                    if not char_key:
                        # Fuzzy fallback: find the closest name.
                        char_key = next(
                            (k for n, k in name_to_key.items()
                             if char_name and (char_name in n or n in char_name)),
                            None,
                        )
                    if char_key is None:
                        continue
                    char = ctx.base_settings.characters.get(char_key)
                    all_outfits.append({
                        **o,
                        "name": o.get("outfit_name") or o.get("name") or "Outfit",
                        "_char_key": char_key,
                        "_persona": (char.system or "") if char else "",
                        "_appearance": ((char.fields or {}).get("appearance", "")) if char else "",
                    })

            if not all_outfits:
                emit({"type": "phase",
                      "label": "No outfit changes needed — no major events warrant new attire."})
                return {"ok": True, "planned": 0}

            # Refine every outfit into unified prose in parallel.
            emit({"type": "phase", "label": f"Refining {len(all_outfits)} outfits…"})

            from concurrent.futures import ThreadPoolExecutor

            def _refine(o):
                try:
                    r = _compose_outfit_prompt(
                        provider, o["_persona"], o["_appearance"],
                        o.get("name", ""), o.get("concept") or "",
                    )
                    if r.get("attire"):
                        o["attire_prompt"] = r["attire"]
                        o["unified"] = r.get("unified", False)
                except Exception:  # noqa: BLE001
                    pass
                emit({"type": "item", "name": o.get("name", "outfit"),
                      "text": o.get("attire_prompt") or o.get("concept", "")})
                return o

            with ThreadPoolExecutor(max_workers=min(len(all_outfits), 8)) as ex:
                refined = list(ex.map(_refine, all_outfits))

            if cancelled():
                return {"ok": True, "planned": 0}

            # Group by character key and apply to each manifest.
            by_char: dict[str, list[dict]] = {}
            for o in refined:
                by_char.setdefault(o["_char_key"], []).append(o)

            emit({"type": "phase", "label": "Saving wardrobes…"})
            planned = 0
            for char_key, outfits in by_char.items():
                if cancelled():
                    break
                ch = ctx.base_settings.characters.get(char_key)
                name = ch.name if ch else char_key
                emit({"type": "phase", "label": f"Applying {name}'s wardrobe"})
                try:
                    # Preserve expressions/affect that were seeded at character-extraction time —
                    # replace=True would normally clear them, so we re-pass them explicitly.
                    cur = ctx.portrait_manifest(char_key)
                    manifest_body: dict = {"outfits": outfits, "replace": True}
                    if cur.get("expression_prompts"):
                        manifest_body["expressions"] = cur["expression_prompts"]
                    if isinstance(cur.get("affect"), dict) and cur["affect"].get("range"):
                        manifest_body["affect"] = cur["affect"]
                    _apply_manifest(ctx, char_key, manifest_body, provider=provider)
                    planned += 1
                    emit({"type": "item", "name": name, "text": f"{len(outfits)} outfits"})
                except Exception as exc:  # noqa: BLE001
                    emit({"type": "phase", "label": f"{name} skipped ({exc})"})
            return {"ok": True, "planned": planned}

        job = _start_stream_job("wardrobe", "Plan all wardrobes", st.name,
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
            # 3. Persist the rewritten card (name / persona / role / appearance + base_prompt + height).
            safe = re.sub(r"[^\w\-]+", "", char_key)
            path = ctx.char_dir() / f"{safe}.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data["name"] = revised["name"] or data.get("name") or char_key
            data["system"] = revised["persona"]
            data["fields"] = {**(data.get("fields") or {}), "role": revised["role"],
                              "appearance": revised["appearance"], "base_prompt": base_prompt}
            try:
                if height_cm:
                    data["fields"]["height_cm"] = int(height_cm)
            except (TypeError, ValueError):
                pass
            from ..config.schema import Character
            Character(**data)  # validate
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            ctx.reload_settings()
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
        # Save onto the story.
        safe = re.sub(r"[^\w\-]+", "", key)
        path = ctx.story_dir() / f"{safe}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for l in data.get("locations", []):
            if l.get("id") == loc:
                l["background_prompt"] = prompt
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        ctx.reload_settings()
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
        model = ctx.role_model("scene", (body or {}).get("image_model"))
        provider, model_id = ctx.image_provider(model)
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

"""Story play HTTP endpoints.

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


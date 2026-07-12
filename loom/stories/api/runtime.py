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
        from ..pipeline._helpers import _card_context
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
        from .. import graph_ops as _GO
        world_hits = [e for e in world_hits if not _GO.is_function_entry(e)]
        if world_hits:
            system = system + "\n\n" + format_lore_block(world_hits)

        retrieved_lore = craft_hits + world_hits

        # The writer may have hand-edited the working spine in the graph pane — feed
        # it back so the model builds on their changes rather than its own last draft.
        working_spine = body.get("spine") or body.get("graph")
        if isinstance(working_spine, dict) and working_spine:
            from ..authoring.pipeline_graph import spine_prose as _spine_prose
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
        from .. import agent_config as _AC
        return {"modes": _AC.modes_list(ctx.root)}

    @app.post("/api/stories/agent/dump-prompt")
    def dump_agent_prompt(body: dict):
        """DEBUG — render the system prompt the agent WOULD assemble for this request, WITHOUT calling
        the model. Same body shape as /graph-ops (graph, messages, mode, target, propose, etc.). Returns
        {system, active_modes, label} so you can inspect exactly what the model would see. No persistence,
        no model call, no side effects — pure inspection. Use this to verify prompt edits in
        configs/story_agent.json (edit → reload → dump)."""
        from .. import agent_config as _AC
        from .. import agent as _AG
        from ..pipeline import grounding as _G
        body = body or {}
        root = ctx.root
        cfg = _AC.load_config(root)
        graph = body.get("graph") if isinstance(body.get("graph"), dict) else {}
        messages = body.get("messages") or []
        req_text = next((str(m.get("content", "")) for m in reversed(messages)
                         if isinstance(m, dict) and m.get("role") == "user"), "")
        agents = cfg.get("agents") or {}
        from ..agent_modes import translate_key as _translate_key
        explicit = _translate_key((body.get("mode") or "").strip())
        active_ids = ([explicit] if (explicit and explicit in agents)
                      else _AC.match_modes(root, req_text))
        adopted = "\n\n".join(agents[a]["persona"] for a in active_ids
                              if agents.get(a, {}).get("persona"))
        adopted_examples = "\n\n".join(agents[a]["example"] for a in active_ids
                                       if agents.get(a, {}).get("example"))
        primary = agents.get(active_ids[0], {}) if active_ids else {}
        story_ctx = _AG._story_context(ctx, body.get("story"),
                                       cfg.get("story_context_fields") or [])
        craft_block = _G.MINIMALISM       # literary-minimalist stance (replaced the _craft lorebook)
        inject = primary.get("inject") or []
        ground = []
        if "concreteness" in inject:
            ground.append(_G.CONCRETENESS)
        if "psyche" in inject:
            ground.append(_G.ADAPTATION)      # the wound→lie→coping adaptation basis (replaced Big Five)
        char_ground = "\n\n".join(p for p in ground if p)
        target = (body.get("target") or "story").strip()
        draft = (target == "draft") or (body.get("commit", True) is False)
        label = (body.get("artifact_label") or "DOCUMENT").strip()
        system = _AG.assemble_system_prompt(
            cfg=cfg, graph=graph, adopted=adopted, story_ctx=story_ctx,
            craft_block=craft_block, char_ground=char_ground, label=label,
            draft=draft, propose=bool(body.get("propose")),
            adopted_examples=adopted_examples)
        return {"system": system, "active_modes": active_ids, "label": label,
                "draft": draft, "propose": bool(body.get("propose")),
                "length": len(system)}

    @app.post("/api/stories/graph-ops")
    def story_graph_ops(body: dict):
        """The story chat agent — single-shot tool-calling for general editing. The caller asks in
        natural language; the agent resolves the right tools + persona and applies them. For
        structured story work (spine, beats, arc design) use the explicit /task endpoint instead —
        it's direct dispatch (no keyword inference, canon-grounded, one call)."""
        from .. import agent as _AG
        out = _AG.run_turn(ctx, body or {})
        st = out.pop("_status", None)
        return JSONResponse(out, status_code=st) if st else out

    @app.post("/api/stories/{key}/task")
    def story_task(key: str, body: dict):
        """EXPLICIT task dispatch — the caller names the task and the system runs exactly that,
        grounded in canon, one model call. No keyword inference, no agent-switching, no drift.
        Body: {task: 'set_spine'|'add_beat'|..., focus?: 'writer steer', brief?: 'beat context',
               model?: 'model override'}."""
        from ..authoring import tasks as _ST
        body = body or {}
        task = (body.get("task") or "").strip()
        if not task:
            return JSONResponse({"ok": False, "error": "missing 'task' parameter"}, status_code=400)
        provider, _ = ctx.builder_ctx(body, body.get("script") or "workshop")
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"ok": False, "error": "no chat connection — connect a chat model first"}, status_code=400)
        import asyncio as _aio
        kwargs = {}
        if body.get("focus"): kwargs["focus"] = body["focus"]
        if body.get("brief"): kwargs["brief"] = body["brief"]
        out = _aio.run(_ST.run_task(ctx, key, task, provider, **kwargs))
        st = out.pop("_status", None)
        return JSONResponse(out, status_code=st) if st else out

    @app.get("/api/stages")
    def list_stage_tools():
        """The catalog of callable STAGE TOOLS — what an agent can trigger in a story surface
        (used by the Scripts panel + the post-history tool protocol)."""
        from ..authoring import stages as ST
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
        from .. import agent_config as AC
        from .. import graph_ops as GO
        from ..authoring import scripts as S
        from ..authoring import stages as ST

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

        # The chat's REAL menu: which story_agent.json agent(s) offer each tool (its `tools` list).
        # This is the source of truth now — grouping by it shows what the agent can actually call.
        modes = (AC.load_config(ctx.root).get("agents") or {})
        mode_fns = {mid: set(m.get("tools") or []) for mid, m in modes.items()}
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

        from ..authoring import scripts as S
        from ..authoring import stages as ST

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
        from ..authoring import stages as ST
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
        from .. import state_engine as _SE
        from .. import state_doc as _SD
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
        from .. import state_engine as _SE
        body = body or {}
        sid = body.get("sid") or f"play-{key}"
        sess = load_session(ctx.root, sid) or {}
        ws = _SE.normalize(body.get("state") or {})
        save_session(ctx.root, sid, {**sess, "state": _SE.with_world(sess.get("state"), ws)})
        return {"ok": True, "state": ws}

    @app.post("/api/stories/{key}/state/reset")
    def reset_world_state(key: str, body: dict | None = None):
        from ..server.services.story_sessions import load_session, save_session
        from .. import state_engine as _SE
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
        from .. import state_engine as _SE0
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
        from ..runtime.engine import run_play_turn, PlayState, PlayDeps
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
        from ..worldgen import generate_prologue
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
        from .. import state_engine as _SE
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
        from .. import state_engine as _SE
        from ..runtime.director import generate_arc

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
            from ..queue import set_pending
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
        from ..runtime.director import design_arc
        from ..queue import set_pending
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
        from .. import state_engine as _SE
        from ..runtime.director import suggest_slot_scenes, advance_slot, day_of
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

    """Attach manuscript endpoints without changing their public URLs."""

    @app.get("/api/stories/{key}/manuscript")
    def story_manuscript(key: str, sid: str = ""):
        """Return the prologue and play narration grouped into scenes."""
        from ...server.services.story_sessions import load_session
        from .. import state_engine as state

        story = ctx.base_settings.stories.get(key)
        if story is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        session = load_session(ctx.root, sid or f"play-{key}") or {}
        world = state.world_of(session.get("state"))
        scenes = world.get("manuscript") or []
        if not scenes and world.get("transcript"):
            scenes = [{"loc": "The story so far", "opened": 0,
                       "pages": [{"step": i, "text": text, "beat": "", "ti": i}
                                 for i, text in enumerate(world["transcript"])
                                 if (text or "").strip()]}]
        prologue = ((story.fields or {}).get("prologue") or {}).get("sections") or []
        return {"prologue": prologue, "scenes": scenes}

    @app.post("/api/stories/{key}/manuscript/bake")
    def story_manuscript_bake(key: str, body: dict):
        """Convert canonical play material into clean, standalone chapter prose."""
        from ...server.services.story_sessions import load_session, save_session
        from .. import state_engine as state

        story = ctx.base_settings.stories.get(key)
        if story is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        sid = body.get("sid") or f"play-{key}"
        session = load_session(ctx.root, sid) or {}
        world = state.world_of(session.get("state"))
        source = world.get("manuscript") or []
        if not source and world.get("transcript"):
            source = [{"loc": "The story so far", "pages": [{"text": text}
                      for text in world["transcript"] if (text or "").strip()]}]
        if not source:
            return JSONResponse({"error": "play at least one turn before baking"}, status_code=400)
        provider = ctx.text_provider_for(body.get("model") or "deepseek/deepseek-v4-pro",
                                         {"reasoning_effort": "low"})
        if provider is None:
            return JSONResponse({"error": "no prose model configured"}, status_code=400)

        system = """You are adapting a PLAYED interactive-fiction scene into a finished chapter.

This is not a summary and not a transcript cleanup. Write a scene the reader can inhabit.

SOURCE AUTHORITY
- The played material is canon. Preserve its events, choices, consequences, names, concrete objects,
  promises, and unresolved uncertainty. Never repair, improve, explain away, or add plot to it.
- Treat the player's actions and dialogue as the protagonist's actions and dialogue. Do not call them
  "the player", mention roleplay, or expose game mechanics.
- When the source is ambiguous, keep the ambiguity. Omit only mechanical repetition, empty transitions,
  and narration that does not change the scene.

DRAMATIC ADAPTATION
- Find the scene's turn: what somebody wants, what resists it, and what is different by the end.
  Let that turn determine the opening, selection of detail, and final beat.
- Choose the POV that is most sustained by the source; write close third person, past tense. Stay inside
  what that person notices, thinks, and can reasonably infer. Do not head-hop or provide omniscient
  explanations.
- Keep consequential dialogue as dialogue, but compress conversational filler. Replace generic emotion
  labels with physical behavior, selection of detail, and what a person does not say.
- Use concrete, plain, lived prose. No purple language, moral thesis, melodramatic escalation, stock
  metaphors, or retrospective "little did they know" narration. Paragraphs should move the scene.
- End on the actual changed pressure, image, decision, or question left by the source — not a fabricated
  cliffhanger or a recap.

OUTPUT
- Write 500-1,000 words when the source supports it; prefer a complete, well-paced scene over padding.
- Return ONLY the chapter prose. No title, outline, notes, markdown, or explanation."""
        arc = world.get("arc") if isinstance(world.get("arc"), dict) else {}
        context = (f"STORY TITLE: {story.name}\n"
                   f"PREMISE: {getattr(story, 'premise', '') or ''}\n"
                   f"RUNNING STORY SO FAR: {world.get('story_so_far', '') or '(none)'}\n"
                   f"CURRENT ARC: {arc.get('name', '') or '(none)'}\n"
                   f"ARC QUESTION: {arc.get('question', '') or '(none)'}")
        baked = []
        for index, scene in enumerate(source):
            raw = "\n\n".join(str(page.get("text", "")) for page in scene.get("pages", [])
                              if page.get("text"))[:12000]
            if not raw:
                continue
            try:
                prose = (provider.generate_text(
                    system=system,
                    prompt=(f"{context}\n\nCHAPTER {index + 1} LOCATION: {scene.get('loc', '')}\n\n"
                            f"PLAYED MATERIAL (canonical source):\n{raw}\n\nWrite the chapter now."),
                ).text or "").strip()
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"bake failed: {exc}"}, status_code=502)
            if prose:
                baked.append({"title": scene.get("loc") or f"Chapter {index + 1}", "text": prose,
                              "source_scene": index})
        if not baked:
            return JSONResponse({"error": "the prose model returned no chapters"}, status_code=502)
        world["baked_story"] = baked
        save_session(ctx.root, sid, {**session, "state": state.with_world(session.get("state"), world)})
        return {"chapters": baked}

    @app.post("/api/stories/{key}/manuscript/illustrate")
    def story_manuscript_illustrate(key: str, body: dict):
        """Render one lead illustration for each baked chapter."""
        from ...server.services.batch_images import render_batch
        from ...server.services.story_sessions import load_session, save_session
        from .. import state_engine as state

        story = ctx.base_settings.stories.get(key)
        if story is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        sid = body.get("sid") or f"play-{key}"
        session = load_session(ctx.root, sid) or {}
        world = state.world_of(session.get("state"))
        chapters = world.get("baked_story") or []
        if not chapters:
            return JSONResponse({"error": "bake the manuscript before illustrating it"}, status_code=400)
        provider, model_id = ctx.role_image_provider("scene", body.get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        style = ctx.art_style(story_key=key) if (story.art_style or "").strip() else ""
        prompts = [{"prompt": f"{style} illustrated novel scene, {chapter.get('title', '')}. "
                              f"Depict the most visual, story-defining moment. "
                              f"{chapter.get('text', '')[:1800]}".strip()}
                   for chapter in chapters]
        images = []
        outdir = ctx.story_bg_dir(key)
        outdir.mkdir(parents=True, exist_ok=True)
        for index, png in enumerate(render_batch(
            provider, prompts, ctx=ctx,
            out_prefix_template=ctx.output_prefix_for(model_id, "story", key),
        )):
            if not png:
                continue
            filename = f"chapter_{index + 1}.png"
            (outdir / filename).write_bytes(png)
            chapters[index]["image"] = f"/api/stories/{key}/bg/{filename}"
            images.append(chapters[index]["image"])
        world["baked_story"] = chapters
        save_session(ctx.root, sid, {**session, "state": state.with_world(session.get("state"), world)})
        return {"images": images, "chapters": chapters}

    @app.post("/api/stories/{key}/manuscript/edit")
    def story_manuscript_edit(key: str, body: dict):
        """Edit either a constant prologue section or a single play page."""
        from ...server.services.story_sessions import load_session, save_session
        from .. import state_engine as state

        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        text = (body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "empty text"}, status_code=400)
        if body.get("prologue") is not None:
            try:
                data = ctx._read_story_data(key)
                sections = ((data.get("fields") or {}).get("prologue") or {}).get("sections") or []
                sections[int(body["prologue"])]["text"] = text
                ctx._write_story_data(key, data)
                return {"ok": True}
            except (FileNotFoundError, IndexError, KeyError):
                return JSONResponse({"error": "no editable prologue"}, status_code=400)
        sid = body.get("sid") or f"play-{key}"
        session = load_session(ctx.root, sid) or {}
        world = state.world_of(session.get("state"))
        try:
            scenes = world.get("manuscript") or []
            page = (scenes[int(body["scene"])]["pages"][int(body["page"])] if scenes
                    else {"ti": int(body["page"])})
            transcript_index = page.get("ti")
            if transcript_index is not None and 0 <= int(transcript_index) < len(world.get("transcript") or []):
                world["transcript"][int(transcript_index)] = text
            page["text"] = text
        except (IndexError, KeyError, ValueError, TypeError):
            return JSONResponse({"error": "no such page"}, status_code=400)
        save_session(ctx.root, sid, {**session, "state": state.with_world(session.get("state"), world)})
        return {"ok": True}
    """Attach endpoints that expose a play session's current state."""

    @app.get("/api/stories/{key}/state-card")
    def story_state_card(key: str, sid: str = ""):
        """Return the derived state card and the session's live card evolution."""
        from ...server.services.story_sessions import load_session
        from .. import state_engine as state
        from ..runtime.director import state_card

        story = ctx.base_settings.stories.get(key)
        if story is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        live_sid = sid or f"play-{key}"
        session = load_session(ctx.root, live_sid) or {}
        world = state.world_of(session.get("state"))
        try:
            from ...server.services import story_store
            evolution = story_store.get_play_cards(ctx.root, key, live_sid)
        except Exception:  # noqa: BLE001 — a derived state card remains useful offline
            evolution = {}
        return {"card": state_card(world, story), "step": world.get("step"),
                "revision": world.get("revision"), "evolution": evolution}

    @app.get("/api/stories/{key}/live-cards")
    def story_live_cards(key: str, sid: str = "", kind: str = "", card_key: str = ""):
        """Return session-scoped mutable cards and optional evidence for one card."""
        from ...server.services import story_store

        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        live_sid = sid or f"play-{key}"
        history = (story_store.get_card_history(ctx.root, key, live_sid, kind, card_key)
                   if kind and card_key else [])
        return {"cards": story_store.get_play_cards(ctx.root, key, live_sid), "history": history}

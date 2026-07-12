"""Story authoring HTTP endpoints.

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

    @app.post("/api/stories/agent/dump-prompt")
    def dump_agent_prompt(body: dict):
        """DEBUG — render the system prompt the agent WOULD assemble for this request, WITHOUT calling
        the model. Same body shape as /graph-ops (graph, messages, mode, target, propose, etc.). Returns
        {system, active_modes, label} so you can inspect exactly what the model would see. No persistence,
        no model call, no side effects — pure inspection. Use this to verify prompt edits in
        configs/story_agent.json (edit → reload → dump)."""
        from . import agent_config as _AC
        from . import agent as _AG
        from .pipeline import grounding as _G
        body = body or {}
        root = ctx.root
        cfg = _AC.load_config(root)
        graph = body.get("graph") if isinstance(body.get("graph"), dict) else {}
        messages = body.get("messages") or []
        req_text = next((str(m.get("content", "")) for m in reversed(messages)
                         if isinstance(m, dict) and m.get("role") == "user"), "")
        agents = cfg.get("agents") or {}
        from .agent_modes import translate_key as _translate_key
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
        from . import agent as _AG
        out = _AG.run_turn(ctx, body or {})
        st = out.pop("_status", None)
        return JSONResponse(out, status_code=st) if st else out

    @app.post("/api/stories/{key}/task")
    def story_task(key: str, body: dict):
        """EXPLICIT task dispatch — the caller names the task and the system runs exactly that,
        grounded in canon, one model call. No keyword inference, no agent-switching, no drift.
        Body: {task: 'set_spine'|'add_beat'|..., focus?: 'writer steer', brief?: 'beat context',
               model?: 'model override'}."""
        from . import story_tasks as _ST
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


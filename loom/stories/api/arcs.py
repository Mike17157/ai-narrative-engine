"""Story arcs HTTP endpoints.

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
    # and stories persist as one <key>.json each. See loom/stories/story_db.py.)


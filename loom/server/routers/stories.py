from __future__ import annotations

import base64
import json
import re

import yaml
from fastapi.responses import FileResponse, JSONResponse

from ...config.schema import ModelDef
from ..services import config_files
from ..services.config_files import STORY_BUILDER_DEFAULT
from ..services.images import _clean_reference_png, _randomize_seeds, _render
from ..services.jobs_util import _start_stream_job
from ..services.prompts import FEATURES_SCHEMA, PLAY_SCHEMA, _assemble_base_prompt
from ..services.wardrobe import apply_manifest as _apply_manifest, plan_and_apply as _plan_and_apply


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

        from ...scenario import parse_storyboard, storyboard_inputs

        body = body or {}
        ch = ctx.base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, systems = ctx.builder_ctx(body, "storyboard")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        system, prompt = storyboard_inputs(name=ch.name, persona=ch.system,
                                           extras=ctx.card_extras(ch, body["character"]),
                                           systems=systems,
                                           premise=(body.get("premise") or "").strip())

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

    @app.post("/api/stories/builder/prompt")
    def builder_prompt(body: dict):
        """Preview/inspect a builder STAGE's prompt before generating. Returns the
        editable `base` system prompt, the `default` (for reset), and the effective
        `system` (the effective system prompt). For the storyboard stage it also
        composes the user-message `prompt` for the given character. Stages:
        storyboard · locations · characters · wardrobe."""
        from ...scenario.builder import NEEDS_IMAGE, DEFAULT_SYSTEMS, _sys, storyboard_inputs

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

        from ...scenario import builder as B
        from ...scenario import extract_characters, extract_locations, plan_wardrobe

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
        t_prov = ctx.author_provider(body.get("model") or config_files._stage_model(cfg, stage))
        if t_prov is None:
            return JSONResponse({"error": "no author model configured"}, status_code=400)
        extras = ctx.card_extras(ch, body.get("character"))
        fields = ch.fields or {}

        def gen_board(target: bool):
            sysd = systems if (target and stage == "storyboard") else saved
            prov = t_prov if (target and stage == "storyboard") else ctx.author_provider(config_files._stage_model(cfg, "storyboard"))
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
        from ...scenario import extract_locations

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
        from ...scenario import extract_characters, extract_protagonist

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
            from loom.pipeline import compose_base_prompt as _compose_base_prompt
            _bp_cfg = ctx.load_story_builder()
            _bp_prov = ctx.author_provider(config_files._stage_model(_bp_cfg, "base_image"))
            _bp_sys = (_bp_cfg.get("systems") or {})

            def _bp(item):
                idx, p = item
                try:
                    r = _compose_base_prompt(_bp_prov, p.get("name", ""), p.get("persona", ""),
                                             p.get("appearance", ""), p.get("role", ""),
                                             systems=_bp_sys)
                    if not isinstance(r, dict):
                        return (idx, "", None)
                    return (idx, r.get("prompt", ""), (r.get("features") or {}).get("height_cm"))
                except Exception:  # noqa: BLE001
                    return (idx, "", None)

            with ThreadPoolExecutor(max_workers=min(len(cast), 6)) as ex:
                bps = {idx: (pr, h) for idx, pr, h in ex.map(_bp, list(enumerate(cast)))}
            for idx, member in enumerate(cast):
                pr, h = bps.get(idx, ("", None))
                if pr:
                    member["base_prompt"] = pr
                if h:
                    member["height_cm"] = h
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

        provider, systems = ctx.builder_ctx(body, "storyboard")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        # Build character context for the workshop system prompt.
        extras = ctx.card_extras(ch, body["character"])
        from ...pipeline._helpers import _card_context
        card = _card_context(ch.name, ch.system, extras)

        system = (
            f"You are a collaborative story architect. You know {ch.name} deeply — their card is "
            f"provided below — and your job is to help the user develop the perfect story concept "
            f"for them before committing to a full generation.\n\n"
            f"Be curious and creative: ask what the user wants to feel, propose bold concepts, "
            f"offer alternatives. Build toward a refined premise through conversation. Keep each "
            f"response focused and conversational — 2-4 paragraphs at most. When the user seems "
            f"satisfied or asks you to, offer to proceed with a full storyboard.\n\n"
            f"Do NOT generate a full storyboard here — just converse and refine the concept.\n\n"
            f"CHARACTER CARD:\n{card}"
        )

        # Build the conversation transcript as a single prompt string.
        # The system prompt already has all the character context; the prompt is the dialogue.
        messages = list(body.get("messages") or [])
        premise = (body.get("premise") or "").strip()
        if not messages and premise:
            messages = [{"role": "user", "content": f"I have a premise in mind: {premise}"}]
        elif not messages:
            messages = [{"role": "user", "content": "Help me develop a story for this character."}]

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

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()

        def on_delta(t: str):
            loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": t})

        async def run():
            try:
                await run_in_threadpool(lambda: provider.generate_text(
                    system=system, prompt=prompt, on_delta=on_delta,
                    cancel=cancel_evt.is_set))
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

        from ...scenario import parse_storyboard

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
        from ...pipeline._helpers import _card_context
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

    @app.post("/api/stories")
    def save_story(body: dict):
        """Materialize an accepted draft: create Character files for proposed NPCs,
        remap cast names→keys, link beats to locations, and write the Story."""
        from ...config.schema import Story

        draft = body or {}
        name = (draft.get("name") or "Story").strip()
        primary_key = draft.get("source_character") or draft.get("character")
        if primary_key and primary_key not in ctx.base_settings.characters:
            primary_key = None

        # Ensure a UNIQUE display name (not just a unique key) so the story list isn't
        # full of identically-named entries.
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
        from ...scenario import extract_protagonist

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
        from loom.pipeline import compose_base_prompt as _compose_base_prompt
        _bp_cfg = ctx.load_story_builder()
        _bp_prov = ctx.author_provider(config_files._stage_model(_bp_cfg, "base_image"))
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

        story = {
            "name": name, "premise": draft.get("premise", ""), "tone": draft.get("tone", ""),
            "themes": draft.get("themes", []), "storyboard": storyboard, "cast": cast,
            "lorebook": draft.get("lorebook") or {}, "locations": locations,
            "start": draft.get("start"), "background": draft.get("background"),
            "fields": {"source_character": primary_key} if primary_key else {},
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
        from ...config.schema import Story

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        data = st.model_dump()
        for f in ("name", "premise", "tone", "themes", "storyboard", "cast",
                  "lorebook", "locations", "start", "background", "fields"):
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

        from ...scenario import extract_characters, extract_protagonist

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

            from loom.pipeline import compose_base_prompt as _compose_base_prompt
            _bp_cfg = ctx.load_story_builder()
            _bp_prov = ctx.author_provider(config_files._stage_model(_bp_cfg, "base_image"))
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

            # Auto-plan wardrobes for every new cast member so the wardrobe view is
            # immediately populated when the user arrives at the cast page.
            emit({"type": "phase", "label": "Planning wardrobes…"})
            try:
                w_prov, w_sys = ctx.builder_ctx({}, "wardrobe")
                if w_prov is not None:
                    full_story = st.model_dump()
                    for ckey in created:
                        if cancelled():
                            break
                        ch = ctx.base_settings.characters.get(ckey)
                        if ch is None:
                            continue
                        emit({"type": "phase", "label": f"Planning {ch.name}'s wardrobe"})
                        _plan_and_apply(ctx, w_prov, ckey, ch, full_story, w_sys, emit=emit, replace=True)
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
        from ...providers.registry import build_provider

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        tconn = ctx.store.active("text")
        if tconn is None:
            return JSONResponse({"error": "no chat connection"}, status_code=400)
        opts = {**tconn.to_model_options(), "max_tokens": 1500}
        if body.get("chat_model"):
            opts["model"] = body["chat_model"]
        provider = build_provider(ModelDef(provider=tconn.provider, kind="text", options=opts))

        # Compose the director's brief from the story.
        from ..services.emotions import EMOTION_KEYS, NORMAL_KEYS

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
        lore = "; ".join(e.get("comment", "") for e in (st.lorebook or {}).get("entries", []) if e.get("comment"))
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
            + (f"WORLD: {lore}\n" if lore else "")
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
        moved = body.get("choice")
        directive = ""
        if moved:
            dest = next((l.name for l in st.locations if l.id == moved), moved)
            directive = f"\n\n[The player moves to: {dest}. Narrate the transition and arrival there; set location to '{moved}'.]"
        prompt = (f"CURRENT LOCATION: {cur}\n\nTRANSCRIPT:\n{transcript}{directive}\n\n"
                  f"Narrate the next turn and report the scene state.")
        try:
            res = provider.generate_text(system=system, prompt=prompt, emits=PLAY_SCHEMA)
            data = res.data or {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        if not data:
            return JSONResponse({"error": "director returned no structured data (model may not support it)"},
                                status_code=500)
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
        return {
            "reply": data.get("reply", ""), "location": loc,
            "present": [k for k in present_keys if k],
            "emotions": emotions,
            "movement": bool(data.get("movement")),
        }

    @app.post("/api/stories/{key}/plan-wardrobe")
    async def story_plan_wardrobe(key: str, body: dict):
        """Plan one cast character's wardrobe (outfits) + story-derived expression prompts,
        STREAMED live as a job. Returns {job}; the final `result` event carries the plan for
        review (persist via the portraits wardrobe endpoint). Renders nothing."""
        from ...scenario import plan_wardrobe

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
            from loom.pipeline import refine_outfits as _refine_outfits
            plan["outfits"] = _refine_outfits(provider, plan.get("outfits"), ch.system, appearance, emit=emit)
            return {"character": char_key, **plan}

        job = _start_stream_job("wardrobe", "Plan wardrobe", ch.name,
                                f"stories/{key}/cast", work)
        return {"job": job.id}

    @app.post("/api/stories/{key}/plan-wardrobe-all")
    async def story_plan_wardrobe_all(key: str, body: dict):
        """Plan + SAVE (replace) the wardrobe for EVERY cast member, STREAMED as one job. Plans
        only — renders no sprites; the user renders those per-character afterward. Returns {job}."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        provider, systems = ctx.builder_ctx(body or {}, "wardrobe")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        story = st.model_dump()
        members = [m.character for m in st.cast]

        def work(emit, cancelled):
            planned = 0
            for ckey in members:
                if cancelled():
                    break
                ch = ctx.base_settings.characters.get(ckey)
                if ch is None:
                    continue
                emit({"type": "phase", "label": f"Planning {ch.name}'s wardrobe"})
                try:
                    outfits = _plan_and_apply(ctx, provider, ckey, ch, story, systems,
                                              emit=emit, replace=True)
                    planned += 1
                    emit({"type": "item", "name": ch.name, "text": f"{len(outfits)} outfits"})
                except Exception as exc:  # noqa: BLE001 — one character failing must not sink the rest
                    emit({"type": "phase", "label": f"{ch.name} skipped ({exc})"})
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
        from ...scenario import revise_character

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
            from loom.pipeline import compose_base_prompt as _compose_base_prompt
            _bp_cfg = ctx.load_story_builder()
            _bp_prov = ctx.author_provider(config_files._stage_model(_bp_cfg, "base_image"))
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
            from ...config.schema import Character
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
                    iprov, _mid = ctx.image_provider(ctx.role_model("base"))
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
                    from loom.pipeline import (compose_expressions as _compose_expressions,
                                               compose_poses as _compose_poses,
                                               compose_affect_range as _compose_affect_range,
                                               plan_wardrobe as _plan_wardrobe,
                                               refine_outfits as _refine_outfits)
                    emit({"type": "phase", "label": "Composing expression + pose range"})
                    fresh_exprs = _compose_expressions(w_prov or provider, revised["persona"])
                    fresh_poses = _compose_poses(w_prov or provider, revised["persona"])
                    emit({"type": "phase", "label": "Composing emotional expression range"})
                    _emo_cfg = ctx.load_story_builder()
                    _emo_prov = ctx.author_provider(config_files._stage_model(_emo_cfg, "emotion"))
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
        from ...scenario.builder import DEFAULT_SYSTEMS

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        location = next((l for l in st.locations if l.id == loc), None)
        if location is None:
            return JSONResponse({"error": "no such location"}, status_code=404)
        cfg = config_files.load_story_builder(ctx.root)
        provider = ctx.author_provider(config_files._stage_model(cfg, "locations"))
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

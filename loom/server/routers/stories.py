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
from ..services.prompts import (
    FEATURES_SCHEMA,
    OUTFIT_SCHEMA,
    PLAY_SCHEMA,
    _FULLBODY_FRAMING,
    _assemble_base_prompt,
    _regionize_prompt,
    _safe_image_tags,
    _snap_prompt,
)


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
        provider, invention, systems = ctx.builder_ctx(body, "storyboard")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        system, prompt = storyboard_inputs(name=ch.name, persona=ch.system,
                                           extras=ctx.card_extras(ch, body["character"]),
                                           invention=invention, systems=systems)

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
        `system` (base + invention directive). For the storyboard stage it also
        composes the user-message `prompt` for the given character. Stages:
        storyboard · locations · characters · wardrobe."""
        from ...scenario.builder import NEEDS_IMAGE, NO_INVENTION, DEFAULT_SYSTEMS, _sys, storyboard_inputs

        body = body or {}
        stage = body.get("stage", "storyboard")
        if stage not in DEFAULT_SYSTEMS:
            return JSONResponse({"error": f"unknown stage '{stage}'"}, status_code=400)
        cfg = config_files.load_story_builder(ctx.root)
        invention = config_files._stage_invention(cfg, stage)
        systems = cfg.get("systems") or {}
        out = {
            "stage": stage,
            "base": systems.get(stage) or DEFAULT_SYSTEMS[stage],
            "default": DEFAULT_SYSTEMS[stage],
            "system": _sys(systems, stage, invention),
            "model": config_files._stage_model(cfg, stage, body.get("model")),  # this stage's model ('' = active chat)
            "invention": invention,                                 # this stage's invention level
            "no_invention": stage in NO_INVENTION,
            "requires_image": stage in NEEDS_IMAGE,                  # needs a vision model
        }
        ch = ctx.base_settings.characters.get(body.get("character"))
        if stage == "storyboard" and ch is not None:
            _, out["prompt"] = storyboard_inputs(name=ch.name, persona=ch.system,
                                                 extras=ctx.card_extras(ch, body["character"]),
                                                 invention=invention, systems=systems)
        return out

    @app.post("/api/stories/builder/test")
    async def builder_test(body: dict):
        """Test ONE builder stage end-to-end for a chosen character, using that stage's
        (possibly unsaved) model + invention + system prompt. Prerequisite stages run
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
        t_inv = body.get("invention") or config_files._stage_invention(cfg, stage)
        t_prov = ctx.author_provider(body.get("model") or config_files._stage_model(cfg, stage))
        if t_prov is None:
            return JSONResponse({"error": "no author model configured"}, status_code=400)
        extras = ctx.card_extras(ch, body.get("character"))
        fields = ch.fields or {}

        def gen_board(target: bool):
            inv = t_inv if (target and stage == "storyboard") else config_files._stage_invention(cfg, "storyboard")
            sysd = systems if (target and stage == "storyboard") else saved
            prov = t_prov if (target and stage == "storyboard") else ctx.author_provider(config_files._stage_model(cfg, "storyboard"))
            s, p = B.storyboard_inputs(name=ch.name, persona=ch.system, extras=extras, invention=inv, systems=sysd)
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
                locs = extract_locations(t_prov, board=bd, invention=t_inv, systems=systems).get("locations", [])
                return f"{len(locs)} locations", "\n".join(
                    f"• {l.get('name')} ({l.get('id')})\n  {l.get('background_prompt') or l.get('description','')}" for l in locs)
            if stage == "characters":
                npcs = extract_characters(t_prov, name=ch.name, persona=ch.system, board=bd,
                                          extras=extras, invention=t_inv, systems=systems).get("npcs", [])
                return f"{len(npcs)} characters", "\n\n".join(
                    f"• {n.get('name')} — {n.get('role','')}\n  {n.get('appearance','')}" for n in npcs) or "(no supporting cast in this storyboard)"
            if stage == "wardrobe":
                story = {"premise": bd.get("premise", ""), "tone": bd.get("tone", ""),
                         "storyboard": {"logline": bd.get("logline", ""), "beats": bd.get("beats", [])}}
                plan = plan_wardrobe(t_prov, char_name=ch.name, persona=ch.system,
                                     appearance=fields.get("appearance", ""), story=story,
                                     invention=t_inv, systems=systems)
                outs = "\n".join(f"• {o['name']}: {o['attire_prompt']}" for o in plan.get("outfits", []))
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

        provider, invention, systems = ctx.builder_ctx(body or {}, "locations")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        board = (body or {}).get("board") or {}
        try:
            return extract_locations(provider, board=board, invention=invention, systems=systems)
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
        provider, invention, systems = ctx.builder_ctx(body, "characters")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        extras = ctx.card_extras(ch, body["character"])
        try:
            base = extract_protagonist(provider, name=ch.name, persona=ch.system or "",
                                       extras=extras, invention="faithful", systems=systems)
            out = extract_characters(provider, name=base["name"], persona=base["persona"],
                                     board=body.get("board") or {}, extras=extras,
                                     invention=invention, systems=systems,
                                     reference_card=base["persona"])
            npcs = out.get("npcs", [])
            # ONE uniform cast list — protagonist first, flagged `primary`. Compose the SUPERIOR ✨
            # image description (_compose_base_prompt: FEATURES_SCHEMA → Danbooru co-occurrence) for
            # EVERY member IN PARALLEL, stored on `base_prompt` (what the renderer uses), so the
            # wizard shows/saves the rich description, not the basic tags. The basic `appearance` is
            # kept as the seed fed into the composer.
            cast = [{**base, "primary": True}] + [{**n, "primary": False} for n in npcs]
            from concurrent.futures import ThreadPoolExecutor

            def _bp(item):
                idx, p = item
                try:
                    r = ctx.compose_base_prompt(p.get("name", ""), p.get("persona", ""),
                                             p.get("appearance", ""), p.get("role", ""))
                    return (idx, r.get("prompt", "") if isinstance(r, dict) else "")
                except Exception:  # noqa: BLE001
                    return (idx, "")

            with ThreadPoolExecutor(max_workers=min(len(cast), 6)) as ex:
                bps = dict(ex.map(_bp, list(enumerate(cast))))
            for idx, member in enumerate(cast):
                if bps.get(idx):
                    member["base_prompt"] = bps[idx]
            return {"cast": cast}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

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
                provider, _inv, systems = ctx.builder_ctx(draft, "characters")
                if provider is not None:
                    base_card = extract_protagonist(
                        provider, name=src.name, persona=src.system or "",
                        extras=ctx.card_extras(src, primary_key), invention="faithful", systems=systems)
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

        def _bp(item):
            idx, p = item
            existing = (p.get("base_prompt") or "").strip()
            if existing:
                return (idx, existing)
            try:
                r = ctx.compose_base_prompt(p.get("name", ""), p.get("persona", ""),
                                         p.get("appearance", ""), p.get("role", ""))
                return (idx, r.get("prompt", "") if isinstance(r, dict) else "")
            except Exception:  # noqa: BLE001
                return (idx, "")

        bps: dict[int, str] = {}
        if cast_in:
            with ThreadPoolExecutor(max_workers=min(len(cast_in), 6)) as ex:
                bps = dict(ex.map(_bp, list(enumerate(cast_in))))

        # Write EVERY member through the SAME _write_npc path. The protagonist differs only by
        # carrying the source card's reference image (ref_from) and the `primary` flag — exactly one
        # member is primary.
        cast = []
        seen_primary = False
        for idx, member in enumerate(cast_in):
            is_primary = bool(member.get("primary")) and not seen_primary
            seen_primary = seen_primary or is_primary
            k = ctx.write_npc(member, story_key=skey,
                           ref_from=(primary_key if is_primary else None),
                           base_prompt=bps.get(idx, ""))
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
        board = draft.get("storyboard") or {}
        beats = []
        for b in board.get("beats", []) or []:
            loc = (b.get("location") or "")
            beats.append({"title": b.get("title", ""), "summary": b.get("summary", ""),
                          "location": name_to_loc.get(loc.lower(), loc),
                          "characters": b.get("characters", [])})
        storyboard = {"logline": board.get("logline", ""), "beats": beats}

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
            (ctx.story_dir() / f"{re.sub(r'[^\w\-]+', '', key)}.yaml").write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            ctx.reload_settings()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "key": key}

    @app.delete("/api/stories/{key}")
    def delete_story(key: str):
        p = ctx.story_dir() / f"{re.sub(r'[^\w\-]+', '', key)}.yaml"
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
        provider, invention, systems = ctx.builder_ctx(body or {}, "characters")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)

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
                    extras=ctx.card_extras(prot, prot_key), invention="faithful",
                    systems=systems, on_event=emit)
            out = extract_characters(
                provider, name=(prot_data["name"] if prot_data else st.name),
                persona=(prot_data["persona"] if prot_data else ""),
                board=board, extras=ctx.card_extras(prot, prot_key) if prot else {},
                invention=invention, systems=systems,
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

            def _bp(item):
                pid, p = item
                if cancelled():
                    return (pid, "")
                emit({"type": "phase", "label": f"Rendering appearance — {p.get('name', '?')}"})
                r = ctx.compose_base_prompt(p.get("name", ""), p.get("persona", ""),
                                         p.get("appearance", ""), p.get("role", ""))
                bp = r.get("prompt", "") if isinstance(r, dict) else ""
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
                pkey = ctx.write_npc(prot_data, story_key=key, ref_from=prot_key,
                                  base_prompt=bps.get("__prot__", ""))
                cast.append({"character": pkey, "primary": True}); created.append(pkey)
            for i, npc in enumerate(npcs):
                nk = ctx.write_npc(npc, story_key=key, base_prompt=bps.get(str(i), ""))
                cast.append({"character": nk, "primary": False}); created.append(nk)
            path = ctx.story_dir() / f"{re.sub(r'[^\w\-]+', '', key)}.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data["cast"] = cast
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            ctx.reload_settings()
            keep = {source, *created}
            cdir = ctx.char_dir()
            for m in st.cast:
                ck = m.character
                if ck in keep:
                    continue
                ch = ctx.base_settings.characters.get(ck)
                bound = ch and (((ch.fields or {}).get("story") == key) or (ch.fields or {}).get("_generated"))
                if not bound:
                    continue
                safe = re.sub(r"[^\w\-]+", "", ck)
                for fn in (f"{safe}.yaml", f"{safe}.png", f"{safe}.ref.png"):
                    f = cdir / fn
                    if f.is_file():
                        f.unlink()
                shutil.rmtree(ctx.portrait_dir(ck), ignore_errors=True)
            ctx.reload_settings()
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
        def cast_line(m):
            c = ctx.base_settings.characters.get(m.character)
            return f"- {c.name if c else m.character}: {((c.system or '').splitlines()[0] if c else '')[:160]}"
        cast = "\n".join(cast_line(m) for m in st.cast) or "(none)"
        locs = "\n".join(f"- {l.id} | {l.name}: {l.description}" for l in st.locations) or "(none)"
        lore = "; ".join(e.get("comment", "") for e in (st.lorebook or {}).get("entries", []) if e.get("comment"))
        cur = body.get("location") or st.start or (st.locations[0].id if st.locations else "")
        system = (
            f"You are the narrator and director of an interactive visual novel titled \"{st.name}\".\n"
            f"PREMISE: {st.premise}\nTONE: {st.tone}\n"
            + (f"WORLD: {lore}\n" if lore else "")
            + f"CAST (use these names):\n{cast}\n"
            f"LOCATIONS (the scene is in exactly one; use the id):\n{locs}\n\n"
            "Narrate the next moment in-world and in the established tone, responding to the player. "
            "Then report the scene state in your structured output:\n"
            "- reply: the narration (second person to the player, plus character action/dialogue). Vivid but concise.\n"
            "- location: the id of the location the scene is currently in (one of the listed ids).\n"
            "- present: ALWAYS list the names of EVERY cast character physically in the scene right now "
            "(anyone who speaks, acts, or is described as present) — never leave it empty if someone is there.\n"
            "- emotions: each present character's current emotion as ONE lowercase word.\n"
            "- movement: true ONLY when this moment invites the player to move to a different location "
            "(they suggest leaving, a path opens, the beat concludes) — otherwise false."
        )

        history = body.get("history") or []
        lines = []
        for m in history:
            who = "Player" if m.get("role") == "user" else "Narrator"
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
        emotions = {name_to_key.get((e.get("character") or "").lower()): e.get("emotion")
                    for e in data.get("emotions", [])}
        loc = data.get("location") if any(l.id == data.get("location") for l in st.locations) else cur
        return {
            "reply": data.get("reply", ""), "location": loc,
            "present": [k for k in present_keys if k],
            "emotions": {k: v for k, v in emotions.items() if k},
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
        provider, invention, systems = ctx.builder_ctx(body or {}, "wardrobe")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        story = st.model_dump()
        appearance = (ch.fields or {}).get("appearance", "")

        def work(emit, cancelled):
            plan = plan_wardrobe(provider, char_name=ch.name, persona=ch.system,
                                 appearance=appearance, story=story, invention=invention,
                                 systems=systems, on_event=emit)
            # Pass 2 — refine EACH outfit into careful, consistent booru tags, in parallel (same
            # 2-step pipeline the base image gets).
            emit({"type": "phase", "label": "Refining each outfit — booru tags"})
            plan["outfits"] = ctx.refine_outfits(plan.get("outfits"), ch.system, appearance, emit=emit)
            return {"character": char_key, **plan}

        job = _start_stream_job("wardrobe", "Plan wardrobe", ch.name,
                                f"stories/{key}/cast", work)
        return {"job": job.id}

    @app.post("/api/stories/{key}/plan-wardrobe-all")
    async def story_plan_wardrobe_all(key: str, body: dict):
        """Plan + SAVE (replace) the wardrobe for EVERY cast member, STREAMED as one job. Plans
        only — renders no sprites; the user renders those per-character afterward. Returns {job}."""
        from ...scenario import plan_wardrobe

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        provider, invention, systems = ctx.builder_ctx(body or {}, "wardrobe")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
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
                    appr = (ch.fields or {}).get("appearance", "")
                    plan = plan_wardrobe(provider, char_name=ch.name, persona=ch.system,
                                         appearance=appr, story=story, invention=invention,
                                         systems=systems, on_event=emit)
                    outfits = ctx.refine_outfits(plan.get("outfits"), ch.system, appr, emit=emit)
                    # Emotions are the fixed canonical taxonomy — apply composes them from the
                    # persona; we do NOT pass the (legacy) plan expressions.
                    portraits_apply_wardrobe(ckey, {"outfits": outfits, "replace": True})
                    planned += 1
                    emit({"type": "item", "name": ch.name,
                          "text": f"{len(plan.get('outfits', []))} outfits"})
                except Exception as exc:  # noqa: BLE001 — one character failing must not sink the rest
                    emit({"type": "phase", "label": f"{ch.name} skipped ({exc})"})
            return {"ok": True, "planned": planned}

        job = _start_stream_job("wardrobe", "Plan all wardrobes", st.name,
                                f"stories/{key}/cast", work)
        return {"job": job.id}

    @app.post("/api/characters/{key}/portraits/wardrobe")
    def portraits_apply_wardrobe(key: str, body: dict):
        """Merge a planned wardrobe into the character's portrait studio (additive): add outfits
        (with attire_prompt). Emotions are the FIXED canonical taxonomy stored ONCE at the character
        level (composed here from the persona if absent), shared by every outfit; sprites are
        rendered later.

        With `replace: true` it's DESTRUCTIVE — the existing outfits and their rendered sprites are
        deleted first (e.g. after the base image changed, so the old sprites are stale), then the new
        plan is written fresh. The canonical expression set is preserved across a replace."""
        c = ctx.base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        m = ctx.portrait_manifest(key)
        if body.get("replace"):
            import shutil
            pdir = ctx.portrait_dir(key)
            for o in m.get("outfits", []) or []:
                od = pdir / (o.get("id") or "")
                if o.get("id") and od.is_dir():
                    shutil.rmtree(od, ignore_errors=True)
            m["outfits"] = []
        # Character-level canonical emotion prompts (the FIXED taxonomy) — compose ONCE, reuse for
        # every outfit. An explicit `expressions` in the body overrides/merges; else compose from the
        # persona if the manifest doesn't have them yet.
        canon = dict(m.get("expression_prompts") or {})
        body_exprs = body.get("expressions") if isinstance(body.get("expressions"), dict) else None
        if body_exprs:
            canon.update(body_exprs)
        if not canon:
            from ..services.prompts import _persona_text
            try:
                canon = ctx.compose_expressions(_persona_text(c))
            except Exception:  # noqa: BLE001
                canon = {}
        m["expression_prompts"] = canon
        existing = {o.get("name", "").lower() for o in m.get("outfits", [])}
        for o in body.get("outfits", []) or []:
            nm = (o.get("name") or "").strip()
            if not nm or nm.lower() in existing:
                continue
            oid = re.sub(r"[^\w\-]+", "-", nm.lower()).strip("-") or "outfit"
            base_oid, n = oid, 2
            ids = {x.get("id") for x in m["outfits"]}
            while oid in ids:
                oid, n = f"{base_oid}-{n}", n + 1
            # Snap the attire to real Danbooru tags + split into BREAK regions — same care the base
            # image gets (the going-forward shape; re-derived again when combined at render).
            attire = _regionize_prompt(_snap_prompt(_safe_image_tags((o.get("attire_prompt") or "").strip())))
            m["outfits"].append({"id": oid, "name": nm, "instruction": "",
                                 "prompt": attire, "attire_prompt": attire, "expressions": {}})
            existing.add(nm.lower())
        ctx.save_portrait_manifest(key, m)
        return {"ok": True, "outfits": [o["name"] for o in m["outfits"]],
                "emotions": list(canon.keys())}

    @app.post("/api/stories/{key}/regenerate-character")
    async def regenerate_character(key: str, body: dict):
        """DESTRUCTIVE, STREAMED: re-derive ONE cast member end-to-end, steered by an optional
        free-text `instruction`. Rewrites their persona + role + appearance (so the story overview
        updates), recomposes + saves the base-image prompt, re-renders the base image, and rebuilds
        the wardrobe (old outfits + sprites cleared). Returns {job}; GenStream watches it live."""
        from ...scenario import plan_wardrobe, revise_character

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        char_key = body.get("character")
        ch = ctx.base_settings.characters.get(char_key)
        if ch is None or not any(m.character == char_key for m in st.cast):
            return JSONResponse({"error": "character is not in this story's cast"}, status_code=404)
        provider, invention, systems = ctx.builder_ctx(body, "characters")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        instruction = (body.get("instruction") or "").strip()
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
                                       invention=invention, systems=systems, on_event=emit)
            if cancelled():
                return {"cancelled": True}
            # 2. Compose the rich base-image prompt from the rewritten persona + appearance.
            emit({"type": "phase", "label": "Composing the base-image prompt"})
            comp = ctx.compose_base_prompt(revised["name"], revised["persona"],
                                        revised["appearance"], revised["role"])
            base_prompt = comp.get("prompt", "") if isinstance(comp, dict) else ""
            # 3. Persist the rewritten card (name / persona / role / appearance + base_prompt).
            safe = re.sub(r"[^\w\-]+", "", char_key)
            path = ctx.char_dir() / f"{safe}.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data["name"] = revised["name"] or data.get("name") or char_key
            data["system"] = revised["persona"]
            data["fields"] = {**(data.get("fields") or {}), "role": revised["role"],
                              "appearance": revised["appearance"], "base_prompt": base_prompt}
            from ...config.schema import Character
            Character(**data)  # validate
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            ctx.reload_settings()
            emit({"type": "item", "name": revised["name"], "text": base_prompt or revised["appearance"]})
            if cancelled():
                return {"cancelled": True}
            # 4. Re-render the base image from the new prompt and set it as the reference (best-effort).
            emit({"type": "phase", "label": "Rendering the new base image"})
            try:
                iprov, _mid = ctx.image_provider(ctx.role_model("base"))
                if iprov is not None and base_prompt:
                    from ...comfy.server import get_server
                    get_server(iprov.base_url).ensure_up()
                    res = iprov.generate_image(prompt=base_prompt)
                    if res.images:
                        (ctx.char_dir() / f"{safe}.ref.png").write_bytes(_clean_reference_png(res.images[0]))
            except Exception as exc:  # noqa: BLE001 — a render failure must not sink the rewrite
                emit({"type": "phase", "label": f"Base image skipped ({exc})"})
            if cancelled():
                return {"cancelled": True}
            # 5. Rebuild the wardrobe (replace) — old outfits + stale sprites cleared.
            emit({"type": "phase", "label": "Rebuilding the wardrobe"})
            try:
                plan = plan_wardrobe(provider, char_name=revised["name"], persona=revised["persona"],
                                     appearance=revised["appearance"], story=story, invention=invention,
                                     systems=systems, on_event=emit)
                outfits = ctx.refine_outfits(plan.get("outfits"), revised["persona"],
                                          revised["appearance"], emit=emit)
                # Persona changed → recompose the fixed canonical emotion set from the new persona.
                emit({"type": "phase", "label": "Composing expression range"})
                fresh_exprs = ctx.compose_expressions(revised["persona"])
                portraits_apply_wardrobe(char_key, {"outfits": outfits,
                                                    "expressions": fresh_exprs, "replace": True})
            except Exception as exc:  # noqa: BLE001
                emit({"type": "phase", "label": f"Wardrobe rebuild skipped ({exc})"})
            emit({"type": "phase", "label": f"Done — {revised['name']} regenerated"})
            return {"ok": True, "character": char_key, "name": revised["name"], "role": revised["role"]}

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
            png = await _render(provider, prompt)
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

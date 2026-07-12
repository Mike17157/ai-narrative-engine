"""Routes for reading, editing, and producing a playthrough manuscript."""

from __future__ import annotations

from fastapi.responses import JSONResponse


def register(app, ctx) -> None:
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

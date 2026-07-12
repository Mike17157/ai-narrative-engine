"""Story assets HTTP endpoints.

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
    @app.post("/api/stories/{key}/dream")
    def story_dream(key: str, body: dict):
        """The storymaster's FEVER-DREAM. Reads the on-stage cast's hidden psychology (want/lie/wound/
        secret) + bonds + recent events and returns ONE foreboding, oblique dream-portent (NOT options,
        NOT a recap). This is the DM's conflict signal delivered as a dream — fired primarily when the
        player SLEEPS (see consolidate_on_rest), exposed here for a deliberate rest/dream. Pure read.
        Body: { sid?, present?[keys], you? } → { dream }."""
        from ..server.services.story_sessions import load_session
        from . import state_engine as _SE
        from . import stage_tools as _ST

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        sid = body.get("sid") or f"play-{key}"
        ws = _SE.world_of((load_session(ctx.root, sid) or {}).get("state"))
        dream = _ST.generate_dream(ctx, key, ws, present=body.get("present"), you=(body.get("you") or "").strip())
        return {"dream": dream}

    @app.post("/api/stories/{key}/geography")
    def story_geography(key: str, body: dict):
        """Generate the story's LIVED GEOGRAPHY — areas → spots → who habitually occupies them
        (orbits) + travel notes — grounded in the cast's daily lives, weaving in existing
        locations. `apply` (default true) writes locations/places/fields.travel onto the story,
        which arms the whereabouts lines + the move rate-limiter in play (geography.py).
        Body: { model?, apply? } → { geography, applied, locations, places, travel }."""
        from .geography import gen_geography, apply_geography

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        provider, err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": err}, status_code=400)

        name_to_key, cast_lines = {}, []
        for m in st.cast:
            c = ctx.base_settings.characters.get(m.character)
            nm = (c.name if c else m.character) or m.character
            name_to_key[nm] = m.character
            cast_lines.append({"name": nm,
                               "about": ((c.system or "").splitlines()[0] if c else "")[:160]})
        try:
            geo = gen_geography(provider, premise=st.premise, tone=st.tone,
                                world_note=(st.storyboard.logline if st.storyboard else ""),
                                cast=cast_lines, existing=[l.name for l in st.locations],
                                root=ctx.root)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"geography generation failed: {exc}"}, status_code=500)
        if not geo:
            return JSONResponse({"error": "the model returned no geography — try another model."},
                                status_code=502)
        out = apply_geography(geo, st, name_to_key)
        applied = False
        if body.get("apply", True):
            fields = {**(st.fields or {}), "travel": out["travel"]}
            ctx.update_story_fields(key, {"locations": out["locations"], "fields": fields})
            applied = True
        return {"geography": geo, "applied": applied, **out}

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
            # 3. Persist the rewritten card (routes to the owning story DB if embedded, else global YAML).
            data = ctx._read_character_data(char_key) or {}
            data["name"] = revised["name"] or data.get("name") or char_key
            data["system"] = revised["persona"]
            data["fields"] = {**(data.get("fields") or {}), "role": revised["role"],
                              "appearance": revised["appearance"], "base_prompt": base_prompt}
            try:
                if height_cm:
                    data["fields"]["height_cm"] = int(height_cm)
            except (TypeError, ValueError):
                pass
            ctx._write_character_data(char_key, data)
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
        # Save onto the story (DB-backed or legacy YAML — routed).
        data = ctx._read_story_data(key)
        for l in data.get("locations", []):
            if l.get("id") == loc:
                l["background_prompt"] = prompt
        ctx._write_story_data(key, data)
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
        # L0 of the image card: the story's art style leads the scene prompt, so
        # locations and sprites come out of the same visual world.
        if (st.art_style or "").strip():
            prompt = f"{ctx.art_style(story_key=key)} {prompt}"
        # Go through the single role chokepoint so the active image preset's LoRA
        # look is injected, same as base/sprite renders.
        provider, model_id = ctx.role_image_provider("scene", (body or {}).get("image_model"))
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

    @app.post("/api/stories/{key}/scene/{sid}/background/candidate")
    async def scene_background_candidate(key: str, sid: str, body: dict):
        """Render ONE background candidate for a location's Scene (fresh seed each call).
        Returns a data URI — not saved. Mirrors the location background flow but the
        prompt comes from the scene's own background_prompt (the spot's empty plate)."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        scene = next((s for l in st.locations for s in (l.scenes or []) if s.id == sid), None)
        if scene is None:
            return JSONResponse({"error": "no such scene"}, status_code=404)
        prompt = (scene.background_prompt or scene.name or "").strip()
        if not prompt:
            return JSONResponse({"error": "scene has no background prompt"}, status_code=400)
        if (st.art_style or "").strip():   # L0: same style layer as sprites + locations
            prompt = f"{ctx.art_style(story_key=key)} {prompt}"
        provider, model_id = ctx.role_image_provider("scene", (body or {}).get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)
        try:
            png = await _render(provider, prompt, out_prefix=ctx.output_prefix_for(model_id, "scene", sid))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode()}

    @app.post("/api/stories/{key}/scene/{sid}/background/select")
    def select_scene_background(key: str, sid: str, body: dict):
        """Save a chosen candidate (base64 data URI) as the scene's background, writing
        it back into the right scene inside the story's locations."""
        st = ctx.base_settings.stories.get(key)
        if st is None or not any(s.id == sid for l in st.locations for s in (l.scenes or [])):
            return JSONResponse({"error": "no such story/scene"}, status_code=404)
        uri = (body or {}).get("data", "")
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        try:
            png = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad image data"}, status_code=400)
        d = ctx.story_bg_dir(key); d.mkdir(parents=True, exist_ok=True)
        fname = f"scene_{re.sub(r'[^a-z0-9_]+', '', sid.lower())}.png"
        (d / fname).write_bytes(png)
        url = f"/api/stories/{key}/bg/{fname}"
        data = ctx._read_story_data(key)
        for l in data.get("locations", []):
            for s in (l.get("scenes") or []):
                if s.get("id") == sid:
                    s["background"] = url
        ctx._write_story_data(key, data)
        return {"ok": True, "url": url}

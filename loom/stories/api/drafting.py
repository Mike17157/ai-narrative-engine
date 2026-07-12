"""Story drafting HTTP endpoints.

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
    def _chapter_ctx(sd: dict, idx: int) -> str:
        """Running context for drafting/consolidating a novel chapter: premise/tone/cast + the prior
        chapters' recaps (what's carried forward). This is the serial memory — never the whole book."""
        chapters = sd.get("chapters") or []
        cast = ", ".join(getattr(ctx.base_settings.characters.get(m.get("character")), "name", m.get("character"))
                         for m in (sd.get("cast") or []) if m.get("character")) or "(unspecified)"
        prior = "\n".join(
            f"Ch.{i + 1} {chapters[i].get('title', '') or ''}: {chapters[i].get('recap', '') or '(no recap)'}"
            for i in range(idx)) or "(this is the first chapter)"
        return (f"STORY: {sd.get('name')}\nPREMISE: {sd.get('premise', '')}\nTONE: {sd.get('tone', '')}\n"
                f"CAST: {cast}\n\nWHAT HAS HAPPENED SO FAR:\n{prior}")

    def _find_chapter(key: str, cid: str):
        """(story_dict, chapters_list, index) for a novel chapter, or (None, None, -1)."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return None, None, -1
        sd = st.model_dump()
        chapters = sd.get("chapters") or []
        idx = next((i for i, c in enumerate(chapters) if c.get("id") == cid), -1)
        return sd, chapters, idx

    @app.post("/api/stories/{key}/chapter/{cid}/draft")
    def chapter_draft(key: str, cid: str, body: dict):
        """Draft ONE novel chapter's prose (Narrative voice) from its harness + the running context.
        Serial-gated: the previous chapter must be `drafted`, so the book is never generated at once."""
        sd, chapters, idx = _find_chapter(key, cid)
        if idx < 0:
            return JSONResponse({"error": "no such chapter"}, status_code=404)
        if idx > 0 and chapters[idx - 1].get("status") != "drafted":
            return JSONResponse({"error": f"draft chapter {idx} first — chapters generate in order"},
                                status_code=409)
        provider, _systems = ctx.builder_ctx(body or {}, "chapter")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)
        ch = chapters[idx]
        beats = "; ".join(ch.get("beats") or []) or (ch.get("purpose") or "")
        system = ("You are the NARRATIVE voice writing a novel chapter by chapter. Render THIS chapter "
                  "as prose — vivid, in-scene, consistent with what came before. Don't summarize or skip "
                  "ahead, don't write headings or meta; just the chapter's prose.")
        prompt = (f"{_chapter_ctx(sd, idx)}\n\nCHAPTER {idx + 1} — {ch.get('title', '')}\n"
                  f"POV: {ch.get('pov', '')}\nSETTING: {ch.get('setting', '')}\n"
                  f"PURPOSE: {ch.get('purpose', '')}\nBEATS: {beats}\n\nWrite chapter {idx + 1} now.")
        try:
            res = provider.generate_text(system=system, prompt=prompt)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"draft failed: {exc}"}, status_code=500)
        draft = (res.text or "").strip()
        if not draft:
            return JSONResponse({"error": "model returned nothing"}, status_code=500)
        ch["draft"] = draft
        ch["status"] = "drafted"
        ctx.update_story_fields(key, {"chapters": chapters})
        return {"ok": True, "id": cid, "status": "drafted", "words": len(draft.split()), "draft": draft}

    @app.post("/api/stories/{key}/chapter/{cid}/consolidate")
    def chapter_consolidate(key: str, cid: str, body: dict):
        """Storymaster: distill a drafted chapter into a tight carry-forward `recap` (what changed,
        what now matters) — the input the NEXT chapter draws on."""
        sd, chapters, idx = _find_chapter(key, cid)
        if idx < 0:
            return JSONResponse({"error": "no such chapter"}, status_code=404)
        ch = chapters[idx]
        if ch.get("status") != "drafted" or not (ch.get("draft") or "").strip():
            return JSONResponse({"error": "draft the chapter before consolidating"}, status_code=409)
        provider, _systems = ctx.builder_ctx(body or {}, "consolidate")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)
        system = ("You are the Storymaster. Distill this chapter into a tight RECAP that the next "
                  "chapter will rely on: what changed, for whom, and what now matters going forward. "
                  "2-4 concrete sentences. No preamble.")
        try:
            res = provider.generate_text(system=system, prompt=(ch.get("draft") or "")[:8000])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"consolidate failed: {exc}"}, status_code=500)
        recap = (res.text or "").strip()
        if not recap:
            return JSONResponse({"error": "model returned nothing"}, status_code=500)
        ch["recap"] = recap
        ctx.update_story_fields(key, {"chapters": chapters})
        return {"ok": True, "id": cid, "recap": recap}

    import operator as _operator
    _COND_OPS = {">=": _operator.ge, "<=": _operator.le, "==": _operator.eq,
                 "!=": _operator.ne, ">": _operator.gt, "<": _operator.lt}
    _COND_RE = re.compile(r"^\s*(\w+)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

    def _cond_met(cond: str, state: dict) -> bool:
        """Cheap emergent-condition check — '<feature> <op> <number>' against the play state. This is
        the DETECT step; it's free (no model), so the runtime can watch every turn."""
        m = _COND_RE.match(cond or "")
        if not m:
            return False
        var, op, num = m.group(1), m.group(2), float(m.group(3))
        try:
            return _COND_OPS[op](float(state.get(var, 0) or 0), num)
        except Exception:  # noqa: BLE001
            return False

    @app.post("/api/stories/{key}/scene/{sid}/evaluate")
    def scene_evaluate(key: str, sid: str, body: dict):
        """The detect→ask→act decision for an AI-played VN scene (the same shape as the sleep/death
        detectors, generalized to scene-switching). DETECT which divergence triggers are armed: a
        `choice` trigger fires on the player's pick (deterministic); `emergent` triggers fire when a
        tracked feature crosses its threshold — and then we ASK the tool whether it's dramatically
        time to switch. Body: { state:{feature:num}, choice?:str, last?:str }.
        Returns { transition, branch, why, armed:[conditions] }."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        scene = next((s for s in (st.model_dump().get("scenes") or []) if s.get("id") == sid), None)
        if scene is None:
            return JSONResponse({"error": "no such scene"}, status_code=404)
        body = body or {}
        state = body.get("state") or {}
        choice = (body.get("choice") or "").strip().lower()
        last = (body.get("last") or "").strip()
        triggers = scene.get("triggers") or []

        # A player CHOICE fires deterministically — they decided.
        if choice:
            hit = next((t for t in triggers if t.get("kind") == "choice" and t.get("condition")
                        and (choice in t["condition"].strip().lower() or t["condition"].strip().lower() in choice)), None)
            if hit:
                return {"transition": True, "branch": hit.get("branch"), "why": "player choice",
                        "armed": [hit.get("condition")]}

        # EMERGENT conditions: detect (free), then ASK the tool whether it's time.
        armed = [t for t in triggers if t.get("kind") == "emergent" and _cond_met(t.get("condition"), state)]
        if not armed:
            return {"transition": False, "branch": None, "armed": []}
        provider, _systems = ctx.builder_ctx(body, "director")
        if provider is None:
            return JSONResponse({"error": _systems}, status_code=400)
        opts = "\n".join(f'- branch "{t.get("branch")}" (triggered by {t.get("condition")}): {t.get("intent")}'
                         for t in armed)
        schema = {"type": "object", "additionalProperties": False, "required": ["transition", "branch", "why"],
                  "properties": {"transition": {"type": "boolean"}, "branch": {"type": "string"},
                                 "why": {"type": "string"}}}
        system = ("You are the Dungeon Master deciding whether an AI-played scene should TRANSITION now. "
                  "One or more divergence conditions have triggered. Switch only if it is dramatically "
                  "earned given the scene's goal and what just happened — otherwise stay in the scene "
                  "and let it breathe. Pick from the triggered branches only.")
        prompt = (f"SCENE goal: {scene.get('goal', '')}\nTONE: {scene.get('tone', '')}\n"
                  f"WHAT JUST HAPPENED: {last or '(unspecified)'}\n\nTRIGGERED BRANCHES:\n{opts}\n\n"
                  "Decide whether to transition now, and to which branch.")
        try:
            out = (provider.generate_text(system=system, prompt=prompt, emits=schema).data) or {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"evaluate failed: {exc}"}, status_code=500)
        armed_branches = {t.get("branch") for t in armed}
        branch = out.get("branch") if out.get("branch") in armed_branches else armed[0].get("branch")
        go = bool(out.get("transition"))
        return {"transition": go, "branch": branch if go else None,
                "why": str(out.get("why") or "").strip(), "armed": [t.get("condition") for t in armed]}

    @app.post("/api/stories/extract-scenes")
    def story_extract_scenes(body: dict):
        """Stage 2 — extract neutral locations (pure backgrounds) from the beats."""
        from .pipeline import extract_locations

        provider, systems = ctx.builder_ctx(body or {}, "locations")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        body = body or {}
        board = body.get("board") or {}
        premise = (body.get("premise") or "").strip()
        try:
            return extract_locations(provider, board=board, premise=premise, systems=systems)
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
        from .pipeline import grounding as _G
        _craft = _G.MINIMALISM
        try:
            base = extract_protagonist(provider, name=ch.name, persona=ch.system or "",
                                       extras=extras, systems=systems, craft=_craft)
            out = extract_characters(provider, name=base["name"], persona=base["persona"],
                                     board=body.get("board") or {}, extras=extras,
                                     systems=systems,
                                     reference_card=base["persona"], craft=_craft)
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


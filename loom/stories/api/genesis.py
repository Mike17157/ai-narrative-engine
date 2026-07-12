"""Story genesis HTTP endpoints.

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
    _PREMISE_COMPONENTS = [
        ("root", "The root pressure",
         "the ONE standing force the whole story grows from — a world-level condition, not an event "
         "(an ecology at its limit, a faith in decline, a power running out). Everything below derives "
         "from it. Rewrite's is a planet that can no longer afford humanity; Embergloom's is fading magic."),
        ("question", "The question it forces",
         "the unanswerable moral question the root pressure puts to everyone, with two GENUINELY "
         "defensible answers — a real contest, not a theme-word (is humanity worth its cost to the "
         "world? is a father's life worth a kingdom's?). Characters embody the sides through their choices."),
        ("creeds", "The competing creeds",
         "the organized answers — the factions / faiths / orders that each embody one side of the "
         "question and believe they are SAVING everyone. This is where religion and ideology enter as "
         "STRUCTURE, not decoration. Each is internally righteous; there are no villains, only sides."),
        ("tragedy", "The tragic bind",
         "why the creeds cannot both win and why each is sympathetic — the reason the conflict destroys "
         "good people instead of resolving cleanly. Both are partly right; any victory is also a loss."),
        ("protagonist", "The protagonist in the crossfire",
         "the specific person caught between the creeds (not a type), and the LIE they live by that the "
         "conflict will test — where they start, and what belief the story will break in them."),
        ("stakes", "Stakes",
         "what is concretely lost if it goes wrong — at the scale of the WORLD and of this one person."),
        ("texture", "Tone & texture", "the mood, genre and sensory feel through which the system is lived"),
    ]

    @app.post("/api/stories/{key}/premise-parts/draft")
    def premise_parts_draft(key: str, body: dict):
        """AI-DRAFT premise components (protagonist / lie / inciting / opposition / stakes /
        texture). Body {component: id} drafts that ONE (even if already filled — a redraft);
        empty body drafts every empty component. Drafts from the premise + tone + themes + cast
        + the parts already written, so components stay consistent with each other. Returns
        {parts: {id: text}} — NOT saved; the overview's fields are the editing surface and the
        normal story PUT persists them."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        sd = st.model_dump()
        parts = {k: v for k, v in (sd.get("premise_parts") or {}).items() if (v or "").strip()}
        only = ((body or {}).get("component") or "").strip()
        want = ([c for c in _PREMISE_COMPONENTS if c[0] == only] if only
                else [c for c in _PREMISE_COMPONENTS if c[0] not in parts])
        if not want:
            return JSONResponse({"error": "unknown component" if only else "nothing to draft"},
                                status_code=400)
        cast = ", ".join(getattr(ctx.base_settings.characters.get(m.get("character")), "name", m.get("character"))
                         for m in (sd.get("cast") or []) if m.get("character")) or "(none yet)"
        written = "\n".join(f"- {cid}: {parts[cid]}" for cid, _l, _d in _PREMISE_COMPONENTS if cid in parts)
        # WORLD-FIRST: premise & theme is DISTILLED from the defined world, not struck before it. The
        # persisted world is the foundation — root ← its pressure/ache, creeds ← its forces, the rest
        # earned from the whole world + cast. A thin/empty world means there's little to distil from.
        from .genesis import world_full_brief
        world_block = world_full_brief(sd.get("world"))
        ctx_text = "\n".join(filter(None, [
            "THE DEFINED WORLD (the foundation — distil every component FROM it):\n" + world_block
            if world_block else "THE WORLD IS NOT YET DEFINED — say so; premise & theme should be built "
                                "AFTER the world, not before it.",
            f"Premise (a working synopsis, if any): {sd.get('premise') or '(empty)'}",
            f"Logline: {(sd.get('storyboard') or {}).get('logline') or ''}",
            f"Tone: {sd.get('tone') or ''}",
            f"Themes: {', '.join(sd.get('themes') or [])}",
            f"Cast: {cast}",
            f"Components already written:\n{written}" if written else "",
        ]))
        comp_lines = "\n".join(f"- {cid} ({label}): {desc}" for cid, label, desc in want)
        # NON-THINKING writer (the codebase paradigm): the causal structure lives in the architect
        # system prompt, not in visible CoT. v4pro at high reasoning BLEEDS its chain-of-thought into
        # the text field ("I notice the request asks…") — the scaffold does the thinking, not the model.
        provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no text model available"}, status_code=400)
        schema = {"type": "object", "additionalProperties": False, "required": ["parts"],
                  "properties": {"parts": {"type": "array", "items": {
                      "type": "object", "additionalProperties": False, "required": ["id", "text"],
                      "properties": {"id": {"type": "string", "enum": [c[0] for c in want]},
                                     "text": {"type": "string", "description": "1-2 concrete sentences"}}}}}}
        system = ("You are a story's THEMATIC ARCHITECT. Premise & theme is DISTILLED FROM THE DEFINED "
                  "WORLD above — never invented before it, never striking at the tragedy before the world "
                  "earns it. Read the whole world (its pressure/ache, its forces, its traditions, people, "
                  "and lived fragments) and DERIVE the causal engine from it: the `root` IS the world's "
                  "pressure/ache in one clean line; the `creeds` ARE the world's forces (use their names + "
                  "stances); the `question` is what that pressure asks of everyone; the `tragedy` is why "
                  "those forces cannot both win; protagonist/stakes/texture follow from the world + cast. "
                  "Do not add factions the world doesn't have. Draft each requested component as 1-2 "
                  "concrete sentences that FOLLOW FROM the world and the components already written — name "
                  "the world's names, pick its particulars, no vague archetypes. The creeds must each be "
                  "sympathetic and internally righteous (no villains, only sides); the tragedy must come "
                  "from both sides being partly right. NAMES: "
                  "every faction, creed, religion, order or organization is ONE coined word — never two "
                  "words, never 'The <Adjective> <Noun>' (Crownsworn, Unbound, Emberwake — NOT 'Harvest "
                  "Binding', NOT 'Severance Witnesses'). Stay consistent with what's written.")
        prompt = f"STORY SO FAR:\n{ctx_text}\n\nDRAFT THESE COMPONENTS:\n{comp_lines}"
        try:
            out = (provider.generate_text(system=system, prompt=prompt, emits=schema).data) or {}
        except Exception:  # noqa: BLE001 — reasoning channel can break structured output; retry plain
            try:
                provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
                out = (provider.generate_text(system=system, prompt=prompt, emits=schema).data) or {}
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"draft failed: {exc}"}, status_code=500)
        drafted = {p["id"]: p["text"].strip() for p in (out.get("parts") or [])
                   if isinstance(p, dict) and p.get("id") and (p.get("text") or "").strip()}
        return {"parts": drafted}

    # ── Relationship-first genesis (harnesses → web → derived stories) ────────────
    # See loom/stories/GENESIS.md. Premise is an OUTPUT: design unnamed harnesses, weave
    # the tension web, derive candidate stories, commit one (names the cast + writes the
    # Story). Steps 1-3 are stateless structured passes over client-held draft state; only
    # commit persists. All route through the `premise` builder chokepoint.

    @app.post("/api/stories/genesis/world")
    def genesis_world(body: dict):
        """Step 0 — author the WORLD frame (the stage, not the plot) from a one-line idea.
        Body: { seed?, model? } → { genre, tone, setting, situation }. See genesis.design_world."""
        from .genesis import design_world
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return design_world(provider, seed=body.get("seed", ""))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"world design failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/world/field")
    def genesis_world_field(body: dict):
        """Regenerate ONE field of the world frame (inline ↻). Body: { field, world?, seed?, model? }
        → { value }. Honors the per-request `model` override like every genesis step."""
        from .genesis import regen_world_field
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return {"value": regen_world_field(provider, body.get("world") or {},
                                               (body.get("field") or "").strip(), seed=body.get("seed", ""))}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"regen failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/harnesses")
    def genesis_harnesses(body: dict):
        """Step 1 — design N unnamed character harnesses around an optional `seed`, who BELONG to the
        authored `world`. Body: { seed?, n?, world?, model? } → { harnesses: [{id, role, …}] }."""
        from .genesis import design_harnesses
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        seed = body.get("seed", "")
        # Ground the cast in the SAME adaptation basis the workshop uses (wound→lie→coping) — so
        # genesis characters have real depth, not random traits. See GENESIS.md §6.
        from .pipeline import grounding as _G
        try:
            psyche = _G.ADAPTATION
        except Exception:  # noqa: BLE001 — grounding is best-effort; never block generation
            psyche = ""
        try:
            hs = design_harnesses(provider, seed=seed, n=body.get("n", 4), grounding=psyche,
                                  world=body.get("world") or "")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"harness design failed: {exc}"}, status_code=500)
        if not hs:
            return JSONResponse({"error": "the model returned no characters — it likely declined the "
                                 "prompt or doesn't support structured output. Pick a different model "
                                 "in the ⚙ picker (or your usual chat model) and try again."},
                                status_code=502)
        return {"harnesses": hs}

    @app.post("/api/stories/genesis/roles")
    def genesis_roles(body: dict):
        """Function-first cast — generate ONE focused harness per Truby dramatic role (protagonist / ally /
        opponent / false-ally / mirror), each a separate model run in context of the cast so far. See
        GENESIS.md §6. Body: { seed?, world?, model? } → { harnesses: [{id, function, role, …}] }."""
        from .genesis import design_by_role
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        seed = body.get("seed", "")
        from .pipeline import grounding as _G
        try:
            psyche = _G.ADAPTATION
        except Exception:  # noqa: BLE001 — grounding is best-effort; never block generation
            psyche = ""
        try:
            hs = design_by_role(provider, seed=seed, grounding=psyche, world=body.get("world") or "")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"role design failed: {exc}"}, status_code=500)
        if not hs:
            return JSONResponse({"error": "the model returned no characters — try a different model in "
                                 "the ⚙ picker."}, status_code=502)
        return {"harnesses": hs}

    @app.post("/api/stories/genesis/worldgen")
    def genesis_worldgen(body: dict):
        """BOTTOM-UP world gen: accrete N radically distinct, procedurally-ruled SYSTEMS that interlock
        (à la The Wandering Inn's faerie magic beside the [System]); then let a concrete opening EMERGE
        from their sharpest collision. Body: { seed?, n?, systems?, model? } → { systems, scenario }."""
        from .worldgen import accrete_systems, scenario_from_systems, gen_particulars
        body = body or {}
        provider, systems_err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems_err}, status_code=400)
        seed = body.get("seed", "")
        try:
            if body.get("mode") == "particulars":   # anti-slop: lived fragments, systems implicit
                sub = body.get("substrate") if isinstance(body.get("substrate"), dict) else None
                parts = gen_particulars(provider, seed, int(body.get("n") or 6), substrate=sub)
                if not parts:
                    return JSONResponse({"error": "the model returned nothing — try a different model."}, status_code=502)
                return {"particulars": parts}
            systems = body.get("systems") if isinstance(body.get("systems"), list) else accrete_systems(provider, seed, int(body.get("n") or 4))
            scenario = scenario_from_systems(provider, systems, seed) if systems else {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"worldgen failed: {exc}"}, status_code=500)
        if not systems:
            return JSONResponse({"error": "the model returned no systems — try a different model."}, status_code=502)
        return {"systems": systems, "scenario": scenario}

    @app.post("/api/stories/genesis/premise")
    def genesis_premise(body: dict):
        """Targeted PREMISE — a concrete dramatic situation (want/obstacle/stakes/spark), NOT a theme.
        Body: { seed?, model? } → { premise }."""
        from .worldgen import build_premise
        body = body or {}
        provider, err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": err}, status_code=400)
        try:
            p = build_premise(provider, body.get("seed", ""))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"premise failed: {exc}"}, status_code=500)
        if not p:
            return JSONResponse({"error": "the model returned no premise — try a different model."}, status_code=502)
        return {"premise": p}

    @app.post("/api/stories/genesis/substrate")
    def genesis_substrate(body: dict):
        """The invisible SUBSTRATE — the world's ache + a few real traditions + place + people (the skeleton
        the author knows, never shows). Body: { seed?, model? } → { substrate }."""
        from .worldgen import build_substrate
        body = body or {}
        provider, err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": err}, status_code=400)
        try:
            sub = build_substrate(provider, body.get("seed", ""))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"substrate failed: {exc}"}, status_code=500)
        if not sub:
            return JSONResponse({"error": "the model returned no substrate — try a different model."}, status_code=502)
        return {"substrate": sub}

    @app.post("/api/stories/genesis/scene-loop")
    def genesis_scene_loop(body: dict):
        """Loop-based scene gen — draft → critique → revise, N rounds, converging to the bar (no culling).
        Model-agnostic: tests whether looping lifts a WEAK model to strong-model quality. Body: { brief,
        rounds?, model?, critic_model?, } → { scene, trace, rounds_used }."""
        from .worldgen import loop_scene
        body = body or {}
        provider, systems_err = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems_err}, status_code=400)
        critic = provider
        cm = (body.get("critic_model") or "").strip()
        if cm:
            try:
                critic = ctx.text_provider_for(cm, {}, None) or provider
            except Exception:  # noqa: BLE001
                critic = provider
        try:
            out = loop_scene(provider, body.get("brief") or "", int(body.get("rounds") or 3), critic_provider=critic)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"scene-loop failed: {exc}"}, status_code=500)
        if not out.get("scene"):
            return JSONResponse({"error": "the model returned no scene — try a different model."}, status_code=502)
        return out

    @app.post("/api/stories/genesis/generate")
    async def genesis_generate(body: dict):
        """Grow a WHOLE story in one streamed pydantic-graph run — premise → substrate → particulars →
        cast → weave → scene, each a focused node. Returns {job}; GenStream renders per-node progress
        (events: {type:'node', node, status}). The per-step endpoints above stay for manual editing.
        Body: { seed?, world?, rounds?, model?, critic_model? }."""
        from .genesis_graph import (run_genesis, GenesisState, GenesisDeps, genesis_state_from,
                                     save_run, load_run)
        import uuid as _uuid
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        critic = provider
        cm = (body.get("critic_model") or "").strip()
        if cm:
            try:
                critic = ctx.text_provider_for(cm, {}, None) or provider
            except Exception:  # noqa: BLE001
                critic = provider
        seed = body.get("seed", "")
        # RESUME: a run is keyed by run_id; its checkpoint (the accumulated graph state) is stored as
        # configs/genesis_runs/<run_id>.json. Reload it → completed nodes short-circuit, rest re-run.
        run_id = (body.get("run_id") or "").strip() or _uuid.uuid4().hex[:12]
        saved = load_run(ctx.root, run_id) if body.get("run_id") else None
        if isinstance(saved, dict) and saved:
            state = genesis_state_from(saved)
        else:
            # Ground the cast in the adaptation basis (wound→lie→coping, same as genesis_roles).
            from .pipeline import grounding as _G
            try:
                psyche = _G.ADAPTATION
            except Exception:  # noqa: BLE001 — grounding is best-effort
                psyche = ""
            state = GenesisState(seed=seed, world=body.get("world") or "", grounding=psyche,
                                 rounds=int(body.get("rounds") or 2))

        def work(emit, cancelled):
            import asyncio as _aio
            emit({"type": "run", "run_id": run_id})    # client keeps this to resume an interrupted run
            def checkpoint(state_dict):
                save_run(ctx.root, run_id, state_dict)
            deps = GenesisDeps(provider=provider, root=ctx.root, critic_provider=critic,
                               on_event=emit, cancel=cancelled, on_checkpoint=checkpoint)
            return _aio.run(run_genesis(state, deps))

        job = _start_stream_job("genesis", "Grow story", (seed[:60] or "story"), "stories/genesis", work)
        return {"job": job.id}

    @app.post("/api/stories/genesis/systems")
    async def genesis_systems(body: dict):
        """Bottom-up WORLD gen as a streamed pydantic-graph run — accrete interlocking systems →
        scenario. Returns {job}; GenStream renders per-node progress. Body: { seed?, n?, model? }.
        (The sync /genesis/worldgen stays for the particulars mode + non-streamed callers.)"""
        from .genesis_graph import run_systems, SystemsState, GenesisDeps
        body = body or {}
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        state = SystemsState(seed=body.get("seed", ""), n=int(body.get("n") or 4))

        def work(emit, cancelled):
            import asyncio as _aio
            deps = GenesisDeps(provider=provider, on_event=emit, cancel=cancelled)
            return _aio.run(run_systems(state, deps))

        job = _start_stream_job("genesis", "World systems", (body.get("seed", "")[:60] or "world"),
                                "stories/genesis", work)
        return {"job": job.id}

    @app.post("/api/stories/genesis/weave")
    def genesis_weave(body: dict):
        """Step 2 — wire the tension web between harnesses.
        Body: { harnesses:[…], model? } → { relationships:[…] }."""
        from .genesis import weave_relationships
        body = body or {}
        harnesses = body.get("harnesses") or []
        if len(harnesses) < 2:
            return JSONResponse({"error": "need at least 2 harnesses"}, status_code=400)
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return {"relationships": weave_relationships(provider, harnesses)}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"weave failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/formalize")
    def genesis_formalize(body: dict):
        """Formalize a RATIFIED character's prose into structure + its relationships (the draft cast
        queue ratify step). Body: { persona, role?, others?:[names], model? }
        → { temperament, want, lie, wound, secret, relationships:[{target, nature, dynamic, stance, note}] }."""
        from .genesis import formalize_harness
        body = body or {}
        blurb = (body.get("persona") or body.get("blurb") or "").strip()
        if not blurb:
            return JSONResponse({"error": "no character text to formalize"}, status_code=400)
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return formalize_harness(provider, blurb, role=body.get("role", ""),
                                     others=body.get("others") or [], world=body.get("world") or "")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"formalize failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/potentials")
    def genesis_potentials(body: dict):
        """Suggest relationship POTENTIALS between two characters (the story seed) — each the hidden
        COMMON CORE + a wanted TRAJECTORY/tone. Body: { a, b, n? } (a/b are character dicts:
        name/persona/want/lie/wound) → { potentials: [{common, trajectory, nature, stance}] }."""
        from .genesis import suggest_potentials
        body = body or {}
        a, b = body.get("a") or {}, body.get("b") or {}
        if not a or not b:
            return JSONResponse({"error": "need two characters"}, status_code=400)
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            return {"potentials": suggest_potentials(provider, a, b, n=body.get("n", 3))}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"potentials failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/face")
    async def genesis_face(body: dict):
        """Best-effort FACE PORTRAIT for a draft (keyless) genesis character → a node avatar.
        Body: { name, persona|background, appearance?, model?, image_model? } → { image: dataURI,
        appearance } or { error }. Drafts live in browser state (no key/disk), so nothing is saved;
        the frontend stores the data URI on the harness and clips it into the graph node."""
        body = body or {}
        persona = (body.get("persona") or body.get("background") or "").strip()
        name = (body.get("name") or "").strip()
        appearance = (body.get("appearance") or "").strip()
        if not persona and not appearance:
            return JSONResponse({"error": "need a persona or appearance"}, status_code=400)
        # 1) Booru identity tags (FACE-focused). Use an explicit appearance if given, else infer the
        #    persistent face/identity from the persona (Illustrious wants tags, not prose).
        if not appearance:
            author = ctx.author_provider(body.get("model"))
            if author is None:
                return JSONResponse({"error": "no author model configured"}, status_code=400)
            from .pipeline._helpers import _TAG_RULE
            system = ("You output a short Danbooru tag list describing ONLY a single character's FACE "
                      "and persistent identity for an Illustrious/SDXL anime portrait: sex + honest age, "
                      "hair (colour/length/style), eyes (colour/shape), skin tone, and 1-2 distinguishing "
                      "facial hooks (freckles, a mole, glasses, a scar). NO clothing, NO background, NO "
                      "pose, NO expression. Output ONLY the comma-separated tags, nothing else.\n\n" + _TAG_RULE)
            prompt = f"CHARACTER: {name}\n\n{persona}" if name else persona
            try:
                appearance = (author.generate_text(system=system, prompt=prompt).text or "").strip()
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"appearance failed: {exc}"}, status_code=500)
        appearance = appearance.strip().strip('"').strip()
        if not appearance:
            return JSONResponse({"error": "no appearance tags"}, status_code=500)
        # 2) Render a TIGHT face close-up (the node crop is a small circle — the face must fill it, not
        #    the torso). A SQUARE latent at the model's NATIVE SDXL resolution (1024²) — NOT a small ad-hoc
        #    size, which under-resolves the face; square so the circular crop has no bias. Face-focus tags
        #    keep the head centred.
        from ..server.services.poses import ASPECT_DIMS
        prompt = (appearance + ", solo, portrait, close-up, face focus, looking at viewer, "
                  "detailed face, head shot, simple background")
        provider, model_id = ctx.role_image_provider("base", body.get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)
        try:
            png = await _render(provider, prompt, latent=ASPECT_DIMS["square"])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode(), "appearance": appearance}

    @app.post("/api/stories/genesis/derive")
    def genesis_derive(body: dict):
        """Step 3 — derive candidate stories from the web (premise as output).
        Body: { harnesses:[…], relationships:[…], steer?, model? } → { candidates:[…] }."""
        from .genesis import derive_stories
        body = body or {}
        harnesses = body.get("harnesses") or []
        if not harnesses:
            return JSONResponse({"error": "no harnesses"}, status_code=400)
        provider, systems = ctx.builder_ctx(body, "premise")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)
        try:
            cands = derive_stories(provider, harnesses, body.get("relationships") or [],
                                   steer=body.get("steer", ""))
            return {"candidates": cands}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"derive failed: {exc}"}, status_code=500)

    @app.post("/api/stories/genesis/commit")
    def genesis_commit(body: dict):
        """Step 4 — commit a chosen candidate: name the anchor harnesses into real characters,
        rewrite edge ids → character keys, persist the Story. Body: { candidate, harnesses,
        relationships, name?, type? } → { ok, key }."""
        from ..server.services import story_store as _SS
        from .genesis import name_cast, persona_from_harness, compose_world

        body = body or {}
        cand = body.get("candidate") or {}
        by_id = {h.get("id"): h for h in (body.get("harnesses") or []) if h.get("id")}
        rels = body.get("relationships") or []
        if not cand or not by_id:
            return JSONResponse({"error": "need candidate + harnesses"}, status_code=400)

        anchors = [a for a in (cand.get("anchors") or []) if a in by_id]
        prot = cand.get("protagonist") if cand.get("protagonist") in by_id else (anchors[0] if anchors else None)
        if prot is None:
            return JSONResponse({"error": "candidate has no valid protagonist"}, status_code=400)
        if prot not in anchors:
            anchors = [prot] + anchors

        # New stories are born as ONE self-contained <skey>.json with characters EMBEDDED. Compute
        # the key FIRST + create the file so write_npc can embed each anchor into it. See story_db.py.
        ctx.story_dir().mkdir(parents=True, exist_ok=True)
        name = (body.get("name") or cand.get("title") or "Story").strip()
        existing_names = {st.name for st in ctx.base_settings.stories.values()}
        base_name, j = name, 2
        while name in existing_names:
            name, j = f"{base_name} ({j})", j + 1
        skey_base = re.sub(r"[^\w\-]+", "_", name.lower()).strip("_") or "story"
        skey, i = skey_base, 2
        while _SS.story_exists(ctx.root, skey):
            skey, i = f"{skey_base}_{i}", i + 1
        stype = body.get("type") if body.get("type") in ("novel", "vn") else "novel"
        # The DEFINED WORLD survives commit now (it used to be discarded) — the permanent foundation
        # premise & theme distils from. Composed from the genesis draft: frame + substrate + particulars.
        world = compose_world(body.get("world"), body.get("substrate"), body.get("particulars"))
        story_dict = {
            "name": name, "type": stype, "premise": cand.get("premise", ""),
            "tone": cand.get("tone", ""), "themes": cand.get("themes") or [],
            "storyboard": {"logline": cand.get("logline", "")}, "world": world,
            "fields": {"source": "genesis", "dramatic_question": cand.get("dramatic_question", "")},
        }
        from ..config.schema import Story
        Story(**story_dict)   # validate before writing — closes the genesis bypass
        ctx.story_dir().mkdir(parents=True, exist_ok=True)   # keep the folder for assets
        _SS.save_story(ctx.root, skey, story_dict, {})

        # Name the anchors (commit is the first time harnesses get names) + embed them into the DB.
        nprov, _systems = ctx.builder_ctx(body, "characters")
        names = name_cast(nprov, [by_id[a] for a in anchors]) if nprov is not None else {}
        id_to_key: dict[str, str] = {}
        for idx, hid in enumerate(anchors):
            h = by_id[hid]
            nm = (h.get("name") or "").strip() or (names.get(hid) or {}).get("name") \
                or h.get("role") or f"Character {idx + 1}"
            id_to_key[hid] = ctx.write_npc({
                "name": nm, "persona": persona_from_harness(h),
                "appearance": (names.get(hid) or {}).get("appearance", ""), "role": h.get("role", ""),
                "want": h.get("want", ""), "lie": h.get("lie", ""),
                "contradiction": h.get("contradiction", ""),
                "wound": h.get("wound", ""), "secret": h.get("secret", ""),
            }, story_key=skey)                            # embeds into <skey>.json

        cast = [{"character": id_to_key[hid], "primary": (hid == prot)} for hid in anchors]
        out_rels = []
        for i, e in enumerate(rels):
            s, t = id_to_key.get(e.get("source")), id_to_key.get(e.get("target"))
            if not s or not t or s == t:
                continue  # edge touches a harness that didn't make the cast — drop it
            out_rels.append({"id": f"r{i + 1}", "source": s, "target": t,
                             "nature": e.get("nature", ""), "dynamic": e.get("dynamic", ""),
                             "stance": e.get("stance", "neutral"), "note": e.get("note", "")})
        try:
            ctx.update_story_fields(skey, {"cast": cast, "relationships": out_rels})
        except Exception as exc:  # noqa: BLE001
            _SS.delete_story(ctx.root, skey)   # rollback: drop the half-written story from the store
            return JSONResponse({"error": f"could not save story: {exc}"}, status_code=400)
        return {"ok": True, "key": skey}

    @app.post("/api/stories/{key}/genesis/add")
    def genesis_add(key: str, body: dict):
        """ADD generated characters to an EXISTING story (the in-Structure generate tools): name the
        confirmed harnesses, create characters, append to the cast + relationships. Same naming/
        write_npc path as commit, but onto a live story. Body: { harnesses, relationships? } → { ok }."""
        from .genesis import name_cast, persona_from_harness

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        by_id = {h.get("id"): h for h in (body.get("harnesses") or []) if h.get("id")}
        if not by_id:
            return JSONResponse({"error": "no harnesses"}, status_code=400)

        nprov, _systems = ctx.builder_ctx(body, "characters")
        names = name_cast(nprov, list(by_id.values())) if nprov is not None else {}
        id_to_key: dict[str, str] = {}
        for idx, (hid, h) in enumerate(by_id.items()):
            nm = (h.get("name") or "").strip() or (names.get(hid) or {}).get("name") \
                or h.get("role") or f"Character {idx + 1}"
            id_to_key[hid] = ctx.write_npc({
                "name": nm, "persona": persona_from_harness(h),
                "appearance": (names.get(hid) or {}).get("appearance", ""), "role": h.get("role", ""),
                "want": h.get("want", ""), "lie": h.get("lie", ""),
                "contradiction": h.get("contradiction", ""),
                "wound": h.get("wound", ""), "secret": h.get("secret", ""),
            }, story_key=key)

        sd = st.model_dump()
        cast = (sd.get("cast") or []) + [{"character": k, "primary": False} for k in id_to_key.values()]
        rels = list(sd.get("relationships") or [])
        base = len(rels)
        for i, e in enumerate(body.get("relationships") or []):
            s, t = id_to_key.get(e.get("source")), id_to_key.get(e.get("target"))
            if not s or not t or s == t:
                continue  # only edges among the newly-added characters (drafts use harness ids)
            rels.append({"id": f"r{base + i + 1}", "source": s, "target": t,
                         "nature": e.get("nature", ""), "dynamic": e.get("dynamic", ""),
                         "stance": e.get("stance", "neutral"), "note": e.get("note", "")})
        try:
            ctx.update_story_fields(key, {"cast": cast, "relationships": rels})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not add to cast: {exc}"}, status_code=400)
        return {"ok": True, "added": list(id_to_key.values())}


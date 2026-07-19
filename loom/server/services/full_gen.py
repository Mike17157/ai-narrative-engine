"""Headless end-to-end character generation — the orchestrator the UI's click-sequence does, in one
call. Chains: flesh (if thin) → base prompt → base image (reference) → one outfit → compose
expressions + poses → render the full emotion sprite set. Everything lands on disk in the logical
paths (configs/characters/<key>.ref.png, configs/characters/portraits/<key>/<outfit>/...). Synchronous
(blocking renders) so it runs as a streamed job OR straight from the CLI. Needs ComfyUI + a text model.
"""

from __future__ import annotations

import re

import yaml


def render_reference(ctx, key: str) -> str:
    """Compose the base-image prompt for an ALREADY-MINTED character and render its reference
    portrait → configs/characters/<key>.ref.png (also stores base_prompt on the card). The
    standalone portrait step from generate_full_character — used by the autonomous creator to
    kick off a portrait right after minting. Synchronous (blocking render). Returns the filename.
    Raises on failure (caller decides whether to treat it best-effort)."""
    from loom.comfy.server import get_server
    from loom.stories.pipeline import compose_base_prompt

    from .images import _clean_reference_png, _randomize_seeds

    ch = ctx.base_settings.characters.get(key)
    if ch is None:
        raise ValueError(f"no such character {key!r}")
    fields = ch.fields or {}
    cfg = ctx.load_story_builder()
    comp = compose_base_prompt(ctx.stage_provider("base_image"), ch.name, ch.system or "",
                               fields.get("appearance", ""), fields.get("role", ""),
                               systems=(cfg.get("systems") or {}))
    base_prompt = comp.get("prompt", "") if isinstance(comp, dict) else ""
    if not base_prompt:
        raise ValueError("base prompt generation failed")

    data = ctx._read_character_data(key)   # owning story DB (embedded) or global YAML
    if data is not None:   # persist the base prompt on the card
        data.setdefault("fields", {})["base_prompt"] = base_prompt
        ctx._write_character_data(key, data)

    iprov, mid = ctx.role_image_provider("base")
    if iprov is None:
        raise ValueError(mid)
    get_server(iprov.base_url).ensure_up()
    _randomize_seeds(iprov.workflow)
    res = iprov.generate_image(prompt=base_prompt, latent=ctx.pose_latent("neutral"),
                               out_prefix=ctx.output_prefix_for(mid, "base", key))
    if not res.images:
        raise RuntimeError("portrait render produced no image")
    _d = ctx.char_asset_dir(key); _d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-]+", "", key)
    (_d / f"{safe}.ref.png").write_bytes(_clean_reference_png(res.images[0]))
    ctx.reload_settings()
    return f"{safe}.ref.png"


def generate_full_character(ctx, key: str, emit=None, cancelled=None) -> dict:
    from ...comfy.server import get_server
    from ...config.schema import Character
    from .emotions import EMOTION_KEYS, EMOTION_LABELS
    from .images import _clean_reference_png, _randomize_seeds
    from .prompts import _regionize_prompt, _safe_image_tags, _snap_prompt

    emit = emit or (lambda e: None)
    cancelled = cancelled or (lambda: False)

    ch = ctx.base_settings.characters.get(key)
    if ch is None:
        raise ValueError(f"no such character '{key}'")

    # 1. flesh a thin seed into disciplined prose (the source every section parses)
    emit({"type": "phase", "label": "Fleshing the persona"})
    ctx.ensure_fleshed(key)
    ch = ctx.base_settings.characters.get(key)
    persona = ch.system or ""
    fields = ch.fields or {}

    # 2. base-image prompt (grounds the look to tags)
    emit({"type": "phase", "label": "Composing the base prompt"})
    from loom.stories.pipeline import compose_base_prompt
    from loom.server.services import config_files
    _bp_cfg = ctx.load_story_builder()
    _bp_prov = ctx.stage_provider("base_image")
    comp = compose_base_prompt(_bp_prov, ch.name, persona, fields.get("appearance", ""),
                               fields.get("role", ""), systems=(_bp_cfg.get("systems") or {}))
    base_prompt = comp.get("prompt", "") if isinstance(comp, dict) else ""
    if not base_prompt:
        raise ValueError(comp.get("error", "base prompt generation failed") if isinstance(comp, dict) else "base prompt failed")
    data = ctx._read_character_data(key) or {}   # owning story DB (embedded) or global YAML
    data.setdefault("fields", {})["base_prompt"] = base_prompt
    height_cm = (comp.get("features") or {}).get("height_cm")
    if height_cm:
        try:
            data["fields"]["height_cm"] = int(height_cm)
        except (TypeError, ValueError):
            pass
    ctx._write_character_data(key, data)
    appearance = (ctx.base_settings.characters[key].fields or {}).get("appearance", "") or base_prompt
    emit({"type": "item", "name": "base prompt", "text": base_prompt})
    if cancelled():
        return {"cancelled": True}

    # 3. base image → the character's reference (.ref.png)
    emit({"type": "phase", "label": "Rendering the base image"})
    provider, mid = ctx.role_image_provider("base")
    if provider is None:
        raise ValueError(mid)
    get_server(provider.base_url).ensure_up()
    _randomize_seeds(provider.workflow)
    res = provider.generate_image(prompt=base_prompt, latent=ctx.pose_latent("neutral"),
                                  out_prefix=ctx.output_prefix_for(mid, "base", key))
    if res.images:
        _d = ctx.char_asset_dir(key); _d.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-]+", "", key)
        (_d / f"{safe}.ref.png").write_bytes(_clean_reference_png(res.images[0]))
        emit({"type": "item", "name": "base image", "text": "saved reference"})
    if cancelled():
        return {"cancelled": True}

    # 4. persona-driven expressions + body language
    emit({"type": "phase", "label": "Composing expressions + poses"})
    from loom.stories.pipeline import compose_expressions, compose_poses, compose_outfit_prompt
    _w_cfg = ctx.load_story_builder()
    _w_prov = ctx.stage_provider("wardrobe")
    exprs = compose_expressions(_w_prov, persona)
    poses = compose_poses(_w_prov, persona, ctx.load_pose_library())

    # 5. one default outfit → manifest
    emit({"type": "phase", "label": "Composing the outfit"})
    attire = (compose_outfit_prompt(_w_prov, persona, appearance, "Casual", "") or {}).get("attire", "")
    oid = "casual"
    m = ctx.portrait_manifest(key)
    m["appearance"] = appearance
    m["expression_prompts"] = exprs
    m["pose_prompts"] = poses
    m["outfits"] = [{"id": oid, "name": "Casual", "instruction": "", "prompt": attire,
                     "attire_prompt": attire, "expressions": {}}]
    ctx.save_portrait_manifest(key, m)
    odir = ctx.portrait_dir(key, create=True) / oid
    odir.mkdir(parents=True, exist_ok=True)

    sprov, smid = ctx.role_image_provider("sprite")
    if sprov is None:
        raise ValueError(smid)
    get_server(sprov.base_url).ensure_up()

    # 5b. outfit full-body image (neutral pose)
    emit({"type": "phase", "label": "Rendering the outfit image"})
    full = _regionize_prompt(_snap_prompt(_safe_image_tags(", ".join(
        p for p in (appearance, attire, ctx.pose_tags(key, "neutral"), ctx.pose_framing("neutral")) if p))))
    _randomize_seeds(sprov.workflow)
    r = sprov.generate_image(prompt=full, latent=ctx.pose_latent("neutral"),
                             out_prefix=ctx.output_prefix_for(smid, "outfit", key))
    if r.images:
        (odir / "base.png").write_bytes(r.images[0])
        m["outfits"][0]["base"] = "base.png"
        ctx.save_portrait_manifest(key, m)

    # 6. the full emotion sprite set — fanned out through the shared dispatcher
    #    (concurrent on the serverless endpoint / local GPU, with progress + cancel).
    from .batch_images import render_batch

    emit({"type": "phase", "label": f"Rendering {len(EMOTION_KEYS)} sprites"})
    sprite_prompts = [{
        "prompt": _regionize_prompt(_snap_prompt(_safe_image_tags(", ".join(
            p for p in (appearance, attire, (exprs.get(emo) or emo),
                        ctx.pose_tags(key, emo), ctx.pose_framing(emo)) if p)))),
        "latent": ctx.pose_latent(emo),
    } for emo in EMOTION_KEYS]
    results = render_batch(
        sprov, sprite_prompts,
        out_prefix_template=ctx.output_prefix_for(smid, "sprite", key),
        cancel=cancelled,
        on_progress=lambda d, t: emit({"type": "progress", "done": d, "total": t}),
    )
    done = 0
    for emo, png in zip(EMOTION_KEYS, results):
        if png:
            (odir / f"{emo}.png").write_bytes(png)
            m["outfits"][0]["expressions"][emo] = f"{emo}.png"
            done += 1
    ctx.save_portrait_manifest(key, m)
    emit({"type": "phase", "label": f"Done — {ch.name}: base + 1 outfit + {done} sprites"})
    return {"ok": True, "character": key, "name": ch.name, "sprites": done}

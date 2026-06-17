"""Headless end-to-end character generation — the orchestrator the UI's click-sequence does, in one
call. Chains: flesh (if thin) → base prompt → base image (reference) → one outfit → compose
expressions + poses → render the full emotion sprite set. Everything lands on disk in the logical
paths (configs/characters/<key>.ref.png, configs/characters/portraits/<key>/<outfit>/...). Synchronous
(blocking renders) so it runs as a streamed job OR straight from the CLI. Needs ComfyUI + a text model.
"""

from __future__ import annotations

import re

import yaml


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
    comp = ctx.compose_base_prompt(ch.name, persona, fields.get("appearance", ""), fields.get("role", ""))
    base_prompt = comp.get("prompt", "") if isinstance(comp, dict) else ""
    if not base_prompt:
        raise ValueError(comp.get("error", "base prompt generation failed") if isinstance(comp, dict) else "base prompt failed")
    safe = re.sub(r"[^\w\-]+", "", key)
    path = ctx.char_dir() / f"{safe}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.is_file() else {}
    data.setdefault("fields", {})["base_prompt"] = base_prompt
    height_cm = (comp.get("features") or {}).get("height_cm")
    if height_cm:
        try:
            data["fields"]["height_cm"] = int(height_cm)
        except (TypeError, ValueError):
            pass
    Character(**data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    ctx.reload_settings()
    appearance = (ctx.base_settings.characters[key].fields or {}).get("appearance", "") or base_prompt
    emit({"type": "item", "name": "base prompt", "text": base_prompt})
    if cancelled():
        return {"cancelled": True}

    # 3. base image → the character's reference (.ref.png)
    emit({"type": "phase", "label": "Rendering the base image"})
    provider, mid = ctx.image_provider(ctx.role_model("base"))
    if provider is None:
        raise ValueError(mid)
    get_server(provider.base_url).ensure_up()
    _randomize_seeds(provider.workflow)
    res = provider.generate_image(prompt=base_prompt, latent=ctx.pose_latent("neutral"),
                                  out_prefix=ctx.output_prefix_for(mid, "base", key))
    if res.images:
        (ctx.char_dir() / f"{safe}.ref.png").write_bytes(_clean_reference_png(res.images[0]))
        emit({"type": "item", "name": "base image", "text": "saved reference"})
    if cancelled():
        return {"cancelled": True}

    # 4. persona-driven expressions + body language
    emit({"type": "phase", "label": "Composing expressions + poses"})
    exprs = ctx.compose_expressions(persona)
    poses = ctx.compose_poses(persona)

    # 5. one default outfit → manifest
    emit({"type": "phase", "label": "Composing the outfit"})
    attire = (ctx.compose_outfit_prompt(persona, appearance, "Casual", "") or {}).get("attire", "")
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

    sprite_model = ctx.role_model("sprite")
    sprov, smid = ctx.image_provider(sprite_model)
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

    # 6. the full emotion sprite set
    done = 0
    for i, emo in enumerate(EMOTION_KEYS):
        if cancelled():
            break
        emit({"type": "phase", "label": f"Sprite {i + 1}/{len(EMOTION_KEYS)} — {EMOTION_LABELS[emo]}"})
        expr = exprs.get(emo) or emo
        prompt = _regionize_prompt(_snap_prompt(_safe_image_tags(", ".join(
            p for p in (appearance, attire, expr, ctx.pose_tags(key, emo), ctx.pose_framing(emo)) if p))))
        try:
            prov2, _mid = ctx.image_provider(sprite_model)  # fresh workflow/seed per render
            _randomize_seeds(prov2.workflow)
            rr = prov2.generate_image(prompt=prompt, latent=ctx.pose_latent(emo),
                                      out_prefix=ctx.output_prefix_for(smid, "sprite", key))
            if rr.images:
                (odir / f"{emo}.png").write_bytes(rr.images[0])
                m["outfits"][0]["expressions"][emo] = f"{emo}.png"
                done += 1
        except Exception as exc:  # noqa: BLE001 — one sprite failing must not sink the run
            emit({"type": "phase", "label": f"  {emo} skipped ({exc})"})
    ctx.save_portrait_manifest(key, m)
    emit({"type": "phase", "label": f"Done — {ch.name}: base + 1 outfit + {done} sprites"})
    return {"ok": True, "character": key, "name": ch.name, "sprites": done}

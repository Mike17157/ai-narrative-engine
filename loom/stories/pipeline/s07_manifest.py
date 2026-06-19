"""Step 07 — Manifest: apply a planned wardrobe to the portrait manifest, composing persona-derived
prompts (expressions, poses, affect) when they are absent.

apply_manifest  — merge outfits + compose missing prompts → save manifest
plan_and_apply  — full pipeline wrapper: plan → refine → apply

Cascade flags
─────────────
apply_manifest(..., compose_persona=True)
    True  (default) — compose expressions/poses/affect when absent from the manifest.
    False — skip persona composition entirely; only merge outfits and explicit body overrides.
    Useful when you want to push a fresh outfit set without re-running the persona LLM calls,
    or when you intend to call compose_expressions/poses/affect separately.

plan_and_apply(..., cascade=True)
    True  (default) — full plan → refine → apply pipeline.
    False — plan only; return the raw [{name, concept}] list without refining or applying.
    Useful for a "preview concepts" UI step where the user reviews the plan before committing.
"""

from __future__ import annotations

import re as _re


def apply_manifest(ctx, key: str, body: dict, *, provider=None,
                   compose_persona: bool = True) -> dict:
    """Merge a planned wardrobe into the character's portrait manifest.

    Composes canonical expression prompts, per-character body-language poses, and the
    personality-rooted emotion range from the persona whenever they are absent from the manifest
    — unless `compose_persona=False`, which skips those LLM calls entirely.

    Body keys `expressions`, `poses`, `affect` override / merge those fields explicitly
    regardless of `compose_persona`.

    With `replace: true` the manifest is reset first — existing outfits and their sprite
    directories are deleted, and the persona-derived prompt caches (expressions, poses, affect)
    are cleared so they are recomposed fresh from the current persona.

    `provider` (optional): if supplied, used for persona composition; otherwise resolved from
    ctx's story_builder config (wardrobe stage for expressions/poses, emotion stage for affect).
    """
    from loom.server.services.prompts import _persona_text, _regionize_prompt, _safe_image_tags, _snap_prompt

    c = ctx.base_settings.characters.get(key)
    if c is None:
        raise ValueError(f"no such character: {key!r}")
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
        m["expression_prompts"] = {}
        m["pose_prompts"] = {}
        m.pop("affect", None)

    def _resolve(stage: str):
        if provider is not None:
            return provider
        try:
            from loom.server.services import config_files
            cfg = ctx.load_story_builder()
            return ctx.author_provider(config_files._stage_model(cfg, stage))
        except Exception:  # noqa: BLE001
            return None

    persona_text = _persona_text(c)

    # Canonical expression prompts (fixed 44-emotion taxonomy, character-level).
    canon = dict(m.get("expression_prompts") or {})
    body_exprs = body.get("expressions") if isinstance(body.get("expressions"), dict) else None
    if body_exprs:
        canon.update(body_exprs)
    if not canon and compose_persona:
        from .s08_expressions import compose_expressions
        try:
            canon = compose_expressions(_resolve("wardrobe"), persona_text)
        except Exception:  # noqa: BLE001
            canon = {}
    m["expression_prompts"] = canon

    # Per-character body-language poses (persona-driven, character-level).
    poses = dict(m.get("pose_prompts") or {})
    body_poses = body.get("poses") if isinstance(body.get("poses"), dict) else None
    if body_poses:
        poses.update(body_poses)
    if not poses and compose_persona:
        from .s09_poses import compose_poses
        try:
            poses = compose_poses(_resolve("wardrobe"), persona_text)
        except Exception:  # noqa: BLE001
            poses = {}
    m["pose_prompts"] = poses

    # Personality-rooted emotion range — the curated subset of keys this character expresses.
    affect = m.get("affect") if isinstance(m.get("affect"), dict) else None
    body_affect = body.get("affect") if isinstance(body.get("affect"), dict) else None
    if body_affect and isinstance(body_affect.get("range"), list):
        body_range = body_affect["range"]
        if body_range and isinstance(body_range[0], dict):
            body_range = [e["emotion"] for e in body_range if e.get("emotion")]
        affect = {"range": body_range}
    elif not (affect and affect.get("range")) and compose_persona:
        from .s10_affect import compose_affect_range
        try:
            systems = {}
            try:
                cfg = ctx.load_story_builder()
                systems = cfg.get("systems") or {}
            except Exception:  # noqa: BLE001
                pass
            affect = compose_affect_range(_resolve("emotion"), persona_text, systems=systems)
            if not isinstance(affect, dict) or not affect.get("range"):
                affect = None
        except Exception:  # noqa: BLE001
            affect = None
    if affect:
        m["affect"] = affect

    # Merge outfits — additive by default; duplicates (by name) are skipped.
    existing = {o.get("name", "").lower() for o in m.get("outfits", [])}
    for o in body.get("outfits", []) or []:
        nm = (o.get("name") or "").strip()
        if not nm or nm.lower() in existing:
            continue
        oid = _re.sub(r"[^\w\-]+", "-", nm.lower()).strip("-") or "outfit"
        base_oid, n = oid, 2
        ids = {x.get("id") for x in m["outfits"]}
        while oid in ids:
            oid, n = f"{base_oid}-{n}", n + 1
        attire = _regionize_prompt(_snap_prompt(_safe_image_tags((o.get("attire_prompt") or "").strip())))
        m["outfits"].append({
            "id": oid, "name": nm, "instruction": "",
            "prompt": attire, "attire_prompt": attire, "expressions": {},
            "unified": o.get("unified", False),
        })
        existing.add(nm.lower())

    ctx.save_portrait_manifest(key, m)
    return {"ok": True, "outfits": [o["name"] for o in m["outfits"]],
            "emotions": list(canon.keys())}


def plan_and_apply(ctx, provider, char_key: str, char, story: dict, systems: dict,
                   emit=None, replace: bool = True, cascade: bool = True) -> list | dict:
    """Wardrobe pipeline for one character.

    cascade=True  (default): plan → refine (parallel) → apply to manifest.
                             Returns the refined outfit list.
    cascade=False:           plan only; return the raw {outfits: [{name, concept}]} plan dict
                             without refining or applying. Useful for a preview/review step.
    """
    from .s05_wardrobe_plan import plan_wardrobe
    appr = (char.fields or {}).get("appearance", "")
    plan = plan_wardrobe(provider, char_name=char.name, persona=char.system or "",
                         appearance=appr, story=story, systems=systems, on_event=emit)
    if not cascade:
        return plan

    from .s06_wardrobe_refine import refine_outfits
    outfits = refine_outfits(provider, plan.get("outfits"), char.system or "", appr, emit=emit)
    apply_manifest(ctx, char_key, {"outfits": outfits, "replace": replace}, provider=provider)
    return outfits

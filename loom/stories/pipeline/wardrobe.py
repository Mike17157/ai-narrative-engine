"""Wardrobe: plan outfits per scene, refine into prose, generate per-outfit poses, apply to manifest.

Functions:
  plan_scene_wardrobe    — one location, all characters, event-justified outfits
  plan_story_wardrobe    — full story: one call per location
  plan_wardrobe          — legacy per-character planner (builder/test + manual endpoint)
  compose_outfit_prompt  — refine one outfit concept into full prose
  refine_outfits         — refine every outfit in parallel
  compose_poses          — character-level body-language tags per emotion (used by full_gen)
  compose_outfit_poses   — per-outfit body-language tags for all emotions (called when applying)
  apply_manifest         — apply outfits + emotions to the portrait manifest
  plan_and_apply         — convenience: plan → refine → apply for one character (legacy path)
"""

from __future__ import annotations

import re as _re
from concurrent.futures import ThreadPoolExecutor

from ._helpers import (
    SCENE_WARDROBE_SCHEMA, WARDROBE_SCHEMA, _call, _sys,
)


# ---------------------------------------------------------------------------
# Scene-based wardrobe planning
# ---------------------------------------------------------------------------

def plan_scene_wardrobe(provider, *, location: dict, beats: list[dict], cast: list[dict],
                        story: dict, systems: dict | None = None, on_event=None) -> dict:
    """Plan outfits for ALL characters in ONE location, justified by major events.

    One LLM call. Returns {location_id, location_name, outfits: [{character, outfit_name, concept, event}]}.
    """
    loc_name = location.get("name") or location.get("id") or "?"
    loc_id = location.get("id") or ""
    cast_lines = "\n".join(
        f"- {c['name']}: {(c.get('persona') or '')[:250].splitlines()[0]}"
        for c in cast if c.get("name")
    )
    beat_lines = "\n".join(
        f"- {b.get('title', '?')}: {b.get('summary', '')} "
        f"[present: {', '.join(b.get('characters') or [])}]"
        for b in beats
    ) or "(no chapters at this location)"
    if on_event:
        on_event({"type": "phase", "label": f"Wardrobe — {loc_name}"})
    prompt = (
        f"STORY: {story.get('premise', '')}\nTONE: {story.get('tone', '')}\n\n"
        f"LOCATION: {loc_name}\n{location.get('description', '')}\n\n"
        f"CAST:\n{cast_lines}\n\n"
        f"CHAPTERS AT THIS LOCATION:\n{beat_lines}\n\n"
        "Decide which events here (if any) justify a distinct outfit for any character present."
    )
    out = _call(provider, _sys(systems or {}, "scene_wardrobe"), prompt,
                SCENE_WARDROBE_SCHEMA, "scene_wardrobe")
    return {
        "location_id": loc_id,
        "location_name": loc_name,
        "outfits": [o for o in (out.get("outfits") or []) if o.get("outfit_name")],
    }


def plan_story_wardrobe(provider, *, story: dict, cast: list[dict],
                        systems: dict | None = None, on_event=None) -> list[dict]:
    """Plan wardrobe for the entire story — one call per location that has chapters.

    Returns [{location_id, location_name, outfits: [{character, outfit_name, concept, event}]}].
    """
    locations = story.get("locations") or []
    beats = (story.get("storyboard") or {}).get("beats") or []
    loc_names = {(loc.get("name") or "").lower(): loc.get("id") for loc in locations}

    results = []
    for loc in locations:
        lid = (loc.get("id") or "").lower()
        lname = (loc.get("name") or "").lower()
        loc_beats = [
            b for b in beats
            if (b.get("location") or "").lower() == lid
            or (b.get("location") or "").lower() == lname
            or loc_names.get((b.get("location") or "").lower()) == loc.get("id")
        ]
        if not loc_beats:
            continue
        try:
            results.append(plan_scene_wardrobe(
                provider, location=loc, beats=loc_beats, cast=cast,
                story=story, systems=systems, on_event=on_event,
            ))
        except Exception:  # noqa: BLE001
            results.append({
                "location_id": loc.get("id", ""),
                "location_name": loc.get("name", loc.get("id", "")),
                "outfits": [],
            })
    return results


def plan_wardrobe(provider, *, char_name: str, persona: str, appearance: str, story: dict,
                  systems: dict | None = None, on_event=None) -> dict:
    """Legacy per-character wardrobe planner (builder/test endpoint + manual plan-wardrobe).
    Returns {outfits: [{name, concept}]}."""
    beats = story.get("storyboard", {}).get("beats") or story.get("beats") or []
    nl = (char_name or "").lower()
    arc = [b for b in beats if any(nl in (c or "").lower() for c in b.get("characters", []))] or beats
    beat_lines = "\n".join(f"- {b.get('summary', '')}" for b in arc[:24])
    ctx_text = (
        f"STORY: {story.get('premise', '')}\nTONE: {story.get('tone', '')}\n\n"
        f"CHARACTER: {char_name}\nPERSONA:\n{persona or '(none)'}\n"
        f"APPEARANCE: {appearance or '(infer)'}\n\n"
        f"THIS CHARACTER'S CHAPTERS:\n{beat_lines or '(use the story overall)'}\n\n"
        f"Plan {char_name}'s outfits across the story."
    )
    if on_event:
        on_event({"type": "phase", "label": f"Planning {char_name}'s wardrobe"})
    dl = (lambda t: on_event({"type": "delta", "text": t})) if on_event else None
    out = _call(provider, _sys(systems or {}, "wardrobe"), ctx_text, WARDROBE_SCHEMA, "wardrobe", on_delta=dl)
    return {"outfits": [{"name": o.get("name", ""), "concept": o.get("concept", "")}
                        for o in out.get("outfits", []) if o.get("name")]}


# ---------------------------------------------------------------------------
# Outfit prose refinement
# ---------------------------------------------------------------------------

def compose_outfit_prompt(provider, persona: str, base_appearance: str, outfit_name: str,
                          brief_concept: str = "") -> dict:
    """Refine one outfit concept into unified appearance+outfit prose (80-150 words).
    Returns {attire, unified}."""
    from loom.server.services.prompts import _UNIFIED_OUTFIT_SYSTEM, UNIFIED_OUTFIT_SCHEMA
    if provider is None:
        return {"attire": "", "unified": False}
    context = "\n\n".join(p for p in [
        f"CHARACTER PERSONA:\n{persona}" if persona else "",
        (f"BASE APPEARANCE (physical traits — for integration into the outfit prompt):\n{base_appearance}"
         if base_appearance else ""),
        f"OUTFIT NAME: {outfit_name}" if outfit_name else "",
        f"OUTFIT CONCEPT: {brief_concept}" if brief_concept else "",
    ] if p)
    try:
        data = (provider.generate_text(
            system=_UNIFIED_OUTFIT_SYSTEM, prompt=context, emits=UNIFIED_OUTFIT_SCHEMA,
        ).data) or {}
    except Exception:  # noqa: BLE001
        return {"attire": "", "unified": False}
    prose = _re.sub(r"\s+", " ", (data.get("prompt") or "").strip())
    if not prose:
        return {"attire": "", "unified": False}
    return {"attire": prose, "unified": True}


def refine_outfits(provider, outfits: list, persona: str, base_appearance: str,
                   emit=None) -> list:
    """Refine every outfit into unified prose in parallel. One LLM call per outfit."""
    outfits = [dict(o) for o in (outfits or [])]
    if not outfits:
        return outfits

    def _one(o):
        concept = o.get("concept") or o.get("attire_prompt") or o.get("prompt") or ""
        try:
            r = compose_outfit_prompt(provider, persona, base_appearance, o.get("name", ""), concept)
            if r.get("attire"):
                o["attire_prompt"] = r["attire"]
                o["unified"] = r.get("unified", False)
        except Exception:  # noqa: BLE001
            pass
        if emit:
            emit({"type": "item", "name": o.get("name", "outfit"),
                  "text": o.get("attire_prompt", o.get("concept", ""))})
        return o

    with ThreadPoolExecutor(max_workers=len(outfits)) as ex:
        return list(ex.map(_one, outfits))


# ---------------------------------------------------------------------------
# Pose generation
# ---------------------------------------------------------------------------

def compose_poses(provider, persona: str, library: dict | None = None) -> dict:
    """Character-level body-language booru tags for the full emotion taxonomy.
    One structured call. Returns {emotion_key: pose_tags}.

    `library` is the curated pose palette (configs/pose_library.json); the model PICKS from it
    rather than improvising, so the tags stay real. Defaults to the built-in palette."""
    from loom.server.services.emotions import EMOTIONS, EMOTION_KEYS
    from loom.server.services.pose_library import palette_text
    from loom.server.services.poses import _POSE_SYSTEM
    if provider is None:
        return {k: "" for k in EMOTION_KEYS}
    schema = {"type": "object", "additionalProperties": False, "required": EMOTION_KEYS,
              "properties": {k: {"type": "string"} for k in EMOTION_KEYS}}
    listing = "\n".join(f"- {e['key']} ({e['label']})" for e in EMOTIONS)
    palette = palette_text(library)
    system = _POSE_SYSTEM + (
        "\n\nYou are given a FIXED list of emotions and a PALETTE of real pose tags grouped by "
        "body facet. For EVERY emotion key, output how THIS character's BODY carries it, building "
        "the pose by PICKING tags from the palette (a stance + what the arms/hands do + a head "
        "tilt + energy), personalized to the persona. Prefer palette tags; add a short real booru "
        "tag only when the palette truly lacks it. Return exactly one field per emotion key.\n\n"
        f"POSE PALETTE — real tags by facet:\n{palette}")
    prompt = f"CHARACTER PERSONA:\n{persona}\n\nEMOTIONS (give a body-language prompt for each):\n{listing}"
    try:
        data = provider.generate_text(system=system, prompt=prompt, emits=schema).data or {}
    except Exception:  # noqa: BLE001
        data = {}
    return {k: str(data.get(k) or "").strip() for k in EMOTION_KEYS}


def compose_outfit_poses(provider, persona: str, outfit_name: str, outfit_concept: str,
                         emotion_keys: list[str], library: dict | None = None) -> dict:
    """Per-outfit body-language tags for all emotions in the character's affect range.

    The outfit shapes posture and gesture — one structured call per outfit covers all emotions.
    `library` is the curated pose palette the model picks from. Returns {emotion_key: pose_tags}.
    """
    from loom.server.services.emotions import EMOTIONS, EMOTION_KEYS
    from loom.server.services.pose_library import palette_text
    from loom.server.services.poses import _POSE_SYSTEM
    keys = [k for k in emotion_keys if k in set(EMOTION_KEYS)]
    if not keys or provider is None:
        return {k: "" for k in (emotion_keys or [])}
    schema = {"type": "object", "additionalProperties": False, "required": keys,
              "properties": {k: {"type": "string"} for k in keys}}
    listing = "\n".join(
        f"- {e['key']} ({e['label']})" for e in EMOTIONS if e["key"] in set(keys)
    )
    outfit_line = outfit_name + (f" — {outfit_concept}" if outfit_concept else "")
    palette = palette_text(library)
    system = _POSE_SYSTEM + (
        "\n\nThe character is wearing a SPECIFIC OUTFIT. Body language should reflect both "
        "personality AND how the outfit constrains or frees movement "
        "(a gown limits stride; armour adds weight; swimwear leaves the body open). "
        "Build each pose by PICKING from the PALETTE of real pose tags below, personalized to "
        "the persona; prefer palette tags. Return exactly one field per emotion key.\n\n"
        f"POSE PALETTE — real tags by facet:\n{palette}"
    )
    prompt = (
        f"CHARACTER PERSONA:\n{persona}\n\n"
        f"OUTFIT: {outfit_line}\n\n"
        f"EMOTIONS — give body-language booru tags for each while wearing this outfit:\n{listing}"
    )
    try:
        data = provider.generate_text(system=system, prompt=prompt, emits=schema).data or {}
    except Exception:  # noqa: BLE001
        return {k: "" for k in keys}
    return {k: str(data.get(k) or "").strip() for k in keys}


# ---------------------------------------------------------------------------
# Manifest — apply outfits + emotions to the portrait manifest
# ---------------------------------------------------------------------------

def apply_manifest(ctx, key: str, body: dict, *, provider=None,
                   compose_persona: bool = True) -> dict:
    """Merge a planned wardrobe into the character's portrait manifest.

    - Expressions and affect passed in `body` are applied directly.
    - When absent from the manifest and compose_persona=True, they are composed from persona.
    - `replace: true` clears existing outfits + sprite dirs + persona caches first.
    - Each new outfit gets per-outfit poses generated from the character's affect range.
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
            return ctx.stage_provider(stage)
        except Exception:  # noqa: BLE001
            return None

    persona_text = _persona_text(c)

    # Expression prompts — body overrides existing; compose when absent.
    canon = dict(m.get("expression_prompts") or {})
    body_exprs = body.get("expressions") if isinstance(body.get("expressions"), dict) else None
    if body_exprs:
        canon.update(body_exprs)
    if not canon and compose_persona:
        try:
            canon = compose_expressions(_resolve("wardrobe"), persona_text)
        except Exception:  # noqa: BLE001
            canon = {}
    m["expression_prompts"] = canon

    # Character-level pose prompts — kept for legacy render paths + full_gen.
    poses = dict(m.get("pose_prompts") or {})
    body_poses = body.get("poses") if isinstance(body.get("poses"), dict) else None
    if body_poses:
        poses.update(body_poses)
    if not poses and compose_persona:
        try:
            poses = compose_poses(_resolve("wardrobe"), persona_text)
        except Exception:  # noqa: BLE001
            poses = {}
    m["pose_prompts"] = poses

    # Affect range.
    affect = m.get("affect") if isinstance(m.get("affect"), dict) else None
    body_affect = body.get("affect") if isinstance(body.get("affect"), dict) else None
    if body_affect and isinstance(body_affect.get("range"), list):
        body_range = body_affect["range"]
        if body_range and isinstance(body_range[0], dict):
            body_range = [e["emotion"] for e in body_range if e.get("emotion")]
        affect = {"range": body_range}
    elif not (affect and affect.get("range")) and compose_persona:
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

    # Affect range for per-outfit pose generation.
    affect_range: list[str] = (m.get("affect") or {}).get("range") or []

    # Merge outfits — additive; duplicates (by name) skipped.
    # Each new outfit gets per-outfit poses for all emotions in the affect range.
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

        outfit_poses: dict = {}
        if compose_persona and affect_range:
            try:
                outfit_poses = compose_outfit_poses(
                    _resolve("wardrobe"), persona_text,
                    nm, (o.get("concept") or "").strip(),
                    affect_range, ctx.load_pose_library(),
                )
            except Exception:  # noqa: BLE001
                pass

        m["outfits"].append({
            "id": oid, "name": nm, "instruction": "",
            "prompt": attire, "attire_prompt": attire, "expressions": {},
            "poses": outfit_poses,
            "unified": o.get("unified", False),
        })
        existing.add(nm.lower())

    ctx.save_portrait_manifest(key, m)
    return {"ok": True, "outfits": [o["name"] for o in m["outfits"]],
            "emotions": list(canon.keys())}


def plan_and_apply(ctx, provider, char_key: str, char, story: dict, systems: dict,
                   emit=None, replace: bool = True, cascade: bool = True) -> list | dict:
    """Legacy convenience wrapper: plan → refine → apply for one character.

    cascade=True (default): full pipeline, returns refined outfit list.
    cascade=False: plan only, returns raw {outfits: [{name, concept}]}.
    """
    appr = (char.fields or {}).get("appearance", "")
    plan = plan_wardrobe(provider, char_name=char.name, persona=char.system or "",
                         appearance=appr, story=story, systems=systems, on_event=emit)
    if not cascade:
        return plan
    outfits = refine_outfits(provider, plan.get("outfits"), char.system or "", appr, emit=emit)
    apply_manifest(ctx, char_key, {"outfits": outfits, "replace": replace}, provider=provider)
    return outfits


# ---------------------------------------------------------------------------
# Needed by apply_manifest — imported at module level to avoid circular refs
# ---------------------------------------------------------------------------
from .characters import compose_expressions, compose_affect_range  # noqa: E402

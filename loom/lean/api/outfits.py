"""Story-scoped wardrobe: outfit CRUD plus the active-outfit selection.

The lean Story app mounts no global character routes, so a character's
portrait manifest is always read and written with the explicit Story key —
the same resolution the cast routes use. Outfit creation composes the prompt
the way the legacy studio does (``_OUTFIT_SYSTEM`` / ``_gen_text`` /
``_persona_text`` from ``loom.server.services.prompts`` — plain helpers with
no legacy-app coupling), but never renders images here: rendering needs
ComfyUI, which may not be running, so creation returns the manifest object
and leaves sprite rendering to the caller.
"""

from __future__ import annotations

import re
import shutil
from types import SimpleNamespace

from fastapi.responses import JSONResponse

from ...config.schema import Character
from ...server.services.emotions import EMOTION_HINTS, EMOTION_KEYS, EMOTION_LABELS, NORMAL_KEYS
from ...server.services.images import SPRITE_SEED, _render, _set_seeds
from ...server.services.prompts import (
    _OUTFIT_SYSTEM, _gen_text, _persona_text, compose_sprite_prompt, sprite_prompt,
)
from .. import images as image_capability
from .cast import _cast_member


def _manifest(ctx, story_key: str, char_key: str) -> dict:
    m = ctx.portrait_manifest(char_key, story_key=story_key)
    m.setdefault("appearance", "")
    m.setdefault("outfits", [])
    return m


def _slug(name: str, taken: set) -> str:
    oid = re.sub(r"[^\w\-]+", "-", name.lower()).strip("-") or "outfit"
    base, n = oid, 2
    while oid in taken:
        oid, n = f"{base}-{n}", n + 1
    return oid


def _compose_prompt(ctx, character, appearance: str, instruction: str) -> str:
    """The outfit prompt, composed like the legacy studio: the image-prompt
    model rewrites the canonical appearance for the instruction. Without a
    configured model — or when it fails — degrade to appearance+instruction
    prose so wardrobe authoring never hard-depends on a live provider."""
    if not instruction:
        return appearance
    provider = None
    try:
        provider = ctx.ip_provider()
    except Exception:  # noqa: BLE001
        provider = None
    if provider is not None and appearance:
        try:
            composed = _gen_text(
                provider, _OUTFIT_SYSTEM,
                f"Persona:\n{_persona_text(character)}\n\nCanonical appearance tags:\n{appearance}\n\n"
                f"Outfit instruction: {instruction}")
            if composed:
                return composed
        except Exception:  # noqa: BLE001
            pass
    return ", ".join(p for p in (appearance, instruction) if p)


def _write_selection(ctx, story_key: str, char_key: str, outfit: str | None) -> None:
    """Rewrite the cast list with this member's outfit selection, preserving
    every other member field (cast_doc_to_members conventions: character /
    primary / outfit / home all survive the rewrite)."""
    data = ctx._read_story_data(story_key)
    cast = []
    for member in (data.get("cast") or []):
        if not isinstance(member, dict):
            continue
        member = dict(member)
        if member.get("character") == char_key:
            if outfit:
                member["outfit"] = outfit
            else:
                member.pop("outfit", None)
        cast.append(member)
    ctx.update_story_fields(story_key, {"cast": cast})


def _selection(ctx, story_key: str, char_key: str) -> str:
    try:
        data = ctx._read_story_data(story_key)
    except FileNotFoundError:
        return ""
    entry = next((m for m in (data.get("cast") or []) if isinstance(m, dict)
                  and m.get("character") == char_key), None)
    return str((entry or {}).get("outfit") or "")


def _character_card(ctx, story_key: str, char_key: str):
    data = ctx._read_story_character_data(story_key, char_key) or {}
    try:
        return Character(**data)
    except Exception:  # noqa: BLE001 — a partial legacy card still gets a persona blob
        return SimpleNamespace(name=char_key, system="", fields={})


# ---------------------------------------------------------------------------
# Sprite rendering — the outfit's base + full emotion set through the
# allow-listed Krea2 workflow (the same admission as Story images).
# ---------------------------------------------------------------------------

def _emotion_set(manifest: dict, outfit: dict) -> list[str]:
    """The emotions to render for one outfit: its register-specific range, else the
    character's affect range, else the character's authored expression prompts,
    else the everyday default set. 'neutral' always leads — it is both the
    representative sprite and the base render."""
    rng = outfit.get("range")
    if isinstance(rng, list) and rng:
        keys = [k for k in rng if k in EMOTION_KEYS]
    else:
        raw = (manifest.get("affect") or {}).get("range") if isinstance(manifest.get("affect"), dict) else None
        if isinstance(raw, list) and raw:
            if isinstance(raw[0], dict):
                keys = [e.get("emotion") for e in raw if e.get("emotion") in EMOTION_KEYS]
            else:
                keys = [k for k in raw if k in EMOTION_KEYS]
        else:
            keys = [k for k in (manifest.get("expression_prompts") or {}) if k in EMOTION_KEYS]
    if not keys:
        keys = list(NORMAL_KEYS)
    return ["neutral", *[k for k in keys if k != "neutral"]]


def _sprite_prompt_for(ctx, key: str, character, manifest: dict, outfit: dict, emotion: str) -> str:
    """One sprite prompt, assembled the legacy way: v4pro prose when the compose
    model is reachable, else the deterministic emotion-led formula (style →
    framing → emotion → capped attire → pose)."""
    appearance = ((getattr(character, "fields", None) or {}).get("appearance")
                  or manifest.get("appearance", ""))
    attire = outfit.get("attire_prompt") or outfit.get("prompt") or ""
    expr = ((outfit.get("expression_prompts") or {}).get(emotion)
            or (manifest.get("expression_prompts") or {}).get(emotion) or emotion or "")
    style = ctx.art_style(char_key=key)
    prompt = ""
    try:
        v4 = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
        if v4 is not None and hasattr(v4, "generate_text"):
            prompt = compose_sprite_prompt(v4, style=style, appearance=appearance, attire=attire,
                                           emotion_label=EMOTION_LABELS.get(emotion, emotion),
                                           hint=(EMOTION_HINTS.get(emotion) or expr or emotion))
    except Exception:  # noqa: BLE001 — deterministic assembly is the fallback
        prompt = ""
    if not prompt:
        prompt = sprite_prompt(appearance, attire, expr,
                               ctx.pose_tags(key, emotion, outfit_id=outfit.get("id")),
                               ctx.pose_framing(emotion), emotion=emotion, style=style)
    return prompt


def _sprite_provider(ctx, model: str | None = None):
    """Resolve the allow-listed Krea2 sprite workflow — the lean image capability's
    admission, never a raw legacy role lookup."""
    selected = image_capability.resolve_krea_model(ctx, role="sprite", model=model)
    provider, resolved = ctx.image_provider(selected)
    if provider is None:
        raise RuntimeError(str(resolved or "no Krea2 image provider is configured"))
    return provider, selected


def _render_guard(app, ctx, story_key: str, char_key: str, oid: str):
    """Shared preflight for the render routes: (char_key, manifest, outfit, error)."""
    char_key, error = _cast_member(ctx, story_key, char_key)
    if error:
        return char_key, None, None, error
    if not bool(getattr(app.state, "lean_comfy_enabled", True)):
        return char_key, None, None, JSONResponse({
            "error": "Krea2/Comfy rendering is disabled for this lean app",
            "retryable": False,
        }, status_code=503)
    m = _manifest(ctx, story_key, char_key)
    outfit = ctx.portrait_outfit(m, oid)
    if outfit is None:
        return char_key, None, None, JSONResponse({"error": "no such outfit"}, status_code=404)
    return char_key, m, outfit, None


def _runner_error(exc: Exception) -> JSONResponse:
    return JSONResponse({
        "error": f"could not reach the Krea2/Comfy runner: {exc}. "
                 "Start ComfyUI (or check its address in the model config) and retry.",
        "retryable": True,
    }, status_code=503)


def register(app, ctx) -> None:
    @app.post("/api/stories/{story_key}/cast/{key}/outfits")
    def create_outfit(story_key: str, key: str, body: dict):
        key, error = _cast_member(ctx, story_key, key)
        if error:
            return error
        body = body or {}
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=422)
        instruction = (body.get("instruction") or "").strip()
        m = _manifest(ctx, story_key, key)
        prompt = _compose_prompt(ctx, _character_card(ctx, story_key, key),
                                 m.get("appearance", ""), instruction)
        outfit = {
            "id": _slug(name, {o.get("id") for o in m["outfits"]}),
            "name": name, "instruction": instruction,
            "prompt": prompt, "attire_prompt": prompt,
            "expressions": {},
        }
        m["outfits"].append(outfit)
        ctx.save_portrait_manifest(key, m, story_key=story_key)
        return outfit

    @app.patch("/api/stories/{story_key}/cast/{key}/outfits/{oid}")
    def patch_outfit(story_key: str, key: str, oid: str, body: dict):
        key, error = _cast_member(ctx, story_key, key)
        if error:
            return error
        m = _manifest(ctx, story_key, key)
        outfit = ctx.portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        body = body or {}
        if "name" in body:
            outfit["name"] = (body["name"] or "").strip() or outfit.get("name", oid)
        if "instruction" in body:
            outfit["instruction"] = (body["instruction"] or "").strip()
        if "prompt" in body:
            outfit["prompt"] = (body["prompt"] or "").strip()
            outfit["attire_prompt"] = outfit["prompt"]
        if "attire_prompt" in body:
            outfit["attire_prompt"] = (body["attire_prompt"] or "").strip()
            outfit["prompt"] = outfit["attire_prompt"]
        ctx.save_portrait_manifest(key, m, story_key=story_key)
        return outfit

    @app.delete("/api/stories/{story_key}/cast/{key}/outfits/{oid}")
    def delete_outfit(story_key: str, key: str, oid: str):
        key, error = _cast_member(ctx, story_key, key)
        if error:
            return error
        m = _manifest(ctx, story_key, key)
        if ctx.portrait_outfit(m, oid) is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        m["outfits"] = [o for o in m["outfits"] if o.get("id") != oid]
        ctx.save_portrait_manifest(key, m, story_key=story_key)
        directory = ctx.portrait_dir(key, story_key=story_key) / oid
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
        # A deleted outfit can't stay the cast member's selected look.
        cleared = False
        if _selection(ctx, story_key, key) == oid:
            _write_selection(ctx, story_key, key, None)
            cleared = True
        return {"ok": True, "cleared": cleared}

    @app.post("/api/stories/{story_key}/cast/{key}/outfit")
    def select_outfit(story_key: str, key: str, body: dict):
        key, error = _cast_member(ctx, story_key, key)
        if error:
            return error
        body = body or {}
        outfit = body.get("outfit")
        outfit = (str(outfit).strip() or None) if outfit is not None else None
        if outfit is not None and ctx.portrait_outfit(_manifest(ctx, story_key, key), outfit) is None:
            return JSONResponse({"error": "no such outfit"}, status_code=422)
        _write_selection(ctx, story_key, key, outfit)
        return {"ok": True, "character": key, "outfit": outfit}

    @app.post("/api/stories/{story_key}/cast/{key}/outfits/{oid}/render")
    async def render_outfit(story_key: str, key: str, oid: str, body: dict | None = None):
        """One click: render the outfit's BASE (if missing) + every emotion in its set
        through the Krea2 workflow, saving sprites into the story-scoped portrait dir.
        Existing sprites are kept unless body.force — re-clicking is cheap. A base
        failure fails the request; a single emotion's failure is reported per-emotion
        and never sinks the set. The manifest only ever records files that exist."""
        key, m, outfit, error = _render_guard(app, ctx, story_key, key, oid)
        if error:
            return error
        body = body or {}
        force = bool(body.get("force"))
        try:
            provider, model_id = _sprite_provider(ctx, body.get("model"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=502)
        _set_seeds(provider.workflow, SPRITE_SEED)   # one latent region for the whole set
        character = _character_card(ctx, story_key, key)
        directory = ctx.portrait_dir(key, create=True, story_key=story_key) / oid
        directory.mkdir(parents=True, exist_ok=True)
        oprefix = ctx.output_prefix_for(model_id, "sprite", key)
        existing = outfit.setdefault("expressions", {})
        emotions = _emotion_set(m, outfit)
        fresh: set = set()   # emotions already rendered by THIS request (via the base)

        async def _one(emotion: str):
            prompt = _sprite_prompt_for(ctx, key, character, m, outfit, emotion)
            return await _render(provider, prompt, out_prefix=oprefix,
                                 latent=ctx.pose_latent(emotion))

        # The BASE (neutral full-body) gates the request: no base, no sprite set.
        if force or not outfit.get("base"):
            try:
                png = await _one("neutral")
            except Exception as exc:  # noqa: BLE001 — runner down / unreachable
                return _runner_error(exc)
            if not png:
                return JSONResponse({"error": "the Krea2 workflow returned no image for the "
                                              "outfit base", "retryable": True}, status_code=502)
            (directory / "base.png").write_bytes(png)
            outfit["base"] = "base.png"
            if "neutral" in emotions and (force or "neutral" not in existing):
                (directory / "neutral.png").write_bytes(png)   # same render doubles as the sprite
                existing["neutral"] = "neutral.png"
                fresh.add("neutral")
            ctx.save_portrait_manifest(key, m, story_key=story_key)

        results = []
        for emo in emotions:
            if existing.get(emo) and (not force or emo in fresh):
                results.append({"emotion": emo, "status": "skipped"})
                continue
            try:
                png = await _one(emo)
            except Exception as exc:  # noqa: BLE001 — one emotion never sinks the set
                results.append({"emotion": emo, "status": "error", "error": str(exc)})
                continue
            if not png:
                results.append({"emotion": emo, "status": "error", "error": "no image returned"})
                continue
            (directory / f"{emo}.png").write_bytes(png)
            existing[emo] = f"{emo}.png"
            results.append({"emotion": emo, "status": "rendered"})
        ctx.save_portrait_manifest(key, m, story_key=story_key)
        failed = [r for r in results if r["status"] == "error"]
        return {
            "ok": not failed,
            "outfit": oid, "model": model_id, "base": outfit.get("base"),
            "rendered": [r["emotion"] for r in results if r["status"] == "rendered"],
            "skipped": [r["emotion"] for r in results if r["status"] == "skipped"],
            "failed": failed,
            "url_base": f"/api/stories/{story_key}/cast/{key}/portraits/img/{oid}",
        }

    @app.post("/api/stories/{story_key}/cast/{key}/outfits/{oid}/render/{emotion}")
    async def render_outfit_emotion(story_key: str, key: str, oid: str, emotion: str,
                                    body: dict | None = None):
        """Re-render ONE emotion sprite (a re-roll). Rendering 'neutral' also backfills
        the outfit base when it's missing."""
        key, m, outfit, error = _render_guard(app, ctx, story_key, key, oid)
        if error:
            return error
        emo = (emotion or "").strip().lower()
        if emo not in EMOTION_KEYS:
            return JSONResponse({"error": "no such emotion"}, status_code=422)
        body = body or {}
        force = bool(body.get("force"))
        existing = outfit.setdefault("expressions", {})
        base_too = emo == "neutral" and not outfit.get("base")
        if not force and existing.get(emo) and not base_too:
            return {"ok": True, "outfit": oid, "emotion": emo, "status": "skipped",
                    "url": f"/api/stories/{story_key}/cast/{key}/portraits/img/{oid}/{emo}.png"}
        try:
            provider, model_id = _sprite_provider(ctx, body.get("model"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=502)
        if force:
            from ...server.services.images import _randomize_seeds
            _randomize_seeds(provider.workflow)   # a re-roll must actually differ
        else:
            _set_seeds(provider.workflow, SPRITE_SEED)
        prompt = _sprite_prompt_for(ctx, key, _character_card(ctx, story_key, key), m, outfit, emo)
        try:
            png = await _render(provider, prompt,
                                out_prefix=ctx.output_prefix_for(model_id, "sprite", key),
                                latent=ctx.pose_latent(emo))
        except Exception as exc:  # noqa: BLE001 — runner down / unreachable
            return _runner_error(exc)
        if not png:
            return JSONResponse({"error": "the Krea2 workflow returned no image",
                                 "retryable": True}, status_code=502)
        directory = ctx.portrait_dir(key, create=True, story_key=story_key) / oid
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{emo}.png").write_bytes(png)
        existing[emo] = f"{emo}.png"
        if base_too:
            (directory / "base.png").write_bytes(png)
            outfit["base"] = "base.png"
        ctx.save_portrait_manifest(key, m, story_key=story_key)
        return {"ok": True, "outfit": oid, "emotion": emo, "status": "rendered",
                "url": f"/api/stories/{story_key}/cast/{key}/portraits/img/{oid}/{emo}.png"}

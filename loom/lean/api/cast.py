"""Small cast-card boundary required by Story authoring and play.

This is deliberately not the general character studio.  It exposes only the
data and existing portrait files a Story needs, plus the one card edit used by
the Story workspace.
"""

from __future__ import annotations

import re

from ...config.schema import Character
from fastapi.responses import FileResponse, JSONResponse


_IMPORTED_SIGNATURE = frozenset({
    "description", "personality", "scenario", "first_mes", "mes_example",
    "character_book", "spec", "spec_version", "character_version", "extensions",
})


def _is_imported(character) -> bool:
    fields = character.fields or {}
    return bool(fields.get("imported")) or bool(_IMPORTED_SIGNATURE.intersection(fields))


def _story_cast_keys(ctx, story_key: str) -> list[str] | None:
    story = ctx.base_settings.stories.get(story_key)
    if story is None:
        return None
    keys = [member.character for member in story.cast]
    source = str((getattr(story, "fields", None) or {}).get("source_character") or "").strip()
    if source and source not in keys:
        keys.append(source)
    return keys


def _story_image_url(story_key: str, character_key: str, suffix: str) -> str:
    return f"/api/stories/{story_key}/cast/{character_key}/{suffix}"


def _cast_list(ctx, story_key: str, keys: list[str]) -> list[dict]:
    story = ctx.base_settings.stories.get(story_key)
    worn = {m.character: m.outfit for m in (story.cast if story else [])}
    payload: list[dict] = []
    for key in keys:
        data = ctx._read_story_character_data(story_key, key)
        if data is None:
            continue
        try:
            character = Character(**data)
        except Exception:  # noqa: BLE001 - an invalid legacy card is omitted, never leaked
            continue
        fields = character.fields or {}
        safe_key = re.sub(r"[^\w-]+", "", key)
        payload.append({
            "key": key,
            "name": character.name,
            "greeting": character.greeting,
            "system": character.system,
            "fields": fields,
            "playable": bool(getattr(character, "playable", False)),
            "imported": _is_imported(character),
            "outfit": worn.get(key) or None,   # the CastMember's selected wardrobe look
            "home_scenes": [scene.model_dump() for scene in getattr(character, "home_scenes", []) or []],
            "image": character.image.model_dump(),
            "avatar": _story_image_url(story_key, key, "avatar") if (ctx.char_asset_dir(key, story_key=story_key) / f"{safe_key}.png").is_file() else None,
            "reference": _story_image_url(story_key, key, "reference") if ctx.reference_path(key, story_key=story_key) else None,
            # General character-library images deliberately do not cross the
            # Story boundary. Story-owned images live in the lean image API.
            "images": [],
            "story": story_key,
            "generated": bool(fields.get("_generated")),
        })
    return payload


def _cast_member(ctx, story_key: str, character_key: str):
    keys = _story_cast_keys(ctx, story_key)
    if keys is None:
        return None, JSONResponse({"error": "no such story"}, status_code=404)
    if character_key not in keys or ctx._read_story_character_data(story_key, character_key) is None:
        return None, JSONResponse({"error": "no such story cast member"}, status_code=404)
    return character_key, None


def register(app, ctx) -> None:
    @app.get("/api/stories/{story_key}/cast")
    def cast_cards(story_key: str):
        keys = _story_cast_keys(ctx, story_key)
        if keys is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        return _cast_list(ctx, story_key, keys)

    @app.get("/api/stories/{story_key}/cast/{key}/avatar")
    def cast_avatar(story_key: str, key: str):
        key, error = _cast_member(ctx, story_key, key)
        if error:
            return error
        safe_key = re.sub(r"[^\w-]+", "", key)
        path = ctx.char_asset_dir(key, story_key=story_key) / f"{safe_key}.png"
        if not path.is_file():
            return JSONResponse({"error": "no avatar"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.get("/api/stories/{story_key}/cast/{key}/reference")
    def cast_reference(story_key: str, key: str):
        key, error = _cast_member(ctx, story_key, key)
        if error:
            return error
        path = ctx.reference_path(key, story_key=story_key)
        if path is None:
            return JSONResponse({"error": "no reference"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.get("/api/stories/{story_key}/cast/{key}/portraits")
    def cast_portraits(story_key: str, key: str):
        key, error = _cast_member(ctx, story_key, key)
        if error:
            return error
        return ctx.portrait_payload(
            key,
            story_key=story_key,
            image_base=_story_image_url(story_key, key, "portraits/img"),
        )

    @app.get("/api/stories/{story_key}/cast/{key}/portraits/img/{oid}/{file}")
    def cast_portrait_image(story_key: str, key: str, oid: str, file: str):
        key, error = _cast_member(ctx, story_key, key)
        if error:
            return error
        if not re.fullmatch(r"[\w-]+", oid) or not re.fullmatch(r"[\w-]+\.png", file):
            return JSONResponse({"error": "bad path"}, status_code=404)
        directory = ctx.portrait_dir(key, story_key=story_key).resolve()
        path = (directory / oid / file).resolve()
        if directory not in path.parents or not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/stories/{story_key}/cast/{key}/card")
    def update_cast_card(story_key: str, key: str, body: dict):
        key, error = _cast_member(ctx, story_key, key)
        if error:
            return error
        data = ctx._read_story_character_data(story_key, key)
        if data is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        try:
            if "name" in body:
                data["name"] = (body.get("name") or "").strip() or data.get("name") or key
            if "system" in body:
                data["system"] = body.get("system") or ""
            if "greeting" in body:
                data["greeting"] = body.get("greeting") or None
            if "playable" in body:
                data["playable"] = bool(body.get("playable"))
            if isinstance(body.get("home_scenes"), list):
                data["home_scenes"] = body["home_scenes"]
            if isinstance(body.get("fields"), dict):
                data["fields"] = {**(data.get("fields") or {}), **body["fields"]}
            ctx._write_story_character_data(story_key, key, data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True}

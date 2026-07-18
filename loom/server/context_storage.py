"""File-backed story, character, persona, and asset responsibilities for AppContext."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml
from fastapi import UploadFile, File  # noqa: F401 — kept for parity; methods don't define routes
from fastapi.responses import JSONResponse

from ..config import load_settings
from ..config.schema import Character, ModelDef, Settings
from ..connections import ConnectionStore  # noqa: F401 — type reference for callers

from .services import config_files
from .services import prompts as _prompts


_IMG_URL_RE = re.compile(r"https?://[^\s\"'<>)]+?\.(?:png|jpe?g|webp|gif)", re.IGNORECASE)



def _self_heal_refs(data: dict, known_chars=None) -> list[str]:
    """Drop the REPAIRABLE dangling references in a raw story dict, in place, and return a list of
    human-readable notes on what was healed (empty = nothing). These are the refs where "the target
    was deleted/never existed, so forget the pointer" is always the right move — clearing them lets
    an unrelated edit succeed instead of being blocked by the validators.

    When `known_chars` is given, ALSO heals the cross-aggregate case: a cast member whose `character`
    isn't a real character record (what Settings._validate_references rejects) is dropped, and every
    intra-story ref to that key is CASCADE-dropped (bonds, scene anchors, arc cast/owner) so the
    story stays valid for Story._check_references. This is the guard that stops a section-edit from
    inventing a cast member with no character behind it (create_character is the integrity-preserving
    path for new people). Omit `known_chars` to keep the location-only heal (unchanged behaviour)."""
    repairs: list[str] = []
    loc_ids = {l.get("id") for l in (data.get("locations") or []) if isinstance(l, dict)}
    scene_ids = {s.get("id") for l in (data.get("locations") or []) if isinstance(l, dict)
                 for s in (l.get("scenes") or []) if isinstance(s, dict)}
    # start → location id
    if data.get("start") and data["start"] not in loc_ids:
        data["start"] = None
    # cast.home → location id
    for m in (data.get("cast") or []):
        if isinstance(m, dict) and m.get("home") and m["home"] not in loc_ids:
            m["home"] = ""
    # location.parent → location id
    for l in (data.get("locations") or []):
        if isinstance(l, dict) and l.get("parent") and l["parent"] not in loc_ids:
            l["parent"] = ""
    # connection.source/target → location or scene id
    for c in (data.get("connections") or []):
        if isinstance(c, dict):
            for end in ("source", "target"):
                if c.get(end) and c[end] not in loc_ids and c[end] not in scene_ids:
                    c[end] = ""
    # start_scene → VN scene id
    if data.get("start_scene") and not any(s.get("id") == data["start_scene"]
                                           for s in (data.get("scenes") or []) if isinstance(s, dict)):
        data["start_scene"] = ""

    # cast.character → real character record (cross-aggregate). Only when we know the registry.
    # Drop the phantom member AND cascade every intra-story ref to its key, or Story._check_references
    # would raise on the now-orphaned bonds/scenes/arcs.
    if known_chars is not None:
        known = set(known_chars or ())
        cast = [m for m in (data.get("cast") or []) if isinstance(m, dict)]
        phantom = {m.get("character") for m in cast if m.get("character") and m.get("character") not in known}
        if phantom:
            data["cast"] = [m for m in cast if m.get("character") not in phantom]
            # bonds touching a phantom
            data["relationships"] = [r for r in (data.get("relationships") or [])
                                     if isinstance(r, dict) and r.get("source") not in phantom
                                     and r.get("target") not in phantom]
            # scene anchors on locations
            for l in (data.get("locations") or []):
                for s in (l.get("scenes") or []) if isinstance(l, dict) else []:
                    if isinstance(s, dict):
                        if s.get("character") in phantom:
                            s["character"] = None
                        if isinstance(s.get("characters"), list):
                            s["characters"] = [c for c in s["characters"] if c not in phantom]
            # arc cast / owner
            for a in (data.get("arcs") or []):
                if not isinstance(a, dict):
                    continue
                if isinstance(a.get("cast"), list):
                    a["cast"] = [c for c in a["cast"] if c not in phantom]
                if a.get("owner") in phantom:
                    a["owner"] = ""
            repairs.append("dropped cast member(s) with no character record: "
                           + ", ".join(sorted(str(p) for p in phantom)))
    return repairs

class StorageContextMixin:
    def active_comfy_url(self) -> str:
        conn = self.store.active("image")
        return (conn.base_url if conn and conn.base_url else self.comfy_url)

    def comfy_base_dir(self) -> Path | None:
        """The managed ComfyUI base directory (holds models/checkpoints, models/
        loras). None for connect-only setups where we don't know the layout."""
        from ..comfy.server import get_server
        launch = getattr(get_server(self.comfy_url), "launch", None)
        bd = getattr(launch, "base_directory", None) if launch else None
        return Path(bd) if bd else None

    def workflow_path(self, model_key: str) -> Path | None:
        md = self.base_settings.models.get(model_key)
        if md is None or md.kind != "image":
            return None
        wf = md.options.get("workflow")
        if not wf:
            return None
        path = (self.root / wf).resolve()
        # Guard against path traversal — must stay inside the project root.
        if self.root.resolve() not in path.parents:
            return None
        return path

    # -- paths --------------------------------------------------------------
    def char_dir(self) -> Path:
        return self.root / "configs" / "characters"

    def persona_dir(self, *, create: bool = False) -> Path:
        """configs/personas — one YAML per persona (filename stem = key), with a
        <key>.png avatar alongside. Created on demand."""
        d = self.root / "configs" / "personas"
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d

    def persona_avatar_path(self, key: str) -> Path | None:
        """The persona's avatar PNG (<key>.png), or None if absent."""
        safe = re.sub(r"[^\w\-]+", "", key)
        p = self.persona_dir() / f"{safe}.png"
        return p if p.is_file() else None

    def save_persona_avatar(self, key: str, png: bytes) -> str:
        """Write the chosen portrait candidate's bytes to <key>.png."""
        safe = re.sub(r"[^\w\-]+", "", key)
        self.persona_dir(create=True)
        (self.persona_dir() / f"{safe}.png").write_bytes(png)
        return f"/api/personas/{key}/avatar"

    def write_persona(self, key: str, data: dict) -> dict:
        """Validate + persist a persona YAML (configs/personas/<key>.yaml), then reload.
        `key` is the filename stem; the caller is responsible for slug/dedup."""
        from ..config.schema import Persona
        Persona(**{k: v for k, v in data.items() if k != "key"})  # validate (drops a stray 'key')
        self.persona_dir(create=True)
        payload = {k: v for k, v in data.items() if k in ("name", "description", "summary",
                                                          "appearance", "fields") and v not in (None, "")}
        (self.persona_dir() / f"{key}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.reload_settings()
        return {"ok": True, "key": key}

    def delete_persona(self, key: str) -> bool:
        """Remove a persona's YAML + avatar PNG, then reload. Returns whether anything was removed."""
        safe = re.sub(r"[^\w\-]+", "", key)
        removed = False
        for fn in (f"{safe}.yaml", f"{safe}.png"):
            p = self.persona_dir() / fn
            if p.is_file():
                p.unlink()
                removed = True
        if removed:
            self.reload_settings()
        return removed

    def story_dir(self) -> Path:
        return self.root / "configs" / "stories"

    def story_bg_dir(self, key: str) -> Path:
        return self.story_dir() / re.sub(r"[^\w\-]+", "", key) / "bg"

    def char_asset_dir(self, key: str, *, story_key: str | None = None) -> Path:
        """The directory holding a character's IMAGE assets — avatar `<key>.png`, reference
        `<key>.ref.png`, and `portraits/<key>/…`. For a STORY-OWNED character this lives inside the
        story's own folder (`configs/stories/<owner>/chars`) so the story is self-contained and a
        delete/backup is one folder; for a global/imported library card it's the shared
        `configs/characters`. The filename convention inside is identical either way — only the root
        differs — so per-character path code just swaps `char_dir()` for this."""
        owner = story_key if story_key is not None else self._char_owner(key)
        if owner is not None:
            return self.story_dir() / re.sub(r"[^\w\-]+", "", owner) / "chars"
        return self.char_dir()

    def portrait_dir(self, key: str, *, create: bool = False, story_key: str | None = None) -> Path:
        safe = re.sub(r"[^\w\-]+", "", key)
        d = self.char_asset_dir(key, story_key=story_key) / "portraits" / safe
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d

    def portrait_manifest(self, key: str, *, story_key: str | None = None) -> dict:
        p = self.portrait_dir(key, story_key=story_key) / "manifest.json"
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — corrupt manifest: start fresh
                pass
        return {"appearance": "", "outfits": []}

    def save_portrait_manifest(self, key: str, data: dict, *, story_key: str | None = None) -> None:
        d = self.portrait_dir(key, create=True, story_key=story_key)
        (d / "manifest.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def portrait_outfit(self, manifest: dict, oid: str) -> dict | None:
        return next((o for o in manifest.get("outfits", []) if o.get("id") == oid), None)

    def reference_path(self, key: str, *, story_key: str | None = None) -> Path | None:
        """The character's img2img reference image: a dedicated <key>.ref.png if
        set, else the card avatar <key>.png. None if neither exists."""
        safe = re.sub(r"[^\w\-]+", "", key)
        base = self.char_asset_dir(key, story_key=story_key)
        for fn in (f"{safe}.ref.png", f"{safe}.png"):
            p = base / fn
            if p.is_file():
                return p
        return None

    def sets_dir(self) -> Path:
        return self.root / "configs" / "prompt_sets"

    def dataset_dir(self, name: str) -> Path | None:
        safe = re.sub(r"[^\w\-]+", "_", name or "").strip("_")
        d = (self.root / "datasets" / safe).resolve()
        if not safe or self.root.resolve() not in d.parents or not d.is_dir():
            return None
        return d

    # -- domain ops ---------------------------------------------------------
    def character_images(self, k: str, c) -> list[dict]:
        """All images attached to a character, categorized and checked. Local files
        are verified to exist; URLs found in the card are listed as external."""
        char_dir = self.char_asset_dir(k)
        out: list[dict] = []
        has_avatar = (char_dir / f"{k}.png").is_file()
        has_ref = (char_dir / f"{k}.ref.png").is_file()
        if has_avatar:
            out.append({"kind": "avatar", "url": f"/api/characters/{k}/avatar", "ok": True, "source": "card"})
        # the reference is the dedicated ref if present, else the avatar
        if has_ref or has_avatar:
            out.append({"kind": "reference", "url": f"/api/characters/{k}/reference",
                        "ok": True, "source": "dedicated" if has_ref else "avatar"})
        # image URLs embedded in the card (description, fields…)
        seen = set()
        for blob in (c.system or "", c.greeting or "", json.dumps(c.fields, ensure_ascii=False)):
            for m in _IMG_URL_RE.findall(blob):
                if m not in seen:
                    seen.add(m)
                    out.append({"kind": "card link", "url": m, "ok": None, "source": "external"})
        return out

    def portrait_payload(self, key: str, *, story_key: str | None = None,
                         image_base: str | None = None) -> dict:
        """The manifest enriched with served image URLs for the frontend.

        affect.range in the manifest may be either the old format (list of dicts with
        emotion/valence/arousal) or the new format (list of strings). Both are normalised to
        a plain list of keys here; the API response enriches back to [{emotion, valence, arousal}]
        for the frontend carousel and AffectScatter."""
        from .services.emotions import (EMOTION_KEYS, EMOTION_LABELS, EMOTION_COORDS,
                                        NORMAL_KEYS, range_to_display)
        m = self.portrait_manifest(key, story_key=story_key)
        base = image_base or f"/api/characters/{key}/portraits/img"
        canon = m.get("expression_prompts") or {}
        # Normalise affect.range to a list of string keys regardless of stored format.
        raw_range = (m.get("affect") or {}).get("range") if isinstance(m.get("affect"), dict) else None
        if isinstance(raw_range, list) and raw_range:
            if isinstance(raw_range[0], dict):
                # Old format: [{emotion, valence?, arousal?}] → extract keys
                affect_key_list = [e["emotion"] for e in raw_range if e.get("emotion") in EMOTION_KEYS]
            else:
                # New format: [key1, key2, ...]
                affect_key_list = [k for k in raw_range if k in EMOTION_KEYS]
        else:
            affect_key_list = list(NORMAL_KEYS)
        affect_keys = set(affect_key_list)
        # Build display-ready range (sorted by circumplex angle) for carousel + AffectScatter.
        affect_range_display = range_to_display(affect_key_list)
        outfits = []
        for o in m.get("outfits", []):
            oid = o["id"]
            files = o.get("expressions") or {}
            exprs = {emo: f"{base}/{oid}/{fn}" for emo, fn in files.items()}
            out_prompts = o.get("expression_prompts") or {}
            # Per-OUTFIT emotion range (register-specific). Falls back to the character range for
            # outfits authored before per-outfit ranges existed. `in_range` drives the pane's cells.
            o_raw = o.get("range")
            o_keys = ([k for k in o_raw if k in EMOTION_KEYS]
                      if isinstance(o_raw, list) and o_raw else affect_key_list)
            o_keyset = set(o_keys)
            expression_set = [{
                "emotion": k, "label": EMOTION_LABELS.get(k, k),
                "prompt": out_prompts.get(k) or canon.get(k, ""),
                "url": f"{base}/{oid}/{files[k]}" if files.get(k) else None,
                "valence": EMOTION_COORDS[k][0],
                "arousal": EMOTION_COORDS[k][1],
                "in_range": k in o_keyset,
            } for k in EMOTION_KEYS]
            outfits.append({
                "id": oid, "name": o.get("name") or oid,
                "instruction": o.get("instruction", ""), "prompt": o.get("prompt", ""),
                "attire_prompt": o.get("attire_prompt", o.get("prompt", "")),
                "base": f"{base}/{oid}/base.png" if (o.get("base")) else None,
                "expressions": exprs,
                "expression_set": expression_set,
                "range": o_keys,   # this outfit's register-specific emotion set (keys)
            })
        return {"appearance": m.get("appearance", ""),
                "emotions": EMOTION_KEYS,
                "affect": {"range": affect_range_display, "keys": affect_key_list,
                           "dimensions": ["valence", "arousal"]},
                "expression_prompts": canon, "outfits": outfits}

    def write_npc(self, npc: dict, story_key: str = "", ref_from: str | None = None,
                  base_prompt: str = "") -> str:
        """Create a story-bound Character (a generated NPC, or the duplicated protagonist);
        returns its key. Tagged with its owning `story` so it's scoped / grouped and hidden from
        global pickers. If `ref_from` is given, that character's reference image is copied across.
        `base_prompt` (from the shared ✨ composer) is stored as fields.base_prompt so the base
        image renders richly without a manual ✨ pass."""
        from ..server.services import story_store as SS
        char_dir = self.char_dir()
        char_dir.mkdir(parents=True, exist_ok=True)
        taken = {p.stem for p in char_dir.glob("*.yaml")}
        if story_key:
            # Runtime and image helpers historically address a character by
            # key alone. Keep Story-generated keys globally unique so a later
            # Story cannot silently shadow an existing embedded character.
            taken |= SS.all_character_keys(self.root)
        base = re.sub(r"[^a-z0-9]+", "_", (npc.get("name") or "npc").lower()).strip("_") or "npc"
        key, i = base, 2
        while key in taken:
            key, i = f"{base}_{i}", i + 1
        fields = {"appearance": npc.get("appearance", ""), "role": npc.get("role", ""),
                  "story": story_key, "_generated": True}
        if base_prompt:
            fields["base_prompt"] = base_prompt
        # The portable core harness (relationship-first genesis) lives on the card — see GENESIS.md.
        for hk in ("want", "lie", "contradiction", "wound", "secret"):
            if npc.get(hk):
                fields[hk] = npc[hk]
        try:                                              # numeric stature → sprite scaling (not a tag)
            if npc.get("height_cm"):
                fields["height_cm"] = int(npc["height_cm"])
        except (TypeError, ValueError):
            pass
        cdata = {
            "name": npc.get("name") or key,
            "system": npc.get("persona") or "",
            "fields": fields,
            "_migrated_split": True,
        }
        Character(**cdata)  # validate (extra top-level keys ignored)
        if story_key:
            SS.upsert_character(self.root, story_key, key, cdata)   # embedded (authoritative)
        else:
            (char_dir / f"{key}.yaml").write_text(
                yaml.safe_dump(cdata, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if ref_from:
            ref = self.reference_path(ref_from)
            if ref and ref.is_file():
                import shutil
                try:
                    dest = self.char_asset_dir(key)   # story folder if the NPC was just embedded
                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ref, dest / f"{key}.ref.png")
                except Exception:  # noqa: BLE001
                    pass
        return key

    def write_character(self, cdata: dict, avatar_png: bytes | None) -> dict:
        """Validate + persist a normalized character: one configs/characters/
        <key>.yaml, the avatar PNG alongside (loader globs *.yaml, so the .png is
        ignored by config loading), then reload settings so it's selectable."""
        Character(**cdata)  # raises on a malformed card
        char_dir = self.char_dir()
        char_dir.mkdir(parents=True, exist_ok=True)
        base = re.sub(r"[^a-z0-9]+", "_", cdata["name"].lower()).strip("_") or "character"
        key, i = base, 2
        while (char_dir / f"{key}.yaml").exists():
            key, i = f"{base}_{i}", i + 1
        (char_dir / f"{key}.yaml").write_text(
            yaml.safe_dump(cdata, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if avatar_png and avatar_png.startswith(b"\x89PNG\r\n\x1a\n"):
            (char_dir / f"{key}.png").write_bytes(avatar_png)
        self.reload_settings()
        return {"ok": True, "key": key, "name": cdata["name"],
                "avatar": (char_dir / f"{key}.png").is_file()}

    def prune_orphan_characters(self) -> list[str]:
        """Auto-delete GENERATED characters that no longer belong to any story — their owning
        story was deleted, or they were never cast. Library / imported cards (not `_generated`)
        are NEVER touched; they're independent. Removes the yaml + avatar/ref + portraits, like
        delete_character. Idempotent — safe to call after any story/cast change or at startup.
        Returns the keys removed."""
        import shutil
        live_stories = set(self.base_settings.stories.keys())
        cast_members = {m.character for st in self.base_settings.stories.values() for m in st.cast}
        cdir = self.char_dir()
        removed: list[str] = []
        for k, c in list(self.base_settings.characters.items()):
            fields = c.fields or {}
            if not fields.get("_generated"):
                continue  # a library card — keep it regardless of story membership
            story_key = fields.get("story")
            has_story = (bool(story_key) and story_key in live_stories) or (k in cast_members)
            if has_story:
                continue
            safe = re.sub(r"[^\w\-]+", "", k)
            for fn in (f"{safe}.yaml", f"{safe}.png", f"{safe}.ref.png"):
                fp = cdir / fn
                if fp.is_file():
                    fp.unlink()
            shutil.rmtree(self.portrait_dir(k), ignore_errors=True)
            removed.append(k)
        if removed:
            self.reload_settings()
        return removed

    def save_location_bg(self, key: str, loc: str, png: bytes) -> str:
        """Write a chosen background and record it on the story's location."""
        d = self.story_bg_dir(key); d.mkdir(parents=True, exist_ok=True)
        (d / f"{loc}.png").write_bytes(png)
        data = self._read_story_data(key)
        for l in data.get("locations", []):
            if l.get("id") == loc:
                l["background"] = f"/api/stories/{key}/bg/{loc}.png"
        self._write_story_data(key, data)
        return f"/api/stories/{key}/bg/{loc}.png"

    # ── Persistence routing ── A story is JSON-backed: one self-contained <key>.json file that
    # embeds its characters (authoritative for them). A character not embedded in any story is a
    # global YAML card. These helpers hide the split so read/write paths are store-agnostic.
    # See loom/stories/records/store.py.
    def _story_file(self, key: str):
        # Kept for callers that still resolve a path (mostly the asset dirs + the migrator). The
        # store is the source of truth now; this returns the JSON path if one lingers on disk.
        from ..stories.records import store as SDB
        p = SDB.story_json_path(self.story_dir(), key)   # folder form, legacy flat as fallback
        return p if p.is_file() else None

    def _read_story_data(self, key: str) -> dict:
        from ..server.services import story_store as SS
        loaded = SS.load_story(self.root, key)
        if loaded is None:
            raise FileNotFoundError(key)
        return loaded[0]   # the story dict (chars fetched separately via _read_character_data)

    def _write_story_data(self, key: str, data: dict) -> None:
        """Validate + persist a whole story dict to the relational store, keeping the embedded
        characters.

        Validates BEFORE the write — both the intra-story shape (Story) AND the cross-aggregate
        cast→character invariant (story_reference_errors). Checking it here means a broken write
        NEVER touches the DB (the store's save_story runs in one transaction, so a validation
        raise happens before any row is written)."""
        from ..config.schema import Story, story_reference_errors
        from ..server.services import story_store as SS
        Story(**data)   # intra-story refs (Story._check_references)
        loaded = SS.load_story(self.root, key)   # recover embedded chars to preserve them
        if loaded is None:
            raise FileNotFoundError(key)
        _story, chars = loaded
        known = set(chars) | set(self.base_settings.characters)
        errs = story_reference_errors(data, known)
        if errs:
            raise ValueError(f"story '{key}' {errs[0]}")
        SS.save_story(self.root, key, data, chars)
        self.reload_settings()

    def create_story(self, name: str, fields: dict, character_keys=None, type_: str = "novel") -> str:
        """Mint a NEW story in the relational store, EMBEDDING the records of any referenced
        characters. Derives a unique key from `name`, validates, reloads. Returns the key.
        The single creation chokepoint (genesis_commit + the cast/wizard saves route here)."""
        from ..config.schema import Story
        from ..server.services import story_store as SS
        existing = {st.name for st in self.base_settings.stories.values()}
        nm, j = name.strip() or "Story", 2
        while nm in existing:
            nm, j = f"{(name.strip() or 'Story')} ({j})", j + 1
        base = re.sub(r"[^\w\-]+", "_", nm.lower()).strip("_") or "story"
        skey, i = base, 2
        while SS.story_exists(self.root, skey):
            skey, i = f"{base}_{i}", i + 1
        chars: dict = {}
        for ck in (character_keys or []):
            rec = self._read_character_data(ck)
            if rec is not None:
                chars[ck] = rec                       # embed the referenced character's record
        story = {"name": nm, "type": type_ if type_ in ("novel", "vn") else "novel", **(fields or {})}
        Story(**story)                                # validate before writing
        self.story_dir().mkdir(parents=True, exist_ok=True)   # keep the folder for assets
        SS.save_story(self.root, skey, story, chars)
        self.reload_settings()
        return skey

    def _char_owner(self, char_key: str) -> str | None:
        """The story key that OWNS this character (embeds it), or None for a global library card.
        Indexed lookup on the relational store (was an O(stories) JSON scan)."""
        from ..server.services import story_store as SS
        return SS.story_owner(self.root, char_key)

    def art_style(self, story_key: str | None = None, char_key: str | None = None) -> str:
        """Layer 0 of the image card: the art style every render in a story opens with.
        Resolution: the story's authored art_style (Overview tab) → the global broadcast
        anchor → the built-in default. A char_key resolves its owning story first."""
        from .services.prompts import style_anchor
        skey = story_key or (self._char_owner(char_key) if char_key else None)
        st = self.base_settings.stories.get(skey) if skey else None
        s = (getattr(st, "art_style", "") or "").strip()
        if s:
            return s if s.endswith(".") else s + "."
        return style_anchor(self.root)

    def _read_character_data(self, key: str) -> dict | None:
        from ..server.services import story_store as SS
        owner = self._char_owner(key)
        if owner is not None:
            return SS.get_character(self.root, owner, key)
        safe = re.sub(r"[^\w\-]+", "", key)
        path = self.char_dir() / f"{safe}.yaml"
        return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else None

    def _read_story_character_data(self, story_key: str, key: str) -> dict | None:
        """Read a cast card from one explicit Story before considering its library source.

        Character keys are historically global-looking, but a Story embeds an
        independent card record.  Callers that already know the Story must not
        resolve through ``_char_owner``: the same source card can legitimately
        be embedded in more than one Story.
        """
        from ..server.services import story_store as SS
        embedded = SS.get_character(self.root, story_key, key)
        if embedded is not None:
            return embedded
        safe = re.sub(r"[^\w\-]+", "", key)
        path = self.char_dir() / f"{safe}.yaml"
        return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else None

    def _write_character_data(self, key: str, cdata: dict) -> None:
        """Persist a character to its OWNING story (embedded in the relational store) or the
        global YAML library."""
        from ..config.schema import Character
        from ..server.services import story_store as SS
        Character(**cdata)   # validate FIRST
        owner = self._char_owner(key)
        if owner is not None:
            SS.upsert_character(self.root, owner, key, cdata)
        else:
            safe = re.sub(r"[^\w\-]+", "", key)
            (self.char_dir() / f"{safe}.yaml").write_text(
                yaml.safe_dump(cdata, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.reload_settings()

    def _write_story_character_data(self, story_key: str, key: str, cdata: dict) -> None:
        """Persist one Story's embedded cast card without touching another Story or YAML.

        An initially library-backed cast member becomes an embedded copy on its
        first Story-local edit.  That is deliberate: authoring a Story cannot
        silently rewrite the reusable source card or a second Story that used
        the same source.
        """
        from ..config.schema import Character
        from ..server.services import story_store as SS
        Character(**cdata)  # validate FIRST
        SS.upsert_character(self.root, story_key, key, cdata)
        self.reload_settings()

    def update_story_fields(self, key: str, fields: dict) -> list[str]:
        """Patch top-level fields onto a saved story + reload. SELF-HEALS repairable dangling refs
        BEFORE writing — including cast members with no character record (dropped + cascaded) — so a
        bad op can't corrupt the store or block valid co-edits in the same turn. Returns the list of
        repairs made (empty = none) so the caller can surface them to the writer. See _self_heal_refs
        + _write_story_data (which validates cross-refs before the write)."""
        from ..server.services import story_store as SS
        data = self._read_story_data(key)
        data.update(fields)
        known = set(SS.character_keys(self.root, key)) | set(self.base_settings.characters)
        repairs = _self_heal_refs(data, known)
        self._write_story_data(key, data)
        return repairs

    def persist_character_entry(self, key: str, doc: dict) -> None:
        """Persist one character's conversational field edits (the agent's `character:<key>` target).
        `doc` is a {cast:[entry]} artifact the agent mutated; the entry maps back onto the Character
        YAML (persona→system; role/appearance/base_prompt + the core harness onto fields). Only
        non-empty fields are written (so a wardrobe/no-op turn never blanks anything). Validates
        before writing; raises FileNotFoundError for an unknown character."""
        cast = [c for c in (doc.get("cast") or []) if isinstance(c, dict)]
        entry = next((c for c in cast if str(c.get("id")) == key), None) or (cast[0] if cast else None)
        if entry is None:
            raise ValueError("no character entry to persist")
        data = self._read_character_data(key)   # from the owning story DB or the global library
        if data is None:
            raise FileNotFoundError(key)
        if entry.get("name"):
            data["name"] = entry["name"]
        if entry.get("persona"):
            data["system"] = entry["persona"]
        fields = dict(data.get("fields") or {})
        for k in ("role", "appearance", "base_prompt", "temperament", "want", "lie", "wound", "secret"):
            if entry.get(k):
                fields[k] = entry[k]
        data["fields"] = fields
        self._write_character_data(key, data)   # routes back to the same store

    def cast_doc_to_members(self, cast_list) -> list[dict]:
        """Convert an inline cast doc ({name,role,persona,primary}) into Story CastMember refs
        ({character,primary,outfit?}), CREATING a character card for any member that isn't
        already a reference. Idempotent by name — an existing character of the same name is
        reused, never duplicated — and each member is stamped with its resolved `character`
        key so a re-persist won't create a second card. This is how the cast tools persist:
        authoring an inline character actually mints a card via write_character."""
        members: list[dict] = []
        for c in cast_list or []:
            if not isinstance(c, dict):
                continue
            key = (c.get("character") or "").strip()
            if not key:
                name = (c.get("name") or "").strip()
                if not name:
                    continue
                key = next((k for k, ch in self.base_settings.characters.items()
                            if (getattr(ch, "name", "") or "").strip().lower() == name.lower()), "")
                if not key:   # mint a new card from the inline persona
                    res = self.write_character(
                        {"name": name, "system": c.get("persona") or "",
                         "fields": {"role": c.get("role") or ""}}, None)
                    key = (res or {}).get("key", "") if isinstance(res, dict) else ""
                c["character"] = key   # stamp it back so the doc becomes a reference (idempotent)
            if key:
                m = {"character": key, "primary": bool(c.get("primary"))}
                if c.get("outfit"):
                    m["outfit"] = c["outfit"]
                members.append(m)
        return members

    def save_story_bg(self, key: str, png: bytes) -> str:
        """Write a story's COVER image and record it as the story background. Mirrors
        save_location_bg but for the whole-story cover (served at /api/stories/{key}/bg/_cover.png)."""
        d = self.story_bg_dir(key); d.mkdir(parents=True, exist_ok=True)
        (d / "_cover.png").write_bytes(png)
        url = f"/api/stories/{key}/bg/_cover.png"
        self.update_story_fields(key, {"background": url})
        return url


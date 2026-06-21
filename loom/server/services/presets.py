"""Model PRESETS — the first-class "model side" of a generation, managed centrally like
lorebooks are. A preset bundles only the model concerns: which text model, the address
*mode* (roleplay vs assist — see chat router), a base system prompt, and inference params.

The abstraction is deliberately layered:  FUNCTION  →  LOREBOOK  →  PRESET.

  - A *function* (an iteration view / pipeline stage: workshop, scenes, characters,
    wardrobe, prompt-gen, …) attaches one or more lorebooks.
  - A *lorebook* (book metadata, in lorebook_store) carries a `preset` binding.
  - A *preset* (here) supplies the model + mode + system + params.

So changing "what model the characters step uses" = set the `_character_fns` book's
preset. Nothing ever silently borrows the free-chat config: functions can't cross over,
because each function reaches its model only through its own book's binding.

Stored in ``configs/presets.json`` as ``{presets: [ {id,name,description,model,mode,
system,params} ]}``. The Presets manager UI is the central place to audit/edit them.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config_files import _clean_params

_MODES = ("", "roleplay", "assist")


def _default_preset() -> dict:
    return {"id": "default", "name": "Default", "description": "",
            "connection": "", "model": "", "mode": "",
            # Prompt slots (positions in the assembled prompt):
            #   system       — top system message (standing rules)
            #   author_note  — injected into history `author_depth` turns from the end (strong steer)
            #   post_history — placed AFTER the history, last thing before the reply (strongest steer)
            "system": "", "author_note": "", "author_depth": 4, "post_history": "",
            # Inference: numeric sampling lives in `params` (see _PARAM_SPEC). `stop` is a list of
            # stop sequences; `reasoning_effort` drives OpenRouter `reasoning:{effort}` on reasoning models.
            "params": {}, "stop": [], "reasoning_effort": ""}


# Seed presets — one per kind of MODEL JOB in the app, so the manager reflects the full
# functional surface out of the box. Reusable: several functions may point at the same
# preset, but always EXPLICITLY (via their book), never by accident. `mode` doubles as an
# organizing axis — roleplay presets speak in-character, assist presets do craft work.
_SEED_PRESETS = [
    # ── Roleplay (the model BECOMES someone) ──
    {"id": "free_chat", "name": "Free Chat", "mode": "roleplay",
     "description": "In-character roleplay — the standalone chat surface speaks AS the character."},
    {"id": "npc_actor", "name": "NPC Actor", "mode": "roleplay",
     "description": "Voices a single info-isolated cast member in the story simulation — knows only "
                    "what that character knows, acts on their goals + secrets. Never breaks character."},
    # ── Assist · story development ──
    {"id": "story_consultant", "name": "Story Consultant", "mode": "assist",
     "description": "A developmental craft collaborator that builds the story document with the "
                    "writer and never roleplays. Used by the workshop / storyboard / scenes / cast flows."},
    {"id": "spine_architect", "name": "Spine Architect", "mode": "assist",
     "description": "Character psychologist — maps the emotional spine (wound / lie / truth / heart), "
                    "the inner journey, not events."},
    {"id": "scene_director", "name": "Scene Director", "mode": "assist",
     "description": "Runs emergent scene bursts in the simulation, decides who acts, and progresses "
                    "the story's features. Directs, never roleplays."},
    # ── Assist · extraction & building ──
    {"id": "extractor", "name": "Structured Extractor", "mode": "assist",
     "description": "Pulls structured data (locations, cast, beats) out of prose. Terse, literal, no roleplay."},
    {"id": "character_builder", "name": "Character Builder", "mode": "assist",
     "description": "Art-directs a character: fills persona, physical features and appearance from the "
                    "card, inferring tasteful detail that fits their world/age/role."},
    {"id": "wardrobe_stylist", "name": "Wardrobe Stylist", "mode": "assist",
     "description": "Designs outfits/wardrobe for a character and renders them as image-ready tags."},
    {"id": "lore_author", "name": "Lore Author", "mode": "assist",
     "description": "Authors lorebook entries (places, factions, items, rules) in the established tone "
                    "— the engine behind ✨ Augment."},
    # ── Assist · image prompting (some need a VISION model) ──
    {"id": "tag_prompter", "name": "Tag Prompter", "mode": "assist",
     "description": "Writes booru-tag image prompts. Tags only, never prose or roleplay."},
    {"id": "chat_prompt", "name": "Chat Prompt Writer", "mode": "assist",
     "description": "Turns the current chat moment into a scene image prompt for inline illustration."},
    {"id": "vision_describer", "name": "Vision Describer", "mode": "assist",
     "description": "Reads a reference IMAGE and writes the base physical-feature prompt from it. "
                    "Pick a VISION-capable model for this preset."},
]


def _clean_preset(raw: dict) -> dict:
    p = _default_preset()
    p.update({k: v for k, v in (raw or {}).items()
              if k in ("id", "name", "description", "connection", "model", "mode", "system",
                       "author_note", "author_depth", "post_history", "params", "stop",
                       "reasoning_effort")})
    p["id"] = str(p.get("id") or "").strip() or "preset"
    p["name"] = str(p.get("name") or p["id"]).strip()
    p["description"] = str(p.get("description") or "").strip()
    p["connection"] = str(p.get("connection") or "").strip()
    p["model"] = str(p.get("model") or "").strip()
    p["mode"] = p["mode"] if p.get("mode") in _MODES else ""
    p["system"] = str(p.get("system") or "")
    p["author_note"] = str(p.get("author_note") or "")
    try:
        p["author_depth"] = max(0, int(p.get("author_depth") or 0))
    except (TypeError, ValueError):
        p["author_depth"] = 4
    p["post_history"] = str(p.get("post_history") or "")
    p["params"] = _clean_params(p.get("params") or {})
    p["stop"] = [str(s) for s in (p.get("stop") or []) if str(s).strip()][:4]
    p["reasoning_effort"] = p["reasoning_effort"] if p.get("reasoning_effort") in ("", "low", "medium", "high") else ""
    return p


def _path(root: Path) -> Path:
    return root / "configs" / "presets.json"


def load_presets(root: Path) -> dict:
    """The preset library: {active, presets:[...]}. `active` is the preset that drives free
    chat (the standalone surface) when no lorebook binds one. Always returns at least the
    seeds, lazily written on first load so the central manager is never empty."""
    path = _path(root)
    data = {"active": "free_chat", "presets": []}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded.get("presets"), list):
                data = loaded
        except (ValueError, OSError):
            pass
    data["presets"] = [_clean_preset(p) for p in data.get("presets") or []]
    # Ensure the default + every built-in seed exists (by id). New seeds appear on existing
    # installs too; user EDITS to a seed are preserved (we only add missing ids). A deleted
    # built-in seed re-appears on next load — like the reserved lorebooks.
    have = {p["id"] for p in data["presets"]}
    added = False
    if "default" not in have:
        data["presets"].insert(0, _default_preset()); added = True; have.add("default")
    for s in _SEED_PRESETS:
        if s["id"] not in have:
            data["presets"].append(_clean_preset(s)); added = True; have.add(s["id"])
    if data.get("active") not in have:
        data["active"] = "free_chat" if "free_chat" in have else data["presets"][0]["id"]
        added = True
    if added:
        data = save_presets(root, data)
    return data


def save_presets(root: Path, data: dict) -> dict:
    presets = [_clean_preset(p) for p in (data or {}).get("presets") or []]
    if not presets:
        presets = [_default_preset()]
    ids = {p["id"] for p in presets}
    active = (data or {}).get("active") or "free_chat"
    out = {"active": active if active in ids else next(iter(ids)), "presets": presets}
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def set_active(root: Path, preset_id: str) -> dict:
    lib = load_presets(root)
    if any(p["id"] == preset_id for p in lib["presets"]):
        lib["active"] = preset_id
        lib = save_presets(root, lib)
    return lib


def active_preset(root: Path) -> dict:
    """The preset driving free chat. Always returns a preset (falls back to default)."""
    lib = load_presets(root)
    return get_preset(root, lib.get("active")) or lib["presets"][0]


def get_preset(root: Path, preset_id: str | None) -> dict | None:
    if not preset_id:
        return None
    for p in load_presets(root)["presets"]:
        if p["id"] == preset_id:
            return p
    return None


def upsert_preset(root: Path, body: dict) -> dict:
    lib = load_presets(root)
    clean = _clean_preset(body or {})
    presets = [p for p in lib["presets"] if p["id"] != clean["id"]]
    presets.append(clean)
    save_presets(root, {"active": lib.get("active"), "presets": presets})
    return clean


def delete_preset(root: Path, preset_id: str) -> dict:
    lib = load_presets(root)
    if preset_id == "default":
        return lib                      # the default is the floor; never delete it
    presets = [p for p in lib["presets"] if p["id"] != preset_id]
    return save_presets(root, {"active": lib.get("active"), "presets": presets})


def preset_for_books(root: Path, books: list) -> dict | None:
    """Resolve the PRESET a function flow should use, from its attached lorebooks.

    The chain is Function → Lorebook → Preset: among the attached books, a FUNCTION book's
    binding wins (it's the one defining the operation); otherwise the first bound book.
    Returns the preset dict, or None if no attached book carries a binding (the caller then
    uses a clearly-named default — never the free-chat config)."""
    from . import lorebook_store as _LS
    books = [str(b) for b in (books or []) if b]
    if not books:
        return None
    metas = {b["id"]: b for b in _LS.list_books(root)}
    fn_bound, any_bound = None, None
    for bid in books:
        m = metas.get(bid)
        pid = (m or {}).get("preset")
        if not pid:
            continue
        if any_bound is None:
            any_bound = pid
        if (m or {}).get("category") == "function" and fn_bound is None:
            fn_bound = pid
    pid = fn_bound or any_bound
    return get_preset(root, pid) if pid else None

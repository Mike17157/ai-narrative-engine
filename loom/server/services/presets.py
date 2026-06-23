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

# Built-in preset ids that were renamed/removed — dropped on load so they don't linger.
# 'extractor' (generic "Structured Extractor") → 'location_builder' (a real function).
_RETIRED_PRESETS = {"extractor"}


def _default_preset() -> dict:
    return {"id": "default", "name": "Default", "description": "",
            # Organization: `group` buckets presets by function; `order` sequences them
            # (the pipeline flow — spine → arc → cast → image → sim — since they feed each other).
            "group": "", "order": 0,
            # `connection` is the saved API endpoint (incl. local Ollama — see connections.py);
            # `model` is a model on it. Picking the auto-seeded `ollama-local` connection = run local.
            "connection": "", "model": "", "mode": "",
            # Image side — the unified preset bundles the IMAGE WORKFLOW too (not an image
            # "model"; a workflow already encodes its checkpoints/LoRAs). `image_workflow` is a
            # models.yaml image key; `image_provider` picks where it runs ("" = global default,
            # "local" = ComfyUI, "cloud" = RunPod serverless); `image_preset` is the LoRA "look".
            "image_workflow": "", "image_provider": "", "image_preset": "",
            # Lorebooks this preset composes (the universal container — world info, sprites,
            # functions/scripts). A list of book ids.
            "lorebooks": [],
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
    # ══ Chat ══
    {"id": "free_chat", "name": "Free Chat", "mode": "roleplay", "group": "Chat", "order": 0,
     "description": "In-character roleplay — the standalone chat surface speaks AS the character.",
     # Roleplay: the character's own persona governs voice; keep the base light.
     "system": ""},

    # ══ Story arc — develop the shape of the story ══
    {"id": "spine_architect", "name": "Spine Architect", "mode": "assist",
     "group": "Story arc", "order": 10,
     "description": "Character psychologist — maps the emotional spine (wound / lie / truth / heart), "
                    "the inner journey, not events.",
     "system": (
         "You are a character psychologist and story architect. You do NOT outline events — you map "
         "the INNER journey of a person, the skeleton every character-driven arc hangs from.\n\n"
         "From the character (and any premise), surface their EMOTIONAL SPINE:\n"
         "• WOUND — the specific unhealed hurt that carved them. A formative event or pattern, not a "
         "trait. Concrete and rooted in something real.\n"
         "• LIE — the false belief they formed to protect themselves from the wound. This is the "
         "central dramatic engine; the whole story exists to dismantle it. Psychologically honest, "
         "never abstract.\n"
         "• TRUTH — what they must finally accept to grow: the earned opposite of the lie, paid for "
         "at cost, never given.\n"
         "• HEART — the human resonance at the center; the thing a stranger would recognize and feel.\n\n"
         "Keep each anchor specific to THIS person. Everything downstream — arc, scenes, choices — "
         "must hang from this spine.")},
    {"id": "story_consultant", "name": "Story Consultant", "mode": "assist",
     "group": "Story arc", "order": 11,
     "description": "A developmental craft collaborator that builds the story document with the "
                    "writer and never roleplays. Workshop / storyboard / scenes / cast flows.",
     "system": (
         "You are a story developmental editor working WITH a writer to find the truest story latent "
         "in a character. You think the way working modern-craft writers think (Will Storr, Lisa "
         "Cron, K.M. Weiland, John Truby, George Saunders, Donald Maass, Shawn Coyne) — not in "
         "academic literary vocabulary.\n\n"
         "HOW YOU WORK:\n"
         "- DIAGNOSTIC FIRST. Open with what you SEE — the wound, the misbelief, the gap between what "
         "they want and what they need, the shape of change latent in them. Don't open with questions.\n"
         "- OPINIONATED. If an idea dodges the character's real developmental potential, say so and "
         "propose the harder, truer arc. Think in growth cycles: meet the truth, flinch, retreat into "
         "the lie, pay a cost, circle back — what finally breaks the pattern?\n"
         "- CONCRETE. Ground every craft principle in THIS character. When you name an event, name "
         "the internal inflection it exists to force; plot is just the machine that tests the person.\n"
         "- The writer wants interiority, tension, melancholy and earned change — resist tidy or "
         "shallow premises. Conversational and substantive (2–4 short paragraphs), never bullet lists.")},

    # ══ Cast & world — build the people, places and lore ══
    {"id": "location_builder", "name": "Location Builder", "mode": "assist",
     "group": "Cast & world", "order": 20,
     "description": "Builds the story's settings — neutral places described objectively, ready for backgrounds.",
     "system": (
         "You build the story's LOCATIONS — the neutral places where scenes happen. Describe each "
         "place OBJECTIVELY: physical appearance, materials, atmosphere, light and layout — no people, "
         "no events, no plot. Give each a short name and a concrete description a set designer could "
         "build from, plus (when useful) an image background prompt. Stay consistent with the world's "
         "tone, era and geography; invent grounded detail where the source is sparse, never contradict it.")},
    {"id": "character_builder", "name": "Character Builder", "mode": "assist",
     "group": "Cast & world", "order": 21,
     "description": "Art-directs a character: persona + physical features + appearance from the card, "
                    "inferring tasteful detail that fits their world/age/role.",
     "system": (
         "You are a character art director and biographer. Given a card, flesh out a vivid, "
         "internally consistent person:\n"
         "- PERSONA: personality, voice, wants, fears and the contradiction that makes them worth "
         "reading about.\n"
         "- ROLE: how they function in the story.\n"
         "- APPEARANCE: persistent physical traits as EXPLICIT, ATOMIC visual descriptors — one "
         "attribute per item ('silver hair', 'long hair', 'wavy hair', 'violet eyes', 'pale skin', "
         "'mole under eye'); split every compound ('long silver hair' → 'long hair' + 'silver hair').\n\n"
         "Draw specific detail from the notes; where sparse, INFER tasteful detail that fits their "
         "world, age and role — but never contradict anything stated. Persistent traits ONLY: no "
         "clothing, pose, expression or background (those come later).")},
    {"id": "wardrobe_stylist", "name": "Wardrobe Stylist", "mode": "assist",
     "group": "Cast & world", "order": 22,
     "description": "Designs outfits/wardrobe for a character and renders them as image-ready descriptors.",
     "system": (
         "You are a wardrobe stylist. Design outfits that express a character's personality, role, "
         "status and world — a small coherent set (everyday, occasion, and a signature look). For "
         "each, give a one-line concept, then the garments as explicit, image-ready descriptors. "
         "Keep every choice grounded in the setting and the person; avoid generic 'fantasy outfit' "
         "filler. Describe only what they WEAR — never restate persistent body traits.")},
    {"id": "lore_author", "name": "Lore Author", "mode": "assist",
     "group": "Cast & world", "order": 23,
     "description": "Authors lorebook entries (places, factions, items, rules) in the established tone "
                    "— the engine behind ✨ Augment.",
     "system": (
         "You author lorebook entries — self-contained facts: a place, person, faction, item, rule or "
         "event. Each entry has a short TITLE, a few lowercase trigger KEYWORDS that should pull it "
         "into context when mentioned, and a concise, concrete CONTENT paragraph written in the "
         "established tone. Propose genuinely new, complementary entries; never duplicate what already "
         "exists and never contradict canon. Vivid and specific over generic.")},

    # ══ Image prompts — turn people/scenes into prompts (some need a VISION model) ══
    {"id": "vision_describer", "name": "Vision Describer", "mode": "assist",
     "group": "Image prompts", "order": 30,
     "description": "Reads a reference IMAGE and writes the base physical-feature prompt from it. "
                    "Pick a VISION-capable model for this preset.",
     "system": (
         "You are given a reference IMAGE of a character. Read it and produce their PERSISTENT "
         "physical features as explicit, atomic visual descriptors ('long hair', 'silver hair', "
         "'violet eyes', 'pale skin', 'mole under eye') — split every compound into single attributes. "
         "Describe only what is actually VISIBLE and persistent: hair, eyes, skin, build, "
         "distinguishing marks. No clothing, pose, expression or background. Stay faithful to the "
         "image; never invent a trait it doesn't show. (Requires a vision-capable model.)")},
    {"id": "tag_prompter", "name": "Tag Prompter", "mode": "assist",
     "group": "Image prompts", "order": 31,
     "description": "Writes booru-tag image prompts. Tags only, never prose or roleplay.",
     "system": (
         "You write image prompts as Danbooru/booru TAGS only — never prose. Output comma-separated "
         "lowercase tags using real booru vocabulary, ordered roughly subject → physical features → "
         "clothing → setting → framing (e.g. '1girl, silver hair, long hair, violet eyes, school "
         "uniform, classroom, looking at viewer'). No sentences, no narration, no quality boilerplate "
         "unless asked. If the prompt uses BREAK regions, preserve them.")},
    {"id": "chat_prompt", "name": "Chat Prompt Writer", "mode": "assist",
     "group": "Image prompts", "order": 32,
     "description": "Turns the current chat moment into a scene image prompt for inline illustration.",
     "system": (
         "You turn the CURRENT chat moment into an image prompt for inline illustration. Read the "
         "latest exchange, identify the character(s) present and their expression, pose, action and "
         "setting right now, and render exactly THIS beat as booru tags (subject → expression/pose → "
         "action → setting → framing). Not a generic portrait — the specific moment. Tags only, no prose.")},

    # ══ Simulation — runtime, emergent play ══
    {"id": "scene_director", "name": "Scene Director", "mode": "assist",
     "group": "Simulation", "order": 40,
     "description": "Runs emergent scene bursts in the simulation, decides who acts, and progresses "
                    "the story's features. Directs, never roleplays.",
     "system": (
         "You are the DIRECTOR of an emergent, character-driven simulation. You do not voice "
         "characters — you decide what happens between them. Each beat: read the current world state, "
         "the cast's goals and secrets and the dramatic pressure; choose who acts and the event that "
         "tests them; then advance the story's open features (relationships, plots, revelations) by "
         "the smallest honest increment. Favor consequence and friction over comfort, keep the world "
         "consistent, and never resolve tension for free. Output direction, not prose.")},
    {"id": "npc_actor", "name": "NPC Actor", "mode": "roleplay",
     "group": "Simulation", "order": 41,
     "description": "Voices a single info-isolated cast member in the story simulation — knows only "
                    "what that character knows, acts on their goals + secrets. Never breaks character.",
     "system": (
         "You voice a SINGLE character in a living scene. You know only what this character knows — "
         "act on their goals, fears and secrets, and never reveal or rely on anything they couldn't "
         "know. Stay fully in character: speech, body language and choices consistent with who they "
         "are and what they want right now. React truthfully to what just happened; never narrate "
         "other characters' inner lives and never break the fourth wall.")},
]


def _clean_preset(raw: dict) -> dict:
    p = _default_preset()
    p.update({k: v for k, v in (raw or {}).items()
              if k in ("id", "name", "description", "group", "order", "connection", "model",
                       "mode", "image_workflow", "image_provider", "image_preset", "lorebooks",
                       "system", "author_note", "author_depth", "post_history",
                       "params", "stop", "reasoning_effort")})
    p["id"] = str(p.get("id") or "").strip() or "preset"
    p["name"] = str(p.get("name") or p["id"]).strip()
    p["description"] = str(p.get("description") or "").strip()
    p["group"] = str(p.get("group") or "").strip()
    try:
        p["order"] = int(p.get("order") or 0)
    except (TypeError, ValueError):
        p["order"] = 0
    p["connection"] = str(p.get("connection") or "").strip()
    p["model"] = str(p.get("model") or "").strip()
    p["mode"] = p["mode"] if p.get("mode") in _MODES else ""
    p["image_workflow"] = str(p.get("image_workflow") or "").strip()
    p["image_provider"] = p["image_provider"] if p.get("image_provider") in ("", "local", "cloud") else ""
    p["image_preset"] = str(p.get("image_preset") or "").strip()
    p["lorebooks"] = [str(b).strip() for b in (p.get("lorebooks") or []) if str(b).strip()]
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
    # One-time migration: the legacy per-preset `local` flag (routed to configs/app.json →
    # local_model) is gone. A preset that had it now points at the auto-seeded `ollama-local`
    # connection instead — flipped here on the raw dict so _clean_preset (which dropped the
    # field) persists the new shape on first load.
    raw = [p for p in data.get("presets") or [] if p.get("id") not in _RETIRED_PRESETS]
    migrated = False
    for p in raw:
        if p.pop("local", False):
            p["connection"] = "ollama-local"
            if not (p.get("model") or "").strip():
                p["model"] = "meromero"
            migrated = True
    data["presets"] = [_clean_preset(p) for p in raw]
    # Ensure the default + every built-in seed exists (by id). New seeds appear on existing
    # installs too; user EDITS to a seed are preserved (we only add missing ids). A deleted
    # built-in seed re-appears on next load — like the reserved lorebooks.
    by_id = {p["id"]: p for p in data["presets"]}
    have = set(by_id)
    added = migrated
    if "default" not in have:
        data["presets"].insert(0, _default_preset()); added = True; have.add("default")
    for s in _SEED_PRESETS:
        seed = _clean_preset(s)
        cur = by_id.get(seed["id"])
        if cur is None:
            data["presets"].append(seed); added = True; have.add(seed["id"])
            continue
        # Backfill new/blank fields from the seed without clobbering user edits — so an
        # existing install picks up the fleshed system prompts + grouping the first time.
        for f in ("system", "group", "description"):
            if not cur.get(f) and seed.get(f):
                cur[f] = seed[f]; added = True
        if not cur.get("order") and seed.get("order"):
            cur["order"] = seed["order"]; added = True
        # Repair a name that leaked to the generic "Default" (a partial upsert with no name
        # defaults it there) — restore the seed's real name.
        if seed.get("name") and seed["id"] != "default" and cur.get("name") in ("", "Default"):
            cur["name"] = seed["name"]; added = True
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
    """Create or update one preset. A PARTIAL body merges onto the existing preset (so e.g.
    posting just {id, name} never wipes its system/params)."""
    lib = load_presets(root)
    body = dict(body or {})
    existing = next((p for p in lib["presets"] if p["id"] == body.get("id")), None)
    clean = _clean_preset({**existing, **body} if existing else body)
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


# Convention id for a stage lorebook (a function book declaring one pipeline stage).
def stage_book_id(stage: str) -> str:
    return f"_stage_{stage}"


# Pipeline stages, folded into the lorebook→preset model like every other function. Each gets
# a `stage_<stage>` PRESET (its model + system) bound to a `_stage_<stage>` function book.
_STAGE_NAMES = {
    "storyboard": "Storyboard", "spine": "Spine", "locations": "Locations",
    "characters": "Characters", "wardrobe": "Wardrobe", "base_image": "Base image",
    "emotion": "Emotion", "workshop": "Workshop", "sim_director": "Sim director",
    "sim_actor": "Sim actor",
}


def seed_stage_presets(root: Path) -> None:
    """Ensure each pipeline STAGE has a behavior-matched preset `stage_<stage>` (model from
    story_builder.json, system from the pipeline's DEFAULT_SYSTEMS where it has one — else
    empty, so the caller's own system stands). Stages resolve straight to these presets by
    convention (see stage_preset) — no lorebook middleman. Idempotent: only fills what's
    missing, so user edits are never clobbered."""
    from .config_files import load_story_builder
    try:
        from ...stories.pipeline._helpers import DEFAULT_SYSTEMS
    except Exception:  # noqa: BLE001
        DEFAULT_SYSTEMS = {}

    models = load_story_builder(root).get("models") or {}
    lib = load_presets(root)
    have = {p["id"] for p in lib["presets"]}
    added = False
    for stage in _STAGE_NAMES:
        pid = f"stage_{stage}"
        if pid not in have:
            lib["presets"].append(_clean_preset({
                "id": pid, "name": _STAGE_NAMES[stage], "group": "Pipeline stages",
                "mode": "assist", "model": models.get(stage, "") or "",
                "system": DEFAULT_SYSTEMS.get(stage, "") or "",
            }))
            added = True
    if added:
        save_presets(root, lib)


def stage_preset(root: Path, stage: str) -> dict | None:
    """Resolve a pipeline STAGE → its preset. Convention: stage `X` is backed by the preset
    `stage_X` (seeded by seed_stage_presets) — a direct lookup, no lorebook middleman. For
    back-compat a user may instead bind a stage via a `function` book holding a
    `{kind:"stage", fn:X}` entry; that's honored as a fallback. Returns the preset dict, or
    None (the caller then falls back to story_builder.json)."""
    if not stage:
        return None
    p = get_preset(root, f"stage_{stage}")
    if p:
        return p
    return _stage_preset_from_books(root, stage)


def _stage_preset_from_books(root: Path, stage: str) -> dict | None:
    """Back-compat fallback: find a stage's preset via a `function` book that declares it
    (`{kind:"stage", fn:<stage>}` + book.preset). Only reached when no `stage_<stage>` preset
    exists — e.g. a hand-authored binding."""
    from . import lorebook_store as _LS
    from ...stories.graph_ops import stage_spec as _stage_spec

    def _from_book(meta) -> dict | None:
        if not meta.get("preset"):
            return None
        for e in _LS.load_lorebook(root, meta["id"]):
            spec = _stage_spec(getattr(e, "content", "") or "")
            if spec and str(spec.get("fn") or "").strip() == stage:
                return get_preset(root, meta["preset"])
        return None

    fast = _LS.get_book(root, stage_book_id(stage))
    if fast:
        hit = _from_book(fast)
        if hit is not None:
            return hit
    for meta in _LS.list_books(root):
        if meta.get("category") == "function" and meta["id"] != stage_book_id(stage):
            hit = _from_book(meta)
            if hit is not None:
                return hit
    return None

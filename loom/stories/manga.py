"""Manga-panel planning and prompt assembly for Krea2.

The image model receives a compact visual brief, never a whole chapter.  This
keeps a panel readable, gives every depicted character a concrete identity, and
makes the LoRA choice an explicit, reviewable part of the plan.
"""

from __future__ import annotations

import re
from typing import Any


MANGA_PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["panels"],
    "properties": {
        "panels": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["chapter", "moment", "characters", "composition", "shot", "setting", "lighting", "layout", "body_lora"],
                "properties": {
                    "chapter": {"type": "integer"},
                    "moment": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                    "composition": {"type": "string"},
                    "shot": {"type": "string"},
                    "setting": {"type": "string"},
                    "lighting": {"type": "string"},
                    "layout": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                        "required": ["character", "slot", "depth", "scale", "action"], "properties": {
                            "character": {"type": "string"},
                            "slot": {"type": "string", "enum": ["left", "center_left", "center", "center_right", "right"]},
                            "depth": {"type": "string", "enum": ["background", "middle", "foreground"]},
                            "scale": {"type": "number", "minimum": 0.35, "maximum": 0.95},
                            "action": {"type": "string"},
                        }}},
                    "body_lora": {"type": "boolean"},
                },
            },
        },
    },
}


MANGA_PLAN_SYSTEM = """You are a manga art director turning finished prose into image plans.

Create exactly ONE cinematic illustration plan for every supplied chapter. Every panel must show
EXACTLY TWO of the supplied cast members: this is an ensemble manga, not a sequence of isolated
portraits. Choose the two people who make the chapter's pressure visible, give each a
distinct place in the frame, and show their relationship through gesture, eyelines, distance, or
an object between them. Never invent a person, merge people, or use a name outside the supplied cast.

Choose a decisive visual beat rather than trying to summarize prose. `composition` is a concise,
practical camera plan: foreground / middle ground / background, character positions, action,
setting, light, and the key prop or visual tension. `shot` is a conventional camera framing such
as wide two-shot, over-the-shoulder medium shot, or high-angle group shot. `moment` is one crisp
sentence describing the dramatic instant.

`setting` and `lighting` are the background-only brief: name only architecture, furnishings, props,
weather/time, palette and illumination. Never include a person, name, action, silhouette, or pose.

`layout` is the production layout for automated compositing. Give every depicted character exactly
one entry. `slot` is their horizontal screen position, `depth` controls overlap order, and `scale`
is their final height as a fraction of the frame (0.35–0.95). Make the emotional focus larger and
in the foreground; keep people who need to be readable apart instead of stacking faces or torsos.
Reserve a clean shared space between people for the key prop or gesture. This layout is binding for
the render, so make it match the stated composition. `action` is that ONE character's silent pose
or gesture only; it must not name, describe, touch, or imply another person.

The panel will be rendered by an image model. It cannot reliably typeset dialogue: do not request
speech bubbles, written signs, captions, or readable text. Keep every person fully clothed unless
the supplied chapter specifically requires otherwise. Use `body_lora` only for a restrained,
naturalistic enhancement of a compatible slim, petite, athletic, or slender adult woman; a slender
woman may still be full-busted. Set it false for a voluptuous, curvy, plus-size, or plump woman,
and false when it is not clearly useful. A male cast member's build is not relevant to this choice.
Return only the requested structured data."""


# The successful Krea2 mimic passes were not watercolor illustration.  Their
# distinctive read is crisp dark linework, saturated cel colour, glossy hair and
# eye highlights, and controlled cinematic light.  Keep this as a shared anchor
# so a story's local mood cannot accidentally turn the production stack soft.
MIMIC_MANGA_STYLE = (
    "Crisp high-polish anime key visual with a premium manga-cover finish: clean confident dark "
    "lineart, sharp edges, vivid saturated colours, clear cel-shaded forms, luminous glossy hair "
    "highlight bands, layered gradient irises with bright catchlights, soft warm cheek blush, and "
    "cinematic warm rim light. No watercolor, no painterly wash, no paper texture, no sketchy lines, "
    "no soft-focus airbrush rendering."
)


_BODY_LORA_BLOCK = re.compile(
    r"\b(voluptuous|curvy|hourglass|plus[ -]?size|plump|chubby)\b", re.I,
)
_BODY_LORA_OK = re.compile(
    r"\b(petite|slim|slender|willowy|athletic|small bust|modest bust|narrow frame)\b", re.I,
)
_MALE_BODY = re.compile(r"\b(man|male|boy|gentleman|nobleman|noble|lord)\b", re.I)

_SLOT_X = {"left": .18, "center_left": .36, "center": .50, "center_right": .64, "right": .82}
_DEPTH_ORDER = {"background": 0, "middle": 1, "foreground": 2}


def body_lora_allowed(appearance: str) -> bool:
    """Gate the tested body LoRA away from the range where it exaggerated anatomy.

    The mimic sweep was stable on petite / athletic / slender silhouettes but broke the
    voluptuous example.  An explicit hard block wins over a compatible descriptor.
    """
    text = str(appearance or "")
    return bool(_BODY_LORA_OK.search(text)) and not bool(_BODY_LORA_BLOCK.search(text))


def panel_body_lora_allowed(members: list[str], cast: dict[str, dict[str, str]]) -> bool:
    """Allow a compatible woman's body LoRA without a male castmate vetoing it.

    This is deliberately narrower than a broad gendered style pass: at least one
    non-male member must be in the tested compatible range, and no non-male member
    may have the voluptuous / plump profile that distorted in the sweep.
    """
    relevant = [str((cast.get(key) or {}).get("appearance") or "") for key in members
                if not _MALE_BODY.search(str((cast.get(key) or {}).get("appearance") or ""))]
    return bool(relevant) and any(body_lora_allowed(text) for text in relevant) and not any(
        _BODY_LORA_BLOCK.search(text) for text in relevant
    )


def _default_layout(members: list[str]) -> list[dict[str, Any]]:
    """Safe fallback for an old/repaired plan that lacks explicit slots."""
    slots = {2: ("left", "right"), 3: ("left", "center", "right"),
             4: ("left", "center_left", "center_right", "right")}.get(len(members), ("left", "right"))
    return [{"character": key, "slot": slot, "depth": "middle", "scale": .70,
             "action": "a natural readable pose angled toward the center of the frame"}
            for key, slot in zip(members, slots)]


def normalize_layout(raw_layout: Any, members: list[str], aliases: dict[str, str]) -> list[dict[str, Any]]:
    """Canonicalize the planner layout; fall back rather than guessing during rendering."""
    selected: dict[str, dict[str, Any]] = {}
    claimed_slots: set[str] = set()
    for entry in raw_layout if isinstance(raw_layout, list) else []:
        if not isinstance(entry, dict):
            continue
        key = aliases.get(str(entry.get("character") or "").strip().casefold())
        slot, depth = str(entry.get("slot") or ""), str(entry.get("depth") or "")
        try:
            scale = float(entry.get("scale"))
        except (TypeError, ValueError):
            continue
        if (key not in members or key in selected or slot not in _SLOT_X or slot in claimed_slots
                or depth not in _DEPTH_ORDER or not .35 <= scale <= .95):
            continue
        action = str(entry.get("action") or "").strip()
        selected[key] = {"character": key, "slot": slot, "depth": depth, "scale": round(scale, 2),
                         "action": action[:400] or "a natural readable pose angled toward the center of the frame"}
        claimed_slots.add(slot)
    return [selected[key] for key in members] if len(selected) == len(members) else _default_layout(members)


def normalize_panel_plan(raw: dict[str, Any] | None, *, chapters: list[dict],
                         cast: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Validate the planner's output against canonical chapters and cast keys.

    Invalid or single-character panels are discarded rather than silently rendered;
    callers can ask the planner again instead of receiving misleading artwork.
    """
    by_alias = {key.casefold(): key for key in cast}
    by_alias.update({str(value.get("name") or "").casefold(): key
                     for key, value in cast.items() if value.get("name")})
    panels: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in (raw or {}).get("panels") or []:
        if not isinstance(candidate, dict):
            continue
        try:
            chapter = int(candidate.get("chapter"))
        except (TypeError, ValueError):
            continue
        if chapter < 0 or chapter >= len(chapters) or chapter in seen:
            continue
        members: list[str] = []
        for value in candidate.get("characters") or []:
            key = by_alias.get(str(value or "").strip().casefold())
            if key and key not in members:
                members.append(key)
        if len(members) != 2:
            continue
        moment = str(candidate.get("moment") or "").strip()
        composition = str(candidate.get("composition") or "").strip()
        shot = str(candidate.get("shot") or "").strip()
        if not (moment and composition and shot):
            continue
        wants_body_lora = bool(candidate.get("body_lora"))
        allowed = wants_body_lora and panel_body_lora_allowed(members, cast)
        panels.append({
            "id": f"chapter-{chapter + 1}", "chapter": chapter,
            "title": str(chapters[chapter].get("title") or f"Chapter {chapter + 1}"),
            "moment": moment, "characters": members, "composition": composition, "shot": shot,
            "setting": str(candidate.get("setting") or "cinematic story environment with practical props").strip(),
            "lighting": str(candidate.get("lighting") or "clear cinematic key light with gentle rim light").strip(),
            "layout": normalize_layout(candidate.get("layout"), members, by_alias),
            "body_lora": allowed,
            "body_lora_note": ("enabled for compatible cast silhouettes" if allowed
                               else "standard manga stack; anatomy LoRA withheld"),
        })
        seen.add(chapter)
    panels.sort(key=lambda item: item["chapter"])
    return panels


def build_panel_prompt(panel: dict[str, Any], cast: dict[str, dict[str, str]], *, style: str = "") -> str:
    """Make one image-model-facing prose prompt from a reviewed panel plan."""
    people = []
    for key in panel.get("characters") or []:
        member = cast.get(key) or {}
        name = str(member.get("name") or key)
        appearance = str(member.get("appearance") or "").strip()
        people.append(f"{name}: {appearance}" if appearance else name)
    lead = str(style or "").strip()
    if lead and not lead.endswith("."):
        lead += "."
    return " ".join(part for part in (
        lead,
        MIMIC_MANGA_STYLE,
        f"A single readable manga panel, {panel.get('shot', '')}.",
        f"The dramatic instant: {panel.get('moment', '')}",
        f"Composition: {panel.get('composition', '')}",
        "Depict these distinct recurring characters faithfully: " + "; ".join(people) + ".",
        "No text, no speech bubbles, no captions, no watermark, no extra people, no duplicate character.",
    ) if part).strip()


def build_background_prompt(panel: dict[str, Any], *, style: str = "") -> str:
    """Render only the stage; actors are assembled into its reserved areas later."""
    lead = str(style or "").strip()
    if lead and not lead.endswith("."):
        lead += "."
    return " ".join(part for part in (
        lead, MIMIC_MANGA_STYLE,
        "One empty cinematic environment plate, landscape composition.",
        f"Setting only: {panel.get('setting', '')}",
        f"Lighting only: {panel.get('lighting', '')}",
        "Render architecture, furnishings, props, perspective, and intentional negative space only. "
        "No people, no character silhouettes, no faces, no bodies, no text, no speech bubbles, no watermark.",
    ) if part).strip()


def build_character_prompt(panel: dict[str, Any], member: dict[str, str], layout: dict[str, Any], *, style: str = "") -> str:
    """A consistent isolated actor pass that can be positioned without model guesswork."""
    lead = str(style or "").strip()
    if lead and not lead.endswith("."):
        lead += "."
    name = str(member.get("name") or layout.get("character") or "character")
    appearance = str(member.get("appearance") or "").strip()
    return " ".join(part for part in (
        lead, MIMIC_MANGA_STYLE,
        f"One isolated recurring manga character, {name}. {appearance}",
        f"Individual pose only: {layout.get('action', '')}",
        "One complete person only, centered and fully visible from head to feet, clean separated silhouette. "
        "Use a perfectly plain pale blue studio backdrop with no scenery, furniture, floor details, or cast shadow. "
        "No other people, no duplicate limbs, no text, no speech bubbles, no watermark.",
    ) if part).strip()


def build_integration_prompt(panel: dict[str, Any], cast: dict[str, dict[str, str]], *, style: str = "") -> str:
    """Conservative final-pass instruction: harmonize the composite, never recompose it."""
    lead = str(style or "").strip()
    if lead and not lead.endswith("."):
        lead += "."
    names = ", ".join(str((cast.get(key) or {}).get("name") or key) for key in panel.get("characters") or [])
    return " ".join(part for part in (
        lead, MIMIC_MANGA_STYLE,
        f"Polish this existing {panel.get('shot', '')} manga panel of {names}.",
        "Preserve the exact cast count, framing, character identities, clothing, body silhouettes, "
        "screen positions, poses, and every important prop. Only harmonize lighting, contact shadows, "
        "line edges, and colour grading. No new people, no duplicate characters, no text, no speech bubbles, no watermark.",
    ) if part).strip()


def build_regional_prompts(panel: dict[str, Any], cast: dict[str, dict[str, str]], *, style: str = "") -> dict[str, str]:
    """One-pass global + left/right spatial briefs for Comfy ConditioningSetArea.

    This is intentionally not a ``BREAK`` prompt.  The base describes only shared camera and
    setting, while the two actor descriptions are sent to distinct coordinate regions.
    """
    members = list(panel.get("characters") or [])
    layouts = {entry.get("character"): entry for entry in panel.get("layout") or []}
    if len(members) != 2 or any(key not in layouts for key in members):
        raise ValueError("regional Krea2 workflow currently requires exactly two planned characters")
    ordered = sorted(members, key=lambda key: _SLOT_X.get(str(layouts[key].get("slot")), .50))
    lead = str(style or "").strip()
    if lead and not lead.endswith("."):
        lead += "."
    base = " ".join(part for part in (
        lead, MIMIC_MANGA_STYLE,
        f"One empty cinematic {panel.get('shot', 'landscape')} environment plate.",
        f"Setting: {panel.get('setting', '')}", f"Lighting: {panel.get('lighting', '')}",
        "Keep the left and right thirds visually open for foreground subjects. "
        "No people, no character silhouettes, no faces, no bodies, no text, no speech bubbles, no watermark.",
    ) if part).strip()

    def actor(key: str, side: str) -> str:
        person, layout = cast.get(key) or {}, layouts[key]
        return " ".join(part for part in (
            MIMIC_MANGA_STYLE,
            f"{side} frame region only: {person.get('name') or key}. {person.get('appearance') or ''}",
            f"Pose: {layout.get('action') or 'natural readable pose toward frame center'}.",
            f"Place this character at {layout.get('slot')} in the {layout.get('depth')} depth, "
            "with one complete body and a clean silhouette. Do not depict anyone else in this region.",
        ) if part).strip()
    return {"base": base, "left": actor(ordered[0], "Left"), "right": actor(ordered[1], "Right")}


def build_guided_panel_prompt(panel: dict[str, Any], cast: dict[str, dict[str, str]], *, style: str = "") -> str:
    """The complete, coherent brief used with a spatial guide image in a single Krea pass."""
    lead = str(style or "").strip()
    if lead and not lead.endswith("."):
        lead += "."
    roles = []
    by_layout = {entry.get("character"): entry for entry in panel.get("layout") or []}
    for key in panel.get("characters") or []:
        member, layout = cast.get(key) or {}, by_layout.get(key) or {}
        roles.append(
            f"{layout.get('slot', 'frame')} only: {member.get('name') or key}, "
            f"{member.get('appearance') or ''}; {layout.get('action') or 'natural pose toward frame center'}"
        )
    return " ".join(part for part in (
        lead, MIMIC_MANGA_STYLE,
        f"One natural {panel.get('shot', 'wide two-shot')} with exactly {len(roles)} recurring adult characters.",
        f"Setting: {panel.get('setting', '')}", f"Lighting: {panel.get('lighting', '')}",
        f"Shared scene beat: {panel.get('moment', '')}",
        "Character placement, exactly once each: " + " | ".join(roles) + ".",
        "Follow the supplied composition guide for character count, screen placement, scale, and open space. "
        "No extra people, no duplicate characters, no giant faces, no collage, no text, no speech bubbles, no watermark.",
    ) if part).strip()


def build_composed_t2i_prompt(panel: dict[str, Any], cast: dict[str, dict[str, str]], *, style: str = "") -> str:
    """A single, spatially bound text-to-image brief for Krea2 ensemble scenes."""
    lead = str(style or "").strip()
    if lead and not lead.endswith("."):
        lead += "."
    layouts = {entry.get("character"): entry for entry in panel.get("layout") or []}
    roles = []
    for key in sorted(panel.get("characters") or [], key=lambda item: _SLOT_X.get(str((layouts.get(item) or {}).get("slot")), .5)):
        member, layout = cast.get(key) or {}, layouts.get(key) or {}
        roles.append(
            f"{layout.get('slot', 'frame').replace('_', ' ')}: one and only one {member.get('name') or key}, "
            f"{member.get('appearance') or ''}, {layout.get('action') or 'naturally facing the shared action'}"
        )
    return " ".join(part for part in (
        lead, MIMIC_MANGA_STYLE,
        f"A single natural {panel.get('shot', 'medium two-shot')} with exactly two adult recurring characters.",
        f"Setting: {panel.get('setting', '')}", f"Lighting: {panel.get('lighting', '')}",
        f"Shared action: {panel.get('moment', '')}",
        "Character placement: " + " | ".join(roles) + ".",
        "Both characters are interacting naturally in the same scene. No third person, no extra people, "
        "no duplicate character, no giant face, no collage, no text, no speech bubbles, no watermark.",
    ) if part).strip()


def build_composition_guide(panel: dict[str, Any], *, size: tuple[int, int] = (1216, 832)) -> bytes:
    """Make a disposable scene-layout guide: perspective cues plus unambiguous actor silhouettes.

    It is deliberately abstract—Krea receives it only as geometry, then renders the whole scene in
    one pass.  No generated character art is pasted into the final image.
    """
    from io import BytesIO
    from PIL import Image, ImageDraw

    width, height = size
    image = Image.new("RGB", size, "#d5c5b2")
    draw = ImageDraw.Draw(image)
    # No scenery: img2img should not mistake guide geometry for props or furniture.  Faint,
    # neutral pose skeletons carry only count, position, scale and inward-facing gesture.
    for index, layout in enumerate(panel.get("layout") or []):
        x = round(width * _SLOT_X.get(str(layout.get("slot")), .50))
        target_h = round(height * float(layout.get("scale", .70)))
        foot_y = round(height * .91)
        head_r = max(22, round(target_h * .105))
        body_top = foot_y - target_h + head_r * 2
        skeleton = "#a99b8d"
        shoulder_y = body_top + round(target_h * .12)
        hip_y = body_top + round(target_h * .55)
        draw.ellipse((x - head_r, body_top - head_r * 2, x + head_r, body_top), outline=skeleton, width=5)
        draw.line((x, body_top, x, hip_y), fill=skeleton, width=6)
        arm = round(target_h * .18)
        direction = 1 if _SLOT_X.get(str(layout.get("slot")), .5) < .5 else -1
        draw.line((x, shoulder_y, x + direction * arm, shoulder_y + round(target_h * .15)), fill=skeleton, width=6)
        draw.line((x, shoulder_y, x - direction * round(arm * .55), shoulder_y + round(target_h * .18)), fill=skeleton, width=6)
        draw.line((x, hip_y, x - round(target_h * .13), foot_y), fill=skeleton, width=6)
        draw.line((x, hip_y, x + round(target_h * .13), foot_y), fill=skeleton, width=6)
    output = BytesIO(); image.save(output, format="PNG"); return output.getvalue()


def grade_guided_panel(png: bytes) -> bytes:
    """Restore contrast lost to the deliberately low-contrast composition guide."""
    from io import BytesIO
    from PIL import Image, ImageEnhance, ImageOps

    image = Image.open(BytesIO(png)).convert("RGB")
    image = ImageOps.autocontrast(image, cutoff=.5)
    image = ImageEnhance.Contrast(image).enhance(1.22)
    image = ImageEnhance.Color(image).enhance(1.18)
    image = ImageEnhance.Sharpness(image).enhance(1.08)
    output = BytesIO(); image.save(output, format="PNG"); return output.getvalue()


def composite_panel(plate_png: bytes, cutouts: list[tuple[dict[str, Any], bytes]]) -> bytes:
    """Place actor cutouts at planned slots, sharing a baseline, and return a PNG composite."""
    from io import BytesIO
    from PIL import Image

    plate = Image.open(BytesIO(plate_png)).convert("RGBA")
    width, height = plate.size
    for layout, raw in sorted(cutouts, key=lambda item: _DEPTH_ORDER[item[0].get("depth", "middle")]):
        actor = Image.open(BytesIO(raw)).convert("RGBA")
        bbox = actor.getbbox()
        if not bbox:
            continue
        actor = actor.crop(bbox)
        target_height = max(1, min(height, round(height * float(layout.get("scale", .70)))))
        target_width = max(1, round(actor.width * target_height / actor.height))
        actor = actor.resize((target_width, target_height), Image.Resampling.LANCZOS)
        x = round(width * _SLOT_X.get(str(layout.get("slot")), .50) - target_width / 2)
        plate.alpha_composite(actor, (x, height - target_height))
    output = BytesIO()
    plate.convert("RGB").save(output, format="PNG")
    return output.getvalue()

"""Step 04 — Base prompt: author a character's appearance description → image prompt + features."""

from __future__ import annotations

from ._helpers import DEFAULT_SYSTEMS


def compose_base_prompt(provider, name: str, persona: str, appearance_notes: str = "",
                        role: str = "", systems: dict | None = None) -> dict:
    """Author the base-image appearance prompt for a character.

    Primary path: one natural-language prose pass (compose_nl_base) that derives structured
    fields AND authors personality-integrated prose in a single call.
    Fallback: atomic feature schema → deterministic prose assembly (_assemble_base_prompt).

    Returns {prompt, features, companions} or {error}.
    """
    from loom.server.services.prompts import (
        compose_nl_base, _assemble_base_prompt, FEATURES_SCHEMA,
    )
    if provider is None:
        return {"error": "no author model configured"}

    try:
        nl = (compose_nl_base(provider, name, persona, appearance_notes, role)
              or compose_nl_base(provider, name, persona, appearance_notes, role))
    except Exception:  # noqa: BLE001
        nl = None
    if nl:
        feats = {k: v for k, v in nl.items() if k != "prompt"}
        return {"prompt": nl["prompt"], "features": feats, "companions": []}

    # Fallback: atomic descriptors → deterministic prose assembly
    system = (systems or {}).get("base_image") or DEFAULT_SYSTEMS["base_image"]
    context = "\n\n".join(p for p in [
        f"NAME: {name}",
        f"PERSONA:\n{persona}" if persona else "",
        f"APPEARANCE NOTES: {appearance_notes}" if appearance_notes else "",
        f"ROLE: {role}" if role else "",
    ] if p)
    context = ("Fill the feature schema from this character's WRITTEN DESCRIPTION below "
               "(persona + appearance). Give `appearance` as a LIST of short, explicit, ATOMIC "
               "descriptors (one attribute each) — the system weaves each into the image prompt.\n\n"
               + context)

    def _gen_feats():
        return (provider.generate_text(system=system, prompt=context, emits=FEATURES_SCHEMA).data) or {}

    try:
        feats = _gen_feats() or _gen_feats()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"appearance generation failed: {exc}"}
    if not feats:
        return {"error": "model returned no structured features "
                         "(author model may not support structured output)"}
    return {"prompt": _assemble_base_prompt(feats), "features": feats, "companions": []}

"""One-time, idempotent migration: split bundled character cards into a clean
Character (who they are) + a default Scenario (the situation they're in).

Imported SillyTavern cards are "book experiences": they weld a persona to a
situation (scenario text, opening line, alternate greetings, world lorebook).
`to_character` preserved every original component verbatim under `fields`, so we
can losslessly re-derive a scenario-free persona and lift the situational parts
into a Scenario.

Per character file (guarded by a `_migrated_split` marker the loader ignores):
  • write configs/scenarios/<key>.yaml  (setting + openings + lorebook + cast)
  • rewrite the character: system = persona WITHOUT the scenario; drop greeting
  • back up the original once to <key>.yaml.bak
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _clean(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _persona_without_scenario(data: dict) -> str:
    """Re-derive the persona from the card's preserved components, OMITTING the
    scenario. Falls back to the existing `system` for hand-authored characters
    that have no component fields (so nothing is clobbered)."""
    fields = data.get("fields") or {}
    sp = _clean(fields.get("system_prompt"))
    desc = _clean(fields.get("description"))
    pers = _clean(fields.get("personality"))
    if not any((sp, desc, pers, _clean(fields.get("scenario")))):
        return data.get("system", "") or ""  # hand-authored; already scenario-free
    parts: list[str] = []
    if sp:
        parts.append(sp)
    if desc:
        parts.append(desc)
    if pers:
        parts.append(f"Personality: {pers}")
    return "\n\n".join(parts).strip()


def split_cards_to_scenarios(root: str | Path) -> list[str]:
    """Split every not-yet-migrated character into character + scenario. Returns
    the list of character keys migrated this call (empty when there's nothing to
    do — safe to run on every startup)."""
    root = Path(root)
    char_dir = root / "configs" / "characters"
    scen_dir = root / "configs" / "scenarios"
    if not char_dir.is_dir():
        return []
    scen_dir.mkdir(parents=True, exist_ok=True)

    migrated: list[str] = []
    for path in sorted(char_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — skip unreadable files, don't crash startup
            continue
        if not isinstance(data, dict) or data.get("_migrated_split"):
            continue

        stem = path.stem
        fields = data.get("fields") or {}
        name = data.get("name") or stem

        greeting = _clean(data.get("greeting"))
        alts = [g for g in (fields.get("alternate_greetings") or []) if _clean(g)]
        openings = [g for g in ([greeting] + alts) if g]
        lorebook = fields.get("character_book")
        scenario = {
            "name": name,  # the default scenario is named after the character
            "setting": _clean(fields.get("scenario")),
            "openings": openings,
            "lorebook": lorebook if isinstance(lorebook, dict) else {},
            "cast": [{"character": stem, "primary": True}],
            "fields": {k: fields[k] for k in
                       ("creator", "creator_notes", "tags", "character_version", "spec", "spec_version")
                       if fields.get(k)},
        }

        scen_path = scen_dir / f"{stem}.yaml"
        if not scen_path.exists():
            scen_path.write_text(yaml.safe_dump(scenario, allow_unicode=True, sort_keys=False),
                                 encoding="utf-8")

        # Back up the original card once, then clean + mark it.
        bak = path.with_suffix(".yaml.bak")
        if not bak.exists():
            bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

        data["system"] = _persona_without_scenario(data)
        data.pop("greeting", None)
        data["_migrated_split"] = True  # loader ignores unknown keys; this just guards re-runs
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        migrated.append(stem)

    return migrated

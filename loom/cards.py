"""SillyTavern / Character Card import.

Parses V1/V2/V3 character cards from either raw JSON or a PNG with the card
embedded in a text chunk (keyword ``chara`` — base64 JSON; or ``ccv3`` for V3),
and normalizes them to Loom's Character shape (name / system / greeting /
fields). This is the SillyTavern import format, dependency-free (PNG chunks are
parsed by hand so we don't pull in Pillow).
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from typing import Any

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png_text_chunks(data: bytes) -> dict[str, str]:
    """Return {keyword: text} for the tEXt / zTXt chunks of a PNG."""
    if not data.startswith(_PNG_SIG):
        return {}
    out: dict[str, str] = {}
    pos, n = len(_PNG_SIG), len(data)
    while pos + 8 <= n:
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length  # 4 length + 4 type + data + 4 CRC
        if ctype == b"tEXt":
            kw, _, val = body.partition(b"\x00")
            out[kw.decode("latin-1")] = val.decode("latin-1")
        elif ctype == b"zTXt":
            kw, _, rest = body.partition(b"\x00")
            if rest:  # rest = 1 compression-method byte + zlib data
                try:
                    out[kw.decode("latin-1")] = zlib.decompress(rest[1:]).decode("utf-8", "replace")
                except zlib.error:
                    pass
        elif ctype == b"IEND":
            break
    return out


def extract_card_json(data: bytes) -> dict[str, Any]:
    """Pull the raw character-card dict out of PNG or JSON bytes."""
    stripped = data.lstrip()
    if stripped[:1] in (b"{", b"["):
        return json.loads(stripped.decode("utf-8"))
    chunks = _png_text_chunks(data)
    for key in ("ccv3", "chara"):  # prefer the richer V3 chunk when present
        raw = chunks.get(key)
        if raw:
            return json.loads(base64.b64decode(raw).decode("utf-8"))
    raise ValueError("no character data found (need a JSON card or a PNG with an embedded `chara` chunk)")


def _clean(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _empty(v: Any) -> bool:
    # Prune only genuinely empty values — keep 0 / False, which are real data.
    return v is None or v == "" or v == [] or v == {}


def to_character(card: dict[str, Any]) -> dict[str, Any]:
    """Normalize a V1/V2/V3 card to a Loom Character dict, losslessly.

    V2/V3 nest everything under ``data``; V1 cards are flat. For the chat engine
    we derive two convenience values — ``name`` (promoted to the top level) and
    ``system`` (description / personality / scenario / system_prompt synthesized
    into one prompt). Everything else from the card is preserved **verbatim**
    under ``fields``: the raw components, the lorebook (``character_book``), V3
    ``assets`` and extras, ``extensions``, and any unknown future keys. Only
    empty values are pruned, so nothing meaningful is dropped on import.
    """
    data = card["data"] if isinstance(card.get("data"), dict) else card
    name = _clean(data.get("name")) or "Imported character"

    description = _clean(data.get("description"))
    personality = _clean(data.get("personality"))
    scenario = _clean(data.get("scenario"))
    system_prompt = _clean(data.get("system_prompt"))

    parts: list[str] = []
    if system_prompt:
        parts.append(system_prompt)
    if description:
        parts.append(description)
    if personality:
        parts.append(f"Personality: {personality}")
    if scenario:
        parts.append(f"Scenario: {scenario}")
    system = "\n\n".join(parts).strip()

    # Lossless capture: every card field except `name` (promoted above).
    fields: dict[str, Any] = {k: v for k, v in data.items() if k != "name" and not _empty(v)}
    # Record the source spec so the original format is recoverable.
    for meta in ("spec", "spec_version"):
        if not _empty(card.get(meta)):
            fields.setdefault(meta, card[meta])

    # Mark provenance: an imported card is a DIFFERENT kind of thing from a character we
    # author in the pipeline — it's external reference material (only these seed a premise).
    fields["imported"] = True

    out: dict[str, Any] = {"name": name, "system": system}
    greeting = _clean(data.get("first_mes"))
    if greeting:
        out["greeting"] = greeting
    if fields:
        out["fields"] = fields
    return out

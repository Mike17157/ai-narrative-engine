"""Automatic output naming for rendered images.

Turns the generation context (family + role + character) into a ComfyUI `filename_prefix`, so renders
land in organized, predictable subfolders — `loom/<family>/<role>/<character|misc>/…` — instead of the
old hardcoded per-workflow prefixes (`scene`, `anima`). ComfyUI treats forward slashes in a
filename_prefix as nested output subfolders.
"""

from __future__ import annotations

import re


def _slug(s: str | None, default: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", (s or "").strip().lower()).strip("-")
    return s or default


def output_prefix(family: str | None, role: str | None, character: str | None = None) -> str:
    """`loom/<family>/<role>/<character or 'misc'>` — the SaveImage filename_prefix for a render."""
    return "/".join(["loom", _slug(family, "misc"), _slug(role, "image"), _slug(character, "misc")])

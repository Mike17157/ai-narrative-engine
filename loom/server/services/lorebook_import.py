"""Import SillyTavern world-info / lorebook JSON into the libSQL lorebook store.

SillyTavern entries map to our LoreEntry: `key[]` → keywords, `content` → content,
the first line of content (or `comment`) → title. Two safety filters run on every
import, because these exports sometimes smuggle prompt-injection payloads in among
the real entries:

  • SKIP any entry whose content is an instruction-override / jailbreak
    ("override sequence", "ignore all previous instructions", "stop all roleplay", …).
  • STRIP individual lines that order the model to disregard its constraints
    ("disregard … ethical/constraints/instructions", "override all …").

Returns a summary so the caller can report exactly what was added / skipped / cleaned.
"""
from __future__ import annotations

import re

from ...config.schema import LoreEntry
from . import lorebook_store as LS

# Whole-entry rejects (prompt injection / jailbreak, not lore).
_INJECTION_RE = re.compile(
    r"override\s+sequence|override\s+all\s+(previous|subsequent)|"
    r"ignore\s+(all\s+)?(previous|prior)\s+instructions|disregard\s+any\s+other\s+instructions|"
    r"stop\s+all\s+(current\s+)?(processes|roleplay)|begin\s+override|new\s+instructions:\s*you\s+are",
    re.I,
)

# Per-line strips (embedded "ignore your rules" directives inside otherwise-fine entries).
_OVERRIDE_LINE_RE = re.compile(
    r"disregard\s+all.*\b(ethical|constraint|comfort|instruction)|"
    r"override\s+all|ignore\s+(all\s+)?(previous|safety|ethical)",
    re.I,
)


def _title(content: str, comment: str) -> str:
    first = (content or "").splitlines()[0] if content else ""
    t = first.rstrip(":").strip()
    t = re.sub(r"\s*(Performance Guide|Instructions for giving a|Guide)\s*", " ", t, flags=re.I).strip()
    return (t or comment or "Lore")[:70]


def import_sillytavern(root, scope: str, entries: list[dict], default_facet: str = "") -> dict:
    added: list[str] = []
    skipped: list[dict] = []
    cleaned: list[str] = []

    for i, e in enumerate(entries or []):
        if not isinstance(e, dict):
            continue
        content = str(e.get("content") or "")
        title = _title(content, str(e.get("comment") or ""))

        if _INJECTION_RE.search(content):
            skipped.append({"title": title, "reason": "prompt-injection / instruction-override"})
            continue

        kept_lines, stripped_any = [], False
        for ln in content.splitlines():
            if _OVERRIDE_LINE_RE.search(ln):
                stripped_any = True
                continue
            kept_lines.append(ln)
        content = "\n".join(kept_lines).strip()
        if stripped_any:
            cleaned.append(title)

        if not content:
            skipped.append({"title": title, "reason": "empty after cleaning"})
            continue

        keys = e.get("key") or e.get("keys") or []
        keywords = [str(k).strip() for k in keys if str(k).strip()]
        if not keywords:
            skipped.append({"title": title, "reason": "no trigger keywords"})
            continue

        eid = str(e.get("id") if e.get("id") not in (None, "") else i)
        eid = "st-" + re.sub(r"[^\w\-]+", "-", eid).strip("-")
        LS.upsert_entry(root, scope, LoreEntry(
            id=eid, title=title, keywords=keywords, content=content,
            enabled=bool(e.get("enabled", True)), priority=1, facet=default_facet))
        added.append(title)

    return {"scope": scope, "added": added, "skipped": skipped, "cleaned": cleaned,
            "count_added": len(added), "count_skipped": len(skipped)}

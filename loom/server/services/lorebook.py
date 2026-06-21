"""Lorebook formatting helpers.

ALL lorebook STORAGE + retrieval now lives in the unified libSQL/Turso store
(``lorebook_store.py``) — FTS5/BM25 ranked, dynamic, one database for every book. The
legacy JSON-file storage and Python-side BM25 that used to live here are gone; only the
two pure formatters that the rest of the app still imports remain.
"""
from __future__ import annotations

from ...config.schema import LoreEntry


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for context-budget dials."""
    return (len(text or "") + 3) // 4


def format_lore_block(entries: list[LoreEntry], header: str | None = None) -> str:
    """Format retrieved entries as an injectable block.

    *header* overrides the default WORLD INFO heading (used for craft notes).
    """
    if not entries:
        return ""
    parts = [header or "WORLD INFO — context retrieved from the lorebook for this conversation:"]
    for e in entries:
        label = e.title or "Lore"
        parts.append(f"\n[{label}]\n{e.content.strip()}")
    return "\n".join(parts)

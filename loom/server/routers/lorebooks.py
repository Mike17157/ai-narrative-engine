"""Lorebook manager — first-class books (named, rated sfw/nsfw, categorized) over the
libSQL/Turso lore store. This is the contained module the manager UI drives and the one
a chat thread attaches books from.

A *book* is a scope with metadata; its *entries* are keyword-triggered lore fragments
retrieved (FTS5 bm25) and injected into the prompt at runtime. Endpoints:

  GET    /api/lorebooks                         → all books (+ entry counts)
  POST   /api/lorebooks                         → create a book
  GET    /api/lorebooks/{id}                    → one book's metadata + entries
  PATCH  /api/lorebooks/{id}                    → update metadata (name/rating/…/enabled)
  DELETE /api/lorebooks/{id}                    → delete the book and its entries
  PUT    /api/lorebooks/{id}/entries           → upsert one entry
  DELETE /api/lorebooks/{id}/entries/{eid}     → delete one entry
  POST   /api/lorebooks/{id}/import            → import a SillyTavern world-info export
  POST   /api/lorebooks/retrieve               → preview what a query would retrieve

The legacy `/api/lorebook/{scope}` CRUD (in stories/router.py) still works for callers
that speak in raw scopes; this router is the managed, metadata-aware surface.
"""
from __future__ import annotations

import re
import uuid

from fastapi.responses import JSONResponse

from ...config.schema import LoreEntry
from ..services import lorebook_store as LS
from ..services import lorebook_import as LI

_RATINGS = {"sfw", "nsfw"}
_CATEGORIES = {"world", "story", "rpg", "character", "craft", "intimacy", "guard", "function"}


def _slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "-", (name or "").lower()).strip("-_") or "book"


def _clean_meta(body: dict) -> dict:
    """Validate the editable book-metadata fields out of a request body."""
    out: dict = {}
    if body.get("name") is not None:
        out["name"] = str(body["name"]).strip()[:80]
    if body.get("description") is not None:
        out["description"] = str(body["description"]).strip()[:600]
    if body.get("rating") is not None:
        out["rating"] = body["rating"] if body["rating"] in _RATINGS else "sfw"
    if body.get("category") is not None:
        out["category"] = body["category"] if body["category"] in _CATEGORIES else "world"
    if body.get("enabled") is not None:
        out["enabled"] = bool(body["enabled"])
    if body.get("preset") is not None:
        out["preset"] = str(body["preset"]).strip()   # bound model preset id ('' = none)
    return out


def register(app, ctx):
    @app.get("/api/lorebooks")
    def list_lorebooks():
        return {"books": LS.list_books(ctx.root)}

    @app.post("/api/lorebooks")
    def create_lorebook(body: dict):
        """Create a book. Id derives from the name (unique-ified) unless one is given."""
        body = body or {}
        meta = _clean_meta(body)
        name = meta.get("name") or "New lorebook"
        meta["name"] = name
        existing = {b["id"] for b in LS.list_books(ctx.root)}
        bid = _slug(body.get("id") or name)
        base, n = bid, 2
        while bid in existing:
            bid, n = f"{base}-{n}", n + 1
        book = LS.upsert_book(ctx.root, bid, **meta)
        return {"ok": True, "book": book}

    @app.post("/api/lorebooks/reindex")
    def reindex_embeddings(body: dict | None = None):
        """Embed any entries missing a semantic vector. Returns how many were embedded
        and whether the local embedder is available."""
        from ..services import embeddings as _emb
        n = LS.backfill_embeddings(ctx.root, limit=int((body or {}).get("limit") or 5000))
        return {"ok": True, "embedded": n, "embedder": _emb.available(), "model": _emb.MODEL}

    @app.post("/api/lorebooks/retrieve")
    def preview_retrieve(body: dict):
        """What would this query pull from these books? Powers the manager's test box."""
        body = body or {}
        query = str(body.get("query") or "")
        books = [str(b) for b in (body.get("books") or []) if b]
        top_k = int(body.get("top_k") or 6)
        hits = LS.retrieve(ctx.root, query, books, top_k=top_k)
        return {"entries": [e.model_dump() for e in hits]}

    @app.get("/api/lorebooks/{book_id}")
    def get_lorebook(book_id: str):
        book = LS.get_book(ctx.root, book_id)
        if book is None:
            return JSONResponse({"error": "no such lorebook"}, status_code=404)
        entries = [e.model_dump() for e in LS.load_lorebook(ctx.root, book_id)]
        return {"book": book, "entries": entries}

    @app.patch("/api/lorebooks/{book_id}")
    def update_lorebook(book_id: str, body: dict):
        meta = _clean_meta(body or {})
        if not meta:
            return JSONResponse({"error": "nothing to update"}, status_code=400)
        return {"ok": True, "book": LS.upsert_book(ctx.root, book_id, **meta)}

    @app.delete("/api/lorebooks/{book_id}")
    def delete_lorebook(book_id: str):
        book = LS.get_book(ctx.root, book_id)
        if book and book.get("builtin"):
            # Builtin/reserved books keep their metadata; only clear their entries.
            LS.delete_book(ctx.root, book_id)
            return {"ok": True, "kept_metadata": True}
        LS.delete_book(ctx.root, book_id)
        return {"ok": True}

    @app.post("/api/lorebooks/{book_id}/augment")
    def augment_lorebook(book_id: str, body: dict):
        """AI-assisted authoring: given a short instruction, propose structured lorebook
        entries (title + keywords + content) the user can review and add. Nothing is
        written — the frontend confirms, then upserts via PUT …/entries.

        Body: { instruction, model?, count? }. Defaults to deepseek (the configured chat
        model when `model` is blank). Existing titles are passed as context so it doesn't
        duplicate what's already there."""
        body = body or {}
        instruction = str(body.get("instruction") or "").strip()
        if not instruction:
            return JSONResponse({"error": "instruction required"}, status_code=400)
        book = LS.get_book(ctx.root, book_id)
        if book is None:
            return JSONResponse({"error": "no such lorebook"}, status_code=404)

        provider = ctx.author_provider((body.get("model") or "").strip() or None)
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no chat connection — connect a chat model first"}, status_code=400)

        existing = [e.title for e in LS.load_lorebook(ctx.root, book_id) if e.title][:40]
        count = max(1, min(8, int(body.get("count") or 4)))
        schema = {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "keywords": {"type": "array", "items": {"type": "string"}},
                            "content": {"type": "string"},
                        },
                        "required": ["title", "keywords", "content"],
                    },
                }
            },
            "required": ["entries"],
        }
        system = (
            "You are a worldbuilding assistant that authors lorebook entries. Each entry is a "
            "self-contained fact (a place, person, faction, item, rule, or event) with: a short "
            "title; a few lowercase trigger keywords that should pull it into context when "
            "mentioned; and a concise, concrete content paragraph. Write in the established tone. "
            "Do NOT duplicate existing entries; propose genuinely new, complementary ones."
        )
        prompt = (
            f"Lorebook: “{book.get('name') or book_id}”"
            + (f" — {book['description']}" if book.get("description") else "")
            + (f"\nExisting entries (do not repeat): {', '.join(existing)}" if existing else "")
            + f"\n\nRequest: {instruction}\n\nPropose up to {count} new entries."
        )
        try:
            data = provider.generate_text(system=system, prompt=prompt, emits=schema).data or {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"augment failed: {exc}"}, status_code=500)
        entries = [e for e in (data.get("entries") or []) if isinstance(e, dict) and e.get("content")]
        if not entries:
            return JSONResponse({"error": "the model returned no usable entries (it may not "
                                          "support structured output)"}, status_code=502)
        return {"ok": True, "suggestions": entries[:count]}

    @app.put("/api/lorebooks/{book_id}/entries")
    def upsert_lore_entry(book_id: str, body: dict):
        data = dict(body or {})
        if not data.get("id"):
            data["id"] = str(uuid.uuid4())[:8]
        # Only known LoreEntry fields, so stray UI keys don't blow up the model.
        allowed = {"id", "title", "keywords", "content", "enabled", "priority", "facet",
                   "trigger", "script"}
        entry = LoreEntry(**{k: v for k, v in data.items() if k in allowed})
        LS.upsert_entry(ctx.root, book_id, entry)
        return {"ok": True, "entry": entry.model_dump()}

    @app.delete("/api/lorebooks/{book_id}/entries/{entry_id}")
    def delete_lore_entry(book_id: str, entry_id: str):
        LS.delete_entry(ctx.root, book_id, entry_id)
        return {"ok": True}

    @app.post("/api/lorebooks/{book_id}/import")
    def import_lorebook(book_id: str, body: dict):
        """Import a SillyTavern world-info / lorebook export into this book.

        Accepts the raw ST JSON in several shapes: a top-level ``entries`` object/array,
        an ``{ "entries": {...} }`` wrapper, or a bare list. Prompt-injection payloads are
        filtered (see lorebook_import). Returns the added/skipped/cleaned summary."""
        body = body or {}
        raw = body.get("entries", body)
        if isinstance(raw, dict) and "entries" in raw:
            raw = raw["entries"]
        if isinstance(raw, dict):           # ST stores entries as an id→entry object
            raw = list(raw.values())
        if not isinstance(raw, list):
            return JSONResponse({"error": "no importable entries found"}, status_code=400)
        # Ensure the book exists so it shows up even if every entry is filtered out.
        LS.upsert_book(ctx.root, book_id, **_clean_meta(body))
        summary = LI.import_sillytavern(ctx.root, book_id, raw,
                                        default_facet=str(body.get("facet") or ""))
        return {"ok": True, **summary, "book": LS.get_book(ctx.root, book_id)}

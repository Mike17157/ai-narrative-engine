from __future__ import annotations

from fastapi.responses import HTMLResponse, JSONResponse

from ._graph_view import GRAPH_VIEW_HTML


def register(app, ctx):
    # Danbooru category names accepted by the `cat` filter (special-category search).
    _CAT_NAMES = {"general": 0, "artist": 1, "copyright": 3, "character": 4, "meta": 5}

    @app.get("/api/tags/search")
    async def tags_search(q: str = "", limit: int = 20, noisy: bool = False, cat: str = ""):
        """Autocomplete against the real Danbooru vocabulary — prefix-first, ranked by post count.
        `cat` (e.g. 'character') restricts to one category. Returns {tags:[{tag,name,count,category}]}."""
        from fastapi.concurrency import run_in_threadpool

        from ...tags import get_index
        ix = await run_in_threadpool(get_index)          # first call parses the CSV (~0.4s)
        n = min(max(int(limit or 20), 1), 50)
        only = _CAT_NAMES.get((cat or "").strip().lower())
        return {"tags": ix.search(q or "", limit=n, include_noisy=bool(noisy or only is not None), only_cat=only)}

    @app.get("/api/tags/related")
    async def tags_related(tags: str = "", kind: str = "clothing", per: int = 24, sim: int = 60):
        """Navigate the tag similarity graph from comma-separated `tags` and return a faceted palette
        of correlated/compatible tags (kind='appearance'|'clothing'); `per` = max tags per facet,
        `sim` (0-100) trades breadth (low) for tight similarity (high). Powers the prompt editor."""
        from fastapi.concurrency import run_in_threadpool

        from ...tags import get_graph
        seeds = [t.strip() for t in (tags or "").split(",") if t.strip()]
        if not seeds:
            return {"palette": {}}
        g = await run_in_threadpool(get_graph)
        if not g.ready:
            return {"palette": {}, "note": "graph not available"}
        kind = kind if kind in ("appearance", "clothing", "all") else "clothing"
        try:
            per_i = int(per)
        except (TypeError, ValueError):
            per_i = 24
        per_v = None if per_i <= 0 else min(per_i, 200)   # 0/negative → uncapped (full list)
        sim = max(0, min(int(sim), 100))
        from ...tags.facets import FACET_DESC
        pal = await run_in_threadpool(g.palette, seeds, kind, per_v, sim)
        return {"palette": pal, "facets": {f: FACET_DESC.get(f, "") for f in pal}}

    @app.get("/api/tags/graph")
    async def tags_graph(tags: str = "", kind: str = "clothing", sim: int = 60):
        """Neighbourhood around `tags` (comma-separated) from the similarity graph, as
        {nodes, edges, seeds}. `sim` (0-100): breadth↔tight. Backs /tags/graph/view + the modal."""
        from fastapi.concurrency import run_in_threadpool

        from ...tags import get_graph
        seeds = [t.strip() for t in (tags or "").split(",") if t.strip()]
        if not seeds:
            return {"nodes": [], "edges": [], "seeds": []}
        g = await run_in_threadpool(get_graph)
        if not g.ready:
            return {"nodes": [], "edges": [], "seeds": [], "note": "graph not available"}
        kind = "appearance" if kind == "appearance" else "clothing"
        sim = max(0, min(int(sim), 100))
        return await run_in_threadpool(g.subgraph, seeds, kind, 70, 280, sim)

    @app.get("/api/tags/graph/view", response_class=HTMLResponse)
    def tags_graph_view() -> str:
        """A self-contained interactive force-directed view of the tag similarity graph.
        Type seed tags; click a node to re-center. Data from GET /api/tags/graph."""
        return GRAPH_VIEW_HTML

    @app.post("/api/tags/recompose")
    async def tags_recompose(body: dict):
        """AI-refine a set of image-prompt tags: feed the current tags + a graph palette of
        compatible tags to the author model and ask for a coherent set of ~`length` booru tags.
        body: {tags:[...], kind:'clothing'|'appearance', length:int}. Returns {tags:[...]}."""
        from fastapi.concurrency import run_in_threadpool

        from ...tags import get_cooccur, get_graph
        from ..services.prompts import _dedupe_outfit_tags, _safe_image_tags, _snap_prompt
        body = body or {}
        tags = [str(t).strip() for t in (body.get("tags") or []) if str(t).strip()]
        if not tags:
            return JSONResponse({"error": "no tags"}, status_code=400)
        kind = "appearance" if body.get("kind") == "appearance" else "clothing"
        try:
            length = int(body.get("length") or 0)
        except (TypeError, ValueError):
            length = 0
        length = max(6, min(length or 24, 50))
        provider = ctx.author_provider(body.get("model"))
        if provider is None:
            return JSONResponse({"error": "no author model configured"}, status_code=400)

        def _palette():
            try:
                g = get_graph()
                if g.ready:
                    p = g.palette(tags, kind, per_facet=24)
                    if p:
                        return p
            except Exception:  # noqa: BLE001
                pass
            try:
                ix = get_cooccur()
                if ix.ready:
                    return ix.faceted_palette(tags, kind, per_facet=24)
            except Exception:  # noqa: BLE001
                pass
            return {}

        palette = await run_in_threadpool(_palette)
        facet_lines = "\n".join(f"  {f.upper()}: {', '.join(ts)}" for f, ts in palette.items() if ts)
        if kind == "appearance":
            rules = ("Keep the SEX count tag (1girl/1boy) and persistent PHYSICAL identity (hair, "
                     "eyes, skin, body, face/marks). NO clothing, expression, pose or background.")
        else:
            rules = ("Keep the SEX count tag (1girl/1boy). Build a COHERENT outfit — every garment "
                     "with a COLOUR, plus legwear/footwear/accessories/makeup that fit; ONE palette. "
                     "NO body/hair/eye/skin tags, NO expression, pose or background.")
        system = ("You refine a Danbooru-tag image prompt for an Illustrious/SDXL model. Given the "
                  "CURRENT tags and a PALETTE of compatible real tags, output a single coherent set "
                  "of lowercase tags. Draw from the palette where it improves the look; keep the "
                  "subject's identity. PREFER real booru tags, but a short natural descriptor is fine "
                  "when no exact tag exists — never cram several attributes into one invented 'tag'; "
                  "split modifiers into their own tag (write 'rainbow bikini, crochet', not 'crochet "
                  "rainbow bikini'). " + rules + " Output ONLY tags (no prose).")
        prompt = (f"CURRENT TAGS:\n{', '.join(tags)}\n\n"
                  + (f"PALETTE — real compatible tags by facet:\n{facet_lines}\n\n" if facet_lines else "")
                  + f"Produce approximately {length} tags (aim for {max(6, length - 3)}–{length + 3}). "
                  + ("Make it richer/more detailed." if length > len(tags) else
                     "Tighten it to the essentials." if length < len(tags) else "Refine it."))
        schema = {"type": "object", "additionalProperties": False, "required": ["tags"],
                  "properties": {"tags": {"type": "array", "items": {"type": "string"}}}}

        def _gen():
            try:
                data = provider.generate_text(system=system, prompt=prompt, emits=schema).data or {}
            except Exception:  # noqa: BLE001
                data = {}
            out = [str(t) for t in (data.get("tags") or [])]
            if not out:
                return []
            snapped = _snap_prompt(_safe_image_tags(", ".join(out)))
            return _dedupe_outfit_tags([t.strip() for t in snapped.split(",") if t.strip()])

        final = await run_in_threadpool(_gen)
        if not final:
            return JSONResponse({"error": "the model returned no tags"}, status_code=500)
        return {"tags": final}

    @app.post("/api/tags/extract")
    async def tags_extract(body: dict):
        """Ground free natural-language text into real booru tags (greedy longest-match against the
        vocabulary, lemma + stopword aware), keeping leftover content words as free-text. Returns
        {tags:[real…], free:[words…], coverage}. Lets the editor / model write naturally."""
        from fastapi.concurrency import run_in_threadpool

        from ...tags import get_index
        text = (body or {}).get("text", "")
        if not text.strip():
            return {"tags": [], "free": [], "coverage": 0}
        ix = await run_in_threadpool(get_index)
        if not ix.ready:
            return {"tags": [t.strip() for t in text.split(",") if t.strip()], "free": [], "coverage": 0}
        return await run_in_threadpool(ix.extract, text)

    @app.post("/api/tags/regionize")
    async def tags_regionize(body: dict):
        """Order a flat tag list by category and group it into BREAK regions. `mode`:
        'off' (one comma prompt, no BREAK), 'coarse' (a few macro-regions: subject · appearance ·
        outfit · details — default, SDXL-friendly), 'fine' (a BREAK per category). Subject/framing
        tags (no facet) lead. Returns {tags:[...with 'BREAK' separators...], prompt: comma-joined}."""
        from ...tags.facets import regionize
        body = body or {}
        arr = regionize(body.get("tags", []), body.get("mode") or "coarse")
        return {"tags": arr, "prompt": ", ".join(arr)}

    @app.post("/api/tags/snap")
    async def tags_snap(body: dict):
        """Snap a free-text prompt onto real booru tags. Returns the full snap report
        {prompt, tags, items, changed, unknown} so the editor can colour each tag and show
        what changed. Unknown tags are KEPT (never silently dropped) and carry suggestions."""
        from fastapi.concurrency import run_in_threadpool

        from ...tags import get_index
        body = body or {}
        text = body.get("prompt") or body.get("text") or ""
        ix = await run_in_threadpool(get_index)
        if not ix.ready:                                  # no vocabulary available — echo input
            tags = [t.strip() for t in text.split(",") if t.strip()]
            return {"prompt": ", ".join(tags), "tags": tags, "items": [],
                    "changed": [], "unknown": []}
        return ix.snap(text)

    @app.post("/api/tagify")
    async def tagify(body: dict):
        """Convert an existing PROSE image prompt into Danbooru tags in place (for
        stories built before the tag rule). Stateless: takes {text, kind} and returns
        {tags}. kind='scene' formats as a no-humans scenery plate; anything else as a
        character/subject tag list. Uses the active author model."""
        from ...scenario.builder import _APPEARANCE_RULE, _TAG_RULE

        body = body or {}
        text = (body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "no text"}, status_code=400)
        kind = (body.get("kind") or "character").lower()
        provider = ctx.author_provider(body.get("model"))
        if provider is None:
            return JSONResponse({"error": "no author model configured"}, status_code=400)
        if kind == "scene":
            fmt = ("Format: a flat comma-separated list of short lowercase booru tags for an "
                   "Illustrious/SDXL anime model — NO sentences, NO articles, NO connecting words. "
                   "Start with `no humans, scenery`, then the place, then mood/light/atmosphere tags.")
            system = ("You convert an image-generation prompt from prose into Danbooru tags. Preserve "
                      "EVERY concrete detail — translate, never invent or drop. Output ONLY the tag "
                      "list, nothing else (no preamble, no quotes, no explanation).\n\n" + fmt)
        elif kind == "base":
            # Clean a character's base-image prompt: keep only the persistent physical
            # identity, DROP clothing/pose/expression/scene, and append the full-body
            # swimwear template so outfits layer on a clean capture.
            system = ("You rewrite a character image prompt into a CLEAN BASE-IMAGE prompt. From the "
                      "input, KEEP ONLY the persistent physical identity and DISCARD all clothing, "
                      "accessories, pose, gesture, facial expression, action, background and scene "
                      "(those are added later). " + _APPEARANCE_RULE + "\n\n"
                      "After the identity tags, append EXACTLY this framing: `solo, full body, "
                      "standing, facing viewer, <SWIM>, plain simple background, full body shot, head "
                      "to toe, feet visible` — where <SWIM> is `swim trunks, bare chest` if the subject "
                      "is male else `bikini`. Output ONLY the final tag list, nothing else.")
        else:
            system = ("You convert an image-generation prompt from prose into Danbooru tags. Preserve "
                      "EVERY concrete detail (subject, count, hair, eyes, clothing, pose, setting, mood, "
                      "lighting) — translate, never invent or drop. Output ONLY the tag list, nothing "
                      "else (no preamble, no quotes, no explanation).\n\n" + _TAG_RULE)
        from fastapi.concurrency import run_in_threadpool
        try:
            res = await run_in_threadpool(
                lambda: provider.generate_text(system=system, prompt=f"Convert this:\n{text}"))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"tagify failed: {exc}"}, status_code=500)
        tags = (res.text or "").strip().strip('"').strip()
        return {"tags": tags}

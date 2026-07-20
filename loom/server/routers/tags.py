"""Prompt → Danbooru tag conversion (`/api/tagify`).

Used by the story surfaces (scene/location prompts, character base prompts) to
rewrite a prose image prompt as a flat booru tag list with the active author
model. The old tag-vocabulary browser endpoints (search / related / graph /
snap — the deleted Images section's tag picker) are gone, along with the
`loom.tags` package that backed them; this is the only tag endpoint left.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


def register(app, ctx):
    @app.post("/api/tagify")
    async def tagify(body: dict):
        """Convert an existing PROSE image prompt into Danbooru tags in place (for
        stories built before the tag rule). Stateless: takes {text, kind} and returns
        {tags}. kind='scene' formats as a no-humans scenery plate; anything else as a
        character/subject tag list. Uses the active author model."""
        from ...stories.pipeline._helpers import _APPEARANCE_RULE, _TAG_RULE

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

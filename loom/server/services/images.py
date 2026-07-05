"""Pure image-pipeline helpers — free functions with no app state.

Bodies copied verbatim from app.py. Project imports (comfy server, PIL, numpy) are
done INSIDE the functions, exactly as in the original, to avoid import cycles and
hard dependencies.
"""

from __future__ import annotations


async def _render(provider, prompt: str, init_image: bytes | None = None,
                  out_prefix: str | None = None, latent: tuple[int, int] | None = None) -> bytes | None:
    from fastapi.concurrency import run_in_threadpool

    from ...comfy.server import get_server
    await run_in_threadpool(get_server(provider.base_url).ensure_up)
    result = await run_in_threadpool(
        lambda: provider.generate_image(prompt=prompt, init_image=init_image,
                                        out_prefix=out_prefix, latent=latent))
    return result.images[0] if result.images else None


# THE fixed character-sprite seed. A constant seed across a character's whole emotion set (with
# identity + outfit + style held constant in every prompt) keeps composition/palette/framing in
# the same latent region — the standard sprite-sheet consistency trick. Variety between emotions
# comes from the PROMPT (per-emotion pose + expression), not the seed.
SPRITE_SEED = 44


def _set_seeds(graph: dict, seed: int) -> None:
    """Force every sampler seed to `seed`. Mutates in place."""
    for node in graph.values():
        ins = node.get("inputs") if isinstance(node, dict) else None
        if isinstance(ins, dict):
            for k in ("seed", "noise_seed"):
                if isinstance(ins.get(k), (int, float)):
                    ins[k] = seed


def _randomize_seeds(graph: dict) -> None:
    """Give every sampler a fresh seed so repeated renders of one prompt vary
    (workflows ship with a fixed seed). Mutates in place."""
    import random
    _set_seeds(graph, random.randint(0, 2_147_483_646))


def _clean_reference_png(raw: bytes) -> bytes:
    """STORE a reference: strip embedded metadata (the ComfyUI prompt chunk) but PRESERVE
    transparency, so a background-removed candidate stays transparent. We NEVER composite the
    cutout back onto a white (or any) background — not on save, and not when feeding it to a
    model — so the removed background is never re-introduced. Re-filling it would defeat the
    removal and break compositing the character onto scenes."""
    from io import BytesIO

    from PIL import Image
    im = Image.open(BytesIO(raw))
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")          # keep alpha
    else:
        im = im.convert("RGB")
    out = BytesIO(); im.save(out, format="PNG"); return out.getvalue()

"""Pure image-pipeline helpers — free functions with no app state.

Bodies copied verbatim from app.py. Project imports (comfy server, PIL, numpy) are
done INSIDE the functions, exactly as in the original, to avoid import cycles and
hard dependencies.
"""

from __future__ import annotations


async def _render(provider, prompt: str, init_image: bytes | None = None) -> bytes | None:
    from fastapi.concurrency import run_in_threadpool

    from ...comfy.server import get_server
    await run_in_threadpool(get_server(provider.base_url).ensure_up)
    result = await run_in_threadpool(
        lambda: provider.generate_image(prompt=prompt, init_image=init_image))
    return result.images[0] if result.images else None


def _randomize_seeds(graph: dict) -> None:
    """Give every sampler a fresh seed so repeated renders of one prompt vary
    (workflows ship with a fixed seed). Mutates in place."""
    import random
    for node in graph.values():
        ins = node.get("inputs") if isinstance(node, dict) else None
        if isinstance(ins, dict):
            for k in ("seed", "noise_seed"):
                if isinstance(ins.get(k), (int, float)):
                    ins[k] = random.randint(0, 2_147_483_646)


# The base backdrop colour the prompt paints (magenta) — the colour filter keys it out so the
# enclosed arm/body gap RMBG leaves filled goes transparent. The character's palette avoids it.
_KEY_COLOR = (255, 0, 255)


def _strip_key_color(raw: bytes, target=_KEY_COLOR, tol: int = 95) -> bytes:
    """Remove leftover backdrop-colour pixels (the enclosed gap RMBG keeps) by setting their
    alpha to 0. RMBG already cut the silhouette + hair; this only clears the key colour, which
    the character doesn't contain — so it can't hole the character. No-op if numpy is absent."""
    try:
        from io import BytesIO

        import numpy as np
        from PIL import Image
        im = Image.open(BytesIO(raw)).convert("RGBA")
        a = np.array(im)
        rgb = a[..., :3].astype(np.int16)
        dist = np.sqrt(((rgb - np.array(target, dtype=np.int16)) ** 2).sum(-1))
        a[dist < tol, 3] = 0                 # near the key colour → transparent
        out = BytesIO(); Image.fromarray(a, "RGBA").save(out, "PNG"); return out.getvalue()
    except Exception:  # noqa: BLE001 — numpy/Pillow missing or odd image; leave as-is
        return raw


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

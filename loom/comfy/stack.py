"""Inject a checkpoint + LoRA stack into a ComfyUI graph.

This is the single mechanism behind both "preview a trained LoRA" and "apply a
character's portrait preset": take a real workflow, swap its checkpoint, and
rebuild its LoRA chain to an arbitrary stack — re-stitching the MODEL/CLIP wiring
so downstream nodes (sampler, CLIP encoders, FaceDetailer…) follow the new tail.

Working on the *real* workflow (rather than a throwaway minimal graph) means a
sample is representative: same VAE, same detailer, same upscale — only the
checkpoint and LoRAs change.
"""

from __future__ import annotations

import copy
from typing import Any

# Loaders we recognise as "the checkpoint" — the source of the MODEL/CLIP the
# LoRA chain hangs off.
_CKPT_TYPES = ("CheckpointLoaderSimple", "CheckpointLoader", "CheckpointLoaderSimpleShared")
# Split loaders (Anima/DiT, Flux, …): MODEL and CLIP come from separate nodes.
# Includes the Image Saver variant that also emits the filename (used by the
# Anima All-In-One master workflow).
_UNET_TYPES = ("UNETLoader", "UnetLoaderGGUF", "UNet loader with Name (Image Saver)")
_CLIP_TYPES = ("CLIPLoader", "DualCLIPLoader", "TripleCLIPLoader",
               "CLIPLoaderGGUF", "DualCLIPLoaderGGUF")
# Nodes that apply a whole LoRA stack at once (ComfyUI_essentials). Like a
# LoraLoader they emit MODEL (slot 0) + CLIP (slot 1), so a LoraLoader chain can
# hang off them — the master workflow's preset-style stack thus becomes the base
# that Loom's resolved stack layers on top of.
_LORA_APPLY_TYPES = ("easy loraStackApply",)


def resolve_stack(
    stack: dict,
    library: list[dict] | None = None,
    theme: str | None = None,
    state_active: list[dict] | None = None,
) -> dict:
    """Compose a stack into an effective checkpoint + LoRA list by *type*, in
    chain order:

        detail   — every enabled `detail` LoRA in the library (always-on)
        theme    — the picked `theme` LoRA (constant; chosen, not routed)
        identity — the stack's always-on base (`stack.identity`)
        state    — `state_active`: the state LoRAs the router decided are live

    State routing (matching scene → each state LoRA's own tags) happens in
    `comfy.tags.route_state`; this composer just lays the decided layers down,
    keeping the embedding dependency out of the pure merge. De-duplicated by
    name, last-writer-wins on weight, so a state LoRA can re-tune identity. Each
    returned LoRA carries `type`/`why` for the UI. Feeds `inject_models`.
    """
    stack = stack or {}
    library = library or []
    order: list[str] = []
    merged: dict[str, dict] = {}

    def add(loras, kind, why):
        for lr in loras or []:
            name = lr.get("name")
            if not name:
                continue
            if name not in merged:
                order.append(name)
            merged[name] = {"name": name, "weight": float(lr.get("weight", 1.0)),
                            "type": kind, "why": why}

    # 1. detail — always-on quality tail
    for l in library:
        if l.get("type") == "detail" and l.get("enabled", True):
            add([l], "detail", "always-on")
    # 2. theme — constant: only the explicitly picked look
    for l in library:
        if l.get("type") == "theme" and theme and l.get("name") == theme:
            add([l], "theme", "selected")
    # 3. identity — the stack's always-on base
    add(stack.get("identity"), "identity", "always-on")
    # 4. state — whatever the router activated
    for s in state_active or []:
        add([s], "state", s.get("why", "matched"))

    return {"checkpoint": stack.get("checkpoint"), "loras": [merged[n] for n in order]}


def _find_first(graph: dict, types: tuple[str, ...]) -> str | None:
    for nid, node in graph.items():
        if node.get("class_type") in types:
            return nid
    return None


def _find_checkpoint(graph: dict) -> str | None:
    return _find_first(graph, _CKPT_TYPES)


def inject_models(
    graph: dict,
    checkpoint: str | None = None,
    loras: list[dict[str, Any]] | None = None,
) -> dict:
    """Return a copy of *graph* with its base model and LoRA stack replaced.

    Handles both **bundled** checkpoints (CheckpointLoaderSimple → MODEL+CLIP) and
    **split** loaders (UNETLoader → MODEL, CLIPLoader → CLIP), as Anima/DiT and
    Flux use — the LoRA chain hangs off whichever sources the graph has.

    - ``checkpoint``: new base name (ckpt_name for a checkpoint, unet_name for a
      UNet); skipped if None.
    - ``loras``: the new stack. ``None`` leaves the existing chain untouched;
      ``[]`` strips all LoRAs and wires downstream straight to the base.
    """
    g = copy.deepcopy(graph)
    ckpt = _find_first(g, _CKPT_TYPES)
    unet = _find_first(g, _UNET_TYPES)
    clip = _find_first(g, _CLIP_TYPES)

    # base override
    if checkpoint:
        if ckpt:
            g[ckpt].setdefault("inputs", {})["ckpt_name"] = checkpoint
        elif unet:
            g[unet].setdefault("inputs", {})["unet_name"] = checkpoint

    if loras is None:
        return g

    loaders = [nid for nid, n in g.items() if n.get("class_type") in ("LoraLoader", "LoraLoaderModelOnly")]
    # easy loraStackApply nodes act as a single LoRA application point (MODEL+CLIP
    # out, same slots as a LoraLoader). When present, they are the chain tail.
    stack_applies = [nid for nid, n in g.items() if n.get("class_type") in _LORA_APPLY_TYPES]

    # MODEL/CLIP sources. A bundled checkpoint provides both; otherwise UNet +
    # CLIP. But an easy loraStackApply node — which sits AFTER the loaders and
    # applies the preset-style stack — is the true chain source when present:
    # its MODEL (0) / CLIP (1) outputs carry the post-stack model, and any new
    # LoraLoader chain must hang off them, not off a raw loader that the stack
    # node itself consumes.
    if stack_applies:
        sa = stack_applies[0]
        base_model, base_clip = [sa, 0], [sa, 1]
    elif ckpt:
        base_model, base_clip = [ckpt, 0], [ckpt, 1]
    elif unet:
        base_model = [unet, 0]
        base_clip = [clip, 0] if clip else None
    else:
        return g  # nothing to hang LoRAs off

    # The chain tail = a loader whose MODEL output no other loader consumes.
    referenced = {
        g[nid]["inputs"]["model"][0]
        for nid in loaders
        if isinstance(g[nid].get("inputs", {}).get("model"), list)
    }
    if loaders:
        tails = [nid for nid in loaders if nid not in referenced]
        tail = tails[0] if tails else loaders[-1]
        old_model_src, old_clip_src = [tail, 0], [tail, 1]
    elif stack_applies:
        # No LoraLoader chain — the stack-apply node is the tail. Its MODEL (0)
        # and CLIP (1) outputs are the base a fresh LoraLoader chain hangs off.
        # The stack-apply itself is NOT removed (it carries the preset styles).
        tail = stack_applies[0]
        old_model_src, old_clip_src = [tail, 0], [tail, 1]
    else:
        old_model_src, old_clip_src = list(base_model), (list(base_clip) if base_clip else None)

    for nid in loaders:
        g.pop(nid, None)

    # Build the new chain. With a CLIP source use LoraLoader (model+clip); without
    # one (model-only graphs) use LoraLoaderModelOnly so we don't dangle a clip input.
    prev_model, prev_clip = list(base_model), (list(base_clip) if base_clip else None)
    new_ids: set[str] = set()
    used = {int(k) for k in g if k.isdigit()}
    nxt = (max(used) + 1) if used else 1
    for lr in loras:
        nid = str(nxt)
        nxt += 1
        w = float(lr.get("weight", 1.0))
        if prev_clip is not None:
            g[nid] = {"class_type": "LoraLoader",
                      "inputs": {"lora_name": lr["name"], "strength_model": w, "strength_clip": w,
                                 "model": prev_model, "clip": prev_clip},
                      "_meta": {"title": f"LoRA: {lr['name']}"}}
            prev_model, prev_clip = [nid, 0], [nid, 1]
        else:
            g[nid] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"lora_name": lr["name"], "strength_model": w, "model": prev_model},
                      "_meta": {"title": f"LoRA: {lr['name']}"}}
            prev_model = [nid, 0]
        new_ids.add(nid)
    new_model_src, new_clip_src = prev_model, prev_clip

    # Repoint every *pre-existing* consumer of the old tail onto the new tail.
    def fix(ref: Any) -> Any:
        if isinstance(ref, list) and len(ref) == 2:
            if ref[0] == old_model_src[0] and ref[1] == old_model_src[1]:
                return list(new_model_src)
            if old_clip_src and new_clip_src and ref[0] == old_clip_src[0] and ref[1] == old_clip_src[1]:
                return list(new_clip_src)
        return ref

    for nid, node in g.items():
        if nid in new_ids:
            continue
        ins = node.get("inputs")
        if isinstance(ins, dict):
            for key, val in list(ins.items()):
                ins[key] = fix(val)
    return g

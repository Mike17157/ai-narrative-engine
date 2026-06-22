"""Extract a workflow's model references and check them against what's installed.

Works on ComfyUI **API-format** workflows (node-id keyed {class_type, inputs}) —
the same format the app uses. Model names live in labelled input fields, so we
read them directly, see which files are missing from the model directory, and
match the missing ones to the ComfyUI Manager catalog for one-click download.
"""

from __future__ import annotations

from pathlib import Path

# input field -> model kind
_FIELD_KIND = {
    "ckpt_name": "checkpoint", "unet_name": "diffusion", "vae_name": "vae",
    "lora_name": "lora", "control_net_name": "controlnet", "style_model_name": "style_models",
    "clip_name": "clip", "clip_name1": "clip", "clip_name2": "clip", "clip_name3": "clip",
    "ipadapter_file": "ipadapter", "gligen_name": "gligen",
}
# `model_name` is reused by several loaders — disambiguate by node class
_MODELNAME_CLASS = {
    "UltralyticsDetectorProvider": "ultralytics", "SAMLoader": "sam", "UpscaleModelLoader": "upscale",
}
# kind -> ComfyUI folders it may live in (relative to models/)
_KIND_FOLDERS = {
    "checkpoint": ["checkpoints"], "diffusion": ["diffusion_models", "unet"], "vae": ["vae"],
    "lora": ["loras"], "controlnet": ["controlnet"], "style_models": ["style_models"],
    "clip": ["text_encoders", "clip"], "ipadapter": ["ipadapter"], "gligen": ["gligen"],
    "ultralytics": ["ultralytics"], "sam": ["sams", "sam"], "upscale": ["upscale_models"],
}


def model_refs(graph: dict) -> list[dict]:
    """Every model file a workflow references, as {node, class_type, field, kind, name}."""
    refs = []
    for nid, n in (graph or {}).items():
        if not isinstance(n, dict):
            continue
        ct, ins = n.get("class_type"), n.get("inputs")
        if not isinstance(ins, dict):
            continue
        for field, val in ins.items():
            if not isinstance(val, str) or not val:
                continue
            kind = _MODELNAME_CLASS.get(ct) if field == "model_name" else _FIELD_KIND.get(field)
            if kind:
                refs.append({"node": nid, "class_type": ct, "field": field, "kind": kind, "name": val})
    return refs


def _save_score(node: dict) -> int:
    """Rank how likely a node is the workflow's image OUTPUT. Real image sinks consume an
    `images` input (so a 'Scheduler Selector (Image Saver)' helper, which doesn't, ranks
    below the actual 'Image Saver'). Returns -1 for non-output nodes."""
    ct = str(node.get("class_type", "")).lower()
    ins = node.get("inputs") or {}
    is_out = ("save" in ct and "image" in ct) or ct in ("previewimage", "saveimagewebsocket")
    if not is_out:
        return -1
    score = 0
    if isinstance(ins.get("images"), list):
        score += 10
    if ct in ("saveimage", "image saver"):
        score += 5
    if ct == "previewimage":
        score -= 2
    return score


def _follow_to_text(graph: dict, start: str, max_visit: int = 60) -> tuple[str | None, str | None]:
    """Walk backward from a node id through input wires until a text-prompt field is
    found (CLIPTextEncode etc.). Returns (node_id, field) or (None, None)."""
    seen: set[str] = set()
    stack = [start]
    while stack and len(seen) < max_visit:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        node = graph.get(nid) or {}
        ins = node.get("inputs") or {}
        if not isinstance(ins, dict):
            continue
        for f in ("text", "text_g", "text_l", "prompt", "positive_prompt"):
            if isinstance(ins.get(f), str):
                return nid, f
        for v in ins.values():  # follow node references [nid, slot]
            if isinstance(v, list) and v and isinstance(v[0], str):
                stack.append(v[0])
    return None, None


def suggest_io(graph: dict) -> dict:
    """Best-guess the positive-prompt injection point + output (SaveImage) node for an
    arbitrary API workflow, plus all candidates so the UI can offer an override."""
    graph = graph or {}
    outputs = sorted(
        [nid for nid, n in graph.items() if isinstance(n, dict) and _save_score(n) >= 0],
        key=lambda nid: _save_score(graph[nid]), reverse=True)
    text_nodes = [(nid, "text") for nid, n in graph.items()
                  if isinstance(n, dict) and isinstance((n.get("inputs") or {}).get("text"), str)]

    pos_node = pos_field = None
    # Trace from samplers: KSampler.positive, or SamplerCustomAdvanced.guider → BasicGuider.conditioning.
    for nid, n in graph.items():
        if not isinstance(n, dict) or "sampler" not in str(n.get("class_type", "")).lower():
            continue
        ins = n.get("inputs") or {}
        ref = ins.get("positive")
        if isinstance(ref, list) and ref:
            pos_node, pos_field = _follow_to_text(graph, ref[0])
            if pos_node:
                break
        gref = ins.get("guider")
        if isinstance(gref, list) and gref:
            gin = (graph.get(gref[0]) or {}).get("inputs") or {}
            cref = gin.get("conditioning") or gin.get("positive")
            if isinstance(cref, list) and cref:
                pos_node, pos_field = _follow_to_text(graph, cref[0])
                if pos_node:
                    break
    if not pos_node and text_nodes:          # fallback: first text node in the graph
        pos_node, pos_field = text_nodes[0]

    return {
        "positive": ({"node": pos_node, "field": pos_field} if pos_node else None),
        "output_node": (outputs[0] if outputs else None),
        "text_candidates": [{"node": nid, "field": f,
                             "class_type": (graph.get(nid) or {}).get("class_type")} for nid, f in text_nodes],
        "output_candidates": [{"node": nid,
                              "class_type": (graph.get(nid) or {}).get("class_type")} for nid in outputs],
    }


def check_workflow(graph: dict, models_dir: str | Path, catalog_entries: list[dict]) -> dict:
    """Dedupe refs, flag which are installed, and match missing ones to the catalog."""
    models_dir = Path(models_dir)
    cat_by_base: dict[str, dict] = {}
    for e in catalog_entries:
        base = Path((e.get("filename") or "").replace("\\", "/")).name.lower()
        if base:
            cat_by_base.setdefault(base, e)

    out: dict[tuple, dict] = {}
    for r in model_refs(graph):
        key = (r["kind"], r["name"])
        if key in out:
            continue
        rel = r["name"].replace("\\", "/")
        folders = _KIND_FOLDERS.get(r["kind"], [r["kind"]])
        installed = any((models_dir / f / rel).is_file() for f in folders)
        cat = None
        if not installed:
            c = cat_by_base.get(Path(rel).name.lower())
            if c:
                cat = {"url": c["url"], "rel": c["rel"], "name": c.get("name", "")}
        out[key] = {**r, "installed": installed, "catalog": cat}

    refs = list(out.values())
    return {"refs": refs, "missing": [r for r in refs if not r["installed"]],
            "io": suggest_io(graph)}

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
# Custom-node loaders whose filename field isn't one of the standard names above.
# (class_type, field) → kind. WanVideoWrapper uses `model`/`model_name`/`lora`.
_CLASS_FIELD_KIND = {
    ("WanVideoModelLoader", "model"): "diffusion",
    ("LoadWanVideoT5TextEncoder", "model_name"): "clip",
    ("WanVideoVAELoader", "model_name"): "vae",
    ("WanVideoLoraSelect", "lora"): "lora",
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
            kind = (_CLASS_FIELD_KIND.get((ct, field))
                    or (_MODELNAME_CLASS.get(ct) if field == "model_name" else _FIELD_KIND.get(field)))
            if kind:
                refs.append({"node": nid, "class_type": ct, "field": field, "kind": kind, "name": val})
    return refs


_WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}
_CONTROL_SENTINELS = {"fixed", "increment", "decrement", "randomize"}


def ui_to_api(ui: dict, object_info: dict) -> tuple[dict, list[str]]:
    """Convert a ComfyUI *UI/editor* workflow ({nodes,links,…}) into *API* format
    (node-id → {class_type, inputs}). Mirrors ComfyUI's own graph→prompt: linked inputs
    become [src_node, slot] refs; widget values map onto the node's widget inputs in
    definition order (skipping any input that's wired as a link, and the control-after-
    generate sentinel that follows a seed). Muted/bypassed nodes (mode 2/4) and pure
    annotations are dropped. Returns (api_graph, unknown_class_types) — unknowns are nodes
    whose definition isn't installed, so their widget mapping is best-effort."""
    nodes = ui.get("nodes", []) or []
    # link id → [from_node_id(str), from_slot]. Links are [id, from, from_slot, to, to_slot, type].
    src: dict = {}
    for l in ui.get("links", []) or []:
        if isinstance(l, list) and len(l) >= 5:
            src[l[0]] = [str(l[1]), l[2]]
        elif isinstance(l, dict):
            src[l.get("id")] = [str(l.get("origin_id")), l.get("origin_slot", 0)]

    out: dict = {}
    unknown: list[str] = []
    for n in nodes:
        if not isinstance(n, dict) or n.get("mode") in (2, 4):
            continue
        ct = n.get("type")
        if ct in ("Note", "MarkdownNote", "Reroute", "PrimitiveNode"):
            continue
        nid = str(n.get("id"))
        defn = object_info.get(ct)
        inputs: dict = {}
        linked: set = set()
        for inp in (n.get("inputs") or []):
            lk = inp.get("link")
            if lk is not None and lk in src:
                inputs[inp["name"]] = src[lk]
                linked.add(inp.get("name"))
        wv = n.get("widgets_values")
        if defn:
            req = (defn.get("input", {}) or {}).get("required", {}) or {}
            opt = (defn.get("input", {}) or {}).get("optional", {}) or {}
            order = []
            for grp in (req, opt):
                for name, spec in grp.items():
                    t = spec[0] if isinstance(spec, (list, tuple)) else spec
                    if (isinstance(t, list) or t in _WIDGET_TYPES) and name not in linked:
                        order.append(name)
            if isinstance(wv, list):
                vi = 0
                for name in order:
                    if vi >= len(wv):
                        break
                    inputs[name] = wv[vi]; vi += 1
                    if vi < len(wv) and isinstance(wv[vi], str) and wv[vi] in _CONTROL_SENTINELS:
                        vi += 1   # skip control_after_generate
            elif isinstance(wv, dict):
                for name in order:
                    if name in wv:
                        inputs[name] = wv[name]
        else:
            unknown.append(ct)
        out[nid] = {"class_type": ct, "inputs": inputs, "_meta": {"title": n.get("title") or ct}}

    # Drop links that point at nodes we excluded (muted/annotation) so the graph validates.
    valid = set(out)
    for node in out.values():
        node["inputs"] = {k: v for k, v in node["inputs"].items()
                          if not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] not in valid)}
    return out, sorted(set(unknown))


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


def classify_workflow(graph: dict | None, filename: str | None = None) -> dict:
    """Classify an image workflow by combining node-class TEXT MATCHING (what the graph
    actually contains) with FILENAME matching (the author's stated intent). Returns
    ``{type, media, needs_init}``:

      * ``media``     — the file the graph emits: ``video`` (a VHS/Wan clip) or ``image``.
      * ``needs_init``— True when it has a LoadImage node (img2img / a post-process).
      * ``type``      — the functional class: ``txt2img | img2img | video | upscale |
                        detailer | rembg``.

    Filename intent wins for the post-process roles, because a full generation pipeline
    can *contain* a detailer or background-remover without *being* one (e.g. anima_character
    bakes both in but is a txt2img). Node evidence drives media + the txt2img/img2img split.
    """
    graph = graph or {}
    cts = [str(n.get("class_type", "")).lower()
           for n in graph.values() if isinstance(n, dict)]

    def any_ct(*subs: str) -> bool:
        return any(any(s in ct for s in subs) for ct in cts)

    has_video   = any_ct("videocombine", "animatediff", "svd_img2vid") or any(ct.startswith("wanvideo") for ct in cts)
    has_loadimg = any(ct == "loadimage" for ct in cts)
    has_sampler = any_ct("sampler")
    has_upscale = any_ct("ultimatesdupscale", "upscalemodelloader", "upscale")
    has_detail  = any_ct("detailer")
    has_rembg   = any_ct("rembg", "removebg", "remove_bg", "inspyrenet")

    stem = Path(filename).stem.lower() if filename else ""

    def fn(*subs: str) -> bool:
        return any(s in stem for s in subs)

    # 'filetype' the workflow emits — a clip vs a still.
    media = "video" if (has_video or fn("i2v", "t2v", "video", "vid2vid")) else "image"

    if media == "video":
        wf_type = "video"
    elif fn("upscale"):
        wf_type = "upscale"
    elif fn("detailer", "adetailer"):
        wf_type = "detailer"
    elif fn("rembg", "removebg", "cutout"):
        wf_type = "rembg"
    elif fn("img2img", "i2i"):
        wf_type = "img2img"
    elif fn("txt2img", "t2i"):
        wf_type = "txt2img"
    elif has_loadimg and not has_sampler:
        # a pure post-process graph (load → op → save), no generation
        wf_type = ("upscale" if has_upscale else "detailer" if has_detail
                   else "rembg" if has_rembg else "img2img")
    elif has_loadimg:
        wf_type = "img2img"
    else:
        wf_type = "txt2img"

    return {"type": wf_type, "media": media, "needs_init": has_loadimg}


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

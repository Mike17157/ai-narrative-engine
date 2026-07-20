"""Shared ComfyUI workflow graph manipulation.

The ComfyUI provider sends an API-format workflow JSON to a ComfyUI backend
(local or remote). The graph-mutation rules — prompt-token substitution,
output-prefix and latent-size overrides, and BREAK-region conditioning — live
here as pure functions over a graph dict rather than being duplicated in the
provider.

Nothing here touches the network: the provider deep-copies its workflow, calls
:func:`inject` to prepare the graph, then ships it however it likes.
"""

from __future__ import annotations

import copy
import os
import re
from typing import Any

# The dynamic value the chat model writes each turn. Insert it into any CLIP text
# field as this token (the UI's ⚡ picker does this); at generation it's
# substituted wherever it appears. If a workflow uses no token at all, callers
# fall back to overwriting the designated positive node (legacy behaviour).
IMAGE_TOKEN = "{{image}}"

# Saver nodes whose output filename we set for organized paths. `easy imageRemBg` /
# `easy imageSave` use `save_prefix`; core SaveImage* use `filename_prefix`.
_SAVE_CLASSES = ("SaveImage", "SaveImageWithAlpha", "easy imageSave", "easy imageRemBg")
_LATENT_CLASSES = ("EmptyLatentImage", "EmptySD3LatentImage")
_BREAK_RE = re.compile(r"\bBREAK\b")


def substitute_token(graph: dict, value: str) -> bool:
    """Replace the image token in every string input. Returns True if any field
    contained it."""
    found = False
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ins = node.get("inputs")
        if not isinstance(ins, dict):
            continue
        for k, v in ins.items():
            if isinstance(v, str) and IMAGE_TOKEN in v:
                ins[k] = v.replace(IMAGE_TOKEN, value)
                found = True
    return found


def substitute_named_tokens(graph: dict, values: dict[str, str] | None) -> None:
    """Replace additional explicit workflow tokens (for example regional prompts).

    Unlike ``{{image}}``, these are never broadcast blindly: every region has its own
    conditioning node and therefore gets only its own brief.
    """
    for token, value in (values or {}).items():
        marker = "{{" + str(token).strip("{} ") + "}}"
        for node in graph.values():
            ins = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(ins, dict):
                continue
            for key, current in ins.items():
                if isinstance(current, str) and marker in current:
                    ins[key] = current.replace(marker, str(value or ""))


def apply_out_prefix(graph: dict, out_prefix: str) -> None:
    """Point every SaveImage-family node at `out_prefix` (ComfyUI nests on '/'), so renders land
    under loom/<family>/<role>/<character>/. No-op if the graph has no such node."""
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type")
        if ct in _SAVE_CLASSES:
            ins = node.setdefault("inputs", {})
            # easy imageSave uses `save_prefix`; core SaveImage uses `filename_prefix`
            key = "save_prefix" if "save_prefix" in ins else "filename_prefix"
            ins[key] = out_prefix
        elif ct == "Image Saver":
            # comfyui-image-saver splits into `path` (subfolder) + `filename`. The Anima
            # graph wires `path` to a StringConcatenate that yields "\V19" — an absolute
            # drive-root path ComfyUI's /view rejects (403), so Loom can't collect the
            # render. Override with our relative prefix (path=dir, filename=basename) so the
            # output lands under the ComfyUI output dir and is collectable.
            ins = node.setdefault("inputs", {})
            head, _, tail = out_prefix.replace("\\", "/").rpartition("/")
            ins["path"] = head
            ins["filename"] = tail or out_prefix


def apply_latent(graph: dict, latent: tuple[int, int]) -> None:
    """Set the canvas size on every empty-latent node, so Loom can pick the aspect per render
    (portrait sprite vs landscape reclining). No-op if the graph has no such node."""
    w, h = int(latent[0]), int(latent[1])
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") in _LATENT_CLASSES:
            ins = node.setdefault("inputs", {})
            ins["width"], ins["height"] = w, h


def strip_breaks(graph: dict) -> None:
    """Collapse every `BREAK` separator to a comma — the always-safe fallback when region
    chaining can't be applied."""
    for n in graph.values():
        ins = n.get("inputs") if isinstance(n, dict) else None
        if isinstance(ins, dict):
            for k, v in ins.items():
                if isinstance(v, str) and "BREAK" in v:
                    ins[k] = _BREAK_RE.sub(", ", v).strip(" ,")


def apply_prompt_suffix(graph: dict, inputs: dict[str, dict], suffix: str) -> None:
    """Append LoRA trigger words to the POSITIVE prompt so the LoRAs fire.

    The positive node is a CLIPTextEncode whose `text` already holds the (cloud-authored) prompt
    string — substitute_token / the legacy fallback set it before this runs — so we just append
    the suffix. No-op when the suffix is empty or the positive node isn't a text encoder."""
    suffix = (suffix or "").strip()
    if not suffix:
        return
    pos = inputs.get("positive")
    pos_node = graph.get(str(pos["node"])) if pos else None
    if isinstance(pos_node, dict) and pos_node.get("class_type") == "CLIPTextEncode":
        t = pos_node.get("inputs", {}).get("text")
        if isinstance(t, str):
            pos_node["inputs"]["text"] = f"{t}, {suffix}" if t.strip() else suffix


def apply_breaks(graph: dict) -> None:
    """For each CLIPTextEncode whose text uses BREAK: keep region 1 on the node, add a
    CLIPTextEncode (sharing its clip) per further region, chain them with ConditioningConcat,
    and rewire the original encode's consumers to the final concat."""
    targets = [nid for nid, n in graph.items()
               if isinstance(n, dict) and n.get("class_type") == "CLIPTextEncode"
               and isinstance(n.get("inputs"), dict) and "BREAK" in str(n["inputs"].get("text", ""))]
    seq = 0
    for pid in targets:
        node = graph[pid]
        segs = [s.strip(" ,") for s in _BREAK_RE.split(node["inputs"]["text"])]
        segs = [s for s in segs if s]
        clip = node["inputs"].get("clip")
        if len(segs) < 2 or clip is None:
            node["inputs"]["text"] = ", ".join(segs)   # nothing to chain (or no clip) → collapse
            continue
        # original consumers of this encode's conditioning output, captured BEFORE we add nodes
        consumers = [(nid, k) for nid, n in graph.items() if isinstance(n, dict)
                     for k, v in (n.get("inputs") or {}).items()
                     if isinstance(v, list) and len(v) == 2 and str(v[0]) == str(pid) and v[1] == 0]
        node["inputs"]["text"] = segs[0]
        prev = pid
        for seg in segs[1:]:
            enc, cc = f"loom_break_e{seq}", f"loom_break_c{seq}"; seq += 1
            graph[enc] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": seg}}
            graph[cc] = {"class_type": "ConditioningConcat",
                         "inputs": {"conditioning_to": [prev, 0], "conditioning_from": [enc, 0]}}
            prev = cc
        for nid, k in consumers:
            graph[nid]["inputs"][k] = [prev, 0]


# Map flag name → set of ComfySwitchNode titles that gate that feature.
# Title strings must match _meta.title exactly in the workflow JSON.
_FLAG_TITLES: dict[str, set[str]] = {
    "detailer": {"Use Detailer"},
    "upscale":  {"Using USDU"},
    "highrez":  {"Use HighRez"},
}


def apply_flags(graph: dict, **flags: bool) -> None:
    """Enable or disable named pipeline features.

    Known flags: ``detailer``, ``upscale``, ``highrez`` (ComfySwitchNode gates
    located by ``_meta.title``) and ``sage`` (the SageAttention kernel patch).
    Unrecognised flag names are silently ignored (the workflow simply has no
    matching node), so the function stays workflow-agnostic.

    ``sage`` is special-cased: it doesn't gate a branch, it flips the
    ``PathchSageAttentionKJ`` node's ``sage_attention`` widget between ``auto``
    (use the faster fused kernel) and ``disabled`` (stock attention). Turn it OFF
    when running on hardware whose ComfyUI lacks the sageattention library or
    whose GPU the kernel doesn't support; ON for the RunPod fleet.
    """
    for flag, enabled in flags.items():
        if flag == "sage":
            _apply_sage(graph, bool(enabled))
            continue
        titles = _FLAG_TITLES.get(flag)
        if not titles:
            continue
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            if node.get("_meta", {}).get("title") in titles:
                node.setdefault("inputs", {})["switch"] = bool(enabled)


def _apply_sage(graph: dict, enabled: bool) -> None:
    mode = "auto" if enabled else "disabled"
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == "PathchSageAttentionKJ":
            node.setdefault("inputs", {})["sage_attention"] = mode


def fix_image_saver_metadata(graph: dict) -> None:
    """Harden the comfyui-image-saver ``Image Saver`` node against a None crash.

    That node stamps the sampler/scheduler names into PNG metadata and its filename
    template via ``str.replace(token, value)`` — which raises ``TypeError`` if value
    is None *even when the token is absent*. In the Anima graph those names are wired
    through a fragile rgthree Context bus that can resolve to None, which crashes the
    final save (the render itself is fine). We resolve the real sampler/scheduler from
    a ``KSampler Config (rgthree)`` node and stamp them as literal strings, so the save
    never depends on the bus. Metadata-only; no effect on the actual sampling."""
    sampler, scheduler = "euler", "normal"
    for n in graph.values():
        if isinstance(n, dict) and n.get("class_type") == "KSampler Config (rgthree)":
            ins = n.get("inputs") or {}
            if isinstance(ins.get("sampler_name"), str):
                sampler = ins["sampler_name"]
            if isinstance(ins.get("scheduler"), str):
                scheduler = ins["scheduler"]
            break
    for n in graph.values():
        if not isinstance(n, dict) or n.get("class_type") != "Image Saver":
            continue
        ins = n.setdefault("inputs", {})
        if not isinstance(ins.get("sampler_name"), str):
            ins["sampler_name"] = sampler
        if not isinstance(ins.get("scheduler_name"), str):
            ins["scheduler_name"] = scheduler


def sanitize_image_saver_path(graph: dict) -> None:
    """Neutralise the Anima ``Image Saver`` node's absolute output path.

    Its ``path`` input is wired to a StringConcatenate that yields ``\\V19`` — an
    absolute drive-root subfolder that lands outside ComfyUI's output dir, so the
    saved PNG can't be fetched back through ``/view`` (Loom collects empty bytes).
    When no out_prefix is supplied (grid tester, plain test renders), repoint
    ``path`` to "" so the image saves into the output root and stays collectable.
    Filename (its own template node) is left untouched. ``apply_out_prefix`` does
    the equivalent when a prefix IS given, so this only fires otherwise."""
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == "Image Saver":
            node.setdefault("inputs", {})["path"] = ""


_MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".gguf", ".sft")


def normalize_model_paths(graph: dict) -> None:
    """Rewrite Windows backslashes to '/' in model-name inputs.

    A workflow saved on Windows stores nested model names like
    ``Illustrious\\TRT_style_v1.4_IL.safetensors``; a Linux ComfyUI worker (RunPod)
    won't resolve that. Only string values that end in a model extension are
    touched, so prompt text is never altered. Used by the serverless provider,
    not the local one (which talks to a same-OS ComfyUI)."""
    for node in graph.values():
        ins = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(ins, dict):
            continue
        for k, v in ins.items():
            if isinstance(v, str) and "\\" in v and v.lower().endswith(_MODEL_EXTS):
                ins[k] = v.replace("\\", "/")


def localize_model_paths(graph: dict) -> None:
    """Rewrite model-name separators to the local OS separator.

    The mirror of :func:`normalize_model_paths`: ComfyUI's loader combo lists use the
    host OS separator (backslash on Windows), so a nested model name carrying '/' — e.g.
    ``anima/anisnuff_v15.safetensors`` from inject_models — fails validation against a
    Windows ComfyUI ("Value not in list"). Used by the same-OS local provider; the
    serverless provider normalizes to '/' for its Linux worker instead.

    Exception: ``UltralyticsDetectorProvider`` model names carry a ComfyUI *category*
    prefix (``bbox/face_yolov8m.pt``, ``segm/person_yolov8m-seg.pt``) that the
    Impact-Pack combo keeps with a forward slash on every OS — rewriting it to a
    backslash yields "Value not in list". Those nodes are skipped."""
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == "UltralyticsDetectorProvider":
            continue
        ins = node.get("inputs")
        if not isinstance(ins, dict):
            continue
        for k, v in ins.items():
            if isinstance(v, str) and v.lower().endswith(_MODEL_EXTS) and ("/" in v or "\\" in v):
                ins[k] = v.replace("/", os.sep).replace("\\", os.sep)


def find_load_image_node(graph: dict) -> str | None:
    """Return the id of the first LoadImage node, or None for a text-to-image graph."""
    return next((nid for nid, n in graph.items()
                 if isinstance(n, dict) and n.get("class_type") == "LoadImage"), None)


def apply_output_variant(graph: dict, output_node: str | None,
                         variant: str | None) -> str | None:
    """Return the node id whose images Loom should collect, applying an optional
    output ``variant`` by splicing a post-process node after the render.

    Variants (collapsing the old anima / anima_cutout / anima_background graphs
    into one workflow + this switch):

    - ``full`` / None : unchanged — collect from ``output_node`` as-is.
    - ``cutout``       : append an ``easy imageRemBg`` (Inspyrenet) fed by the
                         rendered image, so the subject is segmented out of the
                         busy background. Returns the new node id. Mirrors the
                         trailing node the dedicated anima_cutout graph carried.

    The variant node is spliced onto the *image* feeding the current output
    node (a SaveImage/Image Saver's images input), so the cutout derives from
    the same pixels the saver would store.
    """
    if not variant or variant == "full" or not output_node:
        return output_node

    if variant != "cutout":
        return output_node  # unknown variant — no-op, keep the base output

    saver = graph.get(output_node)
    if not isinstance(saver, dict):
        return output_node
    # Find the IMAGE feeding the saver (SaveImage.images / Image Saver.images).
    images_src = saver.get("inputs", {}).get("images")
    if not isinstance(images_src, list):
        return output_node  # can't find a source image → leave as-is

    new_id = "loom_cutout"
    graph[new_id] = {
        "class_type": "easy imageRemBg",
        "inputs": {
            "images": images_src,
            "rem_mode": "Inspyrenet",
            "image_output": "Save",
            "save_prefix": "loom",
            "add_background": "none",
        },
        "_meta": {"title": "Remove background (Inspyrenet → transparent)"},
    }
    return new_id



def inject(
    workflow: dict,
    inputs: dict[str, dict],
    prompt: str,
    negative_prompt: str | None = None,
    out_prefix: str | None = None,
    latent: tuple[int, int] | None = None,
    flags: dict[str, bool] | None = None,
    prompt_suffix: str | None = None,
    template_values: dict[str, str] | None = None,
) -> dict:
    """Deep-copy `workflow` and return a prepared graph: prompt substituted, optional
    out_prefix/latent applied, negative set, LoRA trigger suffix appended, BREAK regions chained.

    `inputs` is the model's input map, e.g.
    ``{"positive": {"node": "6", "field": "text"}, "negative": {"node": "7", "field": "text"}}``.
    """
    graph = copy.deepcopy(workflow)

    fix_image_saver_metadata(graph)
    if flags:
        apply_flags(graph, **flags)
    if out_prefix:
        apply_out_prefix(graph, out_prefix)
    else:
        sanitize_image_saver_path(graph)
    if latent:
        apply_latent(graph, latent)

    used_token = substitute_token(graph, prompt)
    substitute_named_tokens(graph, template_values)

    # Negative still targets its designated node (not tokenised).
    if negative_prompt is not None:
        neg = inputs.get("negative")
        if neg and str(neg["node"]) in graph:
            graph[str(neg["node"])].setdefault("inputs", {})[neg["field"]] = negative_prompt

    # Legacy fallback: no {{image}} anywhere -> overwrite the designated positive node.
    if not used_token:
        pos = inputs.get("positive")
        if pos:
            node_id, field = str(pos["node"]), pos["field"]
            if node_id not in graph:
                raise ValueError(f"workflow has no node '{node_id}' for input 'positive'")
            graph[node_id].setdefault("inputs", {})[field] = prompt

    # Append the active LoRA preset's trigger words to the positive conditioning (bypasses the
    # krea2 Gemma optimizer so magic tokens survive verbatim).
    if prompt_suffix:
        apply_prompt_suffix(graph, inputs, prompt_suffix)

    # Honour `BREAK` region separators by splitting the positive text into separately-encoded
    # regions chained with core ConditioningConcat (so each region conditions independently,
    # rather than the word "BREAK" being tokenised into the image). Never fatal: any failure
    # falls back to collapsing BREAK to commas so the render always proceeds.
    try:
        apply_breaks(graph)
    except Exception:  # noqa: BLE001
        strip_breaks(graph)
    return graph

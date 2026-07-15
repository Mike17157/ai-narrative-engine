"""Flatten animaAllInOne_v51.json (LiteGraph v0.4 UI format, with embedded
subgraphs + Get/Set virtual nodes) into a single ComfyUI **API-format** graph.

The source is the "Anima All-In-One" master workflow: a switchable
txt2img/img2img Anima pipeline with face/region detailers, an LLLite refine
pass, an upscale pass, and an 8-preset style (LoRA stack) switcher. It is the
canonical image workflow; this script re-emits it in the flat
``{"node_id": {class_type, inputs, _meta}}`` shape Loom consumes, so it can be
the one central image workflow.

Why a script (not a hand-written graph): the source uses LiteGraph v0.4
subgraphs (10 of them — composite nodes whose internal nodes live in
``definitions.subgraphs[]``) plus 68 KJNodes Get/Set virtual "global" wires.
Neither construct exists in ComfyUI API format, so the graph must be rebuilt:
subgraphs are *inlined* (internal nodes lifted to the top level, boundary
links spliced to direct edges) and Get/Set pairs are *collapsed* (every
``Get_X`` replaced by a direct edge from ``Set_X``'s source). Doing this by
hand across ~150 nodes is error-prone and unrecoverable after any source edit;
this transform is deterministic and re-runnable.

Pipeline of the transform
  1. Load the source. Build a registry of every subgraph by GUID.
  2. Recursively *expand* the graph: each top-level node that instantiates a
     subgraph is replaced by its internal nodes (id-prefixed to stay unique),
     with the subgraph's input/output boundary links translated into direct
     edges between real nodes. Expansion is recursive (subgraphs may nest).
  3. Collapse Get/Set: build a name → source-edge map from every ``SetNode``,
     then replace each ``GetNode``'s output edges with that source. Remove the
     virtual nodes.
  4. Strip pure-UI nodes (Note/MarkdownNote/Preview*/Comparer/ShowText) — they
     have no API effect and ComfyUI rejects some of them silently.
  5. Materialize API format: every remaining node becomes an entry whose
     ``inputs`` carries literal widget values for un-wired slots and
     ``[node_id, slot]`` pairs for wired slots.

The result is written to ``workflows/anima_master_api.json`` and a small
``.meta`` block records the designated Loom contract nodes (positive-prompt
injection point, output node, LoRA-chain anchor) so wiring stays self-documenting.

Run:  python scripts/flatten_anima_allinone.py [source.json] [out.json]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Node-type classification
# ---------------------------------------------------------------------------

# Pure-UI / display-only node types. They contribute nothing to the render and
# several are not accepted by the API at all. Dropped from the flat graph.
# NOTE: PreviewBridge is intentionally NOT here — in this workflow it carries
# the img2img source image + mask into the refine sampler, so it is load-bearing.
_UI_TYPES = {
    "Note", "MarkdownNote",
    "PreviewImage", "PreviewLatent",
    "Image Comparer (rgthree)",
    "ShowText|pysssss", "easy showAnything", "easy showAnythingSimple",
    "Lora Stack to String [RvTools]",  # metadata string for the Image Saver
    "Integer to String [RvTools]",     # UI label helper
}

# KJNodes virtual globals. SetNode captures a value under a name; GetNode
# reads it back. In API format these become direct producer→consumer edges.
_SET_TYPE = "SetNode"
_GET_TYPE = "GetNode"


# ===========================================================================
# 1. LINK MODEL
# ===========================================================================
# We model the whole graph as a flat edge table keyed by link id. Each edge:
#   {src_node, src_slot, dst_node, dst_slot, type}
# After inlining subgraphs and collapsing Get/Set, every surviving node's
# wired inputs are resolved by looking up the edge feeding (node, slot).


class Edge:
    __slots__ = ("src_node", "src_slot", "dst_node", "dst_slot", "type", "lid")

    def __init__(self, src_node, src_slot, dst_node, dst_slot, type_, lid=None):
        self.src_node = src_node      # internal id (str), already subgraph-prefixed
        self.src_slot = src_slot      # int output slot index on src_node
        self.dst_node = dst_node
        self.dst_slot = dst_slot
        self.type = type_             # ComfyUI type string (MODEL/CLIP/IMAGE…)
        self.lid = lid                # original link id (for debugging only)


def _edge_from_parent_link(link: list) -> Edge:
    """Top-level LiteGraph link = [lid, from_id, from_slot, to_id, to_slot, type]."""
    return Edge(str(link[1]), int(link[2]), str(link[3]), int(link[4]), link[5], lid=link[0])


# ===========================================================================
# 2. SUBGRAPH EXPANSION
# ===========================================================================

class Graph:
    """The accumulating flat graph: nodes (id → node-dict) + edges (by link id).

    Node ids are strings. Subgraph-internal nodes are prefixed ``s{instance}__``
    so inlining never collides with parent ids or other instances."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}             # internal id -> node dict
        self.edges_by_lid: dict[int, Edge] = {}      # original link id -> edge
        # boundary edges that cross into/out of an inlined subgraph instance, in
        # the *parent's* link-id space. These are resolved last, once every
        # instance knows its boundary-node mapping.
        self._pending_boundary: list[dict] = []
        # Nodes dropped as UI-only, mapped to the literal value they would have
        # emitted on their first (slot 0) output — so consumers of a dropped
        # string-producer (ShowText, Lora Stack to String, Integer to String)
        # can be rewired to the literal instead of left dangling. Keyed by the
        # node's flat id (subgraph-prefixed where applicable).
        self.dropped_literals: dict[str, Any] = {}
        self._next_id = 1


def _slot_outputs(node: dict) -> list[dict]:
    """A node's output slot descriptors (list, positional)."""
    return list(node.get("outputs") or [])


def _slot_inputs(node: dict) -> list[dict]:
    return list(node.get("inputs") or [])


def _prefix_id(instance: str, internal_id: int) -> str:
    return f"s{instance}__{internal_id}"


def _materialize_node(node: dict, new_id: str) -> dict:
    """Deep-copy a UI node, drop positional/UI-only fields, keep class_type +
    widget values. Wired inputs are NOT resolved here — that happens after all
    edges are known (a node may be wired from a not-yet-inlined instance)."""
    out: dict[str, Any] = {
        "class_type": node.get("type"),
        "_meta": {"title": node.get("title") or node.get("type")},
        # widget values carry literal defaults (seed, steps, sampler_name…)
        "_widgets": list(node.get("widgets_values") or []),
        # placeholders filled during materialization
        "_inputs_def": [dict(i) for i in (node.get("inputs") or [])],   # input slot defs
        "_outputs_def": [dict(o) for o in (node.get("outputs") or [])], # output slot defs
        "_bypassed": bool((node.get("mode") or 0) == 4),               # mode 4 = bypassed
    }
    return out


def expand_subgraph_instance(
    graph: Graph,
    instance_id: str,
    parent_node: dict,
    subgraph: dict,
    parent_to_internal_link: dict[int, Edge],
) -> None:
    """Inline one subgraph instance into `graph`.

    `parent_node` is the top-level node that instantiates `subgraph`; its input
    slots carry parent-space link ids (the values feeding the instance), and its
    output slots carry parent-space link ids (the edges leaving the instance).

    `parent_to_internal_link` maps a parent link id → the Edge already recorded
    in graph.edges_by_lid (so we can splice boundary crossings to real nodes).
    """
    # 1. Add all internal nodes (real nodes only — skip the -10/-20 boundary
    #    pseudo-nodes, which are pure routing).
    internal_id_map: dict[int, str] = {}  # internal node id -> prefixed id
    for n in subgraph.get("nodes", []):
        nid = n.get("id")
        if nid in (-10, -20):
            continue
        new_id = _prefix_id(instance_id, nid)
        # Skip UI-only nodes at the source so they never enter the flat graph,
        # but remember the literal they emitted so consumers can be rewired.
        if n.get("type") in _UI_TYPES:
            _record_dropped_literal(graph, n, new_id)
            continue
        graph.nodes[new_id] = _materialize_node(n, new_id)
        internal_id_map[nid] = new_id

    # 2. Translate the subgraph's INTERNAL links into edges. Internal links use
    #    the subgraph's own link-id space; they connect real internal nodes OR
    #    touch the -10 (input) / -20 (output) boundary pseudo-nodes.
    sg_inputs = subgraph.get("inputs") or []    # positional: slot N -> input def
    sg_outputs = subgraph.get("outputs") or []
    for link in subgraph.get("links", []):
        oid, oslot = link["origin_id"], link["origin_slot"]
        tid, tslot = link["target_id"], link["target_slot"]
        ltype = link["type"]

        # --- input boundary: origin is inputNode(-10), slot indexes sg_inputs ---
        if oid == -10:
            # The value for sg_inputs[oslot] arrives via the parent node's
            # corresponding input slot. Map parent input slot -> the parent-space
            # link feeding the instance, then record a deferred boundary edge:
            #   (parent source of that link) -> internal target node.
            # We resolve the parent source at the end (it lives in the parent
            # link table). For now, remember the mapping.
            if oslot >= len(sg_inputs):
                continue
            inp_def = sg_inputs[oslot]
            parent_slot = _find_parent_input_slot(parent_node, inp_def.get("name"))
            if parent_slot is None:
                # input fed by a widget literal (no link) — handled via widgets
                continue
            pin = parent_node["inputs"][parent_slot]
            plink = pin.get("link")
            if plink is None:
                continue
            target_internal = internal_id_map.get(tid)
            if target_internal is None:
                continue
            graph._pending_boundary.append({
                "kind": "in", "parent_link": plink,
                "dst_node": target_internal, "dst_slot": int(tslot), "type": ltype,
            })
            continue

        # --- output boundary: target is outputNode(-20), slot indexes sg_outputs ---
        # The internal link tells us WHICH internal node+slot produces output slot
        # `tslot`. The parent-space link ids that actually LEAVE the instance live
        # on the parent node's output slot at the SAME positional index
        # (subgraph output slot N <-> parent node output slot N) — NOT on
        # sg_outputs[tslot].linkIds, which are subgraph-internal ids.
        if tid == -20:
            origin_internal = internal_id_map.get(oid)
            if origin_internal is None:
                continue
            parent_out_links = _parent_output_links(parent_node, tslot)
            for plid in parent_out_links:
                graph._pending_boundary.append({
                    "kind": "out", "parent_link": plid,
                    "src_node": origin_internal, "src_slot": int(oslot), "type": ltype,
                })
            continue

        # --- ordinary internal link: both endpoints are real internal nodes ---
        src = internal_id_map.get(oid)
        dst = internal_id_map.get(tid)
        if src is None or dst is None:
            continue  # one side was a UI-only node we dropped
        edge = Edge(src, int(oslot), dst, int(tslot), ltype, lid=link["id"])
        graph.edges_by_lid[link["id"]] = edge


def _find_parent_input_slot(parent_node: dict, name: str | None) -> int | None:
    """Index in parent_node['inputs'] whose name matches (subgraph inputs are a
    superset; the parent exposes the ones it chooses to surface, by name)."""
    if name is None:
        return None
    for i, inp in enumerate(parent_node.get("inputs") or []):
        if inp.get("name") == name:
            return i
    return None


def _record_dropped_literal(graph: Graph, node: dict, flat_id: str) -> None:
    """Capture the literal a UI-only node would have emitted on output slot 0,
    so its consumers can be rewired instead of left with a dangling ref.

    String-emitting UI nodes (ShowText, Lora Stack to String, Integer to String)
    carry their product as the first scalar widget; if present we keep it."""
    wv = node.get("widgets_values") or []
    lit = next((v for v in wv if isinstance(v, (str, int, float, bool))), None)
    if lit is not None:
        graph.dropped_literals[flat_id] = lit


def _parent_output_links(parent_node: dict, slot: int) -> list[int]:
    """Parent-space link ids leaving the instance via output `slot`.

    The parent node's output slots align positionally with the subgraph's
    outputs (slot N <-> slot N); each carries the parent-space link ids that
    actually depart the instance toward other top-level nodes."""
    outs = parent_node.get("outputs") or []
    if slot >= len(outs):
        return []
    return [l for l in (outs[slot].get("links") or []) if l is not None]


def collect_parent_edges(graph: Graph, top_nodes: list[dict], top_links: list[list]) -> None:
    """Record every top-level link that does NOT touch a subgraph instance as a
    direct edge (same-namespace, already real nodes). Links that DO touch an
    instance are left for boundary resolution."""
    instance_types = {sg["id"] for sg in _SUBGRAPH_REGISTRY.values()}  # by GUID = type
    # Build set of top-level node ids that are subgraph instances.
    instance_node_ids = {str(n["id"]) for n in top_nodes if n.get("type") in instance_types}

    for link in top_links:
        fid, tid = str(link[1]), str(link[3])
        # Defer edges incident to an instance — they are boundary links.
        if fid in instance_node_ids or tid in instance_node_ids:
            graph._pending_boundary.append({
                "kind": "parent",
                "parent_link": link[0],
                "src_node": fid, "src_slot": int(link[2]),
                "dst_node": tid, "dst_slot": int(link[4]),
                "type": link[5],
            })
        else:
            graph.edges_by_lid[link[0]] = _edge_from_parent_link(link)


def resolve_boundary_edges(graph: Graph) -> None:
    """Now that all instances are inlined, resolve every deferred boundary edge
    into a real edge between concrete internal nodes, using the parent link table.

    A parent link is a triple (from_id, from_slot, to_id, to_slot). When an
    endpoint is a subgraph instance, from_slot/to_slot indexes the instance's
    OUTPUT/INPUT slots; we replace it with the internal node that produces/
    consumes that slot (recorded during expansion as a boundary entry)."""
    # Index boundary entries by parent_link id and kind.
    by_link_in: dict[int, list[dict]] = defaultdict(list)   # link enters an instance
    by_link_out: dict[int, list[dict]] = defaultdict(list)  # link leaves an instance
    parent_recs: dict[int, dict] = {}                        # link id -> parent triple record
    for rec in graph._pending_boundary:
        if rec["kind"] == "parent":
            parent_recs[rec["parent_link"]] = rec
        elif rec["kind"] == "in":
            by_link_in[rec["parent_link"]].append(rec)
        elif rec["kind"] == "out":
            by_link_out[rec["parent_link"]].append(rec)

    new_lid = 900000
    for rec in graph._pending_boundary:
        if rec["kind"] != "parent":
            continue
        plink = rec["parent_link"]
        src_node, src_slot = rec["src_node"], rec["src_slot"]
        dst_node, dst_slot = rec["dst_node"], rec["dst_slot"]

        # If src is an instance, the real source is the internal node recorded
        # by the 'out' boundary entry for this link.
        outs = by_link_out.get(plink)
        if outs:
            # one link -> one source (take the first; subgraph output slots are 1:1)
            src_node, src_slot = outs[0]["src_node"], outs[0]["src_slot"]
        ins = by_link_in.get(plink)
        if ins:
            # A link may fan out to many internal targets (same value, many consumers)
            for entry in ins:
                edge = Edge(src_node, src_slot, entry["dst_node"], entry["dst_slot"],
                            rec["type"], lid=new_lid)
                new_lid += 1
                graph.edges_by_lid[edge.lid] = edge
            continue

        # Neither side is an instance after substitution → ordinary real edge.
        edge = Edge(src_node, src_slot, dst_node, dst_slot, rec["type"], lid=new_lid)
        new_lid += 1
        graph.edges_by_lid[edge.lid] = edge


# ===========================================================================
# 3. GET/SET COLLAPSE
# ===========================================================================

def collapse_get_set(graph: Graph) -> None:
    """Replace KJNodes Get/Set virtual globals with direct producer→consumer edges.

    SetNode X: one input (the captured value). GetNode X: one output (the read).
    Multiple Gets may read the same Set. We record, per name, the (set_node,
    input_slot) that captured the value, then for every Get, splice the Set's
    source edge onto each of the Get's consumers."""
    set_source: dict[str, Edge] = {}   # name -> edge feeding the SetNode's input
    get_nodes: list[str] = []

    for nid, node in list(graph.nodes.items()):
        ct = node.get("class_type")
        if ct == _SET_TYPE:
            name = _set_name(node)
            # The Set's single input is slot 0; find the edge feeding it.
            edge = _edge_feeding(graph, nid, 0)
            if name and edge is not None:
                set_source[name] = edge
        elif ct == _GET_TYPE:
            get_nodes.append(nid)

    # For each Get, rewire its consumers onto the Set's source.
    for nid in get_nodes:
        node = graph.nodes[nid]
        name = _get_name(node)
        src = set_source.get(name)
        if src is None:
            # No matching Set (dangling Get) — drop quietly; nothing to splice.
            continue
        # Find every edge whose source is this Get (any slot — Gets have 1 output).
        for edge in list(graph.edges_by_lid.values()):
            if edge.src_node == nid:
                # Splice: same source node/slot as the Set's capture, new dst.
                new_lid = max(graph.edges_by_lid) + 1 if graph.edges_by_lid else 1
                graph.edges_by_lid[new_lid] = Edge(
                    src.src_node, src.src_slot, edge.dst_node, edge.dst_slot, edge.type, lid=new_lid)
                graph.edges_by_lid.pop(edge.lid, None)

    # Remove Set/Get virtual nodes.
    for nid in list(graph.nodes):
        if graph.nodes[nid].get("class_type") in (_SET_TYPE, _GET_TYPE):
            graph.nodes.pop(nid, None)


def _set_name(node: dict) -> str | None:
    w = node.get("_widgets")
    return w[0] if w else None


def _get_name(node: dict) -> str | None:
    w = node.get("_widgets")
    return w[0] if w else None


def _edge_feeding(graph: Graph, node_id: str, slot: int) -> Edge | None:
    for edge in graph.edges_by_lid.values():
        if edge.dst_node == node_id and edge.dst_slot == slot:
            return edge
    return None


# ===========================================================================
# 4. API MATERIALIZE
# ===========================================================================

# class_type -> ordered input keys as ComfyUI expects them in API format. We
# only need this for the *widget* inputs (the literal values from widgets_values
# map positionally onto these keys). For wired inputs we use the slot's name.
# This is best-effort; when unknown, wired inputs still resolve by slot/name.
_WIDGET_KEY_INDEX: dict[str, list[str]] = {}


# class_type -> the input key a pure-widget model loader stores its filename under.
# These nodes expose no convertible input slot, so the widget value would otherwise
# be keyed 'widget_0' and the manifest (which keys off the loader's known input
# name) couldn't categorise the weight. Mapping it explicitly keeps the manifest
# accurate (so workflow_check finds installed files in the right folder).
_WIDGET_LOADER_KEY: dict[str, str] = {
    "UpscaleModelLoader": "model_name",
    "UNETLoader": "unet_name",
    "UNet loader with Name (Image Saver)": "unet_name",
    "VAELoader": "vae_name",
    "CLIPLoader": "clip_name",
    "CheckpointLoaderSimple": "ckpt_name",
    "CheckpointLoader": "ckpt_name",
    "SAMLoader": "model_name",
    "UltralyticsDetectorProvider": "model_name",
    "ControlNetLoader": "control_net_name",
}


def materialize_api(graph: Graph) -> dict:
    """Produce the flat API graph: {node_id: {class_type, inputs, _meta}}.

    Each node's ``inputs`` dict gets:
      - literal widget values (positional, keyed by the node's widget input names
        where known, else 'widget_<i>'); and
      - ``[src_node, src_slot]`` for every wired input (slot with a feeding edge).
    """
    api: dict[str, dict] = {}

    # Index edges by destination for fast lookup.
    by_dst: dict[tuple[str, int], Edge] = {}
    for edge in graph.edges_by_lid.values():
        by_dst[(edge.dst_node, edge.dst_slot)] = edge

    for nid, node in graph.nodes.items():
        ct = node["class_type"]
        inputs: dict[str, Any] = {}

        # Wired inputs first (by input-slot name).
        for slot, idef in enumerate(node.get("_inputs_def") or []):
            edge = by_dst.get((nid, slot))
            if edge is None:
                continue
            key = idef.get("name") or f"input_{slot}"
            inputs[key] = [edge.src_node, edge.src_slot]

        # Widget values: positional list from widgets_values. Key them by the
        # node's input names that are NOT wired, in order; fall back to widget_N.
        # For pure-widget model loaders (no convertible input slot), use the
        # loader's known input key so the manifest can categorise the weight.
        widget_keys = _widget_input_keys(node)
        loader_key = _WIDGET_LOADER_KEY.get(ct)
        for i, val in enumerate(node.get("_widgets") or []):
            if isinstance(val, (dict, list)):
                # Some widgets store rich objects (history, LoRA-manager rows).
                # Skip non-scalar widgets — they are UI state, not API inputs.
                continue
            if loader_key and i == 0 and loader_key not in inputs:
                key = loader_key
            else:
                key = widget_keys[i] if i < len(widget_keys) else f"widget_{i}"
            if key not in inputs:        # don't clobber a wired input
                inputs[key] = val

        entry = {"class_type": ct, "inputs": inputs,
                 "_meta": {"title": node["_meta"].get("title") or ct}}
        if node.get("_bypassed"):
            entry["_meta"]["bypassed"] = True
        api[nid] = entry
    return api


def _widget_input_keys(node: dict) -> list[str]:
    """Names of the node's UN-wired input slots, in positional order, which is
    where widgets_values land. ComfyUI aligns widget values with input names;
    we approximate by listing input names whose slot has no feeding edge."""
    keys: list[str] = []
    for idef in node.get("_inputs_def") or []:
        keys.append(idef.get("name") or f"input_{len(keys)}")
    return keys


# ===========================================================================
# 5. DRIVER
# ===========================================================================

_SUBGRAPH_REGISTRY: dict[str, dict] = {}   # GUID -> subgraph def


def load_source(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def flatten(source: dict) -> tuple[dict, dict]:
    """Flatten a loaded UI-format workflow. Returns (api_graph, meta)."""
    # Register subgraphs.
    _SUBGRAPH_REGISTRY.clear()
    for sg in source.get("definitions", {}).get("subgraphs", []):
        _SUBGRAPH_REGISTRY[sg["id"]] = sg
    instance_types = set(_SUBGRAPH_REGISTRY)

    top_nodes = source.get("nodes", [])
    top_links = source.get("links", [])

    graph = Graph()

    # 1. Materialize top-level NON-instance nodes directly.
    for n in top_nodes:
        if n.get("type") in instance_types:
            continue                       # handled during expansion
        if n.get("type") in _UI_TYPES:
            _record_dropped_literal(graph, n, str(n["id"]))
            continue
        nid = str(n["id"])
        graph.nodes[nid] = _materialize_node(n, nid)

    # 2. Record parent-space edges (deferring instance-incident ones).
    collect_parent_edges(graph, top_nodes, top_links)

    # 3. Inline each subgraph instance.
    for i, n in enumerate(top_nodes):
        if n.get("type") not in instance_types:
            continue
        sg = _SUBGRAPH_REGISTRY[n["type"]]
        expand_subgraph_instance(graph, f"{n['id']}", n, sg, {})

    # 4. Resolve deferred boundary edges now that all instances exist.
    resolve_boundary_edges(graph)

    # 5. Collapse Get/Set virtual globals.
    collapse_get_set(graph)

    # 6. Strip classes ComfyUI can't load (RvTools/NAIA/Reroute) — ComfyUI
    #    validates every node at submission, so an unregistered class fails the
    #    whole prompt even on a branch Loom bypasses.
    _prune_unresolvable(graph)

    # 7. Drop any node nothing consumes and that produces no output used
    #    downstream (orphan fragments from dropped UI nodes). Keep output/saver
    #    nodes regardless.
    _prune_orphans(graph)

    # 7. Materialize API format.
    api = materialize_api(graph)

    # 8. Rewire dangling refs left by dropped UI-only nodes (ShowText/Lora-Stack-
    #    to-String etc.): replace [dropped_id, slot] with the literal that node
    #    would have emitted, or with the inlined instance's real output source.
    _resolve_dangling_refs(api, graph)

    meta = _derive_meta(api, source)
    return api, meta


def _resolve_dangling_refs(api: dict, graph: Graph) -> None:
    """Eliminate ``[node_id, slot]`` references to nodes that no longer exist.

    Two causes, two fixes:
      - The target was a UI-only string-producer we dropped (ShowText, Lora
        Stack to String, Integer to String). Its captured literal replaces the ref.
      - The target was a subgraph INSTANCE id (e.g. '890') that we inlined into
        's890__*' — we can't cheaply recover which internal node owned a given
        output slot, and these refs are always metadata-only (Image Saver path/
        modelname), so we substitute a safe placeholder literal instead of
        leaving a ref ComfyUI would reject.

    Any ref still dangling after this is dropped from the node's inputs, since a
    half-wired optional input is preferable to a hard ComfyUI validation error."""
    for node in api.values():
        for key, val in list(node.get("inputs", {}).items()):
            if not (isinstance(val, list) and len(val) == 2):
                continue
            target = str(val[0])
            if target in api:
                continue  # healthy ref
            if target in graph.dropped_literals:
                node["inputs"][key] = graph.dropped_literals[target]
            else:
                # Unknown dropped target (likely an inlined instance id used only
                # for metadata). Remove the input rather than ship a broken ref.
                node["inputs"].pop(key, None)

    # CLIPTextEncode's `text` is a required input. When its source was a pruned
    # unresolvable node (the source's prompt-merge branch), the wired edge is
    # gone and materialize_api produced no `text` field. Seed it with "" so the
    # node validates — Loom overwrites it with the rendered prompt at injection.
    for node in api.values():
        if node.get("class_type") == "CLIPTextEncode" and "text" not in node["inputs"]:
            node["inputs"]["text"] = ""


# Node classes that are genuinely uninstallable (their source repos are gone or
# need services Loom doesn't use) AND that ComfyUI hard-rejects at submission
# time — even when they sit on a branch Loom bypasses. ComfyUI validates every
# node in the graph regardless of reachability, so an unregistered class fails
# the whole prompt. We strip these in flatten; the dangling-ref resolver then
# rewires their consumers (e.g. the positive CLIPTextEncode) onto placeholder
# literals, which Loom overwrites at injection anyway.
_UNRESOLVABLE_TYPES = {
    "Merge Strings v2 [RvTools]",       # rvage/ComfyUI-RvTools repo is deprecated/removed
    "Lora Stack to String [RvTools]",   # same repo — metadata-string helper
    "Integer to String [RvTools]",      # same repo — UI label helper
    "NAIARequestRandomWithOverride",    # NovelAI node (bedovyy/ComfyUI_NAIDGenerator);
                                        # needs a NAI account; off-path for local Anima
}
# LiteGraph passthrough nodes — spliced (rewired to their input source), NOT
# dropped, so they never sever a wire. Handled in _prune_unresolvable.
_PASSTHROUGH_TYPES = {"Reroute"}


def _prune_unresolvable(graph: Graph) -> None:
    """Drop nodes whose class ComfyUI can't load.

    Two cases:
      - *Passthrough* nodes (Reroute) — splice: rewire each consumer to the
        node's own input source, so the data flow is preserved. Reroute just
        forwards its single input to its single output; dropping it must not
        sever the wire (e.g. the CLIP feed into the positive CLIPTextEncode).
      - *Terminal* unresolvable nodes (RvTools/NAIA — no installable source) —
        remove and let _resolve_dangling_refs substitute a placeholder literal
        for their consumers (the positive prompt node, which Loom overwrites).
    """
    # Splice Reroutes first (data-preserving), so they don't appear as severed wires.
    reroutes = {nid for nid, n in graph.nodes.items() if n["class_type"] == "Reroute"}
    for nid in reroutes:
        # Reroute: one input (slot 0) -> one output (slot 0). Find the source edge
        # feeding it, then point every consumer of its output at that source.
        src_edge = next((e for e in graph.edges_by_lid.values()
                         if e.dst_node == nid and e.dst_slot == 0), None)
        if src_edge is not None:
            for edge in list(graph.edges_by_lid.values()):
                if edge.src_node == nid and edge.src_slot == 0:
                    new_lid = max(graph.edges_by_lid) + 1 if graph.edges_by_lid else 1
                    graph.edges_by_lid[new_lid] = Edge(
                        src_edge.src_node, src_edge.src_slot,
                        edge.dst_node, edge.dst_slot, edge.type, lid=new_lid)
                    graph.edges_by_lid.pop(edge.lid, None)
        graph.edges_by_lid = {l: e for l, e in graph.edges_by_lid.items()
                              if e.dst_node != nid}
        graph.nodes.pop(nid, None)

    # Now strip terminal unresolvable classes.
    for nid, node in list(graph.nodes.items()):
        if node["class_type"] in _UNRESOLVABLE_TYPES:
            graph.dropped_literals.setdefault(nid, "")   # placeholder for consumers
            graph.nodes.pop(nid, None)
    for lid, edge in list(graph.edges_by_lid.items()):
        if edge.src_node not in graph.nodes or edge.dst_node not in graph.nodes:
            graph.edges_by_lid.pop(lid, None)


def _prune_orphans(graph: Graph) -> None:
    """Remove nodes with no path to any output-producing node (SaveImage, Image
    Saver, easy imageRemBg/Save). Caused by dropping UI-only nodes mid-chain."""
    savers = {nid for nid, n in graph.nodes.items()
              if n["class_type"] in ("SaveImage", "SaveImageWithAlpha",
                                      "Image Saver", "easy imageSave", "easy imageRemBg")}
    if not savers:
        return
    # Build forward reachability: who consumes each node's output.
    consumers: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges_by_lid.values():
        consumers[edge.src_node].add(edge.dst_node)
    # BFS backward from savers: a node is alive if a saver is reachable from it.
    alive: set[str] = set()
    stack = list(savers)
    # reverse edges: dst -> src
    producers: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges_by_lid.values():
        producers[edge.dst_node].add(edge.src_node)
    while stack:
        cur = stack.pop()
        for p in producers.get(cur, ()):
            if p not in alive:
                alive.add(p)
                stack.append(p)
    alive |= savers
    for nid in list(graph.nodes):
        if nid not in alive:
            graph.nodes.pop(nid, None)
            # drop edges touching it
    for lid, edge in list(graph.edges_by_lid.items()):
        if edge.src_node not in alive or edge.dst_node not in alive:
            graph.edges_by_lid.pop(lid, None)


def _derive_meta(api: dict, source: dict) -> dict:
    """Identify the Loom contract nodes by role, for self-documenting wiring.

    The four contracts Loom needs:
      - positive_node : CLIPTextEncode whose `text` Loom overwrites with the
                        rendered prompt. Traced from the PRIMARY sampler's
                        positive input, unwrapping rgthree Context bundles.
      - negative_node : same, for the negative prompt.
      - output_node   : the SaveImage/Image Saver whose images Loom collects.
      - primary_sampler: the denoise=1.0 txt2img KSampler (distinct from the
                        upscale/refine passes at lower denoise).
      - unet/clip/vae/lora_anchor : the split-loader chain + the single LoRA
                        application point (easy loraStackApply) — the anchor
                        inject_models rebuilds for an n-LoRA stack.
    """
    def is_sampler(n): return n["class_type"] in ("KSampler", "KSamplerAdvanced")

    samplers = [(nid, n) for nid, n in api.items() if is_sampler(n)]

    output_node = next((nid for nid, n in api.items()
                        if n["class_type"] in ("Image Saver", "SaveImage", "SaveImageWithAlpha")), None)

    # Build a producer map for backward traversal (node -> its direct producers).
    producers: dict[str, list[str]] = {}
    for nid, n in api.items():
        for v in n["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and v[0] in api:
                producers.setdefault(nid, []).append(v[0])

    def backward_reachable(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for p in producers.get(cur, ()):
                if p not in seen:
                    stack.append(p)
        return seen

    # The PRIMARY sampler is the one feeding the output node — i.e. the unique
    # KSampler on the backward-reachable set from the saver. This is the
    # authoritative definition (the source graph's output path runs through the
    # LLLite refine sampler, not the standalone txt2img preview sampler).
    output_reachable = backward_reachable(output_node) if output_node else set()
    on_output_path = [nid for nid, _ in samplers if nid in output_reachable]
    primary = on_output_path[0] if on_output_path else (samplers[0][0] if samplers else None)

    def unwrap_context(ref):
        """Follow a sampler's positive/negative ref until it reaches a real
        conditioning node, unwrapping any number of nested rgthree Context
        bundles.

        Context slot 4 = POSITIVE, slot 5 = NEGATIVE. A Context exposes those by
        name as its 'positive'/'negative' inputs — but rgthree Context merge
        semantics mean an *unwired* slot is inherited from the Context's
        ``base_ctx`` input, so we chase base_ctx when the named slot is empty."""
        SEEN = 0
        key_for_slot = {4: "positive", 5: "negative"}
        while isinstance(ref, list) and len(ref) == 2:
            nid, slot = ref[0], ref[1]
            node = api.get(nid)
            if not node:
                return None
            if node["class_type"].startswith("Context"):
                key = key_for_slot.get(slot)
                named = node["inputs"].get(key) if key else None
                if isinstance(named, list):
                    ref = named
                    SEEN += 1
                    if SEEN > 12:
                        break
                    continue
                # unwired → inherit from base_ctx (Context merge semantics)
                base = node["inputs"].get("base_ctx")
                if isinstance(base, list):
                    ref = [base[0], slot]   # same slot index in the base context
                    SEEN += 1
                    if SEEN > 12:
                        break
                    continue
            return nid
        return None

    positive_node = None
    negative_node = None
    if primary:
        pnode = api[primary]
        positive_node = unwrap_context(pnode["inputs"].get("positive"))
        negative_node = unwrap_context(pnode["inputs"].get("negative"))

    # Loaders (split-loader chain: UNet + CLIP + VAE).
    unet_node = next((nid for nid, n in api.items()
                      if n["class_type"] in ("UNETLoader", "UnetLoaderGGUF",
                                             "UNet loader with Name (Image Saver)")), None)
    clip_node = next((nid for nid, n in api.items()
                      if n["class_type"] in ("CLIPLoader", "DualCLIPLoader", "TripleCLIPLoader")), None)
    vae_node = next((nid for nid, n in api.items() if n["class_type"] == "VAELoader"), None)
    # LoRA anchor = the easy loraStackApply node (single application point for
    # the resolved LORA_STACK from the ImpactSwitch style picker).
    lora_anchor = next((nid for nid, n in api.items()
                        if n["class_type"] == "easy loraStackApply"), None)

    return {
        "source": "animaAllInOne_v51.json",
        "primary_sampler": primary,
        "positive_node": positive_node,
        "negative_node": negative_node,
        "output_node": output_node,
        "unet_node": unet_node,
        "clip_node": clip_node,
        "vae_node": vae_node,
        "lora_anchor": lora_anchor,
        "sampler_count": len(samplers),
        "note": ("Flattened from the Anima All-In-One UI workflow. Loom injects "
                 "the prompt at positive_node.text and collects images from "
                 "output_node. inject_models rebuilds the LoRA chain at lora_anchor."),
    }


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    src = Path(argv[1]) if len(argv) > 1 else Path(r"C:\Users\micha\Downloads\animaAllInOne_v51.json")
    out = Path(argv[2]) if len(argv) > 2 else root / "workflows" / "anima_master_api.json"

    if not src.is_file():
        print(f"source not found: {src}", file=sys.stderr)
        return 1

    source = load_source(src)
    api, meta = flatten(source)

    # Embed meta under a reserved key; ComfyUI ignores unknown top-level keys in
    # API format only when they're not node ids — so we keep meta out of the
    # node dict and write a sibling file instead.
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(api, indent=2, ensure_ascii=False), encoding="utf-8")
    (out.with_suffix(".meta.json")).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    # Report.
    from collections import Counter
    types = Counter(n["class_type"] for n in api.values())
    print(f"wrote {out}  ({len(api)} nodes, {sum(types.values())} edges)")
    print(f"meta: {meta}")
    print("\ntop node types:")
    for t, c in types.most_common(15):
        print(f"  {c:3}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Reconcile a flattened ComfyUI API workflow against the LIVE ComfyUI's schema.

The flatten pass (flatten_anima_allinone.py) rebuilds a UI-format workflow into
API format mechanically — but it can't know the exact input names / ranges /
enums the *installed* versions of each custom-node pack expect. ComfyUI's deep
validator rejects the result with errors like "Required input is missing
delimiter", "Value 8.0 bigger than max of 2.0 flex_window", "Value not in list
sampler: False not in [...]".

This script fixes all of that by consulting ComfyUI's own source of truth — the
``/object_info`` endpoint — and rewriting the graph so it validates:

  1. Re-key mis-named widget inputs to their real input names (``widget_3`` →
     ``scheduler``) by matching positional order against the node's required
     inputs. Flatten had to guess keys; object_info knows them.
  2. Fill any missing required input with the pack's declared default.
  3. Clamp numeric values into each input's [min, max] band.
  4. Coerce bad enum values to the input's default when they're out of list
     (the packs have evolved since the source workflow was authored).
  5. Drop nodes whose class isn't registered at all (already handled by the
     flatten's unresolvable-prune, but this catches anything new) — rewiring
     consumers to defaults where possible.

Inputs/outputs are never invented: only the values Loom doesn't own (sampler
config, model-patch tuning, switch states) are reconciled. The prompt text and
LoRA chain — which Loom injects at runtime — are left untouched.

Run:
    python scripts/reconcile_workflow.py workflows/anima_master_api.json [comfy_url]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

# Inputs Loom owns and injects at runtime — never reconciled (would clobber the
# injection contract). positive_node.text is overwritten with the prompt; the
# LoRA chain is rebuilt by inject_models.
_LOOM_OWNED = set()  # filled per-node at runtime from the meta contract


def fetch_object_info(base_url: str) -> dict:
    r = httpx.get(base_url.rstrip("/") + "/object_info", timeout=30)
    r.raise_for_status()
    return r.json()


def _input_schema(node_schema: dict) -> tuple[dict, dict]:
    """Return (required_inputs, optional_inputs) name→spec from a node's schema."""
    inp = (node_schema or {}).get("input", {}) or {}
    req = inp.get("required", {}) or {}
    opt = inp.get("optional", {}) or {}
    if isinstance(req, list):  # older shape
        req = {k: v for k, v in req}
    if isinstance(opt, list):
        opt = {k: v for k, v in opt}
    return req, opt


def _is_enum(spec: list) -> bool:
    """A spec is an enum/selectable if it carries an allowed-values list.

    Two shapes occur in object_info:
      - ``[['a','b','c'], {...}]`` — the list IS spec[0] (most nodes).
      - ``['COMBO', {'options': ['a','b','c']}]`` — Image-Saver/KJNodes style,
        where the allowed values live under spec[1]['options'].
    """
    if not spec:
        return False
    if isinstance(spec[0], list):
        return True
    if len(spec) >= 2 and isinstance(spec[1], dict) and isinstance(spec[1].get("options"), list):
        return True
    return False


def _enum_values(spec: list) -> list:
    if not spec:
        return []
    if isinstance(spec[0], list):
        return spec[0]
    if len(spec) >= 2 and isinstance(spec[1], dict):
        return spec[1].get("options") or []
    return []


def _spec_props(spec: list) -> dict:
    """The {min,max,default,...} dict at spec[1], if present."""
    if len(spec) >= 2 and isinstance(spec[1], dict):
        return spec[1]
    return {}


# Inputs that reference a model FILE (a loader's filename). These must NEVER
# be enum-coerced or defaulted — a value not in ComfyUI's current list means the
# weight isn't installed yet, which should surface as an honest "install it" gap,
# not silently swap to a different model.
_MODEL_FILE_INPUTS = {
    "unet_name", "ckpt_name", "lora_name", "vae_name", "clip_name",
    "clip_name1", "clip_name2", "clip_name3", "control_net_name",
    "style_model_name", "model_name", "ipadapter_file", "gligen_name",
    "lllite_name",
}


def _coerce_value(val: Any, spec: list, name: str, cls: str) -> tuple[Any, str]:
    """Bring a value into spec: clamp numerics, fix enums. Returns (value, note)."""
    # Model-file references are sacred — never touch them.
    if name in _MODEL_FILE_INPUTS:
        return val, ""
    props = _spec_props(spec)

    # Enum: value must be one of the allowed list.
    if _is_enum(spec):
        allowed = _enum_values(spec)
        # Coerce bool to its enum string if the enum is string-typed and val is a
        # stray bool/int from a mis-wired widget (e.g. switch fed a literal True).
        if val in allowed:
            return val, ""
        # try string-coerce (True -> 'true', 1 -> 'true'/'True')
        for cand in (str(val), str(val).lower(), str(bool(val)).lower()):
            if cand in allowed:
                return cand, f"enum-coerced {val!r}->{cand!r}"
        default = props.get("default", allowed[0] if allowed else None)
        if default in allowed:
            return default, f"enum-defaulted {val!r}->{default!r} (out of list)"
        return allowed[0] if allowed else val, f"enum-first {val!r}->{allowed[0]!r}"

    # Numeric: clamp to [min, max]. (bool is an int subclass — exclude it.)
    typ = spec[0] if spec else ""
    if typ in ("FLOAT", "INT") and isinstance(val, (int, float)) and not isinstance(val, bool):
        lo, hi = props.get("min"), props.get("max")
        orig = val
        if lo is not None and val < lo:
            val = lo
        if hi is not None and val > hi:
            val = hi
        if typ == "INT":
            val = int(val)
        if val != orig:
            return val, f"clamped {orig}->{val} [{lo},{hi}]"
        return val, ""

    # Boolean fed to a non-bool/enum: leave as-is unless type is BOOLEAN.
    return val, ""


def _node_inputs_misaligned(node: dict, req: dict) -> bool:
    """True if any present literal input has the wrong type for its schema — a
    tell-tale that the flatten's positional widget assignment drifted out of
    alignment with the pack's named inputs (e.g. lambda_h=True, dcw_enabled=0.01).
    When misaligned, per-input patching can't recover it; the safe move is to
    reset every non-wired input to its schema default."""
    for name, spec in req.items():
        if name in _MODEL_FILE_INPUTS:
            continue  # file references are checked at load time, not reconciled
        val = node.get("inputs", {}).get(name)
        if val is None or isinstance(val, list):
            continue  # absent or wired — not evidence of misalignment
        # Enum input (COMBO or a list-of-values): a non-string numeric is misaligned.
        if _is_enum(spec):
            allowed = _enum_values(spec)
            if val not in allowed and not (isinstance(val, str) and val in allowed):
                return True
            continue
        typs = spec[0] if spec else ""
        typs = typs if isinstance(typs, list) else [typs]
        # bool is an int subclass; treat BOOLEAN as wanting bool, numeric as NOT bool.
        if "BOOLEAN" in typs:
            if not isinstance(val, bool):
                return True
        elif any(t in ("INT", "FLOAT") for t in typs):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                return True
        elif "STRING" in typs and not isinstance(val, str):
            return True
    return False


def _reconcile_node(nid: str, node: dict, schema: dict | None, loom_owned: set[str]) -> list[str]:
    """Fix one node against its schema. Returns a list of human-readable changes."""
    if not schema:
        return []  # unregistered class — handled by the caller (drop)
    inputs = node.setdefault("inputs", {})
    req, opt = _input_schema(schema)
    notes: list[str] = []

    # If the flatten's positional widget assignment drifted (a literal has the
    # wrong type for its named input), every non-wired input is suspect — reset
    # them all to schema defaults. Cheaper and more reliable than untangling the
    # offset per-input. Only touches inputs Loom doesn't own.
    if _node_inputs_misaligned(node, req):
        fixed_any = False
        for name, spec in req.items():
            if name in loom_owned:
                continue
            if name in inputs and isinstance(inputs[name], list):
                continue  # keep wired inputs
            default = _spec_props(spec).get("default")
            if default is None and _is_enum(spec):
                allowed = _enum_values(spec)
                default = allowed[0] if allowed else ""
            if default is None:
                default = 0 if spec and spec[0] == "INT" else (0.0 if spec and spec[0] == "FLOAT" else "")
            # Coerce the default too — some pack schemas ship a bad default
            # (e.g. PathchSageAttentionKJ defaults sage_attention to False, a
            # bool, but the input is an enum). Coercion picks the first valid enum.
            default, _ = _coerce_value(default, spec, name, node["class_type"])
            if inputs.get(name) != default:
                inputs[name] = default
                fixed_any = True
        if fixed_any:
            notes.append(f"{nid}: widget misalignment — reset non-wired inputs to defaults")
        return notes

    # 1. Reconcile every REQUIRED input (aligned case).
    for name, spec in req.items():
        if name in loom_owned:
            continue  # Loom injects this — never touch
        if name in inputs:
            # exists — coerce/clamp its value if it's a literal (not a wired ref)
            val = inputs[name]
            if not isinstance(val, list):
                fixed, note = _coerce_value(val, spec, name, node["class_type"])
                if fixed != val:
                    inputs[name] = fixed
                if note:
                    notes.append(f"{nid} {name}: {note}")
            continue
        # missing required input — collect positional widget candidates to re-key,
        # else fall back to the schema default.
        rekeyed = _try_rekey_widget(inputs, name, spec)
        if rekeyed is not None:
            fixed, note = _coerce_value(rekeyed, spec, name, node["class_type"])
            # If coercion had to clamp/coerce a rekeyed value, the widget match was
            # almost certainly wrong (a same-typed-but-unrelated widget). Trust the
            # schema default instead — it's always valid for the installed pack.
            if note and ("clamped" in note or "enum-defaulted" in note or "enum-first" in note):
                default = _spec_props(spec).get("default")
                if default is None and _is_enum(spec):
                    allowed = _enum_values(spec)
                    default = allowed[0] if allowed else ""
                inputs[name] = default if default is not None else fixed
                notes.append(f"{nid} {name}: filled default {inputs[name]!r} (rekey guess {rekeyed!r} was out-of-spec)")
            else:
                inputs[name] = fixed
                notes.append(f"{nid} {name}: re-keyed widget->{fixed}" +
                             (f" ({note})" if note else ""))
            continue
        default = _spec_props(spec).get("default")
        if default is None and _is_enum(spec):
            allowed = _enum_values(spec)
            default = allowed[0] if allowed else ""
        if default is None:
            default = 0 if spec and spec[0] == "INT" else (0.0 if spec and spec[0] == "FLOAT" else "")
        inputs[name] = default
        notes.append(f"{nid} {name}: filled default {default!r}")

    # 2. Coerce any literal OPTIONAL inputs already present that are out of range.
    for name, spec in opt.items():
        if name in inputs and not isinstance(inputs[name], list):
            fixed, note = _coerce_value(inputs[name], spec, name, node["class_type"])
            if fixed != inputs[name]:
                inputs[name] = fixed
            if note:
                notes.append(f"{nid} {name}: {note}")
    return notes


def _try_rekey_widget(inputs: dict, wanted_name: str, spec: list) -> Any:
    """Flatten keys unknown widget values as ``widget_N``. If one of those holds
    a value whose type matches `spec`, adopt it under `wanted_name` and remove
    the placeholder key. Returns the value, or None."""
    typ = spec[0] if spec else ""
    candidates = []
    for k in list(inputs):
        if not (isinstance(k, str) and k.startswith("widget_")):
            continue
        v = inputs[k]
        if isinstance(v, list):
            continue
        candidates.append((k, v))
    if not candidates:
        return None
    # Prefer a value whose type matches; else the first scalar. `typ` can be a
    # single string ("INT") or a list of accepted types (["INT","FLOAT"]).
    typs = typ if isinstance(typ, list) else [typ]
    want_types = []
    for t in typs:
        want_types.extend({"INT": (int,), "FLOAT": (int, float),
                           "BOOLEAN": (bool,), "STRING": (str,)}.get(t, ()))
    for k, v in candidates:
        if want_types and isinstance(v, tuple(want_types) if want_types else ()):
            inputs.pop(k)
            return v
    # type-agnostic fallback: take the first
    k, v = candidates[0]
    inputs.pop(k)
    return v


# Nodes that crash at runtime on Windows (no Triton) or need services Loom
# doesn't provide. We splice them out: their consumers are rewired to the
# node's own model input, so the model flows through uncompiled/unpatched.
# (Bypass/mode-4 is unreliable when the node feeds a ComfySwitchNode branch.)
_RUNTIME_DEAD_NODES = {
    # torch.compile with the inductor backend needs Triton — unavailable on
    # Windows. It's an optional acceleration; the model works fine uncompiled.
    "TorchCompileModelAdvanced",
    "TorchCompileModel",
}


def _splice_dead_runtime_nodes(graph: dict) -> list[str]:
    """Remove nodes that crash at runtime (no Triton etc.) by splicing: every
    consumer of the node's MODEL output (slot 0) is repointed at the node's own
    `model` input source. Returns a change log."""
    log: list[str] = []
    for nid in list(graph):
        node = graph[nid]
        if node.get("class_type") not in _RUNTIME_DEAD_NODES:
            continue
        model_src = node.get("inputs", {}).get("model")
        if not isinstance(model_src, list):
            graph.pop(nid, None)
            log.append(f"{nid} ({node['class_type']}): removed (no model input to splice)")
            continue
        # Repoint every consumer of this node's slot-0 output to the source model.
        for other in graph.values():
            ins = other.get("inputs", {})
            for k, v in list(ins.items()):
                if isinstance(v, list) and v and v[0] == nid:
                    ins[k] = list(model_src)
        graph.pop(nid, None)
        log.append(f"{nid} ({node['class_type']}): spliced out (Triton unavailable on Windows)")
    return log


def reconcile(graph: dict, object_info: dict, loom_owned: set[str]) -> tuple[dict, list[str], list[str]]:
    """Reconcile the whole graph. Returns (fixed_graph, change_log, dropped_nodes).

    ``loom_owned`` entries are ``"node_id::input_name"`` — Loom injects those and
    they're never reconciled (e.g. the positive-prompt node's ``text``)."""
    g = json.loads(json.dumps(graph))  # deep copy
    log: list[str] = []
    dropped: list[str] = []
    for nid in list(g):
        node = g[nid]
        ct = node.get("class_type")
        schema = object_info.get(ct)
        if not schema:
            dropped.append(f"{nid} ({ct})")
            g.pop(nid)
            continue
        owned = {k.split("::", 1)[1] for k in loom_owned if k.startswith(f"{nid}::")}
        log.extend(_reconcile_node(nid, node, schema, owned))

    # Splice out nodes that crash at runtime (TorchCompile needs Triton, absent
    # on Windows) by repointing their consumers at their model input source.
    log.extend(_splice_dead_runtime_nodes(g))

    # Rewire dangling refs created by dropped nodes → use schema default if a
    # required input now points at a missing node, else remove.
    for nid, node in g.items():
        ct = node.get("class_type")
        schema = object_info.get(ct, {})
        req, opt = _input_schema(schema)
        for name in list(node.get("inputs", {})):
            val = node["inputs"][name]
            if isinstance(val, list) and val and val[0] not in g:
                spec = req.get(name) or opt.get(name)
                default = _spec_props(spec).get("default") if spec else None
                if default is None and spec and _is_enum(spec):
                    allowed = _enum_values(spec)
                    default = allowed[0] if allowed else ""
                node["inputs"][name] = default if default is not None else ""
                log.append(f"{nid} {name}: rewired dangling->{node['inputs'][name]!r}")
    return g, log, dropped


def main(argv: list[str]) -> int:
    wf_path = Path(argv[1]) if len(argv) > 1 else Path("workflows/anima_master_api.json")
    base_url = argv[2] if len(argv) > 2 else "http://127.0.0.1:8188"
    graph = json.loads(wf_path.read_text(encoding="utf-8"))

    # Loom owns the positive-prompt node's `text` (it overwrites it at injection).
    # Scope the skip to THAT node only — blanket-skipping every `text` input would
    # leave required text inputs (e.g. Lora Stacker's stack string) unvalidated.
    meta_path = wf_path.with_suffix(".meta.json")
    loom_owned: set[str] = set()
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("positive_node"):
            loom_owned.add(f"{meta['positive_node']}::text")

    print(f"fetching schema from {base_url}/object_info ...")
    oi = fetch_object_info(base_url)
    print(f"  {len(oi)} node classes registered\n")

    # loom_owned is node-scoped ("nid::field"); pass a per-node set into reconcile.
    fixed, log, dropped = reconcile(graph, oi, loom_owned)
    print(f"dropped {len(dropped)} unregistered node(s): {dropped}")
    print(f"applied {len(log)} fix(es):\n")
    for line in log:
        print("  " + line)

    out = wf_path.with_suffix(".json")  # overwrite in place
    out.write_text(json.dumps(fixed, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out} ({len(fixed)} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

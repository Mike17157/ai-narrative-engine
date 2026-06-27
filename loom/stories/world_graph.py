"""The global **world graph** — ONE cross-story doc of the universe's locations and the
edges between entities (character↔character relationships + location↔location scene
connections / containment).

It's the *canon* a per-thread State doc forks its drift from. Stored as a single JSON
blob in libSQL (lorebook_store.get_doc/put_doc), mutated by the SAME registered scripts
the per-story graph uses — `add_location`, `set_location_area`, `connect_scenes`,
`set_relationship`, … — so there's no second mutation engine. Characters themselves stay
file-backed (configs/characters/*.yaml); the graph references them by id/name.

Lazy on purpose: one blob, edited wholesale. When (if) cross-story queries arrive
("which stories use this location"), this is the contained seam to promote to edge rows.
"""
from __future__ import annotations

from pathlib import Path

KEY = "_world"


def _empty() -> dict:
    return {"locations": [], "relationships": [], "connections": []}


def load(root: Path) -> dict:
    from ..server.services import lorebook_store as LS
    doc = LS.get_doc(root, KEY)
    return doc if isinstance(doc, dict) else _empty()


def save(root: Path, doc: dict) -> dict:
    from ..server.services import lorebook_store as LS
    return LS.put_doc(root, KEY, doc)


def all_functions() -> list:
    """Every registered script, exposed as a callable against this doc. No lorebook keyword
    gating — the global graph is the authoring surface for the whole universe, so all of
    them are always available."""
    from .graph_ops import GraphFunction
    from . import scripts as S
    return [GraphFunction(name=sd.name, describe=sd.describe, params=sd.params,
                          keywords=sd.keywords, writes=sd.writes, impl=sd.impl, kind="doc")
            for sd in S.REGISTRY.values()]


def apply(root: Path, calls: list, functions: list | None = None) -> tuple[dict, list]:
    """Run script CALLS (`[{fn, params}]`) against the global graph and persist the result.
    Returns (new_doc, log). Never raises — a bad call is skipped and logged (graph_ops)."""
    from . import graph_ops as GO
    new_doc, log = GO.apply_ops(load(root), calls, functions or all_functions())
    save(root, new_doc)
    return new_doc, log

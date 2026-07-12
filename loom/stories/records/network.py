"""Canonical story-card network and bounded scene-context query.

This is a projection, not a second persistence model. ``Story`` remains the
authored source of truth, character cards remain portable, and a play session's
world state remains its mutable overlay.
"""

from __future__ import annotations

from typing import Any


def _id(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def _add(nodes: dict[str, dict], kind: str, value: str, **data: Any) -> str:
    node_id = _id(kind, value)
    nodes[node_id] = {"id": node_id, "kind": kind, "key": value, **data}
    return node_id


def build_network(story: Any, world: dict | None = None) -> dict:
    """Project an authored Story plus one playthrough overlay into cards and links.

    All links use stable, namespaced ids. Unknown references are deliberately
    skipped: the Story validator is the write boundary, while this read model
    remains safe for a partially-authored/imported story.
    """
    data = story.model_dump() if hasattr(story, "model_dump") else dict(story or {})
    world = world or {}
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    story_key = str(data.get("key") or data.get("id") or data.get("name") or "story")
    root = _add(nodes, "story", story_key, title=data.get("name", ""),
                premise=data.get("premise", ""), tone=data.get("tone", ""),
                themes=data.get("themes") or [])

    characters = set()
    for member in data.get("cast") or []:
        key = str(member.get("character") or "")
        if not key:
            continue
        characters.add(key)
        char = _add(nodes, "character", key, primary=bool(member.get("primary")),
                    home=member.get("home") or "")
        edges.append({"kind": "contains", "from": root, "to": char})
        if member.get("home"):
            edges.append({"kind": "home", "from": char, "to": _id("location", member["home"])})

    for loc in data.get("locations") or []:
        key = str(loc.get("id") or "")
        if not key:
            continue
        node = _add(nodes, "location", key, title=loc.get("name", ""),
                    description=loc.get("description", ""), parent=loc.get("parent", ""))
        edges.append({"kind": "contains", "from": root, "to": node})
        if loc.get("parent"):
            edges.append({"kind": "inside", "from": node, "to": _id("location", loc["parent"])})

    relationships: dict[str, dict] = {}
    for rel in data.get("relationships") or []:
        key, source, target = str(rel.get("id") or ""), str(rel.get("source") or ""), str(rel.get("target") or "")
        if not key or source not in characters or target not in characters:
            continue
        # This is an EDGE, never a card. Character cards hold the people; an
        # edge records only a story-local, cross-character fact with provenance.
        relationships[key] = {"id": f"edge:{key}", "kind": "cross_character",
                              "from": _id("character", source), "to": _id("character", target),
                              "relation": rel.get("nature", ""),
                              "fact": rel.get("note", "") or rel.get("dynamic", ""),
                              "provenance": "authored"}
        edges.append(relationships[key])

    for arc in data.get("arcs") or []:
        key = str(arc.get("id") or "")
        if not key:
            continue
        node = _add(nodes, "arc", key, title=arc.get("name", ""), owner=arc.get("owner", ""),
                    premise=arc.get("premise", ""), conditions=arc.get("conditions") or [])
        edges.append({"kind": "contains", "from": root, "to": node})
        for char in arc.get("cast") or []:
            if char in characters:
                edges.append({"kind": "involves", "from": node, "to": _id("character", char)})
        for rel in arc.get("pressures") or []:
            if rel in relationships:
                # A hyperedge in compact form: the arc pressures this direct
                # character-to-character link. It intentionally points to the
                # edge id rather than inventing a relationship card.
                edges.append({"kind": "pressures", "from": node, "link": relationships[rel]["id"]})

    # Runtime nodes are per-session evidence. They never mutate authored cards.
    for name, entity in (world.get("entities") or {}).items():
        if name not in characters:
            continue
        node = _add(nodes, "state", name, location=entity.get("location", ""),
                    mood=entity.get("mood", ""), status=entity.get("status", ""))
        edges.append({"kind": "state_of", "from": node, "to": _id("character", name)})
    for i, promise in enumerate(world.get("promises") or []):
        if promise.get("status") == "open" and promise.get("setup"):
            node = _add(nodes, "promise", str(i), text=promise["setup"], step=promise.get("step", 0))
            edges.append({"kind": "contains", "from": root, "to": node})
    for i, detail in enumerate(world.get("details") or []):
        if not detail.get("text"):
            continue
        node = _add(nodes, "fact", str(i), text=detail["text"], step=detail.get("step", 0), owner=detail.get("name", ""))
        edges.append({"kind": "contains", "from": root, "to": node})
        if detail.get("name") in characters:
            edges.append({"kind": "about", "from": node, "to": _id("character", detail["name"])})
    for residual in world.get("residuals") or []:
        if not residual.get("active", True) or not residual.get("id"):
            continue
        node = _add(nodes, "residual", str(residual["id"]), subject=residual.get("subject", ""),
                    predicate=residual.get("predicate", ""), object=residual.get("object", ""),
                    evidence=residual.get("evidence") or [], status=residual.get("status", "inferred"),
                    requires_source=bool(residual.get("requires_source")))
        edges.append({"kind": "contains", "from": root, "to": node})
        if residual.get("subject") in characters:
            edges.append({"kind": "about", "from": node, "to": _id("character", residual["subject"])})
    return {"version": 1, "story": root, "nodes": list(nodes.values()), "edges": edges}


def scene_context(network: dict, *, location: str = "", present: list[str] | None = None,
                  mentioned: list[str] | None = None, query: str = "", limit: int = 12) -> dict:
    """Return the deterministic, bounded context packet for a scene.

    Selection is structural first (scene people, place, their bonds, active arc),
    then lexical for runtime facts/promises. ``query`` cannot pull global cards
    into a scene by itself.
    """
    by_id = {n["id"]: n for n in network.get("nodes") or []}
    # Preserve caller order: POV/present order is meaningful in prompt assembly.
    focus_list = list(dict.fromkeys(str(x) for x in (present or []) + (mentioned or []) if x))
    focus = set(focus_list)
    tokens = {t.lower() for t in query.split() if len(t) > 2}
    rels, arcs, facts, promises, residuals = [], [], [], [], []
    edges = network.get("edges") or []
    for edge in edges:
        if edge["kind"] == "cross_character" and {edge["from"].removeprefix("character:"), edge["to"].removeprefix("character:")} <= focus:
            rels.append(edge)
    for node in by_id.values():
        kind = node["kind"]
        # A scene receives bonds *between* its focused people. An off-screen
        # character's bond is not scene context merely because one endpoint is
        # present; the caller can add that person to ``mentioned`` deliberately.
        if kind == "arc" and (not focus or any(e["kind"] == "involves" and e["from"] == node["id"] and e["to"] == _id("character", c) for e in edges for c in focus)):
            arcs.append(node)
        elif kind in ("fact", "promise"):
            text = str(node.get("text") or "").lower()
            score = len(tokens & set(text.split())) + (1 if node.get("owner") in focus else 0)
            if score or not tokens:
                (facts if kind == "fact" else promises).append((score, node))
        elif kind == "residual" and (node.get("subject") in focus or not focus):
            text = " ".join(str(node.get(k) or "") for k in ("subject", "predicate", "object"))
            score = len(tokens & set(text.lower().split())) + (1 if node.get("subject") in focus else 0)
            if score or not tokens:
                residuals.append((score, node))
    facts = [n for _, n in sorted(facts, key=lambda x: (x[0], x[1].get("step", 0)), reverse=True)][:3]
    promises = [n for _, n in sorted(promises, key=lambda x: (x[0], -x[1].get("step", 0)), reverse=True)][:2]
    residuals = [n for _, n in sorted(residuals, key=lambda x: x[0], reverse=True)][:3]
    # Apply one real context budget after structural selection. Place and people have
    # priority; all optional material is then truncated in the documented order.
    remaining = max(0, int(limit))
    location_card = by_id.get(_id("location", location))
    if location_card:
        remaining -= 1

    def take(items: list[dict]) -> list[dict]:
        nonlocal remaining
        selected = items[:max(0, remaining)]
        remaining -= len(selected)
        return selected

    packet = {"location": location_card,
              "characters": take([by_id[_id("character", c)] for c in focus_list if _id("character", c) in by_id]),
              "links": take(rels), "arcs": take(arcs[:2]),
              "state": take([n for n in by_id.values() if n["kind"] == "state" and n["key"] in focus]),
              "residuals": take(residuals), "facts": take(facts), "open_promises": take(promises)}
    packet["budget"] = {"limit": limit, "selected": int(limit) - remaining}
    return packet

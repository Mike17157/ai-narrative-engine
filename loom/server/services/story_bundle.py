"""Portable story bundles — one JSON document holding EVERYTHING a story owns:

- the authored aggregate (story row + its child tables, via story_store.load_story)
- the story-scoped library cards (global_characters stamped with its key) and the personas
  it references (default_personas)
- every session anchored to it, verbatim: messages, state doc, prologue, plus its beats
  (the permanent chronicle) and its play-through cards + evidence history
- the image assets those records point at (backgrounds, character avatars/refs, persona
  avatars), base64-encoded so the bundle is a single self-contained file

``import_story`` reverses this into ANY root. Collisions are remapped, never clobbered:
an existing story key gets a ``_2``/``_3`` suffix (same convention as story.create), session
ids and global card/persona keys are reused when identical and minted fresh when they differ,
and every ``/api/stories/<old>/`` URL inside the payload is rewritten to the new key.

Portrait candidate galleries (``portraits/<key>/…``) are deliberately NOT exported — they are
re-generatable scratch and would balloon the file; only the chosen avatar + reference ride along.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

from . import card_store, story_sessions, story_store

FORMAT = "loom-story-bundle"
VERSION = 1

_SAFE = re.compile(r"[^\w\-]+")


def _safe(s) -> str:
    return _SAFE.sub("", str(s or ""))


def _mint(base: str, taken) -> str:
    """First free ``<base>_2``, ``<base>_3``… (the story.create suffix convention)."""
    n = 2
    cand = f"{base}_{n}"
    while cand in taken:
        n += 1
        cand = f"{base}_{n}"
    return cand


def _b64(p: Path) -> str | None:
    try:
        if p.is_file():
            return base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        pass
    return None


def _same_card(a: dict, b: dict) -> bool:
    """Payload equality ignoring the `fields.story` provenance stamp (which differs by owner)."""
    def strip(d: dict) -> dict:
        d = dict(d or {})
        fields = dict(d.get("fields") or {})
        fields.pop("story", None)
        d["fields"] = fields
        return d
    return strip(a) == strip(b)


# ── Export ─────────────────────────────────────────────────────────────────────

def export_story(root: Path, key: str) -> dict:
    """Bundle one story and everything copackaged with it. Raises KeyError if absent."""
    key = _safe(key)
    loaded = story_store.load_story(root, key)
    if loaded is None:
        raise KeyError(key)
    story, characters = loaded

    library = card_store.characters_for_story(root, key)
    persona_keys = {p for p in (story.get("default_personas") or []) if isinstance(p, str)}
    personas = {k: v for k, v in card_store.load_personas(root).items() if k in persona_keys}

    con = story_sessions._conn(root)          # same db; ensures the sessions schema exists
    sessions = []
    srows = con.execute(
        "SELECT sid, character, messages, graph, draft, lorebooks, world_state, state, prologue,"
        " updated FROM sessions WHERE story_key=? ORDER BY updated, sid", (key,)).fetchall()
    for sid, character, messages, graph, draft, lorebooks, world_state, state, prologue, updated in srows:
        beats = con.execute(
            "SELECT seq, step, ts, text FROM session_beats WHERE sid=? ORDER BY seq",
            (sid,)).fetchall()
        play = con.execute(
            "SELECT kind, card_key, foundation, current, updated FROM play_cards"
            " WHERE story_key=? AND session_id=? ORDER BY kind, card_key", (key, sid)).fetchall()
        history = con.execute(
            "SELECT kind, card_key, seq, turn, event, evidence, created FROM card_history"
            " WHERE story_key=? AND session_id=? ORDER BY kind, card_key, seq",
            (key, sid)).fetchall()
        sessions.append({
            "sid": sid,
            "character": character,
            "messages": json.loads(messages or "[]"),
            "graph": json.loads(graph) if graph else None,
            "draft": json.loads(draft) if draft else None,
            "lorebooks": json.loads(lorebooks or "[]"),
            "world_state": json.loads(world_state or "{}"),
            "state": json.loads(state or "{}"),
            "prologue": json.loads(prologue) if prologue else None,
            "updated": updated,
            "beats": [{"seq": s, "step": st, "ts": ts, "text": t} for s, st, ts, t in beats],
            "play_cards": [{"kind": k, "card_key": ck,
                            "foundation": json.loads(f or "{}"), "current": json.loads(c or "{}"),
                            "updated": u} for k, ck, f, c, u in play],
            "card_history": [{"kind": k, "card_key": ck, "seq": sq, "turn": tn,
                              "event": ev, "evidence": json.loads(evd or "[]"), "created": cr}
                             for k, ck, sq, tn, ev, evd, cr in history],
        })

    assets: dict[str, str] = {}
    sdir = root / "configs" / "stories" / key
    for sub in ("bg", "chars"):
        d = sdir / sub
        if d.is_dir():
            for p in sorted(d.glob("*.png")):       # top level only — never portraits/ galleries
                enc = _b64(p)
                if enc:
                    assets[f"{sub}/{p.name}"] = enc
    for ck in library:
        for fn in (f"{_safe(ck)}.png", f"{_safe(ck)}.ref.png"):
            enc = _b64(root / "configs" / "characters" / fn)
            if enc:
                assets[f"shared/characters/{fn}"] = enc
    for pk in personas:
        enc = _b64(root / "configs" / "personas" / f"{_safe(pk)}.png")
        if enc:
            assets[f"shared/personas/{_safe(pk)}.png"] = enc

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": time.time(),
        "key": key,
        "story": story,
        "characters": characters,
        "library": library,
        "personas": personas,
        "sessions": sessions,
        "assets": assets,
    }


# ── Import ─────────────────────────────────────────────────────────────────────

def import_story(root: Path, doc: dict) -> dict:
    """Restore a bundle into this root. Never overwrites an existing story: key/sid/card
    collisions are remapped (``_2`` suffixes) and every internal reference is rewritten.
    Returns a summary dict. Raises ValueError for a malformed/too-new bundle."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError("not a loom story bundle (missing format marker)")
    if int(doc.get("version") or 0) > VERSION:
        raise ValueError(f"bundle version {doc.get('version')} is newer than supported {VERSION}")

    story = dict(doc.get("story") or {})
    characters = dict(doc.get("characters") or {})
    library = {_safe(k): v for k, v in (doc.get("library") or {}).items() if _safe(k)}
    personas = {_safe(k): v for k, v in (doc.get("personas") or {}).items() if _safe(k)}
    old_key = _safe(doc.get("key")) or _safe(story.get("name")) or "story"

    key = old_key
    if story_store.story_exists(root, key):
        key = _mint(old_key, set(story_store.list_stories(root)))

    # ── personas (global namespace): reuse identical, mint on genuine conflict ──
    remap_persona: dict[str, str] = {}
    existing_personas = card_store.load_personas(root)
    for pk, pdata in personas.items():
        final = pk
        if pk in existing_personas:
            if existing_personas[pk] == pdata:
                continue                              # already here — nothing to do
            final = _mint(pk, set(existing_personas) | set(remap_persona.values()))
            remap_persona[pk] = final
        card_store.upsert_persona(root, final, pdata)
        existing_personas[final] = pdata
    story["default_personas"] = [remap_persona.get(p, p)
                                 for p in (story.get("default_personas") or [])]

    # ── story-scoped library cards (global rows stamped fields.story) ──
    remap_card: dict[str, str] = {}
    existing_cards = card_store.load_characters(root)
    for ck, cdata in library.items():
        cdata = dict(cdata if isinstance(cdata, dict) else {})
        fields = dict(cdata.get("fields") or {})
        fields["story"] = key                          # re-point provenance at the NEW story
        cdata["fields"] = fields
        if ck in existing_cards:
            if _same_card(existing_cards[ck], cdata):
                continue                               # same card already pooled — link by reuse
            final = _mint(ck, set(existing_cards) | set(remap_card.values()))
            remap_card[ck] = final
        else:
            final = ck
        card_store.upsert_character(root, final, cdata)
        existing_cards[final] = cdata

    # ── rewrite asset URLs that carry the old story key / remapped card keys ──
    if key != old_key or remap_card:
        blob = json.dumps({"story": story, "characters": characters}, ensure_ascii=False)
        if key != old_key:
            blob = blob.replace(f"/api/stories/{old_key}/", f"/api/stories/{key}/")
        for old_c, new_c in remap_card.items():
            blob = blob.replace(f"/api/characters/{old_c}/", f"/api/characters/{new_c}/")
        rewired = json.loads(blob)
        story, characters = rewired["story"], rewired["characters"]

    story_store.save_story(root, key, story, characters)

    # ── sessions + beats + play-through cards, verbatim (story_key/sid rewritten) ──
    con = story_sessions._conn(root)
    remap_sid: dict[str, str] = {}
    n_beats = n_play = n_hist = 0
    for sess in (doc.get("sessions") or []):
        sid0 = _safe(sess.get("sid")) or f"sess_{int(time.time() * 1000)}"
        sid = sid0
        if con.execute("SELECT 1 FROM sessions WHERE sid=?", (sid,)).fetchone():
            sid = _mint(sid0, {r[0] for r in con.execute("SELECT sid FROM sessions").fetchall()})
            remap_sid[sid0] = sid
        character = remap_card.get(sess.get("character") or "", sess.get("character") or "")
        con.execute(
            "INSERT OR REPLACE INTO sessions"
            " (sid, story_key, character, messages, graph, draft, lorebooks, world_state, state,"
            " prologue, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, key, character,
             story_sessions._jdumps(sess.get("messages") or []),
             story_sessions._jdumps(sess.get("graph")),
             story_sessions._jdumps(sess.get("draft")),
             story_sessions._jdumps(sess.get("lorebooks") or []),
             story_sessions._jdumps(sess.get("world_state") or {}),
             story_sessions._jdumps(sess.get("state") or {}),
             None if sess.get("prologue") is None else story_sessions._jdumps(sess.get("prologue")),
             float(sess.get("updated") or time.time())))
        for b in (sess.get("beats") or []):
            con.execute(
                "INSERT OR REPLACE INTO session_beats (sid, seq, story_key, step, ts, text)"
                " VALUES (?,?,?,?,?,?)",
                (sid, int(b.get("seq") or 0), key, int(b.get("step") or 0),
                 float(b.get("ts") or 0), str(b.get("text") or "")))
            n_beats += 1
        for pc in (sess.get("play_cards") or []):
            con.execute(
                "INSERT OR REPLACE INTO play_cards"
                " (story_key, session_id, kind, card_key, foundation, current, updated)"
                " VALUES (?,?,?,?,?,?,?)",
                (key, sid, str(pc.get("kind") or ""), str(pc.get("card_key") or ""),
                 story_sessions._jdumps(pc.get("foundation") or {}),
                 story_sessions._jdumps(pc.get("current") or {}),
                 float(pc.get("updated") or 0)))
            n_play += 1
        for h in (sess.get("card_history") or []):
            con.execute(
                "INSERT OR REPLACE INTO card_history"
                " (story_key, session_id, kind, card_key, seq, turn, event, evidence, created)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (key, sid, str(h.get("kind") or ""), str(h.get("card_key") or ""),
                 int(h.get("seq") or 0), int(h.get("turn") or 0), str(h.get("event") or ""),
                 story_sessions._jdumps(h.get("evidence") or []), float(h.get("created") or 0)))
            n_hist += 1
    con.commit()

    # ── image assets (path-sanitized; shared files follow their remapped keys) ──
    n_assets = 0
    sdir = root / "configs" / "stories" / key
    for rel, enc in (doc.get("assets") or {}).items():
        rel = str(rel or "")
        if ".." in rel or rel.startswith("/") or "\\" in rel:
            continue
        fn = rel.rsplit("/", 1)[-1]
        if rel.startswith(("bg/", "chars/")):
            dest = sdir / rel
        elif rel.startswith("shared/characters/"):
            for old_c, new_c in remap_card.items():
                fn = fn.replace(f"{old_c}.png", f"{new_c}.png", 1) if fn.startswith(old_c) else fn
            dest = root / "configs" / "characters" / fn
        elif rel.startswith("shared/personas/"):
            for old_p, new_p in remap_persona.items():
                fn = fn.replace(f"{old_p}.png", f"{new_p}.png", 1) if fn.startswith(old_p) else fn
            dest = root / "configs" / "personas" / fn
        else:
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(enc))
            n_assets += 1
        except (OSError, ValueError):
            continue

    return {
        "ok": True,
        "key": key,
        "name": story.get("name", ""),
        "renamed_from": old_key if key != old_key else "",
        "sessions": len(doc.get("sessions") or []),
        "beats": n_beats,
        "play_cards": n_play,
        "card_history": n_hist,
        "library_cards": len(library),
        "personas": len(personas),
        "assets": n_assets,
        "remapped": {"sessions": remap_sid, "cards": remap_card, "personas": remap_persona},
    }

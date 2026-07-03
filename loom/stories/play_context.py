"""Pure per-turn context assembly for interactive play.

Given the story, the turn's `body`, and the current `world_state`, build the narrator's
`(system, prompt)` plus the little bookkeeping the caller needs after the model call. NO LLM
call, NO mutation — this is the deterministic "what does the narrator see this turn" step,
lifted out of `story_play` so it's testable in isolation AND is a drop-in node if the play
turn ever becomes a graph (LangGraph or otherwise). See memory: narrator-scene-pov.

Role-tiered context: character detail scales with each one's role in THIS scene so the
narrator model isn't flooded — on-screen get full detail + emotion range, referenced get a
one-liner, absent get a name; voice exemplars are budgeted by tier (POV deepest, capped).
"""
from __future__ import annotations

import re

# MINIMAL prose register (see memory: minimal-prompt-facts) — A/B-proven that stacked craft
# rules instruct the whimsy they were meant to prevent; the model's default register is already
# right. Only the two rules the BENCH showed are prompt-load-bearing survive: the POV lock
# (head-hop/cut-away baits complied without it) and established-facts fidelity.
PLAY_CRAFT = (
    "Plain, grounded prose. One viewpoint: render only what the viewpoint character can "
    "perceive — no other minds, no 'meanwhile' elsewhere; to learn what's elsewhere, the "
    "viewpoint must go and find out. Established details stay true — when the player refers to "
    "something they carry or did, use the established particular, never invent a replacement. "
    "Introduce people GRADUALLY — at most ONE new person actually appears in a scene at a time; "
    "people merely spoken of or remembered stay offstage (name them, don't bring them on)."
)


def build_turn_context(ctx, st, key: str, body: dict, world_state: dict, *,
                       story_scope: str, thread_scope: str) -> dict:
    """Assemble both passes' contexts for one turn. Returns {system, prompt, scribe_system,
    cur, prior_pov}: system/prompt drive the PROSE pass (free-text narration, craft register);
    scribe_system drives the SCRIBE pass (structured scene report from the fresh narration);
    cur/prior_pov let `story_play` resolve the reported location and carry the sticky POV."""
    from ..server.services.emotions import EMOTION_KEYS, NORMAL_KEYS
    from ..server.services import lorebook_store as _LS0

    def _char_emotion_keys(char_key: str) -> list[str]:
        """Return this character's affect.range keys (new or old manifest format)."""
        mf = ctx.portrait_manifest(char_key)
        raw = (mf.get("affect") or {}).get("range") if isinstance(mf.get("affect"), dict) else None
        if isinstance(raw, list) and raw:
            if isinstance(raw[0], dict):
                return [e["emotion"] for e in raw if e.get("emotion") in EMOTION_KEYS]
            return [k for k in raw if k in EMOTION_KEYS]
        return list(NORMAL_KEYS)

    locs = "\n".join(f"- {l.id} | {l.name}: {l.description}" for l in st.locations) or "(none)"

    # Story-authored PLACES (containers) + their character-anchored SCENES — the world's
    # spots ("mom's kitchen", "the baker's bakery"). Rendered so the director knows who is
    # usually where, and can place characters in their spots without being told each turn.
    def _cname(k: str) -> str:
        c = ctx.base_settings.characters.get(k)
        return (c.name if c else k) or k

    def _scene_line(s) -> str:
        anchors = [_cname(k) for k in ([s.character] if s.character else []) + list(s.characters or [])]
        who = ", ".join(a for a in anchors if a) or "shared"
        tail = f" — {s.backstory}" if s.backstory else ""
        home = " [HOME slot]" if s.role == "persona_home" else ""
        return f"    · {s.name or s.id} [{who}]{home}{tail}"

    def _place_block(p) -> str:
        lines = [f"- {p.name}" + (f": {p.description}" if p.description else "")]
        lines += [_scene_line(s) for s in p.scenes]
        return "\n".join(lines)

    places = "\n".join(_place_block(p) for p in st.places)
    cur = body.get("location") or st.start or (st.locations[0].id if st.locations else "")

    # The protagonist. The frontend passes who *you* are this playthrough. Two shapes:
    #  - EMBODIED: player.character = a playable character KEY → the human puppets a real card.
    #  - LEGACY: player.{name,description} = the thin persona (or a bare "Player").
    player = body.get("player") or {}
    player_char = (player.get("character") or "").strip()
    player_scope = None
    player_back = ""
    pc = ctx.base_settings.characters.get(player_char) if player_char else None
    if pc is not None:
        player_name = pc.name or (player.get("name") or "Player")
        player_back = (pc.system or "").strip()
        player_desc = (player_back.splitlines()[0][:200] if player_back else "")
        player_scope = re.sub(r"[^\w\-]+", "_", player_char)
    else:
        player_name = (player.get("name") or "Player").strip() or "Player"
        player_desc = (player.get("description") or "").strip()
    player_line = (
        f"PLAYER (the human DRIVES this character — narrate TO them and react to their "
        f"actions; never decide their choices or speak for them): {player_name}"
        + (f" — {player_desc}" if player_desc else "")
    )

    # HISTORY lane — bounded for any play length (the controlled-growth invariant: stores grow,
    # the window doesn't). Last K turn-pairs verbatim; older turns compress to the scribe's own
    # per-turn beat log (world_state.log) — compaction with NO extra LLM call. Mention-matching
    # below reads this bounded transcript, so a character mentioned once in turn 3 of a 200-turn
    # play no longer stays "referenced" forever.
    history = body.get("history") or []

    def _hist_lines(msgs) -> str:
        return "\n".join((player_name if m.get("role") == "user" else "Narrator")
                         + f": {m.get('text', '')}" for m in msgs)

    _K = 8                                   # verbatim turn-pairs kept in the window
    recent_txt = _hist_lines(history[-2 * _K:]) or "(the story is just beginning)"
    if len(history) > 2 * _K:
        _beats = list(world_state.get("log") or [])[:-4]   # older beats; newest ~4 are verbatim below
        _summary = "\n".join(f"- {b}" for b in _beats[-24:]) \
            or f"({(len(history) - 2 * _K + 1) // 2} earlier turns compressed)"
        transcript = ("STORY SO FAR (older turns, compressed to beats):\n" + _summary
                      + "\n\nRECENT TURNS (verbatim):\n" + recent_txt)
    else:
        transcript = recent_txt

    # Resolve a move (a scene/home/location the player chose) into an arrival `directive`
    # for the prompt AND the character keys it brings on stage.
    moved = body.get("choice")
    move_keys: set[str] = set()
    directive = ""
    if moved:
        _sc = _pl = None
        for _p in st.places:
            for _s in _p.scenes:
                if _s.id == moved:
                    _sc, _pl = _s, _p
                    break
            if _sc:
                break
        _hs = next((h for h in (getattr(pc, "home_scenes", []) or []) if h.id == moved), None) if pc else None
        if _sc is not None:
            _akeys = ([_sc.character] if _sc.character else []) + list(_sc.characters or [])
            move_keys = {k for k in _akeys if k}
            _who = ", ".join(a for a in (_cname(k) for k in _akeys) if a)
            _line = f"The player moves to {_sc.name or _sc.id}" + (f" in {_pl.name}" if _pl else "") + "."
            if _who:
                _line += f" {_who} {'are' if ',' in _who else 'is'} here."
            if _sc.backstory:
                _line += f" ({_sc.backstory})"
            directive = (f"\n\n[{_line} Narrate the transition and arrival; bring the named "
                         f"character(s) into the scene and include them in `present`.]")
        elif _hs is not None:
            _line = f"The player goes to their own home, {_hs.name or _hs.id}."
            if _hs.backstory:
                _line += f" ({_hs.backstory})"
            directive = f"\n\n[{_line} Narrate the transition and arrival at the player's home.]"
        else:
            dest = next((l.name for l in st.locations if l.id == moved), moved)
            directive = f"\n\n[The player moves to: {dest}. Narrate the transition and arrival there; set location to '{moved}'.]"

    # The persistent SCENE (space + co-located members + a sticky POV). It carries between
    # turns; the narrator maintains it (reports `present` + `pov`), story_play persists it.
    prior_scene = world_state.get("scene") if isinstance(world_state.get("scene"), dict) else {}
    prior_members = {k for k in (prior_scene.get("members") or []) if k}
    prior_pov = (prior_scene.get("pov") or "").strip()

    # ROLE-TIERED CAST: detail scales with a character's role in THIS scene. ON-SCREEN (scene
    # members / move anchors) get full detail + emotion range (they're rendered as sprites);
    # REFERENCED (named but off-stage) get a one-liner so they can be spoken of; ABSENT get only
    # a name so they can be called in. Voice exemplars — the heaviest block — are budgeted below.
    def _named_in(hay: str, k: str) -> bool:
        nm = _cname(k)
        toks = [t for t in re.split(r"\s+", (nm or "").lower()) if len(t) > 2]
        return any(re.search(rf"\b{re.escape(t)}\b", hay) for t in toks)
    from . import perception as _PC0
    _cast_keys = [m.character for m in st.cast]
    _hay = transcript.lower()
    _beat = " ".join(str(m.get("text", "")) for m in history[-2:]).lower()   # the current beat
    _mentioned = {k for k in _cast_keys if _named_in(_hay, k)}

    if moved:
        on_screen = move_keys & set(_cast_keys)          # a move brings its anchors on stage
    else:                                                 # else the scene's members carry over
        on_screen = (prior_members or set(_PC0.present_at(world_state, int(world_state.get("step") or 0) - 1))) & set(_cast_keys)
    if not prior_members and not on_screen:
        on_screen = set(_cast_keys)                       # true opening → whole (small) cast
    referenced = (_mentioned - on_screen) & set(_cast_keys)
    absent = set(_cast_keys) - on_screen - referenced

    # POV + ACTIVE (named in the current beat) are the deepest tier for the voice budget below.
    pov_key = next((k for k in _cast_keys if _cname(k).lower() == prior_pov.lower()), "") if prior_pov else ""
    active = {k for k in on_screen if _named_in(_beat, k)} | ({pov_key} if pov_key in on_screen else set())
    immediate = on_screen | referenced                    # everyone "in play" (relationship focus)

    # Prose cast lines carry NO emotion-key lists (schema noise the writer doesn't need);
    # the scribe gets the key ranges instead (it picks the sprite emotions from the narration).
    def _cast_full(k):
        c = ctx.base_settings.characters.get(k)
        desc = ((c.system or "").splitlines()[0] if c else "")[:140]
        return f"- {_cname(k)}: {desc}"
    scribe_emotions = "\n".join(
        f"- {_cname(k)}: {', '.join(_char_emotion_keys(k))}" for k in _cast_keys if k in on_screen)
    cast = "\n".join(_cast_full(k) for k in _cast_keys if k in on_screen) or "(none on stage)"
    # WHEREABOUTS: off-screen characters are findable at their last-seen spot or in their
    # ORBIT (habitual spots from place anchors + home scenes) — the narrator is TOLD where
    # people plausibly are instead of inventing; the state engine enforces it (geography.py).
    from .geography import orbit as _orbit

    def _whereabouts(k: str) -> str:
        ent = (world_state.get("entities") or {}).get(_cname(k)) or {}
        bits = []
        if ent.get("location"):
            bits.append(f"last seen: {ent['location']}")
        spots = _orbit(ctx, st, k)
        if spots:
            bits.append("usual spots: " + ", ".join(spots[:2]))
        return f" [{'; '.join(bits)}]" if bits else ""

    if referenced:
        cast += "\n(referenced, off-stage — they are where the whereabouts say, not here):\n" + "\n".join(
            f"- {_cname(k)}" + (f": {((ctx.base_settings.characters.get(k).system or '').splitlines()[0] if ctx.base_settings.characters.get(k) else '')[:90]}")
            + _whereabouts(k)
            for k in _cast_keys if k in referenced)
    if absent:
        # Cap the roster so a huge cast can't flood the prompt — the story DB stays the full
        # registry; the window only names who could plausibly be called in.
        _ab = [f"- {_cname(k)}{_whereabouts(k)}" for k in _cast_keys if k in absent]
        _more = len(_ab) - 30
        cast += ("\n(elsewhere — bring on only when the scene genuinely calls them in):\n"
                 + "\n".join(_ab[:30]) + (f"\n(+{_more} more, elsewhere)" if _more > 0 else ""))

    # PEOPLE, organized by LOCATION (the StoryMaster's world model): who's at the current place is
    # surfaced up front (can't be ignored), everyone else is indexed under their location (a growing
    # cast stays bounded to the current scene + an index). Fleshed people get their card voiced when
    # they're on stage. See loom/stories/storymaster.py.
    from .storymaster import people_by_location
    _people = world_state.get("people") if isinstance(world_state.get("people"), dict) else {}
    _cur_locname = next((l.name for l in st.locations if l.id == cur), cur)
    _people_block = people_by_location(world_state, _cur_locname)
    _born_here = [n for n, r in _people.items() if r.get("born") and r.get("card")
                  and any(w in _hay for w in n.lower().split() if len(w) > 2)]
    _born_block = ("PEOPLE MET IN PLAY (voice them from this — they ARE these people):\n"
                   + "\n".join(f"- {_people[n]['card']}" for n in _born_here)) if _born_here else ""

    system = (
        f"You are the narrator of an interactive novel titled \"{st.name}\".\n"
        f"PREMISE: {st.premise}\nTONE: {st.tone}\n"
        + f"{player_line}\n"
        f"CAST (use these names):\n{cast}\n"
        f"LOCATIONS (the scene is in exactly one):\n{locs}\n"
        + (f"PLACES (containers holding character 'spots' — honor who is usually where; a "
           f"character at home is in their spot unless the scene says otherwise):\n{places}\n" if places else "")
        + "\n" + PLAY_CRAFT + "\n\n"
        "Narrate the next moment in-world, responding to the player: second person to the player, "
        "plus the characters' action and dialogue. Keep it brief — 2-3 short paragraphs, a "
        "screenful at most; advance ONE beat, don't run ahead. Respond with the NARRATION "
        "ONLY — no headers, no lists, no JSON, no out-of-story commentary."
    )

    # SCENE + PERSPECTIVE — the co-location invariant and the sticky POV. The narrator holds one
    # viewpoint at a time and does not head-hop; who's in the room stays put unless someone
    # physically enters or leaves. Feeding last turn's pov/members is what makes the flow stable.
    loc_now = next((l.name for l in st.locations if l.id == cur), cur)
    _pov_name = (_cname(prior_pov) if prior_pov else player_name)
    _member_names = ", ".join(_cname(k) for k in (prior_scene.get("members") or []) if k)
    if prior_pov or _member_names:
        system += (
            f"\n\nPERSPECTIVE — tell this turn through ONE viewpoint (close third, or the player's "
            f"own view). Right now it follows: {_pov_name}. KEEP it there — render only what "
            f"{_pov_name} can perceive; never head-hop into another character's private thoughts. "
            f"Shift the viewpoint ONLY when {_pov_name} leaves the scene, the player moves "
            f"elsewhere, or a clear scene break occurs.\n"
            + (f"SCENE — the scene is in {loc_now}, with: {_member_names}. These characters are "
               f"co-located; keep them together in this space. Do NOT introduce anyone else unless "
               f"they physically enter; do not let a character in this space silently vanish.\n"
               if _member_names else "")
        )
    else:
        system += (
            "\n\nPERSPECTIVE — tell the story through ONE clear viewpoint (the player's own view, "
            "or a single close-third character). Establish it now and hold it steady; do not "
            "head-hop between characters."
        )

    # EMBODIMENT: hand the narrator each cast member's OWN exemplars (life/saying/reaction) from
    # their per-character lorebook. This is the HEAVIEST per-character block, so it's BUDGETED by
    # scene role: POV gets the most, characters active in the current beat get some, present-but-
    # passive get one — and at most a few characters carry voice at all. Off-screen/absent get none.
    def _embody(k, n):
        c = ctx.base_settings.characters.get(k)
        if c is None or n <= 0:
            return ""
        scope = re.sub(r"[^\w\-]+", "_", str(k))
        ex = _LS0.top_by_priority(ctx.root, scope, n)
        lines = "\n".join(f"    · [{e.facet or 'life'}] {e.content}" for e in ex if e.content)
        return f"- {c.name}:\n{lines}" if lines else ""
    # priority order: POV, then active speakers, then present-passive — capped to a few.
    _voice_order = ([pov_key] if pov_key in on_screen else []) \
        + [k for k in _cast_keys if k in active and k != pov_key] \
        + [k for k in _cast_keys if k in on_screen and k not in active and k != pov_key]
    _voice, _seen = [], set()
    for k in _voice_order:
        if k and k not in _seen:
            _seen.add(k); _voice.append(k)
    _voice = _voice[:4]        # soft cap: at most 4 characters carry voice exemplars
    _n_for = lambda k: 5 if k == pov_key else (3 if k in active else 1)
    embodiment = "\n".join(b for b in (_embody(k, _n_for(k)) for k in _voice) if b)
    if embodiment:
        system += ("\n\nEMBODY THE CAST — voice each character from THEIR OWN remembered moments "
                   "below; stay true to these, they ARE the person:\n" + embodiment)
    if _people_block:
        system += "\n\n" + _people_block
    if _born_block:
        system += "\n\n" + _born_block

    # Embodied player: fold the puppet's full backstory into the brief so the director treats the
    # player as a real person in this world while still letting the human steer every choice.
    if player_back:
        system += (
            f"\n\nWHO THE PLAYER IS — {player_name}'s backstory (canon; weave it into the "
            f"world and the cast's reactions, but the human chooses what they do and say):\n"
            f"{player_back}"
        )
    # Persona HOME swap: the embodied puppet brings their OWN home(s), standing in for a story
    # 'persona_home' slot. Falls back to a free-text home_note when no scenes are set.
    if pc is not None:
        _homes = list(getattr(pc, "home_scenes", []) or [])
        if _homes:
            _hl = "\n".join(
                f"  · {h.name or h.id}" + (f" — {h.backstory}" if h.backstory else "") for h in _homes)
            system += (f"\n\n{player_name}'S HOME (the player's OWN places they carry with them — "
                       f"their home spots; one stands in for any 'persona_home' slot above):\n{_hl}")
        else:
            _home = ((pc.fields or {}).get("home_note") or "").strip()
            if _home:
                system += (f"\n\n{player_name}'S HOME (the player's own place — use this as their "
                           f"home, standing in for any 'persona_home' slot above): {_home}")

    # NSFW injection: when the recent transcript hits trigger keywords, prepend the matching
    # guidance — `_nsfw` + `_nsfw_acts`. Deterministic word-match; capped so the prompt stays lean.
    from ..server.services import lorebook_store as _LS
    _recent = " ".join(str(m.get("text", "")) for m in history[-3:]).lower()
    _inject: list[str] = []
    for _scope in ("_nsfw", "_nsfw_acts"):
        for _e in _LS.load_lorebook(ctx.root, _scope):
            if not (_e.enabled and _e.content):
                continue
            if any(re.search(rf"\b{re.escape(k.lower())}\b", _recent) for k in _e.keywords if k):
                _inject.append(_e.content)
                if len(_inject) >= 4:
                    break
        if len(_inject) >= 4:
            break
    if _inject:
        system = "\n\n".join(_inject) + "\n\n" + system

    # Canon retrieval: attached lorebooks + the story's own book + this thread's established facts.
    from ..server.services.lorebook import format_lore_block
    attached = body.get("lorebooks")
    world_scopes = [re.sub(r"[^\w\-]+", "_", str(s)) for s in (attached or []) if s]
    world_scopes += [story_scope, thread_scope]
    if player_scope:
        world_scopes.append(player_scope)
    hits = _LS.retrieve(ctx.root, _recent or transcript, world_scopes, top_k=6)
    _lore_block = format_lore_block(hits) if hits else ""
    if _lore_block:
        system = system + "\n\n" + _lore_block

    # WORLD STATE: the mutable working memory of this playthrough. Scope the character list to the
    # scene (on-stage + referenced + pov); flags/inventory/log stay global.
    from . import state_engine as _SE
    _focus_names = {_cname(k).lower() for k in (immediate | ({pov_key} if pov_key else set()))}
    _state_block = _SE.render_state(world_state, focus=_focus_names or None)
    if _state_block:
        system = system + "\n\n" + _state_block

    # CONTINUITY lane (the ledger, Phase 3): surface the scene-relevant established particulars +
    # the ripest open promises. Worded as PERMISSION, never instruction — forced callbacks read as
    # cargo-cult foreshadowing; the critic's continuity axis watches for that.
    _step_now = int(world_state.get("step") or 0)
    _beat_words = set(_beat.split()) | {w for k in on_screen for w in _cname(k).lower().split()}

    def _dscore(dd: dict) -> int:
        tw = set(str(dd.get("text", "")).lower().split())
        return len(tw & _beat_words) * 2 + (1 if _step_now - int(dd.get("step") or 0) <= 3 else 0)

    _dsel = sorted((world_state.get("details") or []), key=_dscore, reverse=True)[:3]
    _open = [p for p in (world_state.get("promises") or []) if p.get("status") == "open"]
    _ripe = sorted(_open, key=lambda p: int(p.get("step") or 0))[:2]
    _cont_block = ""
    if _dsel or _ripe:
        _cont_block = ("CONTINUITY — small established things; keep them true. You MAY let one "
                       "quietly recur or pay off when the moment genuinely fits (never force it). "
                       "When the player refers obliquely to something they carry or did, resolve it "
                       "to an established particular below — NEVER invent a new object or memory in "
                       "its place:\n"
                       + "".join(f"- {dd['text']}\n" for dd in _dsel if dd.get("text"))
                       + "".join(f"- open thread: {p['setup']}\n" for p in _ripe if p.get("setup")))
        system = system + "\n\n" + _cont_block.rstrip()

    # RELATIONSHIP PROJECTION: inject ONLY the web edges among the on-stage/referenced set — never
    # the whole web every turn. DB-backed stories PULL the relevant edges via an indexed query.
    from . import story_db as _SDB
    from .genesis import project_web
    _focus = set(immediate)
    _db = ctx._story_db(key)
    _relset = (_SDB.relationships_for(_db, _focus) if _db is not None
               else [r.model_dump() for r in st.relationships])
    _rel_block = ""
    if _relset:
        _proj = project_web(_relset, _focus)
        if _proj:
            _plines = []
            for _k, _es in _proj.items():
                _txt = "; ".join(
                    f"{'→' if e['outward'] else '←'} {_cname(e['other'])}"
                    + (f" ({e['nature']})" if e['nature'] else "")
                    + (f": {e['dynamic'] or e['stance']}" if (e['dynamic'] or e['stance']) else "")
                    for e in _es)
                _plines.append(f"- {_cname(_k)} {_txt}")
            _rel_block = ("RELATIONSHIPS (only those on stage or just mentioned — honor these "
                          "dynamics in how they speak and act toward each other):\n" + "\n".join(_plines))
            system += "\n\n" + _rel_block

    # POINT-OF-REFERENCE facts: when the player's CURRENT message overlaps an established
    # particular, restate it right next to the action — a fact in the system lane can be drifted
    # past; a fact beside the reference cannot ("the keepsake" IS the brass compass, not a new
    # invention). Smart-context principle: put information where the model needs it.
    _last_words = {w for w in re.findall(r"[a-z'\-]{3,}",
                   (history[-1].get("text", "") if history else "").lower())}
    _estab_pool = ([dd.get("text", "") for dd in (world_state.get("details") or [])]
                   + [p.get("setup", "") for p in (world_state.get("promises") or [])
                      if p.get("status") == "open"])
    _at_ref = [t for t in _estab_pool
               if len({w for w in t.lower().split()} & _last_words) >= 1][:3]
    _estab = ("\n\n(Established, use as-is: " + "; ".join(_at_ref) + ")") if _at_ref else ""

    prompt = (f"CURRENT LOCATION: {cur}\n\nTRANSCRIPT:\n{transcript}{directive}{_estab}\n\n"
              f"Narrate the next turn.")

    # ── SCRIBE pass context — the structured-state reporter. A separate (cheap) model reads the
    # fresh narration and emits the full scene report + state_deltas. Prose quality is irrelevant
    # here; schema fit is everything. Splitting this OUT of the prose call is degrader-fix D1.
    scribe_system = (
        f"You are the SCRIBE of an interactive novel. You read the latest NARRATION and report the "
        f"scene state as structured output — ONLY what the narration establishes; never invent.\n"
        f"CAST (exact names):\n" + ("\n".join(f"- {_cname(k)}" for k in _cast_keys) or "(none)") + "\n"
        f"EMOTION RANGES (pick each present character's emotion from THEIR list; the exact key "
        f"selects their portrait sprite; 'neutral' if unsure):\n{scribe_emotions or '(none)'}\n"
        f"LOCATIONS (report `location` as one of these ids):\n{locs}\n"
        f"PRIOR SCENE: in {loc_now}, with: {_member_names or '(nobody)'}; viewpoint: {_pov_name}.\n"
        + (f"\n{_state_block}\n" if _state_block else "")
        + (("\nOPEN PROMISES (emit `payoff` if the narration fulfils one):\n"
            + "".join(f"- {p['setup']}\n" for p in _open[:6] if p.get("setup"))) if _open else "")
        + "\nReport:\n"
        "- location: the id the scene is in now (one of the listed ids).\n"
        "- present: EVERY cast character PHYSICALLY in the scene right now (speaking, acting, or "
        "physically described as there) — never empty if someone is there. A character who is "
        "only mentioned, asked about, remembered, or said to be elsewhere is NOT present.\n"
        "- emotions: for each present character, ONE key from their listed range.\n"
        "- pov: the name of the character whose perspective the narration follows (or the "
        f"player's name, {player_name}, for their own view).\n"
        "- movement: true ONLY when the moment invites the player to move elsewhere.\n"
        "- player_status: 'sleeping' if the player sleeps/rests, 'dead' if they die, else 'active'.\n"
        "- people: EVERY named person the narration touched this turn — whether they were physically "
        "present OR only mentioned/remembered. For each: their name, `at` (where they are now: a "
        "location name, 'here' if in the scene, '' if away/unknown), and a one-line `note` (who they "
        "are / their tie to the viewpoint). This is how the story remembers who exists and where.\n"
        "\nAlso report `state_deltas` — what changed THIS turn. Each delta is one op; fill only "
        "the fields that op needs (leave the rest empty). `name` is always a character's exact "
        "cast name. The ops:\n"
        "- detail — a small concrete particular worth keeping true (an object, gesture, scar, or "
        "distinctive phrase, from the narration or the player's action); value names it exactly, "
        "name = its owner if any. If the player's action introduces or handles a specific object, "
        "you MUST emit this — the story's memory depends on it. Skip generic scenery.\n"
        "- promise — a setup or expectation the scene created, awaiting payoff; value states it.\n"
        "- payoff — an OPEN PROMISE above was just fulfilled; value says which.\n"
        "- rel — a present character's stance toward the player shifted; key is 'you', value is a "
        "signed step like +1 or -1.\n"
        "- mood — a character's mood changed; value is the new mood.\n"
        "- move — someone changed location; value is the destination. Only for movement the "
        "narration actually establishes — keep off-screen characters where they were last seen.\n"
        "- item_add / item_remove — the player's belongings changed; value is the item.\n"
        "- fact — a new piece of canon worth remembering; value is the fact, title a short label, "
        "keywords its trigger words.\n"
        "- set_flag — a genuine story variable changed; key is the variable, value the new value. "
        "Do not invent flags.\n"
        "- log — REQUIRED every turn, exactly once: value is a one-line summary of the beat.\n"
        "Be thorough: a mood/rel delta for every present character who shifted; an empty list "
        "only if truly nothing changed."
    )

    # Lane log — per-turn context sizes (chars). This is the harness's own gauge: with a bigger
    # world/cast, these must stay ~flat (stores grow; the window doesn't). Surfaced in the /play
    # response for the UI/bench to watch.
    # CONSEQUENCE pass context — the story's LOGIC, not its prose. A reasoning model works out
    # what the player's action ACTUALLY causes (cause→effect, how each present character reacts,
    # what changes, the new cost/choice) before the prose pass renders it. Gets the FACTS (cast,
    # scene, state, established, relationships, premise) — not the craft register or voice
    # exemplars (those are for VOICING, not logic). This is what the narrator was missing.
    if _member_names:
        _pres_line = f"ON STAGE NOW: {_member_names}."
    else:
        _pres_line = ("SCENE OPENING — no one is established on stage yet. If the player's action "
                      "finds, meets, enters on, or addresses a cast member, that character IS here "
                      "— bring them in. Do NOT narrate an empty scene when the action seeks someone.")
    consequence_system = (
        "You are the LOGIC of this story world — not a writer. Given the situation and what the "
        "player does, work out what ACTUALLY happens: concrete physical and social consequences "
        "in causal order. What does the action directly cause? How does each character present "
        "react — in character, for real reasons? What changes, and what new problem, cost, or "
        "opening does it create? Reason it through; be concrete and causal, never atmospheric. "
        "Keep every established fact true. Bring people on GRADUALLY — at most ONE new person "
        "steps into the scene at a time; people merely mentioned or remembered stay offstage. "
        "End on the real choice or problem the player now faces. "
        "Output a short numbered list of what happens, in order — not prose.\n\n"
        f"WHERE: {loc_now}. {_pres_line}\n"
        f"CAST (who exists in this story; anyone on stage or brought in by the action is present):\n{cast}\n"
        + (f"\n{_state_block}\n" if _state_block else "")
        + (f"\n{_cont_block}\n" if _cont_block else "")
        + (f"\n{_rel_block}\n" if _rel_block else "")
        + (f"\nWORLD: {st.premise}\n" if st.premise else "")
    )

    lanes = {"craft": len(PLAY_CRAFT), "cast": len(cast), "places": len(places),
             "embodiment": len(embodiment), "player_back": len(player_back),
             "lore": len(_lore_block), "state": len(_state_block), "relationships": len(_rel_block),
             "continuity": len(_cont_block), "history": len(transcript),
             "consequence": len(consequence_system),
             "system": len(system), "scribe": len(scribe_system)}

    return {"system": system, "prompt": prompt, "scribe_system": scribe_system,
            "consequence_system": consequence_system,
            "cur": cur, "prior_pov": prior_pov, "lanes": lanes}

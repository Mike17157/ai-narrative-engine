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
                       story_scope: str, thread_scope: str,
                       scenario: dict | None = None, runtime_state: dict | None = None,
                       scenario_scene: dict | None = None) -> dict:
    """Assemble both passes' contexts for one turn. Returns {system, prompt, scribe_system,
    cur, prior_pov}: system/prompt drive the PROSE pass (free-text narration, craft register);
    scribe_system drives the SCRIBE pass (structured scene report from the fresh narration);
    cur/prior_pov let `story_play` resolve the reported location and carry the sticky POV."""
    from ...server.services.emotions import EMOTION_KEYS, NORMAL_KEYS
    from ...server.services import lorebook_store as _LS0

    # SETTING STAGES are arc-scoped: sync the active arc's conditions onto world_state `cond:` flags
    # before anything reads them, so this turn's situational content matches where we are in the plot.
    from .director import sync_arc_conditions
    sync_arc_conditions(world_state)

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

    def _place_block(l) -> str:
        lines = [f"- {l.name}" + (f": {l.description}" if l.description else "")]
        lines += [_scene_line(s) for s in (l.scenes or [])]
        return "\n".join(lines)

    places = "\n".join(_place_block(l) for l in st.locations if l.scenes)
    _compiled = bool(scenario)
    _runtime = runtime_state if isinstance(runtime_state, dict) else {}
    _scenario_state = _runtime.get("scenario_state") if isinstance(_runtime.get("scenario_state"), dict) else {}
    # An activated scenario owns the starting place and clock.  A browser body
    # is an input request, never authority to teleport its deterministic state.
    cur = (_scenario_state.get("location") if _compiled else None) \
        or body.get("location") or st.start or (st.locations[0].id if st.locations else "")

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
        + " (Their persona/backstory describes characterisation, not an unearned supernatural ability.)"
    )

    # HISTORY lane — bounded for any play length (the controlled-growth invariant: stores grow,
    # the window doesn't). Last K turn-pairs verbatim; older turns compress to the scribe's own
    # per-turn beat log (world_state.log) — compaction with NO extra LLM call. Mention-matching
    # below reads this bounded transcript, so a character mentioned once in turn 3 of a 200-turn
    # play no longer stays "referenced" forever.
    history = body.get("history") or []
    history = [message for message in history if isinstance(message, dict)]
    # Closed-world player abilities: entity capabilities and client-side persona
    # prose never grant one.  An activated card gets its immutable compiled
    # registry; an unactivated/legacy card reads the explicit authored field.
    from .player_guard import assess_player_action, compile_player_capabilities
    _capability_source = scenario if _compiled else (getattr(st, "fields", None) or {})
    _player_capabilities = compile_player_capabilities(_capability_source)
    if _compiled and not _player_capabilities:
        _player_capabilities = compile_player_capabilities(getattr(st, "fields", None) or {})
    _last_player_text = next((str(m.get("text", "")) for m in reversed(history)
                              if m.get("role") == "user"), "")
    _player_action_guard = assess_player_action(_last_player_text, _player_capabilities)

    def _hist_lines(msgs) -> str:
        lines = []
        for m in msgs:
            is_player = m.get("role") == "user"
            text = str(m.get("text", ""))
            # A raw browser transcript is not a second authoring channel.  If
            # a past player claim was unsupported, retain the words but append
            # its deterministic outcome so it cannot harden into canon later.
            if is_player:
                text = assess_player_action(text, _player_capabilities)["history_text"]
            lines.append((player_name if is_player else "Narrator") + f": {text}")
        return "\n".join(lines)

    _K = 8                                   # verbatim turn-pairs kept in the window
    shared_recent = ""
    if _compiled:
        # Do not give a newly-arrived character a browser transcript from scenes
        # they never witnessed.  The scenario ledger is recorded with the
        # authoritative roster, so it is the only safe history source here.
        current_people = {str(k) for k in (_scenario_state.get("present") or []) if k}
        observed = [turn for turn in (_scenario_state.get("turns") or [])
                    if isinstance(turn, dict) and current_people <= {str(k) for k in (turn.get("present") or [])}]
        observed = observed[-2 * _K:]
        def _shared_line(turn: dict) -> str:
            """Render one witnessed turn without letting a rejected claim become canon."""
            label = player_name if turn.get("speaker") == "player" else "Narrator"
            text = str(turn.get("text", ""))
            if turn.get("speaker") == "player":
                text = assess_player_action(text, _player_capabilities)["history_text"]
            return f"{label}: {text}"

        shared_recent = "\n".join(
            _shared_line(turn) for turn in observed if turn.get("text")
        )
        transcript = ("SHARED SCENE MEMORY (only events every current person witnessed):\n"
                      + (shared_recent or "(this group has no shared prior scene yet)"))
    else:
        recent_txt = _hist_lines(history[-2 * _K:]) or "(the story is just beginning)"
        if len(history) > 2 * _K:
            _beats = list(world_state.get("log") or [])[:-4]   # older beats; newest ~4 are verbatim below
            _summary = "\n".join(f"- {b}" for b in _beats[-24:]) \
                or f"({(len(history) - 2 * _K + 1) // 2} earlier turns compressed)"
            transcript = ("STORY SO FAR (older turns, compressed to beats):\n" + _summary
                          + "\n\nRECENT TURNS (verbatim):\n" + recent_txt)
        else:
            transcript = recent_txt
        shared_recent = recent_txt

    # Resolve a move (a scene/home/location the player chose) into an arrival `directive`
    # for the prompt AND the character keys it brings on stage.
    moved = body.get("choice")
    move_keys: set[str] = set()
    directive = ""
    if moved:
        _sc = _pl = None
        for _l in st.locations:
            for _s in (_l.scenes or []):
                if _s.id == moved:
                    _sc, _pl = _s, _l
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

    # A picked SCENE SUGGESTION (the day's slot offer): open the scene it names — its location,
    # its people, its hook. Acts like a move for the roster; the hook is an opening, not an outcome.
    seed = body.get("scene_seed") if isinstance(body.get("scene_seed"), dict) else None
    if seed and not moved:
        if _compiled:
            # The transition was already accepted by runtime.compiled before
            # this prompt was made.  Project only its public title/hook.
            cur = _scenario_state.get("location") or cur
            _scene_people = (_scenario_state.get("present") or [])
            move_keys = {str(k) for k in _scene_people if k}
            _who = ", ".join(_cname(k) for k in move_keys)
            _ln = next((l.name for l in st.locations if l.id == cur), cur)
            _title = (scenario_scene or {}).get("title") or seed.get("title") or "the next scene"
            _hook = (scenario_scene or {}).get("visible") or seed.get("hook") or ""
            directive = (f"\n\n[An authored scene opens: {_title} — at {_ln}."
                         + (f" {_who} {'are' if ',' in _who else 'is'} here." if _who else "")
                         + (f" {_hook}" if _hook else "")
                         + " Narrate the player arriving into this situation — an opening, not an outcome.]")
        else:
            cur = seed.get("location") or cur
            _n2k0 = {(_cname(m.character) or "").lower(): m.character for m in st.cast}
            move_keys = {k for k in (_n2k0.get((n or "").lower()) for n in (seed.get("who") or [])) if k}
            _who = ", ".join(n for n in (seed.get("who") or []) if n)
            _ln = next((l.name for l in st.locations if l.id == cur), cur)
            directive = (f"\n\n[A new scene opens: {seed.get('title', 'the next scene')} — at {_ln}."
                         + (f" {_who} {'are' if ',' in _who else 'is'} here." if _who else "")
                         + (f" {seed.get('hook', '')}" if seed.get("hook") else "")
                         + " Narrate the player arriving into this situation — an opening, not an outcome.]")

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
    from . import state as _PC0
    _cast_keys = [m.character for m in st.cast]
    _hay = transcript.lower()
    _beat = " ".join(
        assess_player_action(str(m.get("text", "")), _player_capabilities)["history_text"]
        if m.get("role") == "user" else str(m.get("text", ""))
        for m in history[-2:]
    ).lower()   # the current beat
    _mentioned = {k for k in _cast_keys if _named_in(_hay, k)}

    from .director import StoryMaster
    _cur_ln = next((l.name for l in st.locations if l.id == cur), cur)
    _sm = StoryMaster(ctx, st, world_state, location=_cur_ln)
    _prior_space = (prior_scene.get("space") or "")
    _arrived = bool(_prior_space) and cur != _prior_space   # scene boundary by travel

    if _compiled:
        # Never fall back to "all cast" in a compiled opening.  The card has
        # named the people physically present; any future entrance must be a
        # later authored scene transition, not a convenient narrator invention.
        on_screen = {str(k) for k in (_scenario_state.get("present") or []) if k} & set(_cast_keys)
        roster = [_cname(k) for k in _cast_keys if k in on_screen]
    else:
        if moved or seed:
            on_screen = move_keys & set(_cast_keys)          # a move/scene-seed brings its anchors on
        elif _arrived:
            # Travel boundary: presence RE-DERIVES from the world model — only who is actually
            # recorded AT the new place is here; the old room does NOT teleport along. An empty
            # roster falls back to the scribe's arrival report in apply (then locks next turn).
            _at_here = {n.lower() for n in _sm._names_at(_cur_ln)}
            on_screen = {k for k in _cast_keys if _cname(k).lower() in _at_here}
        else:                                                 # else the scene's members carry over
            on_screen = (prior_members or set(_PC0.present_at(world_state, int(world_state.get("step") or 0) - 1))) & set(_cast_keys)
        if not prior_members and not on_screen:
            on_screen = set(_cast_keys)                       # true opening → whole (small) cast

        # AUTHORITATIVE ROSTER — the StoryMaster decides who is present; the narrator obeys absolutely.
        # Sticky co-location + at most one CONTROLLED entrance (someone recorded at this place whom the
        # player's action actually seeks). This governs `roster` (returned to apply as the real present)
        # and the "PRESENT — exactly these" instruction; the narrator can't summon or drop anyone.
        _last_u = _player_action_guard["safe_action"]
        roster = _sm.plan_scene(present=[_cname(k) for k in _cast_keys if k in on_screen], action=_last_u)
        _n2k = {_cname(k).lower(): k for k in _cast_keys}
        on_screen = {k for k in (_n2k.get(n.lower()) for n in roster) if k}   # cast members in the roster
        if not on_screen and not roster:
            on_screen = {k for k in _cast_keys if k in (move_keys or set())} or set(_cast_keys)
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
    from ..world.creation import orbit as _orbit

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
    from .director import people_by_location
    _people = world_state.get("people") if isinstance(world_state.get("people"), dict) else {}
    _cur_locname = next((l.name for l in st.locations if l.id == cur), cur)
    _people_block = people_by_location(world_state, _cur_locname)
    _born_here = [n for n, r in _people.items() if r.get("born") and r.get("card")
                  and any(w in _hay for w in n.lower().split() if len(w) > 2)]
    _born_block = ("PEOPLE MET IN PLAY (voice them from this — they ARE these people):\n"
                   + "\n".join(f"- {_people[n]['card']}" for n in _born_here)) if _born_here else ""

    # TIME — the day's three-slot rhythm (morning/evening/night); '' before the day model exists.
    _day = world_state.get("day") if isinstance(world_state.get("day"), dict) else {}
    _time_line = (f"TIME: day {_day.get('n')}, {_day.get('slot')}.\n"
                  if _day.get("slot") else "")
    # Never hand a narrator raw entity capabilities or a private director plan.
    # The deterministic runtime decides eligibility; prose only receives what a
    # player could actually see in the selected authored scene.
    _scenario_public = ""
    _returner_memory = []
    if _compiled:
        _safe_scene = scenario_scene if isinstance(scenario_scene, dict) else {}
        _visible = (_safe_scene.get("visible") or _safe_scene.get("hook") or "").strip()
        _title = (_safe_scene.get("title") or _safe_scene.get("id") or "the current scene").strip()
        _scenario_public = (f"AUTHORED SCENE: {_title}.\n"
                            + (f"What is visibly true on arrival: {_visible}\n" if _visible else ""))
        from .compiled import player_memory
        _returner_memory = player_memory(_runtime)

    # THEME-LED ARC SURFACE — an authored arc can hold the private reason a
    # person cannot yet name what they want.  Do not hand that whole design to
    # either prose or consequence generation: both can turn a private premise
    # into a premature confession.  The shared helper admits only the present
    # character's lived belief/protection/visible tell and the selected scene's
    # public pressure.  It deliberately excludes wound, blind spot, need,
    # recognition, revelation, private pressure, and planned change.
    _arc_surface_block = ""
    # Unlike the public dramatic surface, these locks are runtime-only.  They
    # hold no text that is rendered to a model except the safe resistance
    # instruction assembled below; private detector cues stay in ``tc`` for
    # the post-generation repair guard.
    _arc_resistance_locks: list[dict] = []
    _arc_resistance_block = ""
    _arc_design = (getattr(st, "fields", None) or {}).get("arc_design")
    if _arc_design is not None:
        try:
            from ..authoring.arc_design import (
                ArcDesignValidationError,
                active_arc_resistance_locks,
                narrator_arc_surface,
            )
            from .arc_guard import mark_direct_challenges, prompt_block as _arc_prompt_block

            _scene_state = world_state.get("scenario") if isinstance(world_state.get("scenario"), dict) else {}
            _arc_scene_id = str(
                (scenario_scene or {}).get("id") if isinstance(scenario_scene, dict) else ""
            ) or str(_scenario_state.get("active_scene") or _scene_state.get("active_scene") or "")
            _arc_slot = str(_scenario_state.get("time") or _day.get("slot") or "")
            # Both ledgers carry legitimate gates: the public world gets
            # normal scribe deltas, while an activated scenario owns its
            # deterministic eligibility flags.  Scenario values win on a
            # collision because they are the executable contract.
            _world_flags = world_state.get("flags") if isinstance(world_state.get("flags"), dict) else {}
            _scenario_flags = (_scenario_state.get("flags")
                               if isinstance(_scenario_state.get("flags"), dict) else {})
            _arc_flags = {**_world_flags, **_scenario_flags}
            _arc_surface = narrator_arc_surface(
                _arc_design,
                scene_id=_arc_scene_id,
                slot=_arc_slot,
                present=on_screen,
                flags=_arc_flags,
            )
            _arc_resistance_locks = active_arc_resistance_locks(
                _arc_design,
                scene_id=_arc_scene_id,
                slot=_arc_slot,
                present=on_screen,
                flags=_arc_flags,
            )
            for _lock in _arc_resistance_locks:
                _lock["name"] = _cname(str(_lock.get("character") or ""))
            _last_player_text = next((str(m.get("text") or "") for m in reversed(history)
                                      if m.get("role") == "user"), "")
            _arc_resistance_locks = mark_direct_challenges(_arc_resistance_locks, _last_player_text)
            _arc_resistance_block = _arc_prompt_block(_arc_resistance_locks)
        except (ArcDesignValidationError, TypeError, ValueError):
            # A private authoring draft must never make a live turn fail.  The
            # Director board reports its structural problems separately.
            _arc_surface = []
            _arc_resistance_locks = []
        _arc_lines: list[str] = []
        for _thread in (_arc_surface if isinstance(_arc_surface, list) else []):
            if not isinstance(_thread, dict):
                continue
            _character = str(_thread.get("character") or "").strip()
            if not _character:
                continue
            _facets = []
            if _thread.get("starting_belief"):
                _facets.append(f"holds to: {_thread['starting_belief']}")
            if _thread.get("protective_strategy"):
                _facets.append(f"protects themself by: {_thread['protective_strategy']}")
            if _thread.get("limitation"):
                _facets.append(f"is limited by: {_thread['limitation']}")
            if _thread.get("visible_tell"):
                _facets.append(f"on the surface: {_thread['visible_tell']}")
            if _facets:
                _arc_lines.append(f"- {_cname(_character)} — " + "; ".join(_facets))
            for _public_pressure in (_thread.get("public_pressure") or []):
                if isinstance(_public_pressure, str) and _public_pressure.strip():
                    _arc_lines.append(f"- On stage now: {_public_pressure.strip()}")
            for _turn in (_thread.get("possible_public_turns") or []):
                if not isinstance(_turn, dict):
                    continue
                _public_turn = str(_turn.get("surface") or "").strip()
                if _public_turn:
                    _arc_lines.append(f"- The moment can press: {_public_turn}")
        if _arc_lines:
            _arc_surface_block = (
                "CURRENT DRAMATIC SURFACE — these are only observable pressure and behavior. "
                "Play them through choice, evasion, pause, and action; do not diagnose a buried "
                "cause, force self-awareness, or turn them into a confession.\n"
                + "\n".join(_arc_lines)
            )

    system = (
        f"You are the narrator of an interactive novel titled \"{st.name}\".\n"
        f"PREMISE: {st.premise}\nTONE: {st.tone}\n" + _time_line
        + f"{player_line}\n"
        f"CAST (use these names):\n{cast}\n"
        f"LOCATIONS (the scene is in exactly one):\n{locs}\n"
        + (f"PLACES (locations holding character 'spots' — honor who is usually where; a "
           f"character at home is in their spot unless the scene says otherwise):\n{places}\n" if places else "")
        + "\n" + PLAY_CRAFT + "\n\n"
        "Narrate the next moment in-world, responding to the player: second person to the player, "
        "plus the characters' action and dialogue. Keep it brief — 2-3 short paragraphs, a "
        "screenful at most; advance ONE beat, don't run ahead. Respond with the NARRATION "
        "ONLY — no headers, no lists, no JSON, no out-of-story commentary."
    )
    # This is deliberately adjacent to the player's action, not buried in
    # genre/world prose.  A player can attempt a thing; they cannot make an
    # impossible result canonical merely by writing it in first person.
    if _player_action_guard.get("prompt_block"):
        system += "\n\n" + _player_action_guard["prompt_block"]
    if _scenario_public:
        system += "\n\n" + _scenario_public
    if _arc_surface_block:
        system += "\n\n" + _arc_surface_block
    if _arc_resistance_block:
        system += "\n\n" + _arc_resistance_block
    if _compiled:
        system += (
            "\nKNOWLEDGE BOUNDARY — the authored runtime controls time, location, and who is "
            "physically here. Do not introduce an unlisted person, make someone act on an event "
            "they did not witness, or reveal hidden director actions. A character can learn a fact "
            "only through this scene, a prior witnessed scene, or an explicit disclosure."
        )
        if _returner_memory:
            system += (
                "\n\nRETURNER MEMORY — these are private memories carried by the player alone. "
                "Other characters do not know them and must not react to them unless the player "
                "shares evidence in the scene:\n- " + "\n- ".join(_returner_memory)
            )

    # SCENE + PERSPECTIVE — the co-location invariant and the sticky POV. The narrator holds one
    # viewpoint at a time and does not head-hop; who's in the room stays put unless someone
    # physically enters or leaves. Feeding last turn's pov/members is what makes the flow stable.
    loc_now = next((l.name for l in st.locations if l.id == cur), cur)
    _pov_name = (_cname(prior_pov) if prior_pov else player_name)
    _member_names = ", ".join(roster) if roster else ""
    if prior_pov or _member_names:
        system += (
            f"\n\nPERSPECTIVE — tell this turn through ONE viewpoint (close third, or the player's "
            f"own view). Right now it follows: {_pov_name}. KEEP it there — render only what "
            f"{_pov_name} can perceive; never head-hop into another character's private thoughts. "
            f"Shift the viewpoint ONLY when {_pov_name} leaves the scene, the player moves "
            f"elsewhere, or a clear scene break occurs.\n"
            + (f"SCENE — you are in {loc_now}. PRESENT, exactly: {_member_names}. Write ONLY these "
               f"people — you may NOT bring anyone else on stage and may NOT remove anyone here. "
               f"The story decides who enters or leaves; if someone should arrive, the directive "
               f"below will say so.\n" if _member_names else "")
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
    # FAMILIARITY GATE — the narration only reveals what THE OBSERVER (the player's own character,
    # else the POV) could know about each character. surface = anyone; known = people they're
    # bonded to; secret = self only (a character's buried layer never leaks through mere presence —
    # it surfaces through the plot). Keeps deep secrets deep. See [[bond-depth-weave]].
    from ..pipeline.character_scaffold import (entry_tier as _entry_tier,
                                              entry_when as _entry_when,
                                              select_exemplars as _select)
    _observer = player_char or pov_key
    # SETTING STAGE gate: a `when:<id>` exemplar is live only while that stage is active. Active
    # stages are world_state flags prefixed `cond:` (the director flips them; slice c). When a stage
    # holds, its situational content is drawn FIRST — the whole cast re-reads through that lens.
    _active_conds = {k[5:] for k, v in (world_state.get("flags") or {}).items()
                     if isinstance(k, str) and k.startswith("cond:") and v}
    _bonded = set()
    for _r in (st.relationships or []):
        if _r.source == _observer:
            _bonded.add(_r.target)
        elif _r.target == _observer:
            _bonded.add(_r.source)

    def _allowed_tiers(k):
        # SECRET tier is NEVER handed to the narrator (AI can't hold a secret it can see — it leaks
        # or confabulates). Consistency comes from surface/known being authored as the behavioral
        # SHADOW of the secret. Secret content is director/reveal-only. See [[bond-depth-weave]].
        if k == _observer or k in _bonded:
            return {"surface", "known"}              # your own / an intimate's known layer
        return {"surface"}                           # a stranger reads only the daylight face

    def _embody(k, n):
        c = ctx.base_settings.characters.get(k)
        if c is None or n <= 0:
            return ""
        scope = re.sub(r"[^\w\-]+", "_", str(k))
        # SALIENCE: rank the FULL bank against the current beat (BM25+vector RRF — the same
        # retrieval the lore lane uses), so the exemplars that voice this character are the
        # ones the scene is actually touching, not a static top-N. Opening turn (no beat) or
        # empty index falls back to priority order. Never pre-truncate before the gates.
        pool = (_LS0.retrieve(ctx.root, _beat, [scope], top_k=200, one_per_facet=False)
                if _beat.strip() else [])
        ranked = bool(pool)
        # completeness backstop: an entry the search missed (no vector, no lexical hit) still
        # reaches the gates — appended after the ranked ones, so salience order is preserved.
        full = _LS0.top_by_priority(ctx.root, scope, 500)
        _have = {(e.id, e.title) for e in pool}
        pool = pool + [e for e in full if (e.id, e.title) not in _have]
        ex = _select(pool, allowed_tiers=_allowed_tiers(k), active_conds=_active_conds, n=n)
        # A guard renders as a [steer] directive (deflect away from a subject); everything else as
        # its facet. The narrator obeys steers without knowing why — that's how the secret holds.
        lines = "\n".join(
            f"    · [{'steer' if (e.facet or '') == 'guard' else (e.facet or 'life')}] {e.content}"
            for e in ex if e.content)
        block = f"- {c.name}:\n{lines}" if lines else ""
        # Behavioral backstop (NOT the secret — the model never sees that): when the beat is
        # BRUSHING this character's closed ground (a secret-tier entry ranks salient), tell the
        # narrator they DEFLECT there rather than produce an answer, so a pointed question doesn't
        # get a confabulated revelation. Salience-gated so they aren't cagey in innocent scenes;
        # without a relevance ranking (opening turn) it stays on whenever a secret exists.
        if any(_entry_tier(e) == "secret" for e in (pool[:max(n * 2, 8)] if ranked else pool)):
            guard = (f"    · [closed] {c.name} has ground they keep closed off. Pushed onto it, they "
                     f"deflect, deny, go quiet, or change the subject — they do NOT have a "
                     f"revelation to give in this scene, and never invent one. Play the deflection.")
            block = (block + "\n" + guard) if block else f"- {c.name}:\n{guard}"
        return block
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
                   "below; stay true to these, they ARE the person. A [steer] line is a subject "
                   "they turn AWAY from: when it comes up, deflect exactly as written and do NOT "
                   "explain why — you don't know why, and neither do they let on:\n" + embodiment)
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
    from ...server.services import lorebook_store as _LS
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

    # A compiled story may have a private director plan and author-only lore.
    # Do not turn a generic retrieval query into a side channel around its
    # presence ledger.  Its safe continuity comes from shared scene memory,
    # selected public scene surface, and explicitly narrated facts instead.
    if _compiled:
        _lore_block = ""
    else:
        from ...server.services.lorebook import format_lore_block
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
    from . import state as _SE
    _focus_names = {_cname(k).lower() for k in (immediate | ({pov_key} if pov_key else set()))}
    _state_for_prompt = world_state
    if _compiled:
        # These global ledgers are useful to the engine, but they are not a
        # character's knowledge.  Their contents can only re-enter through a
        # witnessed scene or the player's explicit disclosure.
        _state_for_prompt = dict(world_state)
        _state_for_prompt["flags"] = {}
        _state_for_prompt["inventory"] = []
        _state_for_prompt["log"] = []
    _state_block = _SE.render_state(_state_for_prompt, focus=_focus_names or None)
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

    _dsel = [] if _compiled else sorted((world_state.get("details") or []), key=_dscore, reverse=True)[:3]
    _open = [] if _compiled else [p for p in (world_state.get("promises") or []) if p.get("status") == "open"]
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
    # the whole web every turn. project_web filters the loaded story's edges to the focus set.
    from ..world.creation import project_web
    _focus = set(immediate)
    _relset = [r.model_dump() for r in st.relationships]
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

    # RELATIONSHIP LEVELS: the daylight read above is what anyone watching the pair would see.
    # The UNDERCURRENT (a bond's potential/trajectory — the buried weave) is gated like a secret:
    # the observer FEELS the unspoken layer of THEIR OWN bonds (play its weight) but never gets the
    # payload, and never sees other people's undercurrents at all. Reveals are the story's to time.
    _uc = []
    for _r in _relset:
        _s, _t = _r.get("source"), _r.get("target")
        if _observer and _observer in (_s, _t) and ((_r.get("potential") or _r.get("trajectory"))):
            _other = _t if _s == _observer else _s
            if _other in _focus:
                _uc.append(_cname(_other))
    if _uc:
        system += ("\n\n[SEALED] Something unspoken runs beneath " + _cname(_observer) + "'s bond with "
                   + ", ".join(sorted(set(_uc))) + " — let it weight the scene (the pauses, what goes "
                   "unsaid), but do NOT name or reveal what it is. That surfaces only when the story "
                   "decides, not under pressure in this moment.")

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
    if _player_action_guard.get("blocked"):
        scribe_system += (
            "\n\nPLAYER ACTION AUTHORITY — this turn contains an unsupported supernatural claim. "
            "Report only the visible attempt and genuine character reactions. Do not emit an item, "
            "detail, promise, fact, flag, movement, entity change, or ability caused by that claim."
        )
    # ARC MILESTONE WATCH: when a planned arc is active, the scribe (already reading every
    # turn) checks the narration against the current stage's completion condition — free
    # advancement, no extra call. See storymaster.arc_milestone/advance_arc.
    from .director import arc_milestone
    _ms = arc_milestone(world_state)
    if _ms:
        scribe_system += ("\n- arc_milestone: true ONLY if the narration just accomplished "
                          f"this exact story milestone: \"{_ms}\". A step toward it is NOT "
                          "it — the moment itself must happen on the page. Else false.")

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
    from .director import plot_direction, scene_block
    _plot_block = plot_direction(world_state, st)
    # The per-SCENE director's standing agenda (planned once at the scene boundary by
    # step_scene; here we surface it on every LATER turn of the same scene — zero LLM cost).
    _plan = world_state.get("scene_plan") if isinstance(world_state.get("scene_plan"), dict) else {}
    _scene_dir = scene_block(_plan) if (_plan.get("space") or "") == cur else ""
    consequence_system = (
        "You are the DIRECTOR of this story — you work out what ACTUALLY happens AND move the story "
        "forward. Given the situation and what the player does: what does the action directly "
        "cause? How does each character present react — in character, for real reasons? What "
        "changes, and what new problem, cost, or opening does it create? Reason it through; be "
        "concrete and causal, never atmospheric. Don't merely react — ADVANCE the story toward its "
        "arc (below): let pressure build, complications land, and ripe threads pay off when the "
        "moment allows (never force it). Keep every established fact true. Bring people on "
        "GRADUALLY — at most ONE new person steps into the scene at a time; people merely mentioned "
        "or remembered stay offstage. End on the real choice or problem the player now faces. "
        "Output a short numbered list of what happens, in order — not prose.\n\n"
        + (f"{_plot_block}\n\n" if _plot_block else "")
        + (f"{_scene_dir}\n\n" if _scene_dir else "")
        + f"WHERE: {loc_now}. {_pres_line}\n"
        f"CAST (who exists in this story; anyone on stage or brought in by the action is present):\n{cast}\n"
        + (f"\n{_state_block}\n" if _state_block else "")
        + (f"\n{_cont_block}\n" if _cont_block else "")
        + (f"\n{_rel_block}\n" if _rel_block else "")
        + (f"\n{_scenario_public}" if _scenario_public else "")
        + (f"\n{_arc_surface_block}" if _arc_surface_block else "")
        + (f"\n{_arc_resistance_block}" if _arc_resistance_block else "")
    )
    if _player_action_guard.get("prompt_block"):
        consequence_system += "\n\n" + _player_action_guard["prompt_block"]

    lanes = {"craft": len(PLAY_CRAFT), "cast": len(cast), "places": len(places),
             "embodiment": len(embodiment), "player_back": len(player_back),
             "lore": len(_lore_block), "state": len(_state_block), "relationships": len(_rel_block),
             "continuity": len(_cont_block), "history": len(transcript),
              "scene_dir": len(_scene_dir), "arc_surface": len(_arc_surface_block),
              "arc_resistance": len(_arc_resistance_block),
              "player_authority": len(_player_action_guard.get("prompt_block") or ""),
              "consequence": len(consequence_system),
             "system": len(system), "scribe": len(scribe_system)}

    return {"system": system, "prompt": prompt, "scribe_system": scribe_system,
             "consequence_system": consequence_system, "roster": roster,
             "cur": cur, "prior_pov": prior_pov, "shared_recent": shared_recent,
             # Server-only: includes private lexical guard terms and must never
             # be returned by a route or interpolated into any model prompt.
             "arc_resistance_locks": _arc_resistance_locks,
             "player_action_guard": _player_action_guard,
             "player_capabilities": _player_capabilities,
             "lanes": lanes}

"""Story stage-tool registry, context, and implementations."""

from __future__ import annotations

"""Shared context lookups used by story stage-tool implementations."""


STANCES = ("devoted", "warm", "neutral", "strained", "hostile")


def short(s, n: int = 6) -> str:
    """Limit relationship dynamics to a compact phrase."""
    return " ".join(str(s or "").split()[:n])


def character(ctx, body: dict):
    ch = ctx.base_settings.characters.get(body.get("character"))
    if ch is None:
        raise ValueError("no such character")
    return ch


def provider(ctx, body: dict, stage: str):
    resolved, systems = ctx.builder_ctx(body, stage)
    if resolved is None:
        raise RuntimeError(systems if isinstance(systems, str) else "no chat connection")
    return resolved, systems


def preset(ctx, body: dict) -> dict:
    from ..server.services import presets as _presets

    preset_id = (body.get("preset") or "").strip()
    return (_presets.get_preset(ctx.root, preset_id) if preset_id else None) \
        or _presets.active_preset(ctx.root) or {}


def critic_provider(ctx):
    from ..server.services import config_files as _config_files

    fallback = (_config_files.load_text_roles(ctx.root).get("fallback") or "").strip()
    if fallback:
        resolved = ctx.text_provider_for(fallback)
        if resolved is not None and hasattr(resolved, "generate_text"):
            return resolved
    return None


def as_agent(ctx, agent_id: str):
    from ..server.services import presets as _presets

    selected = _presets.get_preset(ctx.root, agent_id) \
        or _presets.get_preset(ctx.root, "character_smith") \
        or _presets.active_preset(ctx.root) or {}
    resolved = ctx.text_provider_for(
        (selected.get("model") or "").strip() or None,
        selected.get("params") or {},
        connection=selected.get("connection") or None,
    )
    return resolved, selected


# --- registration and dispatch ---

"""Registration and dispatch for story stage tools."""

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class StageTool:
    name: str
    describe: str
    keywords: list[str]
    produces: str
    run: Callable
    params: dict = field(default_factory=dict)


TOOLS: dict[str, StageTool] = {}


def stage_tool(name: str, *, describe: str, keywords: list | None = None,
               produces: str = "", params: dict | None = None) -> Callable:
    """Register a callable stage/action tool and normalize its parameter schema."""
    from . import scripts as _scripts

    def deco(fn: Callable) -> Callable:
        TOOLS[name] = StageTool(
            name=name,
            describe=describe,
            keywords=[str(k) for k in (keywords or [])],
            produces=produces,
            run=fn,
            params={k: _scripts._norm_param(v) for k, v in (params or {}).items()},
        )
        return fn

    return deco


def get(name: str) -> StageTool | None:
    return TOOLS.get(name)


def catalog() -> list[dict]:
    """Return the stage tools advertised to authoring and agent surfaces."""
    return [
        {"fn": tool.name, "kind": "stage", "describe": tool.describe,
         "keywords": list(tool.keywords), "produces": tool.produces}
        for tool in TOOLS.values()
    ]


def run_stage(ctx, name: str, body: dict | None = None) -> dict:
    """Run a named tool against the supplied story context."""
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(f"no such stage tool: {name!r}")
    return tool.run(ctx, body or {})


# --- relationship mutations ---

"""Deterministic relationship mutations used by consolidation stage tools."""



def apply_rel_changes(rels, idx, ck, raw_changes, bykey, present, new_id):
    """Apply one character's co-presence-gated relationship changes.

    Returns ``(rels, changes, blocked)``. Removing a relationship rebuilds ``rels``, so
    callers must use the returned list.
    """
    changes, blocked = [], []
    for change in raw_changes:
        target_key = bykey.get(str(change.get("toward", "")).strip().lower())
        if not target_key or target_key == ck:
            continue
        if ck not in present or target_key not in present:
            blocked.append({"source": ck, "target": target_key, "why": "not co-present in events"})
            continue
        dynamic = short(change.get("dynamic")).strip()
        stance = change.get("stance") if change.get("stance") in STANCES else None
        nature = (change.get("nature") or "").strip()
        retire = bool(change.get("retire"))
        if not (dynamic or stance or nature or retire):
            continue
        if retire:
            if (ck, target_key) in idx:
                dead = idx.pop((ck, target_key))
                rels = [item for item in rels if item is not dead]
                changes.append({"toward": target_key, "retired": True})
            continue
        relationship = idx.get((ck, target_key))
        if relationship is None:
            relationship = {"id": new_id(), "source": ck, "target": target_key,
                            "nature": "", "dynamic": "", "stance": "neutral"}
            rels.append(relationship)
            idx[(ck, target_key)] = relationship
        if nature:
            relationship["nature"] = nature
        if dynamic:
            relationship["dynamic"] = dynamic
        if stance:
            relationship["stance"] = stance
        changes.append({"toward": target_key, "nature": relationship["nature"],
                        "dynamic": dynamic, "stance": relationship["stance"]})
    return rels, changes, blocked


# --- tool implementations ---

"""Stage TOOLS — pipeline stages an agent can call from a story surface.

A graph SCRIPT (scripts.py) mutates a doc in place; a STAGE TOOL is heavier: it runs a
generation stage (storyboard, spine, cast, locations) against the active story + character
and returns a structured artifact. Agents trigger these in the workshop / play surfaces
(which have a character to operate on) — NOT in free /chat, which has nothing to run against.

Each runner reuses the very same pipeline units the dedicated stage endpoints use, just
synchronously (returns the result instead of streaming it). Declare a tool with
`@stage_tool(...)`; execute one with `run_stage(ctx, name, body)`; `catalog()` is the list
the Scripts panel / post-history protocol advertise.
"""


# Internal aliases preserve the existing stage implementations below.
_STANCES = STANCES
_as_agent = as_agent
_character = character
_critic_provider = critic_provider
_preset = preset
_provider = provider
_short = short



# ── The stages an agent can call ─────────────────────────────────────────────────

@stage_tool(
    "storyboard",
    describe="Run the storyboarder — generate the story's beats (logline, premise, tone, "
             "themes, BEATS) from the character and premise.",
    keywords=["storyboard", "story board", "storyboard this", "board it", "outline the story",
              "lay out the beats", "generate the beats", "map the beats", "draft the outline"],
    produces="board",
    params={"premise": "the story premise to board from (optional — defaults to the conversation)"},
)
def _storyboard(ctx, body: dict) -> dict:
    from .pipeline import board_to_graph, parse_storyboard, storyboard_inputs
    from .pipeline import grounding as _G
    ch = _character(ctx, body)
    provider, systems = _provider(ctx, body, "storyboard")
    base = body.get("spine") or body.get("graph") or {}
    premise = (body.get("premise") or "").strip()
    craft = _G.MINIMALISM
    system, prompt = storyboard_inputs(
        name=ch.name, persona=ch.system, extras=ctx.card_extras(ch, body["character"]),
        systems=systems, premise=premise, spine=base, craft=craft)
    res = provider.generate_text(system=system, prompt=prompt)
    board = parse_storyboard(res.text or "")
    board = _G.declichify_titles(provider, board)   # post-filter clichéd beat titles
    # Hand back BOTH: the rich board AND a development-graph projection the canvas can render
    # (beats → nodes), merged onto the working graph's spine so wound/lie/truth aren't lost.
    return {"board": board, "graph": board_to_graph(board, base if isinstance(base, dict) else {})}


@stage_tool(
    "generate_image",
    describe="Render an image using THIS agent's image workflow (its preset's bound workflow + "
             "LoRA look, local or cloud). Pass the image prompt after a pipe.",
    keywords=["generate image", "render", "draw", "illustrate", "picture of", "show me",
              "make an image", "portrait of", "render the scene", "outfit image"],
    produces="image",
    params={"prompt": "what to render — the image prompt (booru tags or prose to match the workflow)",
            "negative": "things to avoid in the image (optional)"},
)
def _render(ctx, body: dict):
    """Render one image via THIS agent's preset image workflow. Returns (png_bytes, content_type,
    workflow_id, prompt). The single render path both image tools share."""
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("need a prompt to render an image")
    provider, mid = ctx.preset_image_provider(_preset(ctx, body))
    if provider is None:
        raise RuntimeError(mid)   # mid carries the resolution error (no workflow / no connection)
    res = provider.generate_image(prompt=prompt, negative_prompt=(body.get("negative") or None))
    if not getattr(res, "images", None):
        raise RuntimeError("the image workflow produced no image")
    return res.images[0], res.content_type, mid, prompt


def _data_uri(png: bytes, content_type: str) -> str:
    import base64
    return f"data:{content_type};base64," + base64.b64encode(png).decode()


def _generate_image(ctx, body: dict) -> dict:
    png, ct, mid, prompt = _render(ctx, body)
    return {"image": _data_uri(png, ct), "prompt": prompt, "workflow": mid}


# generate_image is registered AFTER its impl so the decorator wraps the final function.
generate_image = stage_tool(
    "generate_image",
    describe="Render an image using THIS agent's image workflow (its preset's bound workflow + "
             "LoRA look, local or cloud).",
    keywords=["generate image", "render", "draw", "illustrate", "picture of", "show me",
              "make an image", "portrait of", "render the scene", "outfit image"],
    produces="image",
    params={"prompt": "what to render — the image prompt (booru tags or prose to match the workflow)",
            "negative": "things to avoid in the image (optional)"},
)(_generate_image)


@stage_tool(
    "generate_story_cover",
    describe="Render AND SET the story's COVER image, using this agent's image workflow. Saves it "
             "as the story's background. Pass the cover prompt.",
    keywords=["cover", "story image", "cover image", "story art", "poster", "key art", "title image"],
    produces="image",
    params={"prompt": "what the cover shows — the image prompt (match the workflow's prompt style)",
            "negative": "things to avoid in the image (optional)"},
)
def _generate_story_cover(ctx, body: dict) -> dict:
    png, ct, mid, prompt = _render(ctx, body)
    out = {"image": _data_uri(png, ct), "prompt": prompt, "workflow": mid, "cover": True}
    # Persistence is baked in: save it as the story's cover when we know which story we're in.
    key = (body.get("story") or "").strip()
    if key and ctx.base_settings.stories.get(key) is not None:
        out["url"] = ctx.save_story_bg(key, png)
        out["saved"] = True
    return out


@stage_tool(
    "create_character",
    describe="Generate a COMPLETE character from a brief (name + persona + role), mint a card for "
             "it, and add it to the cast. The autonomous creator — for an AI (or agent) to invent "
             "a fitting character without a turn-by-turn interview.",
    keywords=["create a character", "invent a character", "generate a character", "make a character",
              "new cast member", "we need a character", "add a character who"],
    produces="character",
    params={"brief": "who to create — a sentence or two: their role, vibe, what they bring",
            "name": "their name (optional — invent a fitting one if omitted)",
            "relate_to": "optional — the existing cast member (name or key) to anchor this new "
                         "character to; if omitted the creator picks the most fitting one"},
)
def _create_character(ctx, body: dict) -> dict:
    from .pipeline import PROTAGONIST_SCHEMA   # {name, persona, appearance, role}
    brief = (body.get("brief") or "").strip()
    if not brief:
        raise ValueError("need a brief describing who to create")
    # Run as the character-creation agent (character_smith), NOT whatever preset is active — a weak
    # local model produces generic fluff. Falls back to the active preset only if that agent is unset.
    from ..server.services import presets as _P
    smith = _P.get_preset(ctx.root, "character_smith") or {}
    preset = smith if (smith.get("model") or "").strip() else _preset(ctx, body)
    # Generous token budget: these are LARGE structured outputs (5 seeds, the full character, 6-9
    # exemplars). A small cap truncates them into invalid JSON — the real cause of the "flaky" empty/
    # garbled results. 40k is a ceiling, not a reservation.
    _params = {"max_tokens": 40000, **(preset.get("params") or {})}
    provider = ctx.text_provider_for((preset.get("model") or "").strip() or None,
                                     _params, connection=preset.get("connection") or None)
    if provider is None:
        raise RuntimeError("no chat connection for the character creator")
    from .pipeline._helpers import _ANTI_FLUFF
    name = (body.get("name") or "").strip()
    system = (preset.get("system") or
              "You write characters the way a novelist does: a specific person, observed, not a "
              "concept dressed up.") + "\n\n" + _ANTI_FLUFF

    # Story OVERVIEW tints the register; the existing CAST is fed in so the new character diverges
    # (uniqueness). Grounding (wound/lie/contradiction) lives in the agent's system prompt.
    skey = (body.get("story") or "").strip()
    st = ctx.base_settings.stories.get(skey) if skey else None
    overview, cast_lines, cast_opts = "", "", []
    if st is not None:
        themes = ", ".join(str(t) for t in (st.themes or []) if t)
        overview = "\n".join(p for p in [
            f"STORY: {st.premise or ''}", f"TONE: {st.tone or ''}",
            f"THEMES: {themes}" if themes else ""] if p)
        for m in st.cast:
            c = ctx.base_settings.characters.get(m.character)
            if c:
                cast_opts.append({"name": c.name, "key": m.character})
                cast_lines += f"- {c.name}: {(c.system or '')[:160]}\n"

    # RELATIONAL definition: every character after the FIRST is anchored to an existing cast member
    # via a specific bond — defined by who they know, not in a vacuum. Deepens grounding AND builds
    # the relationship web. The first/main character (empty cast) is the exception.
    # Optional caller-forced anchor (relate_to = a name or key); else the model chooses.
    rq = (body.get("relate_to") or "").strip().lower()
    forced = next((o for o in cast_opts if rq and (o["key"].lower() == rq or o["name"].lower() == rq)), None)

    import copy as _copy
    relate = bool(cast_opts)
    schema = _copy.deepcopy(PROTAGONIST_SCHEMA)
    if relate:
        _stance_enum = ["devoted", "warm", "neutral", "strained", "hostile"]
        schema["properties"]["relationships"] = {
            "type": "array", "minItems": 1, "maxItems": 3,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["to", "nature", "dynamic", "stance", "back_nature", "back_dynamic", "back_stance"],
                "properties": {
                    "to": {"type": "string", "description": "EXACT name of the existing cast member"},
                    "nature": {"type": "string", "description": "the KIND of bond the NEW character has "
                               "with them (rival / mentor / lover / sibling / debtor / …)"},
                    "dynamic": {"type": "string", "description": "2-3 WORDS for how the new character "
                                "feels about them ('wary respect', 'old grudge') — never a sentence"},
                    "stance": {"type": "string", "enum": _stance_enum,
                               "description": "coarse feeling of NEW toward them (graph colour only)"},
                    "back_nature": {"type": "string", "description": "the KIND of bond the OTHER has back "
                                    "(often different)"},
                    "back_dynamic": {"type": "string", "description": "2-3 WORDS for how the OTHER feels "
                                     "BACK (usually asymmetric) — never a sentence"},
                    "back_stance": {"type": "string", "enum": _stance_enum,
                                    "description": "coarse feeling of the OTHER back (graph colour only)"},
                },
            },
        }
        schema["required"] = list(schema.get("required", [])) + ["relationships"]

    from .pipeline import grounding as _G

    base_ctx = "\n\n".join(p for p in [
        f"BRIEF: {brief}",
        (f"NAME: {name}" if name else "Invent a fitting NAME."),
        (f"STORY OVERVIEW (match this register; keep them psychologically real):\n{overview}"
         if overview else ""),
        (f"EXISTING CAST — make the new character clearly DISTINCT from these (different drive, "
         f"voice, reactions):\n{cast_lines}" if cast_lines else ""),
    ] if p)
    rel_instr = ("ANCHOR THEM RELATIONALLY: define this character through 1-3 specific RELATIONSHIPS "
                 "to existing cast above — set each `relationships[].to` to that person's EXACT name"
                 + (f", and INCLUDE {forced['name']}" if forced else "")
                 + ". For EACH bond give how THIS character feels (nature/value) AND how the OTHER "
                 "feels back (back_nature/back_value) — bonds are usually asymmetric.") if relate else ""

    # ── DIVERGE (Verbalized Sampling, arXiv:2510.01171): cheap, varied seeds + honest typicality.
    # Picking off the low-typicality tail escapes the model's modal "stock" character.
    seed_schema = {
        "type": "object", "additionalProperties": False, "required": ["candidates"],
        "properties": {"candidates": {"type": "array", "minItems": 4, "maxItems": 5, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "hook", "typicality"],
            "properties": {
                "name": {"type": "string"},
                "hook": {"type": "string", "description": "1-2 sentences: the concrete, distinctive core "
                         "of this person — a specific situation, not adjectives"},
                "typicality": {"type": "number", "description": "0 = wildly unexpected for this brief, "
                               "1 = the obvious stock version. Rate honestly."},
            }}}},
    }
    seeds = ((provider.generate_text(system=system, prompt=(
        base_ctx + "\n\nPropose 4-5 GENUINELY DIFFERENT people who could fit this brief. HOLD FIXED "
        "every fact the brief states (their role, gender, relationship, age if given) — diverge only "
        "on what's left open: temperament, history, class, the angle. Each gets a concrete `hook` and "
        "an honest `typicality`. JSON only."), emits=seed_schema).data) or {}).get("candidates") or []

    chosen_hook = brief
    if seeds:
        for s in seeds:
            s["_cliche"] = bool(_G.cliches_in(str(s.get("hook", ""))))
        # SELECT with an EXTERNAL signal where possible (intrinsic self-critique is unreliable —
        # Huang et al. 2310.01798). Falls back to the deterministic VS tail (least typical, non-cliché).
        pick = None
        critic = _critic_provider(ctx)
        if critic is not None:
            sel_schema = {"type": "object", "additionalProperties": False,
                          "required": ["choice", "reason"], "properties": {
                              "choice": {"type": "integer"}, "reason": {"type": "string"}}}
            listing = "\n".join(f"[{i}] (typicality {s.get('typicality')}) {s.get('hook', '')}"
                                for i, s in enumerate(seeds))
            sel = (critic.generate_text(
                system="You are a sharp story editor. Choose the seed that is the most specific, "
                       "surprising and human, and the LEAST clichéd or generic.",
                prompt=f"SEEDS:\n{listing}\n\nReturn the index of the best one.",
                emits=sel_schema).data) or {}
            c = sel.get("choice")
            if isinstance(c, int) and 0 <= c < len(seeds):
                pick = seeds[c]
        if pick is None:
            pick = sorted(seeds, key=lambda s: (s["_cliche"], float(s.get("typicality") or 1.0)))[0]
        chosen_hook = pick.get("hook") or brief
        if not name:
            name = (pick.get("name") or "").strip()

    # ── DEEPEN: expand the chosen seed into a fully grounded character. Required Conflict + Growth
    # fields (CharacterGPT) + Weiland's lie/wound/want/need/arc tuple, with the Big-Five facet palette
    # and retrieved craft + psyche notes steering toward concrete, dimensional people.
    deep_schema = _copy.deepcopy(schema)
    extra = {
        "conflict": {"type": "string", "description": "their central INNER contradiction — the "
                     "competing motivations that pull them apart"},
        "growth": {"type": "string", "description": "how they could plausibly change across a story"},
        "lie": {"type": "string", "description": "the false belief about themselves or the world they hold"},
        "wound": {"type": "string", "description": "the concrete past hurt that planted the lie"},
        "want": {"type": "string", "description": "the external thing they actively chase"},
        "need": {"type": "string", "description": "the truth they must face to grow"},
        "arc_type": {"type": "string",
                     "enum": ["positive change", "flat", "disillusionment", "fall", "corruption"]},
    }
    deep_schema["properties"].update(extra)
    deep_schema["required"] = list(deep_schema["required"]) + list(extra)
    craft = _G.MINIMALISM
    deep_prompt = "\n\n".join(p for p in [
        base_ctx,
        f"THE PERSON TO WRITE — develop THIS seed and keep its specific angle:\n{chosen_hook}",
        _G.ADAPTATION,
        rel_instr,
        craft, _G.CONCRETENESS,
        "Write this ONE person in full: fill conflict, growth, lie, wound, want, need and arc_type, "
        "and a vivid `persona` built from concrete particulars (a daily situation, an object they "
        "keep, a line they actually say, a habit, a contradiction). JSON only.",
    ] if p)
    data = (provider.generate_text(system=system, prompt=deep_prompt, emits=deep_schema).data) or {}
    cname = (data.get("name") or name or "Unnamed").strip()

    # ── POST-FILTER: bans live here, not in the prompt. Regenerate a clichéd role line ONCE.
    if _G.looks_cliche(str(data.get("role") or "")):
        fix = (provider.generate_text(system=system, prompt=(
            f"This role line is a clichéd epithet: {data.get('role')!r}. Rewrite it as a PLAIN 1-4 "
            f"word job or relationship for {cname} (like 'village healer' or 'the boy's mother'). "
            "Return only the words.")).text or "").strip().strip('".')
        if fix and not _G.looks_cliche(fix):
            data["role"] = fix[:60]
    # ── REFINE: a cliché DETECTOR is a legitimate external signal (intrinsic self-grading is not —
    # Huang et al.). If the persona leans on fluff, revise it ONCE toward the concrete.
    persona0 = data.get("persona") or brief
    pc = _G.cliches_in(persona0)
    if len(pc) >= 2:
        rev = (provider.generate_text(system=system + "\n\n" + _G.CONCRETENESS, prompt=(
            f"This character description leans on clichéd/abstract phrasing (e.g. {', '.join(pc[:4])}). "
            f"Rewrite it concrete and specific — same facts, same length, plain words:\n\n{persona0}"
        )).text or "").strip()
        if rev and len(_G.cliches_in(rev)) < len(pc):
            data["persona"] = rev
    persona = data.get("persona") or brief
    res = ctx.write_character({"name": cname, "system": persona,
                               "fields": {"role": data.get("role") or "",
                                          "appearance": data.get("appearance") or "",
                                          # Weiland tuple + CharacterGPT conflict/growth — drive arcs
                                          # and keep the character dimensional downstream.
                                          **{k: data.get(k) or "" for k in
                                             ("conflict", "growth", "lie", "wound", "want", "need",
                                              "arc_type")}}}, None)
    key = (res or {}).get("key", "") if isinstance(res, dict) else ""
    out = {"character": key, "name": cname, "persona": data.get("persona", ""),
           "role": data.get("role", "")}

    # Persist the anchoring relationships into the web — BOTH directions (reciprocal), 1-3 anchors.
    rel_items = (data.get("relationships") or []) if relate else []
    if key and rel_items and st is not None:
        from . import scripts as _S
        _stance = lambda v: v if v in _STANCES else "neutral"  # noqa: E731
        rels = [r.model_dump() for r in st.relationships]
        seen = {(r["source"], r["target"]) for r in rels}
        added = []
        for it in rel_items:
            if not isinstance(it, dict):
                continue
            a = next((o for o in cast_opts
                      if o["name"].strip().lower() == str(it.get("to", "")).strip().lower()), None)
            if a is None:
                continue
            if (key, a["key"]) not in seen:                       # new → anchor
                rels.append({"id": _S.new_id(), "source": key, "target": a["key"],
                             "nature": str(it.get("nature", ""))[:80],
                             "dynamic": _short(it.get("dynamic")), "stance": _stance(it.get("stance"))})
                seen.add((key, a["key"]))
            if (a["key"], key) not in seen:                       # anchor → new (reciprocal)
                rels.append({"id": _S.new_id(), "source": a["key"], "target": key,
                             "nature": str(it.get("back_nature", ""))[:80],
                             "dynamic": _short(it.get("back_dynamic")), "stance": _stance(it.get("back_stance"))})
                seen.add((a["key"], key))
            added.append({"to": a["name"], "nature": str(it.get("nature", ""))[:80],
                          "dynamic": _short(it.get("dynamic")), "stance": _stance(it.get("stance"))})
        # A caller-forced anchor must appear even if the model skipped it.
        if forced and not any(x["to"] == forced["name"] for x in added):
            if (key, forced["key"]) not in seen:
                rels.append({"id": _S.new_id(), "source": key, "target": forced["key"],
                             "nature": "connected", "dynamic": "", "stance": "neutral"})
            added.append({"to": forced["name"], "nature": "connected", "dynamic": "", "stance": "neutral"})
        if added:
            ctx.update_story_fields(skey, {"relationships": rels})
            out["relationships"] = added

    # Ground them as concrete EXEMPLARS (life vignettes / sayings / reactions) written to the
    # character's own lorebook — the SAME substrate the interview path builds. This is what makes
    # a character feel like a specific real person, not a list of adjectives.
    if key:
        try:
            from ..server.services import lorebook_store as _LS
            from .pipeline.character_scaffold import FACETS_SCHEMA, facet_to_entry
            rel_ctx = ("RELATIONSHIPS (let these bonds colour their behaviour):\n"
                       + "\n".join(f"- {r['to']}: {r['nature']}" for r in out["relationships"])
                       if out.get("relationships") else "")
            _wein = "; ".join(f"{k}: {data.get(k)}" for k in
                              ("lie", "wound", "want", "need", "conflict") if data.get(k))
            fprompt = "\n\n".join(p for p in [
                f"CHARACTER: {cname}\nPERSONA:\n{persona}",
                (f"INNER FRAME — {_wein}" if _wein else ""),
                (f"STORY REGISTER:\n{overview}" if overview else ""),
                rel_ctx, _G.ADAPTATION,
                "Produce 6-9 concrete EXEMPLARS that make this character vivid and specific — a mix "
                "of life (a vivid past moment + what they did), saying (a line in their own voice), "
                "and reaction ('When <situation>, they <do/say>'). Each should reveal the inner frame "
                "above through a SPECIFIC action; idiosyncratic and surprising, match the register in "
                "voice. JSON only.",
            ] if p)
            # Exemplars are the LAST of several rapid calls, so they're the most likely to hit a
            # provider rate-limit (empty data, not an exception). Retry with backoff before giving up.
            import time as _time
            fac = {}
            for _attempt in range(3):
                fac = (provider.generate_text(system=system, prompt=fprompt, emits=FACETS_SCHEMA).data) or {}
                if fac.get("facets"):
                    break
                _time.sleep(2 * (_attempt + 1))
            saved = 0
            for f in fac.get("facets", []):
                e = facet_to_entry(f)
                if e is not None:
                    _LS.upsert_entry(ctx.root, key, e)
                    saved += 1
            out["exemplars"] = saved
        except Exception as exc:  # noqa: BLE001 — exemplars are enrichment; never lose the character
            out["exemplars_error"] = str(exc)

    # Kick off the portrait render right after minting — best-effort: a render failure (ComfyUI
    # off, no base workflow) must NOT lose the freshly-created character.
    if key:
        from ..server.services import full_gen as _FG
        try:
            out["portrait"] = _FG.render_reference(ctx, key)
        except Exception as exc:  # noqa: BLE001
            out["portrait_error"] = str(exc)
    # If we're inside a saved story, add the new character to its cast (idempotent by key).
    skey = (body.get("story") or "").strip()
    st = ctx.base_settings.stories.get(skey) if skey else None
    if key and st is not None:
        cast = [{"character": m.character, "primary": m.primary,
                 **({"outfit": m.outfit} if m.outfit else {})} for m in st.cast]
        if not any(m["character"] == key for m in cast):
            cast.append({"character": key, "primary": False})
            ctx.update_story_fields(skey, {"cast": cast})
            out["added_to_cast"] = True
    return out


@stage_tool(
    "design_wardrobe",
    describe="Delegate to the WARDROBE agent: design a coherent set of outfits for a character "
             "(runs as the Wardrobe agent) and save them to the character's portraits. Lets the "
             "Character agent hand styling off to the specialist.",
    keywords=["wardrobe", "outfit", "outfits", "dress them", "style them", "what they wear",
              "clothes", "attire", "give them an outfit"],
    produces="wardrobe",
    params={"character": "the character key to style",
            "brief": "optional styling direction (vibe, setting, constraints)"},
)
def _design_wardrobe(ctx, body: dict) -> dict:
    import re as _re

    from .pipeline import compose_outfit_prompt, plan_wardrobe
    key = (body.get("character") or "").strip()
    ch = ctx.base_settings.characters.get(key)
    if ch is None:
        raise ValueError(f"no such character {key!r} to style")
    prov, p = _as_agent(ctx, "wardrobe_stylist")   # invoke the Wardrobe agent
    if prov is None:
        raise RuntimeError("no chat connection for the wardrobe agent")
    appearance = (ch.fields or {}).get("appearance", "")
    systems = {"wardrobe": p["system"]} if p.get("system") else None
    # Build the story context — crucially the LOCATIONS, so outfits suit where the character goes.
    brief = (body.get("brief") or "").strip()
    skey = (body.get("story") or "").strip()
    st = ctx.base_settings.stories.get(skey) if skey else None
    if st is not None:
        story_ctx = {
            "premise": (st.premise or "") + (f"\nSTYLING DIRECTION: {brief}" if brief else ""),
            "tone": st.tone or "",
            "themes": list(st.themes or []),
            "storyboard": st.storyboard.model_dump() if getattr(st, "storyboard", None) else {},
            "locations": [{"name": l.name, "description": l.description or l.background_prompt or ""}
                          for l in st.locations],
        }
    else:
        story_ctx = {"premise": brief}
    plan = plan_wardrobe(prov, char_name=ch.name, persona=ch.system or "", appearance=appearance,
                         story=story_ctx, systems=systems)
    m = ctx.portrait_manifest(key)
    existing = {o.get("id") for o in m.get("outfits", [])}
    saved = []
    for o in plan.get("outfits", []):
        nm = (o.get("name") or "Outfit").strip()
        oid = _re.sub(r"[^\w\-]+", "_", nm.lower()).strip("_") or "outfit"
        base, i = oid, 2
        while oid in existing:
            oid, i = f"{base}_{i}", i + 1
        existing.add(oid)
        attire = (compose_outfit_prompt(prov, ch.system or "", appearance, nm,
                                        o.get("concept", "")) or {}).get("attire", "")
        m.setdefault("outfits", []).append(
            {"id": oid, "name": nm, "instruction": "", "prompt": attire, "attire_prompt": attire,
             "concept": o.get("concept", ""), "expressions": {}})
        saved.append({"id": oid, "name": nm, "concept": o.get("concept", "")})
    ctx.save_portrait_manifest(key, m)
    return {"character": key, "outfits": saved}


@stage_tool(
    "plan_cast_outfit",
    describe="Compose the detailed outfit PROMPT (the attire layer that stacks on each character's "
             "face/body description before image-gen) for ONE named outfit ACROSS THE CAST — for "
             "every cast member who already HAS that outfit but whose prompt hasn't been written "
             "yet. Skips anyone whose prompt is already composed. Use to roll a shared wardrobe "
             "staple (a uniform, the current outfit) out to everyone who wears it.",
    keywords=["outfit for everyone", "outfit for the cast", "rest of the cast", "for all characters",
              "this outfit for all", "same outfit for", "compose the outfit", "fill in the outfit",
              "everyone who wears", "across the cast", "whole cast outfit"],
    produces="wardrobe",
    params={"outfit": "the outfit NAME to compose across the cast (e.g. 'Casual', 'Gala gown', "
                      "'School uniform') — matched against each character's existing outfits"},
)
def _plan_cast_outfit(ctx, body: dict) -> dict:
    from loom.server.services.prompts import _regionize_prompt, _safe_image_tags, _snap_prompt

    from .pipeline import compose_outfit_prompt
    name = (body.get("outfit") or "").strip()
    if not name:
        raise ValueError("need an outfit name to compose across the cast")
    skey = (body.get("story") or "").strip()
    st = ctx.base_settings.stories.get(skey) if skey else None
    if st is None:
        raise ValueError("plan_cast_outfit needs a saved story (pass its key)")
    prov, _p = _as_agent(ctx, "wardrobe_stylist")   # the Wardrobe agent composes the prose
    if prov is None:
        raise RuntimeError("no chat connection for the wardrobe agent")

    target = name.lower()
    composed, skipped = [], []
    for m in st.cast:
        ch = ctx.base_settings.characters.get(m.character)
        if ch is None:
            continue
        man = ctx.portrait_manifest(m.character)
        outfit = next((o for o in man.get("outfits", [])
                       if (o.get("name") or "").strip().lower() == target), None)
        if outfit is None:
            continue                                   # doesn't possess this outfit → not ours to plan
        if (outfit.get("attire_prompt") or "").strip():
            skipped.append(ch.name)                     # already generated → leave it
            continue
        appearance = (ch.fields or {}).get("appearance", "")
        attire = (compose_outfit_prompt(prov, ch.system or "", appearance, outfit.get("name") or name,
                                        (outfit.get("concept") or "").strip()) or {}).get("attire", "")
        if not attire:
            continue
        attire = _regionize_prompt(_snap_prompt(_safe_image_tags(attire)))
        outfit["attire_prompt"] = outfit["prompt"] = attire
        outfit["unified"] = True
        ctx.save_portrait_manifest(m.character, man)
        composed.append(ch.name)
    return {"outfit": name, "composed": composed, "skipped": skipped}


@stage_tool(
    "set_story_title",
    describe="Set AND SAVE the story's title (persists to the story).",
    keywords=["title", "call it", "name the story", "story title", "rename the story", "titled"],
    produces="title",
    params={"value": "the story's title"},
)
def _set_story_title(ctx, body: dict) -> dict:
    title = (body.get("value") or body.get("title") or "").strip()
    if not title:
        raise ValueError("need a title")
    key = (body.get("story") or "").strip()
    if not key or ctx.base_settings.stories.get(key) is None:
        raise ValueError("save the story first — then I can set its title")
    ctx.update_story_fields(key, {"name": title})
    return {"title": title, "saved": True}


# ── Per-character scene memory: each character compresses the scene they were in ─────────────
# The RELIABLE memory layer — a narrow, native-model-friendly task done once PER present character
# (small context, parallelizable): "compress what happened to ME, and how it shifted how I feel."
# Memory is the character's own (perspectival); relationship drift is self-reported (so it's
# naturally asymmetric). This is the bookkeeping the storymaster should NOT do — it frees the
# storymaster to make the story interesting instead.
_SCENE_MEM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["memory", "relationship_changes"],
    "properties": {
        "memory": {"type": "string", "description": "1-2 sentences: what happened to THIS character "
                   "this scene and how they took it (their POV — a concrete remembered moment)"},
        "relationship_changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["toward", "shift", "stance"],
            "properties": {
                "toward": {"type": "string", "description": "EXACT name of another character present"},
                "shift": {"type": "string", "description": "2-3 WORDS for how I now feel about them "
                          "after this scene ('newfound trust', 'simmering resentment') — '' if unchanged"},
                "stance": {"type": "string", "enum": ["devoted", "warm", "neutral", "strained", "hostile"],
                           "description": "my coarse feeling toward them now (graph colour only)"},
            }}},
    },
}


@stage_tool(
    "record_scene",
    describe="Each character present compresses the scene from THEIR POV into their own memory and "
             "self-reports how it shifted their feelings — the reliable per-character memory layer "
             "(one focused pass per character).",
    keywords=["record the scene", "remember this", "log the scene", "after the scene",
              "scene memory", "what they remember"],
    produces="memories",
    params={"scene": "the scene / transcript that just happened",
            "present": "who was in it — names or keys, comma-separated (defaults to the whole cast)",
            "steps": "optional indexed transcript (list of per-turn lines); each character then "
                     "compresses ONLY the steps they witnessed (a late joiner sees just from their "
                     "arrival on). Falls back to the flat `scene` for everyone when omitted."},
)
def _record_scene(ctx, body: dict) -> dict:
    from ..server.services import lorebook_store as _LS
    from . import scripts as _S
    from ..runtime import state as _PC
    from .pipeline.character_scaffold import facet_to_entry

    scene = (body.get("scene") or "").strip()
    # Perception scoping: when the caller passes an indexed `steps` transcript + the world-state's
    # `presence` map, each character records only what THEY saw (join = no backlog). Otherwise the
    # flat `scene` goes to everyone (backwards-compatible).
    steps = body.get("steps") if isinstance(body.get("steps"), list) else None
    world = body.get("world_state") if isinstance(body.get("world_state"), dict) else {}
    if not scene and not steps:
        raise ValueError("need a scene to record")
    skey = (body.get("story") or "").strip()
    st = ctx.base_settings.stories.get(skey) if skey else None
    # Resolve present characters (param, else the story's cast).
    name_to_key = {}
    if st is not None:
        for m in st.cast:
            c = ctx.base_settings.characters.get(m.character)
            if c:
                name_to_key[c.name.strip().lower()] = m.character
    raw = [s.strip() for s in (body.get("present") or "").split(",") if s.strip()]
    if raw:
        present = []
        for r in raw:
            k = r if ctx.base_settings.characters.get(r) else name_to_key.get(r.lower())
            if k:
                present.append(k)
    else:
        present = list(name_to_key.values())
    if not present:
        raise ValueError("no present characters to record for")

    # Each character runs as the Character agent (cheap/native-friendly focused pass).
    prov, p = _as_agent(ctx, "character_builder")
    if prov is None:
        raise RuntimeError("no chat connection for the character manager")
    others_by_key = {k: ctx.base_settings.characters.get(k).name for k in present
                     if ctx.base_settings.characters.get(k)}
    key_by_name = {v.strip().lower(): k for k, v in others_by_key.items()}
    rels = [r.model_dump() for r in st.relationships] if st is not None else []
    idx = {(r["source"], r["target"]): r for r in rels}
    recorded = []
    for ck in present:
        ch = ctx.base_settings.characters.get(ck)
        if ch is None:
            continue
        cohort = ", ".join(n for k, n in others_by_key.items() if k != ck) or "(no one else)"
        # Their VIEW: only the steps this character witnessed (join → just the last message, no
        # backlog). Falls back to the whole scene when no indexed steps/presence were supplied.
        view = "\n".join(str(s) for s in _PC.visible(world, ck, steps)) if steps else scene
        if not view.strip():
            continue   # this character saw nothing this scene — nothing to record
        prompt = (f"You are {ch.name}. Others present: {cohort}.\n\nSCENE (what you witnessed):\n{view}\n\n"
                  "Compress THIS scene into your own memory (what happened to you, how you took it). "
                  "If the scene changed how you relate to anyone present, describe the NEW dynamic in "
                  "prose (`shift`) and pick a coarse `stance` — don't quantify it. JSON only.")
        try:
            data = (prov.generate_text(system=f"You are {ch.name}. {ch.system or ''}"[:1500],
                                       prompt=prompt, emits=_SCENE_MEM_SCHEMA).data) or {}
        except Exception:  # noqa: BLE001 — one character's failure never sinks the batch
            continue
        mem = (data.get("memory") or "").strip()
        ch_changes = []
        if mem:
            e = facet_to_entry({"type": "life", "title": "Scene", "content": mem,
                                "keywords": [w for w in (ch.name.split()[:1])]})
            if e is not None:
                e.source = "scene"
                _LS.upsert_entry(ctx.root, ck, e)
        for rc in data.get("relationship_changes", []):
            tk = key_by_name.get(str(rc.get("toward", "")).strip().lower())
            if not tk or tk == ck:
                continue
            shift = _short(rc.get("shift")).strip()
            stance = rc.get("stance") if rc.get("stance") in _STANCES else None
            if not shift and not stance:
                continue   # nothing actually changed
            r = idx.get((ck, tk))
            if r is None:
                r = {"id": _S.new_id(), "source": ck, "target": tk, "nature": "",
                     "dynamic": "", "stance": "neutral"}
                rels.append(r); idx[(ck, tk)] = r
            if shift:                       # DRIFT REWRITES the prose dynamic (not a counter)
                r["dynamic"] = shift
            if stance:
                r["stance"] = stance
            ch_changes.append({"toward": tk, "shift": shift, "stance": r["stance"]})
        recorded.append({"character": ck, "memory": mem[:120], "changes": ch_changes})

    if st is not None and recorded:
        ctx.update_story_fields(skey, {"relationships": rels})
    return {"recorded": recorded}


# ── Storymaster consolidation: ONE interpretive pass → structured per-character impacts ──────
# (Mechanical per-character memory now belongs to record_scene above — more reliable + native +
# parallel. This stays as the storymaster's interpretive pass; its real future job is making the
# story INTERESTING, reading the accreted memories rather than book-keeping them.)
# The token-efficient distill→route contract: the storymaster reads the events + a COMPACT cast
# digest (one call), decides how each character is affected (it may TWIST), and emits structured
# per-character deltas; we then APPLY them deterministically (relationship drift + new exemplars)
# — no per-character model call. This is the consolidation loop the storymaster entity runs.
_IMPACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["impacts"],
    "properties": {"impacts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["character", "summary", "relationship_changes", "new_exemplar"],
        "properties": {
            "character": {"type": "string", "description": "EXACT name of an affected cast member"},
            "summary": {"type": "string", "description": "how the events landed on them (your read — may twist)"},
            "relationship_changes": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["toward", "nature", "dynamic", "stance", "retire"],
                "properties": {
                    "toward": {"type": "string", "description": "EXACT name of the other cast member"},
                    "nature": {"type": "string", "description": "the KIND of bond NOW — set only when it "
                               "CHANGES (e.g. ally→rival, stranger→lover); '' to leave the nature as-is"},
                    "dynamic": {"type": "string", "description": "2-3 WORDS for how they now feel after "
                                "this (may twist resonantly) — never a sentence"},
                    "stance": {"type": "string", "enum": ["devoted", "warm", "neutral", "strained", "hostile"],
                               "description": "coarse feeling now (graph colour only)"},
                    "retire": {"type": "boolean", "description": "true ONLY if this bond is SEVERED / ended "
                               "by the events — removes the relationship entirely"},
                }}},
            "new_exemplar": {
                "type": "object", "additionalProperties": False,
                "required": ["type", "title", "content", "keywords"],
                "properties": {
                    "type": {"type": "string", "enum": ["life", "saying", "reaction", "none"]},
                    "title": {"type": "string"},
                    "content": {"type": "string", "description": "a concrete exemplar the events revealed; '' if none"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                }},
        }}}},
}


@stage_tool(
    "consolidate",
    describe="STORYMASTER consolidation: read what happened and decide how it lands on each "
             "character — relationships drift, reform (new bond), flip (ally→rival) or sever, and "
             "new exemplars form. Edges only change between characters the scene put together. The "
             "storymaster may TWIST how consequences ripple. One pass; applies the changes to the story.",
    keywords=["consolidate", "what happened", "aftermath", "the fallout", "process events",
              "reflect on", "the events", "while they sleep"],
    produces="consolidation",
    params={"events": "what happened — the events / log to consolidate (a summary or transcript)"},
)
def _consolidate(ctx, body: dict) -> dict:
    skey = (body.get("story") or "").strip()
    st = ctx.base_settings.stories.get(skey) if skey else None
    if st is None:
        raise ValueError("consolidation needs a saved story (pass its key)")
    events = (body.get("events") or "").strip()
    if not events:
        raise ValueError("need events to consolidate")
    cast = []
    for m in st.cast:
        c = ctx.base_settings.characters.get(m.character)
        if c:
            cast.append({"name": c.name, "key": m.character})
    if not cast:
        raise ValueError("no cast to consolidate onto")

    prov, p = _as_agent(ctx, "storymaster")
    if prov is None:
        raise RuntimeError("no chat connection for the storymaster")
    system = p.get("system") or (
        "You are the storymaster — a consolidation entity. Decide how events land on each character; "
        "you may TWIST how consequences ripple, in resonant but unexpected ways.")
    prompt = (f"CAST: {', '.join(c['name'] for c in cast)}\n\nWHAT HAPPENED:\n{events}\n\n"
              "For EACH affected character, give a short impact summary, any relationship shifts "
              "(toward whom, the NEW dynamic in prose, a coarse stance — never a number; set `nature` "
              "only if the KIND of bond changed, e.g. ally→rival; set `retire` true only if the bond is "
              "severed), and optionally ONE new exemplar the events reveal (type 'none' + empty content "
              "if there isn't one). You may twist how it lands. Output structured JSON only.")
    data = (prov.generate_text(system=system, prompt=prompt, emits=_IMPACT_SCHEMA).data) or {}

    # APPLY deterministically — relationship drift / restructure (create · flip nature · retire) +
    # new exemplars. CO-PRESENCE INVARIANT (GENESIS.md §7): an edge may only be created or changed in
    # a scene BOTH characters were present for. `present` = cast whose name appears in the events.
    import re
    from . import scripts as _S
    from ..server.services import lorebook_store as _LS
    from .pipeline.character_scaffold import facet_to_entry
    bykey = {c["name"].strip().lower(): c["key"] for c in cast}
    _ev = events.lower()
    def _present(nm: str) -> bool:
        toks = [t for t in re.split(r"\s+", (nm or "").lower()) if len(t) > 2]
        return any(re.search(rf"\b{re.escape(t)}\b", _ev) for t in toks)
    present = {c["key"] for c in cast if _present(c["name"])}
    rels = [r.model_dump() for r in st.relationships]
    idx = {(r["source"], r["target"]): r for r in rels}
    applied = []
    blocked = []
    for imp in data.get("impacts", []):
        if not isinstance(imp, dict):
            continue
        ck = bykey.get(str(imp.get("character", "")).strip().lower())
        if not ck:
            continue
        rels, changes, _blk = apply_rel_changes(
            rels, idx, ck, imp.get("relationship_changes", []), bykey, present, _S.new_id)
        blocked.extend(_blk)
        ex = imp.get("new_exemplar") or {}
        if isinstance(ex, dict) and (ex.get("content") or "").strip() and ex.get("type") in ("life", "saying", "reaction"):
            e = facet_to_entry({"type": ex["type"], "title": ex.get("title", ""),
                                "content": ex["content"], "keywords": ex.get("keywords") or []})
            if e is not None:
                e.source = "event"
                _LS.upsert_entry(ctx.root, ck, e)
                changes.append({"exemplar": ex.get("title") or ex["type"]})
        applied.append({"character": ck, "summary": str(imp.get("summary", "")), "changes": changes})

    ctx.update_story_fields(skey, {"relationships": rels})
    return {"consolidated": applied, "blocked": blocked}


# ── Lifecycle trigger: consolidation runs ONLY when the player sleeps or dies ────────────────
# Memory is NOT book-kept every turn. Instead the player resting/dying is the beat where the cast
# consolidates the scenes they witnessed (record_scene, runtime_state-scoped) — and on DEATH the
# storymaster sends the player back to a significant moment.
# The narrative thread keeps this many most-recent turns verbatim; older turns get compressed into
# memory on consolidation. ponytail: a flat constant — make it a story/world-state setting if tuning matters.
_RECENT_WINDOW = 8

_REWIND_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["step", "reason"],
    "properties": {
        "step": {"type": "integer", "description": "index of the past step to send the player back to"},
        "reason": {"type": "string", "description": "why this moment matters — what the player gets to redo"},
    },
}

# ── The storymaster's FEVER-DREAM ────────────────────────────────────────────────────────────
# When the player SLEEPS, the conflict pressures latent in the cast's hidden psychology surface NOT
# as in-play options but as a single FOREBODING fever-dream — surreal, oblique, ominous. The dreamer
# half-grasps it and can't shake it. This is the DM's conflict signal delivered as a portent. The
# fuel is the Author's scaffold (want/lie/wound/secret); the storymaster is the dream entity.
_DREAM_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["dream"],
                 "properties": {"dream": {"type": "string"}}}
_DREAM_SYS = (
    "You are the dream the protagonist sinks into when they sleep — the unconscious surfacing what is "
    "churning beneath the waking story. You are given the cast's HIDDEN psychology (want / lie / wound / "
    "secret) and how they regard each other and the dreamer ('you'). Distill the strongest latent "
    "PRESSURES into a single FOREBODING FEVER-DREAM: surreal, oblique, ominous dream-logic that ENCODES "
    "the dread without ever naming it — a portent the dreamer half-grasps and cannot shake on waking. "
    "Things stand in for people; spaces fold impossibly; something is subtly, deeply wrong. It is NOT a "
    "choice, NOT a question, NOT 'do you…', NOT a recap of events — it leaves a FEELING, not an "
    "instruction. Second person, present tense, 2-4 sentences. JSON only: {dream}."
)


def generate_dream(ctx, skey: str, world_state: dict, present: list | None = None, you: str = "") -> str:
    """Distil the on-stage cast's hidden pressures (want/lie/wound/secret + bonds + recent events) into
    ONE foreboding fever-dream the storymaster sends when the player sleeps. No options, no recap — a
    portent that lingers. Returns the dream prose, "" on any failure (must never sink the rest pass)."""
    try:
        prov, _p = _as_agent(ctx, "storymaster")
        if prov is None:
            return ""
        st = ctx.base_settings.stories.get(skey) if skey else None
        present = [k for k in (present or []) if k in ctx.base_settings.characters]
        if not present and st is not None:
            present = [m.character for m in st.cast]
        present = present[:8]
        cname = lambda k: getattr(ctx.base_settings.characters.get(k), "name", k)

        def _scaf(k):
            f = getattr(ctx.base_settings.characters.get(k), "fields", {}) or {}
            bits = [f"want: {f['want']}" if f.get("want") else "", f"lie: {f['lie']}" if f.get("lie") else "",
                    f"wound: {f['wound']}" if f.get("wound") else "", f"secret: {f['secret']}" if f.get("secret") else ""]
            inner = "; ".join(b for b in bits if b)
            return f"- {cname(k)}: {inner or '(unknown depths)'}"
        scaffolds = "\n".join(_scaf(k) for k in present) or "(no cast)"

        focus = set(present)
        relset = [r.model_dump() for r in (st.relationships if st else [])]
        bonds = []
        for r in relset:
            s, t = r.get("source"), r.get("target")
            if s not in focus and t not in focus:
                continue
            d = r.get("dynamic") or r.get("stance") or ""
            bonds.append(f"- {cname(s)} → {cname(t)}" + (f": {d}" if d else ""))
        bonds_txt = "\n".join(bonds) or "(no bonds)"

        steps = [str(s) for s in (world_state or {}).get("transcript", []) if str(s).strip()][-6:]
        recent = "\n".join(steps) or "(the story has barely begun)"
        prompt = (f"THE DREAMER = you ({you or 'the protagonist'}).\n\nTHE CAST, with hidden depths:\n{scaffolds}\n\n"
                  f"HOW THEY REGARD EACH OTHER AND YOU:\n{bonds_txt}\n\nWHAT HAS BEEN HAPPENING:\n{recent}\n\n"
                  f"Give the one foreboding fever-dream that rises from this.")
        data = (prov.generate_text(system=_DREAM_SYS, prompt=prompt, emits=_DREAM_SCHEMA).data) or {}
        return (data.get("dream") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


_CAST_CONSOLIDATE_EVERY = 50   # deepen cards + the running story summary every ~50 turns of play

_SUMMARY_SYS = (
    "Update the running STORY-SO-FAR: a tight, plain recap of what has actually happened, in order — "
    "carry the prior summary forward and fold in the recent turns. A few sentences, no drama. Output "
    "only the recap prose, nothing else."
)
_CARDS_SYS = (
    "For each character below, update its LIVING CARD from what the recent play revealed. Its foundation "
    "is not editable. CURRENT STATE is mutable: say what is true NOW about their situation, outward "
    "behavior, changing relationships, and active pressure. HISTORY is one compact, irreversible event "
    "that explains why the state changed. Do not manufacture growth or resolve an unresolved tension. "
    "Output ONE "
    "record per character, records separated by a line of only '---':\n"
    "NAME: <name>\nCURRENT: <short present-tense card>\nHISTORY: <one evidence-based change, or blank>"
)
_ARC_SEGMENT_SYS = (
    "Read the completed stretch of an interactive story. Decide whether its driving question has "
    "resolved or clearly shifted enough to close an arc. Be conservative: do not segment merely "
    "because time passed. Output exactly two lines:\nCLOSE: yes or no\nTITLE: a plain 2-6 word arc title"
)


def consolidate_cast(ctx, skey: str, world_state: dict, sid: str = "") -> dict:
    """Every ~`_CAST_CONSOLIDATE_EVERY` turns, consolidate raw evidence into residual graph claims.

    The normal path never rewrites a character card or replaces source text. A legacy card projection
    can be explicitly enabled per session with ``legacy_card_consolidation`` while old saves migrate.
    This self-gates and never sinks a live turn.
    """
    out: dict = {"summary": "", "cards": [], "residuals": {"applied": [], "rejected": [], "turns": []}}
    try:
        steps = (world_state or {}).get("transcript")
        if not isinstance(steps, list) or not steps:
            return out
        start = int(world_state.get("cast_consolidated_through") or 0)
        if len(steps) - start < _CAST_CONSOLIDATE_EVERY:
            return out                                      # not enough new material yet
        prov, _p = _as_agent(ctx, "storymaster")
        if prov is None:
            return out
        recent = "\n\n".join(str(s) for s in steps[start:] if str(s).strip())[:9000]

        # Raw turns are preserved separately from this legacy prompt window. The
        # Storymaster reads them as prose and emits only small evidence-citing
        # residual patches through tools — no giant JSON object, no card rewrite.
        st = ctx.base_settings.stories.get(skey) if skey else None
        if st is not None:
            try:
                from ..records.residuals import consolidate as _consolidate_residuals
                out["residuals"] = _consolidate_residuals(
                    prov, st, world_state, characters=ctx.base_settings.characters)
            except Exception:  # noqa: BLE001 — a reflective pass must never sink play
                pass

        # Residual consolidation is the default. The former card-rewrite pass
        # remains available only for old sessions that deliberately opt into it;
        # rewriting identity cards from a play window would violate the source /
        # evidence boundary this ledger establishes.
        if not world_state.get("legacy_card_consolidation"):
            world_state["cast_consolidated_through"] = len(steps)
            return out

        # Legacy compatibility projection: summary + mutable cards.
        prior = (world_state.get("story_so_far") or "").strip()
        try:
            summary = (prov.generate_text(
                system=_SUMMARY_SYS,
                prompt=f"PRIOR SUMMARY:\n{prior or '(none yet)'}\n\nRECENT TURNS:\n{recent}\n\nUpdate it.").text or "").strip()
            if summary:
                world_state["story_so_far"] = summary
                out["summary"] = summary
        except Exception:  # noqa: BLE001
            pass

        # 2. update the cards of the characters who actually appeared in the recent window
        from ..characters.labeled import parse_records
        cards = world_state.setdefault("cards", {})
        roster: dict[str, str] = {}
        for m in (getattr(st, "cast", []) or []):
            k = getattr(m, "character", None)
            c = ctx.base_settings.characters.get(k) if k else None
            nm = getattr(c, "name", k) if k else None
            if nm:
                roster[nm] = cards.get(nm) or ((getattr(c, "system", "") or "")[:220])
        for nm, rec in (world_state.get("people") or {}).items():
            if isinstance(rec, dict):
                roster[nm] = cards.get(nm) or rec.get("card") or rec.get("note") or ""
        rlow = recent.lower()
        roster = {nm: d for nm, d in roster.items() if nm and nm.lower() in rlow}
        roster = dict(list(roster.items())[:8])
        if roster:
            block = "\n\n".join(f"NAME: {nm}\nCURRENT CARD: {d or '(thin sketch)'}" for nm, d in roster.items())
            try:
                txt = prov.generate_text(system=_CARDS_SYS,
                                         prompt=f"CHARACTERS:\n{block}\n\nRECENT TURNS:\n{recent}\n\nUpdate each card.").text or ""
                for r in parse_records(txt, ["NAME", "CURRENT", "HISTORY", "CARD"]):
                    nm = (r.get("NAME") or "").strip()
                    # CARD is accepted for old providers/self-checks during the prompt migration.
                    card = (r.get("CURRENT") or r.get("CARD") or "").strip()
                    history = (r.get("HISTORY") or "").strip()
                    if nm and card:
                        cards[nm] = card
                        out["cards"].append(nm)
                        p = (world_state.get("people") or {}).get(nm)
                        if isinstance(p, dict):
                            p.update({"card": card, "born": True, "sketched": True})
                            world_state.setdefault("log", []).append(f"(a new person was deepened: {nm})")
                        if sid:
                            try:
                                from ..server.services import story_store as _SS
                                seed = (world_state.get("people") or {}).get(nm) or {}
                                _SS.apply_play_card_update(
                                    ctx.root, skey, sid, kind="character", card_key=nm,
                                    foundation={"appearance_or_role": seed.get("note", ""),
                                                "archetype": seed.get("archetype", "unknown")},
                                    current={"card": card}, event=history,
                                    evidence=[s for s in recent.split("\n\n")[-3:] if s],
                                    turn=len(steps))
                            except Exception:  # noqa: BLE001 — DB history must never sink play
                                pass
            except Exception:  # noqa: BLE001
                pass

        # Arc segmentation is deliberately batched with the other reflective work.  It records
        # chapters without replacing the live StoryMaster arc, which remains authoritative in play.
        try:
            from ..characters.labeled import parse_labeled
            marked = int(world_state.get("arc_segmented_through") or 0)
            segment = "\n\n".join(str(s) for s in steps[marked:] if str(s).strip())[:9000]
            if segment:
                decision = parse_labeled(prov.generate_text(system=_ARC_SEGMENT_SYS,
                    prompt=f"CURRENT ARC: {(world_state.get('arc') or {}).get('name', '')}\n\nRECENT PLAY:\n{segment}").text or "",
                    ["CLOSE", "TITLE"])
                if decision.get("CLOSE", "").strip().lower() in {"yes", "true"}:
                    title = decision.get("TITLE", "").strip() or "Untitled arc"
                    world_state.setdefault("arcs", []).append({"title": title, "from": marked, "to": len(steps) - 1,
                                                                "summary": world_state.get("story_so_far", "")})
                world_state["arc_segmented_through"] = len(steps)
        except Exception:  # noqa: BLE001
            pass

        # Store the same evolving projection for the story and the active arc. The mutable
        # fields are intentionally small: they are a current reading of THIS playthrough, while
        # the foundation remains the authored premise/arc and the history carries the evidence.
        if sid:
            try:
                from ..server.services import story_store as _SS
                _SS.apply_play_card_update(
                    ctx.root, skey, sid, kind="story", card_key=skey,
                    foundation={"premise": getattr(st, "premise", ""),
                                "central_question": (getattr(st, "premise_parts", {}) or {}).get("question", "")},
                    current={"story_so_far": world_state.get("story_so_far", ""),
                             "active_pressure": ((world_state.get("scene_plan") or {}).get("pressure") or "")},
                    event=world_state.get("story_so_far", ""),
                    evidence=[s for s in recent.split("\n\n")[-3:] if s], turn=len(steps))
                arc = world_state.get("arc") if isinstance(world_state.get("arc"), dict) else {}
                if arc:
                    _SS.apply_play_card_update(
                        ctx.root, skey, sid, kind="arc", card_key=str(arc.get("id") or arc.get("name") or "active"),
                        foundation={"name": arc.get("name", ""), "question": arc.get("question", "")},
                        current={"stage": arc.get("stage", 0), "milestone": arc.get("milestone", ""),
                                 "status": "active"}, event=world_state.get("story_so_far", ""),
                        evidence=[s for s in recent.split("\n\n")[-3:] if s], turn=len(steps))
            except Exception:  # noqa: BLE001
                pass

        world_state["cast_consolidated_through"] = len(steps)
    except Exception:  # noqa: BLE001 — never sink the turn
        return out
    return out


def consolidate_on_rest(ctx, skey: str, world_state: dict, status: str) -> dict:
    """The player slept or died → consolidate. This is a CONTEXT-COMPRESSION step: every present
    character compresses the NEW scenes THEY saw (since the last consolidation) into their memory,
    then the *legacy prompt transcript* is dropped. The immutable ``turns`` ledger remains available
    to evidence-backed residual consolidation. On death the storymaster also
    picks a significant past step to rewind to. Returns {memories, rewind|None, compressed}. Never
    raises — a failed consolidation must not sink the turn."""
    out: dict = {"memories": [], "rewind": None, "compressed": 0, "dream": ""}
    steps = (world_state or {}).get("transcript")
    if not isinstance(steps, list) or not steps:
        return out
    full = list(steps)   # snapshot for the death-rewind selection (taken before compression blanks it)
    # The fever-dream: on sleep, the cast's latent pressures surface as a foreboding portent (NOT
    # options) — generated BEFORE compression so the dream reads the freshest recent events.
    if status == "sleeping":
        out["dream"] = generate_dream(ctx, skey, world_state)
    # SLIDING WINDOW: the narrative thread keeps the most recent N turns verbatim — only steps that
    # have aged out of the window get compressed. Depth is configurable: per-thread world_state
    # override → the story's `recent_window` → the default. Compress the band [start, end):
    # start = watermark (already-consolidated), end = window boundary (keep newer turns raw).
    _st = ctx.base_settings.stories.get(skey) if skey else None
    try:
        win = int(world_state.get("recent_window") or getattr(_st, "recent_window", None) or _RECENT_WINDOW)
    except (TypeError, ValueError):
        win = _RECENT_WINDOW
    win = max(0, win)
    start = int(world_state.get("consolidated_through") or 0)
    end = len(steps) - win
    if end <= start:
        # Death still needs the rewind even when nothing has aged out yet.
        if status != "dead":
            return out
    else:
        # Blank everything outside [start, end) so each character compresses ONLY the newly-aged-out
        # steps they witnessed (visible() indexes by absolute step).
        window = [s if start <= i < end else "" for i, s in enumerate(steps)]
        try:
            out["memories"] = _record_scene(ctx, {"story": skey, "steps": window,
                                                  "world_state": world_state}).get("recorded", [])
        except Exception:  # noqa: BLE001
            pass
        # COMPRESS: drop the raw text of the just-consolidated band (kept as "" to preserve the
        # absolute-index alignment runtime_state relies on) and advance the watermark.
        out["compressed"] = end - start
        for i in range(start, end):
            steps[i] = ""
        world_state["consolidated_through"] = end
    if status != "dead":
        return out
    # Death → storymaster chooses a significant moment to return the player to.
    try:
        prov, p = _as_agent(ctx, "storymaster")
        if prov is None:
            return out
        numbered = "\n".join(f"[{i}] {str(s)[:300]}" for i, s in enumerate(full) if str(s).strip())
        system = p.get("system") or ("You are the storymaster. The player has died; choose the "
                                     "significant past moment to send them back to.")
        prompt = (f"The player died. Here is the story so far, by step:\n{numbered}\n\n"
                  "Pick the ONE step that is the meaningful moment to return the player to — a "
                  "turning point where a different choice matters. Output the step index + why. JSON only.")
        rw = (prov.generate_text(system=system, prompt=prompt, emits=_REWIND_SCHEMA).data) or {}
        i = rw.get("step")
        if isinstance(i, int) and 0 <= i < len(full):
            out["rewind"] = {"step": i, "reason": str(rw.get("reason", "")), "moment": str(full[i])}
    except Exception:  # noqa: BLE001
        pass
    return out

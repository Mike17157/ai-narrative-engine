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
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class StageTool:
    name: str
    describe: str
    keywords: list[str]
    produces: str                # the artifact key it yields (e.g. "board", "spine")
    run: Callable                # def run(ctx, body) -> dict


TOOLS: dict[str, StageTool] = {}


def stage_tool(name: str, *, describe: str, keywords: list | None = None,
               produces: str = "") -> Callable:
    """Decorator: register `fn` as the stage tool called by `name`."""
    def deco(fn: Callable) -> Callable:
        TOOLS[name] = StageTool(name=name, describe=describe,
                                keywords=[str(k) for k in (keywords or [])],
                                produces=produces, run=fn)
        return fn
    return deco


def get(name: str) -> StageTool | None:
    return TOOLS.get(name)


def catalog() -> list[dict]:
    """The advertised tools — what the Scripts panel shows and the post-history protocol lists."""
    return [{"fn": t.name, "kind": "stage", "describe": t.describe,
             "keywords": list(t.keywords), "produces": t.produces} for t in TOOLS.values()]


def run_stage(ctx, name: str, body: dict | None = None) -> dict:
    """Execute a stage tool by name against `body` (character + optional premise/spine)."""
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(f"no such stage tool: {name!r}")
    return tool.run(ctx, body or {})


def _character(ctx, body: dict):
    ch = ctx.base_settings.characters.get(body.get("character"))
    if ch is None:
        raise ValueError("no such character")
    return ch


def _provider(ctx, body: dict, stage: str):
    provider, systems = ctx.builder_ctx(body, stage)
    if provider is None:
        raise RuntimeError(systems if isinstance(systems, str) else "no chat connection")
    return provider, systems


# ── The stages an agent can call ─────────────────────────────────────────────────

@stage_tool(
    "storyboard",
    describe="Run the storyboarder — generate the story's beats (logline, premise, tone, "
             "themes, BEATS) from the character and premise.",
    keywords=["storyboard", "story board", "storyboard this", "board it", "outline the story",
              "lay out the beats", "generate the beats", "map the beats", "draft the outline"],
    produces="board",
)
def _storyboard(ctx, body: dict) -> dict:
    from .pipeline import board_to_graph, parse_storyboard, storyboard_inputs
    ch = _character(ctx, body)
    provider, systems = _provider(ctx, body, "storyboard")
    base = body.get("spine") or body.get("graph") or {}
    system, prompt = storyboard_inputs(
        name=ch.name, persona=ch.system, extras=ctx.card_extras(ch, body["character"]),
        systems=systems, premise=(body.get("premise") or "").strip(), spine=base)
    res = provider.generate_text(system=system, prompt=prompt)
    board = parse_storyboard(res.text or "")
    # Hand back BOTH: the rich board AND a development-graph projection the canvas can render
    # (beats → nodes), merged onto the working graph's spine so wound/lie/truth aren't lost.
    return {"board": board, "graph": board_to_graph(board, base if isinstance(base, dict) else {})}


@stage_tool(
    "spine",
    describe="Run the spine architect — derive the emotional spine (WOUND / LIE / TRUTH and "
             "the psychological beats from lie to truth) for the character.",
    keywords=["spine", "emotional spine", "wound", "lie", "truth", "inner journey",
              "psychological beats", "arc of change"],
    produces="spine",
)
def _spine(ctx, body: dict) -> dict:
    from .pipeline._helpers import SPINE_SCHEMA, _card_context, _sys
    ch = _character(ctx, body)
    provider, systems = _provider(ctx, body, "spine")
    card = _card_context(ch.name, ch.system, ctx.card_extras(ch, body["character"]))
    parts = [f"CHARACTER CARD:\n{card}"]
    if (body.get("premise") or "").strip():
        parts.append("STORY PREMISE:\n" + body["premise"].strip())
    parts.append("Read this character deeply. Reveal the WOUND already present in who they are. "
                 "Derive the LIE they tell themselves. Find the TRUTH they must accept. Map the "
                 "emotional beats from lie to truth. Output structured JSON only.")
    res = provider.generate_text(system=_sys(systems, "spine"), prompt="\n\n".join(parts),
                                 emits=SPINE_SCHEMA)
    return {"spine": res.data or {}}


def protocol_text(tools: list[dict]) -> str:
    """The post-history tool protocol: tells the model which stage tools it can request and how.
    Auto-injected into the workshop/play turn so the model knows its tools (the prompt-split
    slot is where invocation instructions belong)."""
    if not tools:
        return ""
    lines = ["TOOLS: you can run a pipeline stage, but ONLY when the writer EXPLICITLY asks you "
             "to generate/produce it this turn (e.g. \"storyboard this\", \"generate the spine "
             "now\", \"lay out the beats\"). Merely discussing the topic is NOT a request — keep "
             "talking and do NOT run anything. Available stages:"]
    for t in tools:
        kw = ", ".join(t.get("keywords", [])[:4])
        lines.append(f"- {t['fn']}: {t['describe']}" + (f" (ask-phrases: {kw})" if kw else ""))
    lines.append("When (and only when) the writer explicitly asks, finish your normal reply and "
                 "then add ONE final line exactly: [[run: <tool>]] (e.g. [[run: storyboard]]). "
                 "Otherwise never emit that line.")
    return "\n".join(lines)

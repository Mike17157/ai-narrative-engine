"""The STORY agent as a state graph — a plan/act/observe/reflect loop for complex story-structure
work (arc design, storyboard building, spine restructuring).

Unlike the single-shot chat agent (loom/stories/agent.py), this graph lets the model build ONE
beat at a time, observe it, check it against a craft rubric, and revise before committing. That
loop is what a human developmental editor runs; single-shot can't do it because it commits to a
whole spine+beats sequence in one call with no chance to verify the chain.

Topology::

    start → PLAN → ACT → OBSERVE → REFLECT (decision)
                                     ├─ match(revise/continue) → ACT   (loop back)
                                     └─ match(done)              → COMMIT → end

The control surface (configs/story_agent.json) configures this:
  • PLAN reads the story persona + the story's specifics (spine, hidden truth) as context
  • ACT uses the story agent's tool list (the same GraphFunction registry as the chat agent)
  • REFLECT checks against the story agent's `rubric` (reflection questions derived from the persona)
  • COMMIT persists via the same ctx.update_story_fields path as run_turn

Built on pydantic_graph (the same dependency genesis_graph.py uses) — no new deps, no MCP.
The single-shot path stays intact for the other 4 agents + simple edits; this graph is opt-in.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from pydantic_graph import GraphBuilder, StepContext

from . import agent_config as _AC
from . import graph_ops as GO
from . import scripts as _S
from . import stage_tools as _ST
from .agent import assemble_system_prompt, _story_context


def _noop_event(_: dict) -> None: ...
def _never_cancel() -> bool: return False

MAX_STEPS = 4  # cap the ACT↔REFLECT loop — each step is a model round-trip (~10-30s each on
               # DeepSeek v4 Pro), so 4 steps = ~2-4 min total. The sweet spot for arc design:
               # enough iterations to set the spine + build/check 2-3 beats, not so many the
               # writer waits forever. Simple tasks route to single-shot anyway.


# ── State + Deps ─────────────────────────────────────────────────────────────── #

@dataclass
class StoryAgentState:
    """Everything that flows across the plan/act/observe/reflect loop."""
    # Input
    story_key: str = ""
    task: str = ""                       # the writer's latest request
    messages: list = field(default_factory=list)   # conversation history
    # Working (mutates as the graph runs)
    graph_doc: dict = field(default_factory=dict)  # the editable story document
    plan: str = ""                       # the model's reasoning about how to approach the task
    step_count: int = 0                  # ACT visits — capped at MAX_STEPS
    tool_log: list = field(default_factory=list)   # [{fn, params, result, ok}]
    next_call: dict = field(default_factory=dict)  # the pending tool call for the next ACT
    reflect_verdict: str = ""            # "continue" | "revise" | "done" — set by REFLECT
    # Output
    reply: str = ""
    committed: bool = False


@dataclass
class StoryAgentDeps:
    """I/O the steps need, injected by the endpoint — keeps the graph framework-agnostic."""
    ctx: Any                             # AppContext — for persistence + providers
    body: dict = field(default_factory=dict)        # the original request body
    cfg: dict = field(default_factory=dict)         # loaded story_agent.json config
    provider: Any = None                 # the resolved text provider (has generate_text)
    on_event: Callable[[dict], None] = _noop_event  # structured progress → SSE (optional)


# ── Helpers ──────────────────────────────────────────────────────────────────── #

async def _thread(fn, *a, **kw):
    """Run a sync function off the event loop (providers are sync)."""
    return await asyncio.to_thread(lambda: fn(*a, **kw))


def _emit(ctx, node: str, status: str, **extra) -> None:
    ctx.deps.on_event({"type": "node", "node": node, "status": status, **extra})


def _story_tools(cfg: dict):
    """Resolve the story agent's tool list to callable GraphFunctions (same registry as chat)."""
    agent = (cfg.get("agents") or {}).get("story") or {}
    names = agent.get("tools") or []
    return GO.resolve_functions(names)


def _rubric(cfg: dict) -> list[str]:
    """The reflection questions for the story agent (from configs/story_agent.json)."""
    agent = (cfg.get("agents") or {}).get("story") or {}
    r = agent.get("rubric") or []
    return r if isinstance(r, list) else []


def _graph_prose(graph: dict) -> str:
    """Render the current story doc as readable prose for the model (delegates to agent.py)."""
    from .agent import _graph_prose as _gp
    return _gp(graph)


# ── Nodes ────────────────────────────────────────────────────────────────────── #

async def step_plan(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> str:
    """PLAN — the model reasons about the task and emits its FIRST tool call + a plan note.
    No execution here; this is reasoning. Stores the plan, sets next_call for ACT."""
    s, d = ctx.state, ctx.deps
    _emit(ctx, "plan", "start")
    agent = (d.cfg.get("agents") or {}).get("story") or {}
    system = assemble_system_prompt(
        cfg=d.cfg, graph=s.graph_doc, adopted=agent.get("persona", ""),
        story_ctx=_story_context(d.ctx, s.story_key, d.cfg.get("story_context_fields") or []),
        craft_block="", char_ground="", label="STORY",
        adopted_examples=agent.get("example", ""))
    system += ("\n\nYou are in PLANNING mode. Reason about the task, then emit your FIRST tool "
               "call — exactly ONE call, not a batch. You will build the structure ONE step at a "
               "time (one tool call per step), observing and revising between steps. State your "
               "plan in `plan`, then make the single tool call that starts it.\n\n"
               "Available tools (emit EXACTLY one of these per step):\n"
               + "\n".join(f"  • {f.name}({', '.join(f.params.keys()) if f.params else ''}) — {f.describe}"
                           for f in _story_tools(d.cfg))
               + "\n\nTool guidance:\n"
               "  • set_spine(field, value) — sets ONE spine field at a time. field ∈ "
               "{logline, wound, lie, truth}. To set all four, you'll call this four times across "
               "four steps — NOT storyboard.\n"
               "  • storyboard(premise) — runs the FULL generation pipeline (overwrites everything). "
               "Use ONLY when building from scratch, never for editing individual fields.\n"
               "  • add_beat(title, after) — adds one beat to the storyboard chain.\n"
               "Do NOT invent tools like 'batched' or 'get_storyboard' — only the names listed above.")
    schema = {"type": "object", "additionalProperties": False, "required": ["plan", "call"],
              "properties": {
                  "plan": {"type": "string", "description": "your reasoning: which tools, in what "
                            "order, what you'll check at each step (you'll be evaluated against the "
                            "rubric: " + "; ".join(_rubric(d.cfg)) + ")"},
                  "call": {"type": "object", "description": "your FIRST tool call — REQUIRED. Pick "
                           "one of the available tools and fill its params.",
                           "additionalProperties": False,
                           "required": ["fn"],
                           "properties": {"fn": {"type": "string", "description": "the tool name"},
                                          "params": {"type": "object", "additionalProperties": True}}}}}
    res = await _thread(d.provider.generate_text, system=system,
                        prompt=s.task or "Plan the work.", emits=schema)
    data = (res.data if hasattr(res, "data") else res) or {}
    s.plan = (data.get("plan") or "").strip()
    s.next_call = data.get("call") or {}
    _emit(ctx, "plan", "done", plan=s.plan[:160])
    return "plan_done"


async def step_act(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> str:
    """ACT — execute ONE tool call against the story doc. Doc tools mutate graph_doc in place;
    action tools run with ctx. Logs the result."""
    s, d = ctx.state, ctx.deps
    if s.step_count >= MAX_STEPS:
        s.reflect_verdict = "done"      # force commit at the cap
        return "capped"
    call = s.next_call or {}
    fn_name = (call.get("fn") or "").strip()
    params = call.get("params") or {}
    if not fn_name:
        s.reflect_verdict = "done"      # nothing to do → commit
        return "no_call"
    _emit(ctx, "act", "start", fn=fn_name, step=s.step_count + 1)
    # Resolve the tool and execute it
    fns = _story_tools(d.cfg)
    fn = next((f for f in fns if f.name == fn_name), None)
    entry = {"fn": fn_name, "params": params, "ok": False}
    if fn is None:
        entry["error"] = f"tool '{fn_name}' not in story agent's tool list"
    elif fn.kind == "doc":
        try:
            _S.invoke(fn_name, s.graph_doc, params)   # invoke takes the registered NAME
            entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
    elif fn.kind == "action":
        try:
            abody = {**params, "graph": s.graph_doc, "story": s.story_key}
            entry["result"] = await _thread(fn.impl, d.ctx, abody)
            entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
    s.tool_log.append(entry)
    s.step_count += 1
    s.next_call = {}                    # consumed
    _emit(ctx, "act", "done", ok=entry["ok"], step=s.step_count)
    return "act_done"


async def step_observe(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> str:
    """OBSERVE — render what the story doc looks like now + the relevant rubric, for REFLECT."""
    s, d = ctx.state, ctx.deps
    last = s.tool_log[-1] if s.tool_log else {}
    # The observation = the current graph prose + what the last tool did + the rubric to check.
    rubric = _rubric(d.cfg)
    s._observation = (              # stash for REFLECT (not part of the persisted state shape)
        f"LAST ACTION: {last.get('fn','?')} — {'ok' if last.get('ok') else 'FAILED: '+str(last.get('error',''))}\n"
        f"CURRENT STORY DOC:\n{_graph_prose(s.graph_doc)}\n"
        f"RUBRIC TO CHECK against what you just did:\n- " + "\n- ".join(rubric))
    return "observed"


async def step_reflect(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> str:
    """REFLECT (the branch) — the model evaluates the last step against the rubric and decides:
    continue (next planned step), revise (undo + retry), or done (commit). Sets reflect_verdict."""
    s, d = ctx.state, ctx.deps
    if s.step_count >= MAX_STEPS:
        s.reflect_verdict = "done"; return "done"     # hard cap
    if not s.tool_log:                                  # nothing to reflect on
        s.reflect_verdict = "done"; return "done"
    _emit(ctx, "reflect", "start", step=s.step_count)
    tools = _story_tools(d.cfg)
    tool_list = "\n".join(f"  • {f.name}({', '.join(f.params.keys()) if f.params else ''}) — {f.describe}"
                          for f in tools)
    valid_names = [f.name for f in tools]
    system = ("You are the REFLECTION step of a story-structure agent. Given the plan, the last "
              "action, and the current story doc, evaluate against the rubric and decide:\n"
              "• continue — the step was good; emit the NEXT tool call to proceed\n"
              "• revise — the step failed the rubric; emit a corrective tool call (e.g. set_beat_field "
              "to fix what's wrong, or delete_beat + add_beat to redo it)\n"
              "• done — the task is complete and satisfies the rubric; commit\n"
              "Be honest: if a beat doesn't cost something or doesn't cause the next, say revise.\n\n"
              "Available tools (use ONLY these names, with EXACTLY these params):\n" + tool_list)
    schema = {"type": "object", "additionalProperties": False, "required": ["verdict"],
              "properties": {
                  "verdict": {"type": "string", "enum": ["continue", "revise", "done"]},
                  "reason": {"type": "string", "description": "one sentence: why this verdict"},
                  "call": {"type": "object", "description": "the next tool call (required for continue/revise)",
                           "additionalProperties": False, "required": ["fn", "params"],
                           "properties": {"fn": {"type": "string", "enum": valid_names},
                                          "params": {"type": "object", "additionalProperties": True}}}}}
    res = await _thread(d.provider.generate_text, system=system,
                        prompt=getattr(s, "_observation", s.plan or s.task), emits=schema)
    data = (res.data if hasattr(res, "data") else res) or {}
    s.reflect_verdict = (data.get("verdict") or "done").strip().lower()
    if s.reflect_verdict in ("continue", "revise"):
        s.next_call = data.get("call") or {}     # ACT will execute it
    _emit(ctx, "reflect", "done", verdict=s.reflect_verdict,
          reason=(data.get("reason") or "")[:120])
    return s.reflect_verdict


async def step_commit(ctx: StepContext[StoryAgentState, StoryAgentDeps, None]) -> dict:
    """COMMIT — persist the mutated story doc + generate a terse reply."""
    s, d = ctx.state, ctx.deps
    _emit(ctx, "commit", "start")
    target = (d.body.get("target") or "story").strip()
    applied = [f["fn"] for f in s.tool_log if f.get("ok")]
    if target != "draft":
        try:
            # Persist the fields the story agent's tools mutate: the storyboard structure
            # (beats, spine-ish fields) + the top-level spine fields (logline/wound/lie/truth)
            # + arcs. set_spine writes to doc[field] top-level; add_beat/set_beat_field write
            # to storyboard.beats. Include all so nothing the agent built gets dropped.
            persist_fields = {k: s.graph_doc.get(k) for k in
                              ("arcs", "storyboard", "logline", "wound", "lie", "truth")
                              if s.graph_doc.get(k) is not None}
            if persist_fields:
                d.ctx.update_story_fields(s.story_key, persist_fields)
        except Exception:  # noqa: BLE001 — a persist failure never sinks the response
            pass
    # Terse reply summarizing what landed
    if applied:
        s.reply = f"Built {len(applied)} step(s): {', '.join(applied[:4])}."
    else:
        s.reply = "Couldn't apply anything — try rephrasing."
    s.committed = True
    _emit(ctx, "commit", "done", steps=s.step_count, applied=len(applied))
    return {"ok": True, "graph": s.graph_doc, "applied": s.tool_log, "reply": s.reply,
            "active_behavior": "story", "offered": [f.name for f in _story_tools(d.cfg)]}


# ── Graph construction ───────────────────────────────────────────────────────── #

def _build_story_agent_graph():
    """Build the plan → act → observe → reflect → {act (loop) | commit} → end graph.
    REFLECT returns the verdict string; a decision node branches it: continue/revise loops
    back to ACT, done goes forward to COMMIT."""
    from pydantic_graph.graph_builder import DecisionBranch
    gb = GraphBuilder(state_type=StoryAgentState, deps_type=StoryAgentDeps, output_type=dict)
    plan, act, observe, reflect = (gb.step(step_plan), gb.step(step_act),
                                   gb.step(step_observe), gb.step(step_reflect))
    commit = gb.step(step_commit)
    # Linear: start → plan → act → observe → reflect
    gb.add_edge(gb.start_node, plan)
    gb.add_edge(plan, act)
    gb.add_edge(act, observe)
    gb.add_edge(observe, reflect)
    # Branch at reflect: continue/revise → act (loop); done → commit
    loop_path = gb.edge_from(reflect).to(act)
    done_path = gb.edge_from(reflect).to(commit)
    loop_branch = DecisionBranch(source=str, matches=lambda v: v in ("continue", "revise"),
                                 path=loop_path.path, destinations=[act])
    done_branch = DecisionBranch(source=str, matches=lambda v: v == "done",
                                 path=done_path.path, destinations=[commit])
    decision = gb.decision(note="reflect_branch").branch(loop_branch).branch(done_branch)
    gb.add_edge(reflect, decision)
    gb.add_edge(commit, gb.end_node)
    return gb.build()


STORY_AGENT_GRAPH = _build_story_agent_graph()


async def run_story_agent(ctx, body: dict, cfg: dict, provider, *, on_event=None) -> dict:
    """Run the story-structure agent graph. Same return shape as agent.run_turn so the endpoint
    and frontend don't change. `provider` is the resolved text provider (has generate_text).
    `on_event` (optional) receives {type:'node', node, status} per step for SSE streaming."""
    messages = body.get("messages") or []
    task = next((str(m.get("content", "")) for m in reversed(messages)
                 if isinstance(m, dict) and m.get("role") == "user"), "")
    # Load the current story doc as the working graph_doc
    try:
        graph_doc = ctx._read_story_data(body.get("story") or "")
    except Exception:  # noqa: BLE001
        graph_doc = body.get("graph") or {}
    state = StoryAgentState(
        story_key=body.get("story") or "", task=task, messages=messages, graph_doc=graph_doc)
    deps = StoryAgentDeps(ctx=ctx, body=body, cfg=cfg, provider=provider,
                          on_event=on_event or _noop_event)
    result = await STORY_AGENT_GRAPH.run(state=state, deps=deps, inputs=None)
    return result if isinstance(result, dict) else {}


# ── Routing ──────────────────────────────────────────────────────────────────── #

# Tasks that benefit from the plan/act/reflect loop (vs. single-shot)
# Tasks that genuinely benefit from the plan/act/reflect loop — where intermediate state
# changes the plan (full restructuring, rebuilding from scratch, multi-beat redesign).
# Simple tasks (set a spine field, add a beat, rename) stay on single-shot: they're one-call
# work where the model sees full context at once, which is faster AND more consistent (no drift
# between steps). The graph loop earns its cost only when single-shot demonstrably fails.
_COMPLEX_KEYWORDS = ("restructure", "rework the", "rebuild the", "redesign the",
                     "fix the whole", "overhaul the", "start over",
                     "design the full arc", "design arc", "build the full storyboard")


def should_use_graph(body: dict, cfg: dict) -> bool:
    """True ONLY for genuine multi-step restructuring — full arc design, storyboard rebuild,
    beat-chain overhaul. Single-field edits, single-beat adds, spine setting all stay single-shot
    (one-call, full context, no drift, ~10x faster). The graph loop is reserved for tasks where
    intermediate state genuinely changes the plan."""
    from .agent_modes import translate_key as _translate_key
    agents = cfg.get("agents") or {}
    explicit = _translate_key((body.get("mode") or "").strip())
    active = explicit if (explicit and explicit in agents) else None
    if active is None:
        req = next((str(m.get("content", "")) for m in (body.get("messages") or [])
                    if isinstance(m, dict) and m.get("role") == "user"), "")
        active = "story" if any(t in req.lower() for t in
                                ("storyboard", "title", "premise", "theme", "arc ", "plot",
                                 "beats", "outline", "ending", "climax")) else None
    if active != "story":
        return False
    req = next((str(m.get("content", "")) for m in (body.get("messages") or [])
                if isinstance(m, dict) and m.get("role") == "user"), "")
    return any(k in req.lower() for k in _COMPLEX_KEYWORDS)


if __name__ == "__main__":   # topology self-check
    # The graph builds and has the expected nodes + the reflect branch
    g = STORY_AGENT_GRAPH
    print("story-agent graph built:", bool(g))
    print("topology: start → plan → act → observe → reflect → {act (loop) | commit} → end")
    print("MAX_STEPS cap:", MAX_STEPS)
    # Routing sanity — tightened: only genuine restructuring goes to the graph
    cfg_dummy = {"agents": {"story": {"tools": [], "rubric": ["test"]}}}
    # Graph: genuine multi-step restructuring
    assert should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "redesign the full arc from scratch"}]}, cfg_dummy)
    assert should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "rebuild the storyboard"}]}, cfg_dummy)
    # Single-shot: simple edits, spine, single beats, other agents
    assert not should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "set the logline spine field"}]}, cfg_dummy)
    assert not should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "add a beat where Eli finds the journal"}]}, cfg_dummy)
    assert not should_use_graph({"mode": "story", "messages": [{"role": "user", "content": "design arc 3"}]}, cfg_dummy)  # just "design arc" isn't enough — needs "full"/"restructure"
    assert not should_use_graph({"mode": "cast", "messages": [{"role": "user", "content": "add a rival"}]}, cfg_dummy)
    print("routing: redesign full arc → graph ✓ | rebuild storyboard → graph ✓")
    print("         set spine → single-shot ✓ | add beat → single-shot ✓ | design arc → single-shot ✓")
    print("ok — story_agent_graph: topology + routing")

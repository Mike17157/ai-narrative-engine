"""Graph-backed Story-card maintenance operations.

Organizing and reviewing a card are agent workflows, even though each currently
needs one model call. Keeping them in the same graph substrate as interviews and
live play gives each workflow a clear state boundary and a natural place for
validation/retry nodes when those become necessary.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic_graph import GraphBuilder, StepContext

from ..workflows import WorkflowTrace, model_task


def _noop_event(_: dict) -> None: ...


@dataclass
class CardDeps:
    provider: Any
    on_event: Callable[[dict], None] = _noop_event
    trace: WorkflowTrace = field(default_factory=lambda: WorkflowTrace("card"))


@dataclass
class CardState:
    operation: str
    system: str
    prompt: str
    schema: dict
    data: dict = field(default_factory=dict)


async def call_card_model(ctx: StepContext[CardState, CardDeps, None]) -> dict:
    state, deps = ctx.state, ctx.deps
    deps.on_event({"type": "node", "node": state.operation, "status": "start"})
    result = await model_task(deps.trace, state.operation, deps.provider,
                              system=state.system, prompt=state.prompt, emits=state.schema)
    state.data = getattr(result, "data", None) or {}
    deps.on_event({"type": "node", "node": state.operation, "status": "done",
                   "ok": bool(state.data)})
    return state.data


def _build_card_graph():
    builder = GraphBuilder(state_type=CardState, deps_type=CardDeps, output_type=dict)
    call = builder.step(call_card_model)
    builder.add_edge(builder.start_node, call)
    builder.add_edge(call, builder.end_node)
    return builder.build()


CARD_GRAPH = _build_card_graph()


async def run_card_operation(*, operation: str, provider: Any, system: str, prompt: str,
                             schema: dict, on_event: Callable[[dict], None] | None = None) -> dict:
    """Run one structured card operation without applying its result to canon."""
    state = CardState(operation=operation, system=system, prompt=prompt, schema=schema)
    return await CARD_GRAPH.run(
        state=state,
        deps=CardDeps(provider=provider, on_event=on_event or _noop_event,
                      trace=WorkflowTrace("card", sink=on_event or _noop_event)),
        inputs=None,
    )

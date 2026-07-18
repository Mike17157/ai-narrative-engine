"""Explicit orchestration for one story-interview turn.

This graph deliberately owns only transient work: call the interviewer and
parse its command response.  Story-card validation and persistence remain at
the API boundary, where they are already authoritative and transactional.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pydantic_graph import GraphBuilder, StepContext

from .interview import split_response_with_focus
from ..workflows import WorkflowTrace, model_task


@dataclass
class InterviewDeps:
    provider: Any
    trace: WorkflowTrace = field(default_factory=lambda: WorkflowTrace("interview"))


@dataclass
class InterviewState:
    system: str
    prompt: str
    raw_response: str = ""
    reply: str = ""
    patch: dict = field(default_factory=dict)
    next_focus: str = ""


@dataclass
class InterviewResult:
    reply: str
    patch: dict
    next_focus: str = ""
    raw_response: str = ""  # the model's unparsed completion — debug/inspection only


async def ask_interviewer(ctx: StepContext[InterviewState, InterviewDeps, None]) -> str:
    """The only model-owning node; keeping it isolated makes retries explicit later."""
    state = ctx.state
    result = await model_task(ctx.deps.trace, "interviewer", ctx.deps.provider,
                              system=state.system, prompt=state.prompt)
    state.raw_response = (getattr(result, "text", "") or "").strip()
    return state.raw_response


async def parse_interviewer_response(
    ctx: StepContext[InterviewState, InterviewDeps, str],
) -> InterviewResult:
    reply, patch, next_focus = split_response_with_focus(ctx.inputs or ctx.state.raw_response)
    ctx.state.reply = reply
    ctx.state.patch = patch
    ctx.state.next_focus = next_focus
    return InterviewResult(reply=reply, patch=patch, next_focus=next_focus,
                           raw_response=ctx.state.raw_response)


def _build_interview_graph():
    builder = GraphBuilder(
        state_type=InterviewState, deps_type=InterviewDeps, output_type=InterviewResult
    )
    ask = builder.step(ask_interviewer)
    parse = builder.step(parse_interviewer_response)
    builder.add_edge(builder.start_node, ask)
    builder.add_edge(ask, parse)
    builder.add_edge(parse, builder.end_node)
    return builder.build()


INTERVIEW_GRAPH = _build_interview_graph()


async def run_interview_turn(*, provider: Any, system: str, prompt: str,
                             on_event=None, cancel=None) -> InterviewResult:
    """Run a typed, inspectable interview turn without mutating story state."""
    return await INTERVIEW_GRAPH.run(
        state=InterviewState(system=system, prompt=prompt),
        deps=InterviewDeps(provider=provider, trace=WorkflowTrace("interview", sink=on_event or (lambda _: None),
                                                                    cancel=cancel or (lambda: False))),
        inputs=None,
    )

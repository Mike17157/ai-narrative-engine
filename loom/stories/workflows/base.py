"""Common tracing and safe task execution for story workflows.

Graphs decide *what* runs and in which order.  This module standardizes the
operational behavior of their leaf tasks without turning domain logic into a
generic agent framework: events, cancellation checks, timing, and off-thread
provider calls.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4


def _noop(_: dict) -> None: ...
def _never_cancel() -> bool: return False


@dataclass
class WorkflowTrace:
    workflow: str
    sink: Callable[[dict], None] = _noop
    run_id: str = field(default_factory=lambda: uuid4().hex)
    cancel: Callable[[], bool] = _never_cancel

    def emit(self, kind: str, *, node: str = "", status: str = "", **data: Any) -> None:
        self.sink({"type": kind, "workflow": self.workflow, "run_id": self.run_id,
                   **({"node": node} if node else {}),
                   **({"status": status} if status else {}), **data})


async def run_sync_task(trace: WorkflowTrace, node: str, fn, *args, **kwargs):
    """Run deterministic blocking work off-loop with a uniform node trace."""
    if trace.cancel():
        trace.emit("node", node=node, status="skipped", reason="cancelled")
        return None
    started = perf_counter()
    trace.emit("node", node=node, status="started")
    try:
        value = await asyncio.to_thread(lambda: fn(*args, **kwargs))
    except Exception as exc:
        trace.emit("node", node=node, status="failed",
                   duration_ms=round((perf_counter() - started) * 1000), error=str(exc))
        raise
    trace.emit("node", node=node, status="completed",
               duration_ms=round((perf_counter() - started) * 1000))
    return value


async def model_task(trace: WorkflowTrace, node: str, provider: Any, *, system: str,
                     prompt: str, emits: dict | None = None, **kwargs):
    """One idempotent model call with safe operational events.

    Callers validate and commit separately.  This function never retries or
    persists anything, so a retry policy can be added later without duplicating
    canonical writes.
    """
    if trace.cancel():
        trace.emit("model", node=node, status="skipped", reason="cancelled")
        return None
    # Most providers are wrapped by AppContext, but graph tests and injected
    # providers can bypass that factory.  Redact here too at the final async
    # model-call seam.
    from ..visibility import strip_model_hidden
    system = strip_model_hidden(system) or ""
    prompt = strip_model_hidden(prompt) or ""
    started = perf_counter()
    trace.emit("model", node=node, status="started", structured=bool(emits))
    try:
        call = {"system": system, "prompt": prompt, **kwargs}
        if emits is not None:
            call["emits"] = emits
        # A provider-level request timeout protects the socket, while this
        # enclosing deadline protects the HTTP request itself.  `httpx` read
        # timeouts reset as a stream makes progress; an authoring turn still
        # needs one finite total budget so a slow reasoning stream cannot hold
        # the UI indefinitely.
        configured_timeout = getattr(provider, "request_timeout_s", None)
        try:
            timeout_s = float(configured_timeout)
        except (TypeError, ValueError):
            timeout_s = 0.0
        if timeout_s <= 0:
            timeout_s = 0.0

        cancel_event = None
        if timeout_s and bool(getattr(provider, "supports_cancellation", False)):
            from threading import Event
            cancel_event = Event()
            call["cancel"] = cancel_event.is_set

        worker = asyncio.create_task(asyncio.to_thread(lambda: provider.generate_text(**call)))
        if timeout_s:
            try:
                # Shield the worker: cancelling a `to_thread` task does not
                # stop its HTTP request.  Set the provider's cancellation
                # event instead, then drain its eventual result below.
                result = await asyncio.wait_for(asyncio.shield(worker), timeout=timeout_s)
            except asyncio.TimeoutError as exc:
                if cancel_event is not None:
                    cancel_event.set()

                def _consume_late_result(task):
                    try:
                        task.result()
                    except BaseException:  # late provider failures are already reported to the author
                        pass

                worker.add_done_callback(_consume_late_result)
                from ...providers.base import ModelRequestTimeout
                raise ModelRequestTimeout(model=getattr(provider, "model", None), timeout_s=timeout_s) from exc
        else:
            result = await worker
    except Exception as exc:
        trace.emit("model", node=node, status="failed",
                   duration_ms=round((perf_counter() - started) * 1000), error=str(exc))
        raise
    trace.emit("model", node=node, status="completed",
               duration_ms=round((perf_counter() - started) * 1000), structured=bool(emits))
    return result

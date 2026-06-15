"""Job / framework glue — free functions with no app state.

Bodies copied verbatim from app.py. Imports are done INSIDE the functions exactly
as in the original.
"""

from __future__ import annotations


def _start_stream_job(category: str, kind: str, label: str, screen: str, work):
    """Run blocking `work(emit, cancelled)` in the background as a streamable BaseJob.
    `emit(ev)` pushes an SSE event (thread-safe); `cancelled()` reflects the job's cancel.
    The job auto-appears in Activity and is consumed via /api/jobs/<id>/stream — ONE frontend
    component (GenStream) renders any of them. Returns the job (the endpoint returns its id)."""
    import asyncio

    from fastapi.concurrency import run_in_threadpool

    from ..jobhub import BaseJob
    loop = asyncio.get_running_loop()
    job = BaseJob(category, kind, label=label, screen=screen, log_cap=600)

    def emit(ev: dict) -> None:
        loop.call_soon_threadsafe(job._emit, ev)

    async def run():
        try:
            result = await run_in_threadpool(lambda: work(emit, lambda: job.cancelling))
            if not job.cancelling:
                loop.call_soon_threadsafe(job._emit, {"type": "result", "result": result})
            job.status = "cancelled" if job.cancelling else "done"
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            loop.call_soon_threadsafe(job._emit, {"type": "error", "error": str(exc)})
        loop.call_soon_threadsafe(job._emit, {"type": "done"})

    job._task = asyncio.create_task(run())
    return job


async def _cancel_job(job) -> dict:
    """Cancel any hub job + run its optional on_cancel hook (e.g. ComfyUI
    interrupt). Shared by the per-category and the generic /jobs cancel."""
    if job is None or job.status != "running":
        return {"ok": False, "status": job.status if job else "idle"}
    job.cancel()
    await job.on_cancel()
    return {"ok": True}


def _system_stats() -> dict:
    """CPU / RAM (psutil) + GPU (nvidia-smi) for the Activity panel header.
    Any piece that's unavailable comes back as None."""
    import subprocess

    out = {"cpu": None, "mem": None, "gpu": None}
    try:
        import psutil

        out["cpu"] = psutil.cpu_percent(interval=None)  # since last call (polled ~3s)
        vm = psutil.virtual_memory()
        out["mem"] = {"percent": vm.percent, "used": vm.used, "total": vm.total}
    except Exception:  # noqa: BLE001
        pass
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,"
             "power.draw,power.limit,temperature.gpu,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            f = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",", 7)]
            if len(f) >= 8:
                def num(x):
                    try:
                        return float(x)
                    except ValueError:
                        return None
                out["gpu"] = {
                    "util": num(f[0]),        # % of time the GPU was busy
                    "mem_util": num(f[1]),    # memory-controller (bandwidth) load
                    "mem_used": num(f[2]), "mem_total": num(f[3]),
                    "power": num(f[4]), "power_limit": num(f[5]),  # compute strain: draw vs cap
                    "temp": num(f[6]), "name": f[7],
                }
    except Exception:  # noqa: BLE001
        pass
    return out

from __future__ import annotations

from ..services.jobs_util import _cancel_job, _system_stats


def register(app, ctx):
    # -- the job hub: every workload routes through one registry; Activity is
    # just its view, and these generic endpoints work for any job by id --------
    @app.get("/api/jobs")
    def jobs_list() -> dict:
        from ..jobhub import REGISTRY

        snaps = [j.snapshot() for j in reversed(REGISTRY.all())]  # newest first
        return {"jobs": snaps, "running": sum(1 for s in snaps if s["status"] == "running")}

    @app.get("/api/jobs/{job_id}/stream")
    def jobs_stream(job_id: str):
        from fastapi.responses import StreamingResponse
        from starlette.responses import Response

        from ..jobhub import REGISTRY

        job = REGISTRY.get(job_id)
        if job is None:
            return Response(status_code=204)
        return StreamingResponse(job.stream(), media_type="text/event-stream")

    @app.post("/api/jobs/{job_id}/cancel")
    async def jobs_cancel(job_id: str):
        from ..jobhub import REGISTRY

        return await _cancel_job(REGISTRY.get(job_id))

    # /api/activity is kept as an alias of /api/jobs (the Activity panel reads it).
    @app.get("/api/activity")
    def activity() -> dict:
        return {**jobs_list(), "system": _system_stats()}

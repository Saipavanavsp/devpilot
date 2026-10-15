import asyncio
import json
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from devpilot.config import Settings
from devpilot.pipeline import RunOutcome, run_on_github, run_task
from devpilot.runlog import Event, Listener

STATIC_DIR = Path(__file__).parent / "static"
POLL_SECONDS = 0.25


class RunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20_000)
    repo: str | None = None
    open_pr: bool = True


Runner = Callable[[RunRequest, str, Listener], RunOutcome]


@dataclass
class Job:
    id: str
    request: RunRequest
    events: list[Event] = field(default_factory=list)
    status: str = "running"

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "task": self.request.task, "repo": self.request.repo,
                "status": self.status}  # fmt: skip


def default_runner(settings: Settings) -> Runner:
    def run(request: RunRequest, job_id: str, listener: Listener) -> RunOutcome:
        if request.repo:
            return run_on_github(
                request.repo,
                settings,
                task=request.task,
                open_pr=request.open_pr,
                listeners=[listener],
            )
        workspace = settings.workspace_dir / f"web-{job_id}"
        return run_task(request.task, workspace, settings, listeners=[listener])

    return run


class JobManager:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self.jobs: dict[str, Job] = {}

    def start(self, request: RunRequest) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], request=request)
        self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        try:
            outcome = self.runner(job.request, job.id, job.events.append)
        except Exception as exc:
            # Any failure must reach the browser, otherwise the page waits forever.
            job.status = "error"
            job.events.append(
                {"type": "error", "data": {"message": f"{type(exc).__name__}: {exc}"}}
            )
            return
        job.status = outcome.status
        job.events.append({"type": "done", "data": outcome.to_dict()})


async def stream_events(job: Job) -> AsyncIterator[str]:
    sent = 0
    while True:
        while sent < len(job.events):
            event = job.events[sent]
            sent += 1
            yield f"data: {json.dumps(event, default=str)}\n\n"
            if event["type"] in {"done", "error"}:
                return
        await asyncio.sleep(POLL_SECONDS)


def create_app(settings: Settings, runner: Runner | None = None) -> FastAPI:
    app = FastAPI(title="DevPilot")
    manager = JobManager(runner or default_runner(settings))

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {
            "model": settings.model,
            "max_steps": settings.max_steps,
            "max_cost_usd": settings.max_cost_usd,
            "github_enabled": settings.github_token is not None,
        }

    @app.post("/api/runs", status_code=201)
    def start_run(request: RunRequest) -> dict[str, Any]:
        return manager.start(request).summary()

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return [job.summary() for job in reversed(manager.jobs.values())]

    @app.get("/api/runs/{job_id}/events")
    def events(job_id: str) -> StreamingResponse:
        job = manager.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown run")
        return StreamingResponse(stream_events(job), media_type="text/event-stream")

    return app


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(create_app(settings), host=host, port=port)

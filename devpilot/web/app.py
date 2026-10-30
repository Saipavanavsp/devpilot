import asyncio
import json
import secrets
import threading
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from devpilot.config import ConfigError, Settings
from devpilot.pipeline import RunOutcome, run_on_github, run_task
from devpilot.runlog import Event, Listener

STATIC_DIR = Path(__file__).parent / "static"
POLL_SECONDS = 0.25
LOCAL_HOSTS = ("localhost", "127.0.0.1")
LOOPBACK_BIND = {"localhost", "127.0.0.1", "::1"}
WILDCARD_BIND = {"0.0.0.0", "::", ""}
MIN_TOKEN_LENGTH = 16
MAX_KEPT_JOBS = 100


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
    """Runs jobs on background threads, with a cap on concurrent jobs and on starts per hour."""

    def __init__(self, runner: Runner, max_running: int = 2, max_per_hour: int = 20) -> None:
        self.runner = runner
        self.max_running = max_running
        self.max_per_hour = max_per_hour
        self.jobs: dict[str, Job] = {}
        self._started: deque[float] = deque()
        self._lock = threading.Lock()

    def running(self) -> int:
        return sum(job.status == "running" for job in self.jobs.values())

    def start(self, request: RunRequest) -> Job:
        with self._lock:
            now = time.monotonic()
            while self._started and now - self._started[0] > 3600:
                self._started.popleft()
            if self.running() >= self.max_running:
                raise HTTPException(429, f"At most {self.max_running} runs can run at once.")
            if len(self._started) >= self.max_per_hour:
                raise HTTPException(429, f"At most {self.max_per_hour} runs can start per hour.")
            self._started.append(now)
            self._prune()
            job = Job(id=uuid.uuid4().hex[:12], request=request)
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _prune(self) -> None:
        """Forget the oldest finished jobs so memory doesn't grow without bound."""
        finished = [job_id for job_id, job in self.jobs.items() if job.status != "running"]
        for job_id in finished[: max(0, len(self.jobs) - MAX_KEPT_JOBS + 1)]:
            del self.jobs[job_id]

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


def token_checker(token: str | None) -> Callable[[Request], None]:
    def check(request: Request) -> None:
        if token is None:
            return
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, token):
            raise HTTPException(401, "Missing or invalid token.", {"WWW-Authenticate": "Bearer"})

    return check


def create_app(
    settings: Settings,
    runner: Runner | None = None,
    *,
    allowed_hosts: Sequence[str] | None = None,
) -> FastAPI:
    app = FastAPI(title="DevPilot")
    # Rejects requests whose Host header isn't ours, which blocks DNS-rebinding attacks from
    # web pages trying to reach the local server.
    hosts = list(allowed_hosts or [*LOCAL_HOSTS, *settings.web_allowed_hosts])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    manager = JobManager(
        runner or default_runner(settings), settings.web_max_jobs, settings.web_runs_per_hour
    )
    allowed_repos = {repo.lower() for repo in settings.web_allowed_repos}
    authorized = [Depends(token_checker(settings.web_token))]

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/config", dependencies=authorized)
    def config() -> dict[str, Any]:
        return {
            "provider": settings.provider,
            "model": settings.model,
            "max_steps": settings.max_steps,
            "max_cost_usd": settings.max_cost_usd,
            "github_enabled": settings.github_token is not None,
        }

    @app.post("/api/runs", status_code=201, dependencies=authorized)
    def start_run(request: RunRequest) -> dict[str, Any]:
        if request.repo and allowed_repos and request.repo.lower() not in allowed_repos:
            raise HTTPException(403, "This repository is not in DEVPILOT_WEB_ALLOWED_REPOS.")
        return manager.start(request).summary()

    @app.get("/api/runs", dependencies=authorized)
    def list_runs() -> list[dict[str, Any]]:
        return [job.summary() for job in reversed(manager.jobs.values())]

    @app.get("/api/runs/{job_id}/events", dependencies=authorized)
    def events(job_id: str) -> StreamingResponse:
        job = manager.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown run")
        return StreamingResponse(stream_events(job), media_type="text/event-stream")

    return app


def server_hosts(settings: Settings, host: str) -> list[str]:
    """Check that the bind address is safe and return the Host headers to accept."""
    if host in LOOPBACK_BIND:
        return [*LOCAL_HOSTS, *settings.web_allowed_hosts]
    if not settings.web_token:
        raise ConfigError(
            f"Refusing to listen on {host} without authentication. Set DEVPILOT_WEB_TOKEN "
            "(and preferably DEVPILOT_WEB_ALLOWED_REPOS), or use the default 127.0.0.1."
        )
    if len(settings.web_token) < MIN_TOKEN_LENGTH:
        raise ConfigError(f"DEVPILOT_WEB_TOKEN must be at least {MIN_TOKEN_LENGTH} characters.")
    if host in WILDCARD_BIND:
        # The server's public names aren't known here; the token still guards every API call.
        return [*LOCAL_HOSTS, *settings.web_allowed_hosts] if settings.web_allowed_hosts else ["*"]
    return [*LOCAL_HOSTS, host, *settings.web_allowed_hosts]


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    hosts = server_hosts(settings, host)
    uvicorn.run(create_app(settings, allowed_hosts=hosts), host=host, port=port)

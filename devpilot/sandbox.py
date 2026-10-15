import contextlib
import io
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.models.containers import Container

WORKDIR = "/workspace"
TIMEOUT_EXIT_CODES = {124, 137}


class SandboxError(Exception):
    pass


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str
    timed_out: bool = False


class Sandbox(Protocol):
    def run(self, command: str, timeout: int | None = None) -> CommandResult: ...


def sandbox_image_tag(base_image: str) -> str:
    return "devpilot-sandbox:" + re.sub(r"[^a-z0-9_.-]+", "-", base_image.lower())


def _container_user() -> str:
    # Matching the host uid keeps files created in the mounted workspace owned by the user on Linux.
    # Docker Desktop on Windows/macOS maps ownership itself, so any unprivileged uid works there.
    if hasattr(os, "getuid"):
        return f"{os.getuid()}:{os.getgid()}"
    return "1000:1000"


class DockerSandbox:
    """A throwaway container with only the workspace mounted, used for every agent command."""

    def __init__(
        self,
        workspace: Path,
        *,
        base_image: str = "python:3.12-slim",
        network: bool = False,
        timeout: int = 120,
        memory_limit: str = "1g",
        client: docker.DockerClient | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.base_image = base_image
        self.network = network
        self.timeout = timeout
        self.memory_limit = memory_limit
        self._client = client
        self._container: Container | None = None

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.from_env()
                self._client.ping()
            except DockerException as exc:
                raise SandboxError(f"Cannot reach the Docker daemon: {exc}") from exc
        return self._client

    def start(self) -> None:
        if self._container is not None:
            return
        image = self._ensure_image()
        self._container = self.client.containers.run(
            image,
            command=["sleep", "infinity"],
            name=f"devpilot-sandbox-{uuid.uuid4().hex[:8]}",
            detach=True,
            volumes={str(self.workspace): {"bind": WORKDIR, "mode": "rw"}},
            working_dir=WORKDIR,
            user=_container_user(),
            environment={"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"},
            network_mode="bridge" if self.network else "none",
            read_only=True,
            tmpfs={"/tmp": "rw,exec,size=512m"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            mem_limit=self.memory_limit,
            pids_limit=256,
            nano_cpus=2_000_000_000,
        )

    def _ensure_image(self) -> str:
        """Build (once) a derived image that has pytest preinstalled, so tests run offline."""
        tag = sandbox_image_tag(self.base_image)
        try:
            self.client.images.get(tag)
            return tag
        except ImageNotFound:
            pass
        dockerfile = f"FROM {self.base_image}\nRUN pip install --no-cache-dir pytest\n"
        self.client.images.build(fileobj=io.BytesIO(dockerfile.encode()), tag=tag, rm=True)
        return tag

    def run(self, command: str, timeout: int | None = None) -> CommandResult:
        if self._container is None:
            raise SandboxError("Sandbox is not running; call start() first.")
        seconds = timeout or self.timeout
        # coreutils timeout sends SIGTERM, then SIGKILL 5s later if the process ignores it.
        argv = ["timeout", "-k", "5", str(seconds), "sh", "-c", command]
        exit_code, raw = self._container.exec_run(argv, workdir=WORKDIR)
        output = raw.decode("utf-8", errors="replace") if raw else ""
        timed_out = exit_code in TIMEOUT_EXIT_CODES
        if timed_out:
            output += f"\n[command timed out after {seconds}s]"
        return CommandResult(exit_code=exit_code, output=output, timed_out=timed_out)

    def close(self) -> None:
        if self._container is None:
            return
        with contextlib.suppress(NotFound):
            self._container.remove(force=True)
        self._container = None

    def __enter__(self) -> "DockerSandbox":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

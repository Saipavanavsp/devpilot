import contextlib
import hashlib
import io
import os
import re
import shutil
import tempfile
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.models.containers import Container

WORKDIR = "/workspace"
TIMEOUT_EXIT_CODES = {124, 137}

# Pinned so every run, on any machine and any date, gets the same environment. The digest is the
# multi-architecture index of python:3.12-slim, so it works on both amd64 and arm64 hosts.
DEFAULT_BASE_IMAGE = (
    "python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159"
)
PYTEST_VERSION = "9.1.1"

DEPS_DIR = "/opt/devpilot-deps"
DEPS_VENV = f"{DEPS_DIR}/venv"
DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
REQUIREMENT_FILES = [
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "dev-requirements.txt",
    "test-requirements.txt",
]
TEST_EXTRAS = {"test", "tests", "testing", "dev"}
INSTALL_TIMEOUT = 600
REPORT_TAIL_CHARS = 2_000


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
    """Tag for the derived image; it changes whenever the base image or pytest pin changes."""
    name = re.sub(r"[^a-z0-9_.-]+", "-", base_image.lower()).strip("-.")
    tag = f"{name}-pytest-{PYTEST_VERSION}"
    if len(tag) > 120:  # Docker tags are limited to 128 characters.
        tag = f"{name[:40]}-{hashlib.sha256(base_image.encode()).hexdigest()[:16]}"
        tag += f"-pytest-{PYTEST_VERSION}"
    return "devpilot-sandbox:" + tag


def _pyproject_requirements(path: Path) -> list[str]:
    """Runtime and test/dev dependencies declared in pyproject.toml (not the project itself)."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    project = data.get("project", {})
    requirements = list(project.get("dependencies", []))
    groups = [project.get("optional-dependencies", {}), data.get("dependency-groups", {})]
    for group in groups:
        for name, entries in group.items():
            if name.lower() in TEST_EXTRAS:
                requirements.extend(entries)
    # Groups may include other groups as tables; a leading "-" would be read as a pip option.
    return [r for r in requirements if isinstance(r, str) and r.strip() and not r.startswith("-")]


def dependency_sources(workspace: Path) -> tuple[list[str], list[str]]:
    """Find the repository's declared dependencies.

    Returns the files they came from and the matching pip install arguments.
    """
    sources: list[str] = []
    pip_args: list[str] = []
    for name in REQUIREMENT_FILES:
        if (workspace / name).is_file():
            sources.append(name)
            pip_args += ["-r", name]
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        requirements = _pyproject_requirements(pyproject)
        if requirements:
            sources.append("pyproject.toml")
            pip_args += requirements
    return sources, pip_args


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
        base_image: str = DEFAULT_BASE_IMAGE,
        network: bool = False,
        timeout: int = 120,
        memory_limit: str = "1g",
        install_dependencies: bool = False,
        install_timeout: int = INSTALL_TIMEOUT,
        client: docker.DockerClient | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.base_image = base_image
        self.network = network
        self.timeout = timeout
        self.memory_limit = memory_limit
        self.install_dependencies = install_dependencies
        self.install_timeout = install_timeout
        self._client = client
        self._container: Container | None = None
        self._deps_dir: Path | None = None
        # What dependency setup did, for the agent and the run log; None if nothing to install.
        self.setup_report: str | None = None

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
        volumes = {str(self.workspace): {"bind": WORKDIR, "mode": "rw"}}
        git_dir = self.workspace / ".git"
        if git_dir.exists():
            # Git runs on the host (commit, push), so a writable .git would let code in the
            # container plant hooks or config that the host then executes.
            volumes[str(git_dir)] = {"bind": f"{WORKDIR}/.git", "mode": "ro"}
        environment = {"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"}
        deps_dir = self._install_dependencies(image) if self.install_dependencies else None
        if deps_dir is not None:
            volumes[str(deps_dir)] = {"bind": DEPS_DIR, "mode": "ro"}
            environment["VIRTUAL_ENV"] = DEPS_VENV
            environment["PATH"] = f"{DEPS_VENV}/bin:{DEFAULT_PATH}"
        if (self.workspace / "src").is_dir():
            environment["PYTHONPATH"] = f"{WORKDIR}/src"  # src-layout projects aren't installed
        self._container = self.client.containers.run(
            image,
            command=["sleep", "infinity"],
            name=f"devpilot-sandbox-{uuid.uuid4().hex[:8]}",
            detach=True,
            volumes=volumes,
            working_dir=WORKDIR,
            environment=environment,
            network_mode="bridge" if self.network else "none",
            **self._hardening(),
        )

    def _hardening(self) -> dict[str, Any]:
        return {
            "user": _container_user(),
            "read_only": True,
            "tmpfs": {"/tmp": "rw,exec,size=512m"},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "mem_limit": self.memory_limit,
            "pids_limit": 256,
            "nano_cpus": 2_000_000_000,
        }

    def _install_dependencies(self, image: str) -> Path | None:
        """Install the repo's dependencies into a virtualenv, in a separate setup container.

        Only the setup container has network access, and it sees the workspace read-only. The
        virtualenv lives in a host temp directory that the run container mounts read-only, so
        commands the agent runs stay offline unless network was enabled for the whole run.
        Returns the virtualenv's host directory, or None if there was nothing to install or
        the install failed (the reason is in setup_report).
        """
        sources, pip_args = dependency_sources(self.workspace)
        if not sources:
            return None
        self._deps_dir = Path(tempfile.mkdtemp(prefix="devpilot-deps-"))
        script = (
            f"python -m venv --copies --system-site-packages {DEPS_VENV} && "
            f'exec {DEPS_VENV}/bin/pip install --no-cache-dir --disable-pip-version-check "$@"'
        )
        container = self.client.containers.run(
            image,
            command=["sh", "-c", script, "sh", *pip_args],
            name=f"devpilot-setup-{uuid.uuid4().hex[:8]}",
            detach=True,
            volumes={
                str(self.workspace): {"bind": WORKDIR, "mode": "ro"},
                str(self._deps_dir): {"bind": DEPS_DIR, "mode": "rw"},
            },
            working_dir=WORKDIR,
            environment={"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"},
            network_mode="bridge",
            **self._hardening(),
        )
        try:
            try:
                exit_code = int(container.wait(timeout=self.install_timeout)["StatusCode"])
            except Exception:  # The Docker SDK raises a requests timeout error here.
                with contextlib.suppress(DockerException):
                    container.kill()
                exit_code = -1
            output = container.logs().decode("utf-8", errors="replace")
        finally:
            with contextlib.suppress(NotFound):
                container.remove(force=True)
        files = ", ".join(sources)
        if exit_code == 0:
            self.setup_report = (
                f"The project's dependencies from {files} were installed before the run "
                "(network access was enabled only for that setup step)."
            )
            return self._deps_dir
        reason = "timed out" if exit_code == -1 else f"failed with exit code {exit_code}"
        self.setup_report = (
            f"Installing the project's dependencies from {files} {reason}, so some imports may "
            f"be missing. Last output:\n{output[-REPORT_TAIL_CHARS:]}"
        )
        return None

    def _ensure_image(self) -> str:
        """Build (once) a derived image that has pytest preinstalled, so tests run offline."""
        tag = sandbox_image_tag(self.base_image)
        try:
            self.client.images.get(tag)
            return tag
        except ImageNotFound:
            pass
        dockerfile = (
            f"FROM {self.base_image}\n"
            f"RUN pip install --no-cache-dir --disable-pip-version-check pytest=={PYTEST_VERSION}\n"
        )
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
        if self._container is not None:
            with contextlib.suppress(NotFound):
                self._container.remove(force=True)
            self._container = None
        if self._deps_dir is not None:
            shutil.rmtree(self._deps_dir, ignore_errors=True)
            self._deps_dir = None

    def __enter__(self) -> "DockerSandbox":
        try:
            self.start()
        except BaseException:
            self.close()  # __exit__ isn't called when __enter__ fails
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

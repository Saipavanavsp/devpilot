import hashlib
import os
import re
import shlex
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devpilot.safety import check_command
from devpilot.sandbox import WORKDIR, CommandResult, Sandbox

MAX_OUTPUT_CHARS = 12_000
MAX_READ_CHARS = 40_000
MAX_LISTED_FILES = 500
MAX_HASHED_FILE_BYTES = 2_000_000
IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules"}
DEFAULT_TEST_COMMAND = "python -m pytest -q"
PYTEST_PATTERN = re.compile(r"\bpytest\b")
PASSED_PATTERN = re.compile(r"\b(\d+) passed\b")
NOTHING_PASSED = (
    "\n[DevPilot: no test passed (all skipped, deselected or none collected), so this run "
    "does not count as passing.]"
)

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": "List files under a directory of the workspace, recursively. "
        "Skips .git, caches and virtualenvs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory relative to the workspace."}
            },
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file. Each line is prefixed with its line number and '| '; "
        "the prefix is not part of the file. Use start_line/end_line for large files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content. "
        "Prefer edit_file for small changes to existing files.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace one exact occurrence of old_text with new_text in a file. "
        "old_text must match the file exactly (including indentation) and appear only once; "
        "include surrounding lines to make it unique.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command inside the Docker sandbox, with the workspace "
        f"mounted at {WORKDIR} as the working directory. No network access unless enabled. "
        "Returns the exit code and combined stdout/stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 1, "description": "Seconds."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "run_tests",
        "description": f"Run the test suite in the sandbox with `{DEFAULT_TEST_COMMAND}`. "
        "Optionally limit it to a file, directory or test node id. Only a run without a target "
        "verifies the work for finish.",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
        },
    },
    {
        "name": "finish",
        "description": "Call once the task is complete and the full test suite passes. It is "
        "rejected unless run_tests (without a target) passed after your last change. The summary "
        "becomes the pull request description, so describe what changed and why.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False


def truncate_middle(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Keep the head and tail of long output; failures usually show up at the end."""
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n... [{omitted} characters omitted] ...\n{text[-half:]}"


def _format_command_result(result: CommandResult) -> str:
    return f"exit code: {result.exit_code}\n{truncate_middle(result.output)}"


def tests_passed_in(result: CommandResult) -> bool:
    """A pytest run passed if it exited cleanly and at least one test actually passed."""
    if result.exit_code != 0 or result.timed_out:
        return False
    return any(int(count) > 0 for count in PASSED_PATTERN.findall(result.output))


def _is_link(dirpath: str, name: str) -> bool:
    return os.path.islink(os.path.join(dirpath, name))


def workspace_fingerprint(root: Path, exclude: frozenset[Path] = frozenset()) -> str:
    """Hash the workspace's files, to detect any change to the code since the tests ran.

    Contents are hashed because an edit can keep the size and land within the filesystem's
    timestamp granularity; very large files fall back to size and modification time.
    Symlinks and special files are never followed or opened: code in the sandbox can create
    them, and reading a FIFO or a link to /dev/zero on the host would hang or exhaust memory.
    Directories in exclude (DevPilot's own run logs, if they live in the workspace) are skipped.
    """
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in IGNORED_DIRS and (Path(dirpath) / d).resolve() not in exclude
        )
        for filename in sorted([*filenames, *(d for d in dirnames if _is_link(dirpath, d))]):
            path = Path(dirpath) / filename
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    content = f"link:{os.readlink(path)}"
                elif not stat.S_ISREG(info.st_mode):
                    content = f"special:{stat.S_IFMT(info.st_mode)}"
                elif info.st_size <= MAX_HASHED_FILE_BYTES:
                    content = hashlib.sha256(path.read_bytes()).hexdigest()
                else:
                    content = f"{info.st_size}:{info.st_mtime_ns}"
            except OSError:
                continue
            digest.update(f"{path.relative_to(root).as_posix()}\0{content}\n".encode())
    return digest.hexdigest()


class ToolExecutor:
    def __init__(
        self,
        workspace: Path,
        sandbox: Sandbox,
        *,
        command_timeout: int = 120,
        test_command: str = DEFAULT_TEST_COMMAND,
        untracked_dirs: Iterable[Path] = (),
    ) -> None:
        self.workspace = workspace.resolve()
        # Changes here don't invalidate a test run (e.g. the run log, written every step).
        self.untracked_dirs = frozenset(path.resolve() for path in untracked_dirs)
        self.sandbox = sandbox
        self.command_timeout = command_timeout
        self.test_command = test_command
        self.last_tests_passed: bool | None = None
        # Workspace state when the full suite last passed; any later change invalidates it.
        self._verified_state: str | None = None
        self._handlers: dict[str, Callable[..., ToolResult]] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "run_command": self.run_command,
            "run_tests": self.run_tests,
            "finish": self.finish,
        }

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(f"Unknown tool: {name}", is_error=True)
        try:
            return handler(**arguments)
        except TypeError as exc:
            return ToolResult(f"Invalid arguments for {name}: {exc}", is_error=True)
        except (ToolError, OSError) as exc:
            return ToolResult(str(exc), is_error=True)

    def resolve(self, path: str) -> Path:
        """Map a model-supplied path to a file inside the workspace, rejecting anything outside."""
        relative = path.strip()
        # The model sees the container path in tool descriptions and sometimes uses it.
        if relative == WORKDIR or relative.startswith(WORKDIR + "/"):
            relative = relative[len(WORKDIR) :].lstrip("/")
        candidate = (self.workspace / relative).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ToolError(f"Path {path!r} is outside the workspace.")
        if ".git" in candidate.relative_to(self.workspace).parts:
            raise ToolError("The .git directory is managed by DevPilot and cannot be accessed.")
        return candidate

    def _display(self, path: Path) -> str:
        return path.relative_to(self.workspace).as_posix() or "."

    def list_files(self, path: str = ".") -> ToolResult:
        root = self.resolve(path)
        if not root.is_dir():
            raise ToolError(f"{path} is not a directory.")
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            for filename in sorted(filenames):
                files.append(self._display(Path(dirpath) / filename))
        if not files:
            return ToolResult("(no files)")
        listing = "\n".join(files[:MAX_LISTED_FILES])
        if len(files) > MAX_LISTED_FILES:
            listing += f"\n... and {len(files) - MAX_LISTED_FILES} more files"
        return ToolResult(listing)

    def read_file(
        self, path: str, start_line: int | None = None, end_line: int | None = None
    ) -> ToolResult:
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"File not found: {path}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(start_line or 1, 1)
        end = min(end_line or len(lines), len(lines))
        numbered = "\n".join(f"{n}| {lines[n - 1]}" for n in range(start, end + 1))
        if len(numbered) > MAX_READ_CHARS:
            numbered = numbered[:MAX_READ_CHARS] + "\n... [truncated; use start_line/end_line]"
        return ToolResult(numbered or "(empty file)")

    def write_file(self, path: str, content: str) -> ToolResult:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        line_count = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        return ToolResult(f"Wrote {line_count} lines to {self._display(target)}")

    def edit_file(self, path: str, old_text: str, new_text: str) -> ToolResult:
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"File not found: {path}")
        original = target.read_text(encoding="utf-8")
        occurrences = original.count(old_text) if old_text else 0
        if occurrences == 0:
            raise ToolError(
                f"old_text was not found in {path}. Re-read the file and copy the text exactly."
            )
        if occurrences > 1:
            raise ToolError(
                f"old_text appears {occurrences} times in {path}; include more context."
            )
        target.write_text(original.replace(old_text, new_text, 1), encoding="utf-8", newline="\n")
        return ToolResult(f"Edited {self._display(target)}")

    def run_command(self, command: str, timeout: int | None = None) -> ToolResult:
        reason = check_command(command)
        if reason:
            return ToolResult(f"Command blocked by safety policy: {reason}.", is_error=True)
        seconds = min(timeout or self.command_timeout, self.command_timeout)
        result = self.sandbox.run(command, timeout=seconds)
        # Models often run pytest directly instead of calling run_tests; count those runs too.
        # These runs are reported but never verify the work; only a full run_tests does.
        if PYTEST_PATTERN.search(command):
            self.last_tests_passed = tests_passed_in(result)
        return ToolResult(_format_command_result(result), is_error=result.timed_out)

    def run_tests(self, target: str | None = None) -> ToolResult:
        command = self.test_command
        if target:
            command += " " + shlex.quote(target)
        result = self.sandbox.run(command, timeout=self.command_timeout)
        self.last_tests_passed = tests_passed_in(result)
        if not target:
            passed = self.last_tests_passed
            self._verified_state = self._fingerprint() if passed else None
        output = _format_command_result(result)
        if result.exit_code == 0 and not self.last_tests_passed:
            output += NOTHING_PASSED
        return ToolResult(output)

    def tests_verified(self) -> bool:
        """True if the full suite passed and the workspace hasn't changed since."""
        return self._verified_state is not None and self._verified_state == self._fingerprint()

    def _fingerprint(self) -> str:
        return workspace_fingerprint(self.workspace, self.untracked_dirs)

    @property
    def tests_passed(self) -> bool | None:
        """Test status to report: True only when verified, None when stale or never run."""
        if self.tests_verified():
            return True
        return False if self.last_tests_passed is False else None

    def finish(self, summary: str) -> ToolResult:
        return ToolResult(summary)

"""Run DevPilot on every benchmark task and grade the result with hidden tests.

Each task directory holds task.md (the prompt), repo/ (the starting code the agent sees) and
hidden/ (grading tests copied in only after the agent has finished).

    python benchmarks/run_benchmark.py                        # all tasks, once each
    python benchmarks/run_benchmark.py --tasks 01 04          # tasks starting with 01 or 04
    python benchmarks/run_benchmark.py --repeats 3            # three attempts per task

Before the agent runs, each task's untouched starting code is graded too. That baseline shows
what the hidden tests accept without DevPilot (it should be 0 solved), so every solve in the
results is the agent's work.
"""

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from devpilot.config import Settings, load_settings
from devpilot.pipeline import run_task
from devpilot.sandbox import PYTEST_VERSION, DockerSandbox
from devpilot.tools import tests_passed_in

TASKS_DIR = Path(__file__).parent / "tasks"
RESULTS_FILE = Path(__file__).parent / "results.md"
GRADING_DIR = "_grading"
HIDDEN_TEST = re.compile(r"^\s*def test_", re.MULTILINE)
# Any of these would let the hidden tests pass without running them.
FAILED_PATTERN = re.compile(r"\b\d+ (failed|errors?|skipped|xfailed|deselected)\b")
SHADOWING_NAMES = ["pytest.py", "pytest", "_pytest", "py.py", "pluggy", "pluggy.py"]


@dataclass
class Attempt:
    task: str
    attempt: int
    solved: bool
    status: str
    steps: int
    cost_usd: float
    run_id: str | None
    error: str | None = None


@dataclass
class TaskSummary:
    task: str
    hidden_tests: int
    baseline_solved: bool
    attempts: int
    errors: int
    solved: int
    avg_steps: float
    avg_cost_usd: float
    statuses: dict[str, int]

    @property
    def completed(self) -> int:
        """Attempts where the agent actually ran (API or infrastructure errors excluded)."""
        return self.attempts - self.errors

    @property
    def success_rate(self) -> float:
        return self.solved / self.completed if self.completed else 0.0


def discover_tasks(prefixes: list[str] | None) -> list[Path]:
    tasks = sorted(path for path in TASKS_DIR.iterdir() if (path / "task.md").is_file())
    if prefixes:
        tasks = [path for path in tasks if any(path.name.startswith(p) for p in prefixes)]
    return tasks


def hidden_test_count(task_dir: Path) -> int:
    """Number of grading tests, a rough measure of how much behaviour the task specifies."""
    return sum(
        len(HIDDEN_TEST.findall(path.read_text(encoding="utf-8")))
        for path in (task_dir / "hidden").rglob("*.py")
    )


def grade(task_dir: Path, workspace: Path, settings: Settings) -> bool:
    """Run the hidden tests against the agent's code, ignoring anything it could use to cheat.

    The workspace is agent-controlled, so its conftest.py files and pytest config are ignored,
    and a module that would shadow pytest itself fails the task outright.
    """
    if any((workspace / name).exists() for name in SHADOWING_NAMES):
        return False
    grading = workspace / GRADING_DIR
    shutil.rmtree(grading, ignore_errors=True)
    shutil.copytree(task_dir / "hidden", grading)
    (grading / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    command = (
        f"python -m pytest -q --noconftest -p no:cacheprovider "
        f"-c {GRADING_DIR}/pytest.ini --rootdir=. {GRADING_DIR}"
    )
    with DockerSandbox(
        workspace, base_image=settings.sandbox_image, timeout=settings.command_timeout
    ) as sandbox:
        result = sandbox.run(command)
    return tests_passed_in(result) and not FAILED_PATTERN.search(result.output)


def grade_baseline(task_dir: Path, settings: Settings, root: Path) -> bool:
    """Grade the untouched starting code, i.e. the result without the agent."""
    workspace = root / f"{task_dir.name}-baseline"
    shutil.copytree(task_dir / "repo", workspace)
    return grade(task_dir, workspace, settings)


def run_attempt(task_dir: Path, settings: Settings, root: Path, attempt: int) -> Attempt:
    workspace = root / f"{task_dir.name}-{attempt}"
    shutil.copytree(task_dir / "repo", workspace)
    prompt = (task_dir / "task.md").read_text(encoding="utf-8")
    try:
        outcome = run_task(prompt, workspace, settings)
    except Exception as exc:
        return Attempt(task_dir.name, attempt, False, "error", 0, 0.0, None, f"{exc}")
    return Attempt(
        task=task_dir.name,
        attempt=attempt,
        solved=grade(task_dir, workspace, settings),
        status=outcome.status,
        steps=outcome.steps,
        cost_usd=outcome.cost_usd,
        run_id=outcome.run_id,
    )


def summarize(task_dir: Path, baseline: bool, attempts: list[Attempt]) -> TaskSummary:
    statuses: dict[str, int] = {}
    for attempt in attempts:
        statuses[attempt.status] = statuses.get(attempt.status, 0) + 1
    ran = [a for a in attempts if a.status != "error"]
    return TaskSummary(
        task=task_dir.name,
        hidden_tests=hidden_test_count(task_dir),
        baseline_solved=baseline,
        attempts=len(attempts),
        errors=len(attempts) - len(ran),
        solved=sum(a.solved for a in ran),
        avg_steps=mean(a.steps for a in ran) if ran else 0.0,
        avg_cost_usd=mean(a.cost_usd for a in ran) if ran else 0.0,
        statuses=statuses,
    )


def _git_commit() -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit}{' (uncommitted changes)' if dirty else ''}"


def environment(settings: Settings) -> dict[str, Any]:
    """Everything needed to reproduce a benchmark run."""
    return {
        "provider": settings.provider,
        "model": settings.model,
        "effort": settings.effort,
        "max_steps": settings.max_steps,
        "max_cost_usd": settings.max_cost_usd,
        "sandbox_image": settings.sandbox_image,
        "pytest": PYTEST_VERSION,
        "devpilot_commit": _git_commit(),
        "host_python": platform.python_version(),
        "host_os": platform.platform(),
    }


def results_table(summaries: list[TaskSummary], env: dict[str, Any]) -> str:
    env_lines = "\n".join(f"- {key}: `{value}`" for key, value in env.items())
    lines = [
        "| Task | Hidden tests | Solved without agent | Solved | Success rate "
        "| Avg steps | Avg est. cost (USD) | Agent statuses | Errors (excluded) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        statuses = ", ".join(f"{name} x{count}" for name, count in sorted(s.statuses.items()))
        lines.append(
            f"| {s.task} | {s.hidden_tests} | {'yes' if s.baseline_solved else 'no'} "
            f"| {s.solved}/{s.completed} | {s.success_rate:.0%} | {s.avg_steps:.1f} "
            f"| {s.avg_cost_usd:.4f} | {statuses} | {s.errors} |"
        )
    completed = sum(s.completed for s in summaries)
    solved = sum(s.solved for s in summaries)
    total_steps = sum(s.avg_steps * s.completed for s in summaries)
    total_cost = sum(s.avg_cost_usd * s.completed for s in summaries)
    rate = solved / completed if completed else 0.0
    lines.append(
        f"| **Total** | | **{sum(s.baseline_solved for s in summaries)}/{len(summaries)}** "
        f"| **{solved}/{completed}** | **{rate:.0%}** "
        f"| **{total_steps / max(completed, 1):.1f}** "
        f"| **{total_cost / max(completed, 1):.4f}** | "
        f"| **{sum(s.errors for s in summaries)}** |"
    )
    lines.append(f"\nTotal estimated cost of this run: ${total_cost:.4f}")
    return f"Environment:\n\n{env_lines}\n\n" + "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", nargs="*", help="Run only tasks whose name starts with these.")
    parser.add_argument("--repeats", type=int, default=1, help="Attempts per task (default 1).")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    settings = load_settings(args.env_file)
    settings.require_api_key()
    tasks = discover_tasks(args.tasks)
    stamp = f"{datetime.now(UTC):%Y%m%d-%H%M%S}"
    root = settings.workspace_dir.resolve() / "bench" / stamp
    root.mkdir(parents=True)
    env = environment(settings)

    attempts: list[Attempt] = []
    summaries: list[TaskSummary] = []
    for task_dir in tasks:
        baseline = grade_baseline(task_dir, settings, root)
        print(f"{task_dir.name}: starting code solved={baseline}", flush=True)
        task_attempts = []
        for number in range(1, args.repeats + 1):
            print(f"  attempt {number}/{args.repeats} ...", flush=True)
            result = run_attempt(task_dir, settings, root, number)
            print(f"    solved={result.solved} status={result.status} steps={result.steps} "
                  f"cost=${result.cost_usd:.4f}", flush=True)  # fmt: skip
            if result.error:
                print(f"    error: {result.error[:300]}", flush=True)
            task_attempts.append(result)
        attempts += task_attempts
        summaries.append(summarize(task_dir, baseline, task_attempts))

    table = results_table(summaries, env)
    print("\n" + table)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    report = settings.runs_dir / f"benchmark-{stamp}.json"
    report.write_text(
        json.dumps(
            {
                "environment": env,
                "repeats": args.repeats,
                "tasks": [asdict(s) | {"success_rate": s.success_rate} for s in summaries],
                "attempts": [asdict(a) for a in attempts],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if all(a.status == "error" for a in attempts):
        print(f"\nEvery attempt failed before the agent ran; saved {report} only.")
        return 2
    # Append, so runs with different models or task subsets are all kept.
    if not RESULTS_FILE.exists():
        RESULTS_FILE.write_text("# Benchmark results\n", encoding="utf-8")
    with RESULTS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## Run {stamp} UTC ({args.repeats} attempt(s) per task)\n\n{table}\n")
    print(f"\nSaved {report} and appended to {RESULTS_FILE}")
    return 0 if all(a.solved for a in attempts) else 1


if __name__ == "__main__":
    sys.exit(main())

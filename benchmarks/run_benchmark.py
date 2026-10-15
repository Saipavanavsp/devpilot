"""Run DevPilot on every benchmark task and grade the result with hidden tests.

Each task directory holds task.md (the prompt), repo/ (the starting code the agent sees) and
hidden/ (grading tests copied in only after the agent has finished).

    python benchmarks/run_benchmark.py                 # all tasks
    python benchmarks/run_benchmark.py --tasks 01 04   # tasks whose name starts with 01 or 04
"""

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from devpilot.config import Settings, load_settings
from devpilot.pipeline import run_task
from devpilot.sandbox import DockerSandbox

TASKS_DIR = Path(__file__).parent / "tasks"
RESULTS_FILE = Path(__file__).parent / "results.md"
GRADING_DIR = "_grading"


@dataclass
class TaskResult:
    task: str
    solved: bool
    status: str
    steps: int
    cost_usd: float
    run_id: str | None
    error: str | None = None


def discover_tasks(prefixes: list[str] | None) -> list[Path]:
    tasks = sorted(path for path in TASKS_DIR.iterdir() if (path / "task.md").is_file())
    if prefixes:
        tasks = [path for path in tasks if any(path.name.startswith(p) for p in prefixes)]
    return tasks


def grade(task_dir: Path, workspace: Path, settings: Settings) -> bool:
    grading = workspace / GRADING_DIR
    shutil.rmtree(grading, ignore_errors=True)
    shutil.copytree(task_dir / "hidden", grading)
    with DockerSandbox(
        workspace, base_image=settings.sandbox_image, timeout=settings.command_timeout
    ) as sandbox:
        result = sandbox.run(f"python -m pytest -q {GRADING_DIR}")
    return result.exit_code == 0


def run_one(task_dir: Path, settings: Settings, stamp: str) -> TaskResult:
    workspace = settings.workspace_dir.resolve() / "bench" / f"{task_dir.name}-{stamp}"
    shutil.copytree(task_dir / "repo", workspace)
    prompt = (task_dir / "task.md").read_text(encoding="utf-8")
    try:
        outcome = run_task(prompt, workspace, settings)
    except Exception as exc:
        return TaskResult(task_dir.name, False, "error", 0, 0.0, None, f"{exc}")
    solved = grade(task_dir, workspace, settings)
    return TaskResult(
        task=task_dir.name,
        solved=solved,
        status=outcome.status,
        steps=outcome.steps,
        cost_usd=outcome.cost_usd,
        run_id=outcome.run_id,
    )


def results_table(results: list[TaskResult], model: str) -> str:
    solved = sum(result.solved for result in results)
    total_cost = sum(result.cost_usd for result in results)
    total_steps = sum(result.steps for result in results)
    lines = [
        "| Task | Solved | Agent status | Steps | Est. cost (USD) |",
        "|---|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| {result.task} | {'yes' if result.solved else 'no'} | {result.status} "
            f"| {result.steps} | {result.cost_usd:.4f} |"
        )
    lines.append(
        f"| **Total** | **{solved}/{len(results)}** | | **{total_steps}** | **{total_cost:.4f}** |"
    )
    return f"Model: `{model}`\n\n" + "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", nargs="*", help="Run only tasks whose name starts with these.")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    settings.require_anthropic_key()
    tasks = discover_tasks(args.tasks)
    stamp = f"{datetime.now(UTC):%Y%m%d-%H%M%S}"

    results: list[TaskResult] = []
    for task_dir in tasks:
        print(f"Running {task_dir.name} ...", flush=True)
        result = run_one(task_dir, settings, stamp)
        print(f"  solved={result.solved} status={result.status} steps={result.steps} "
              f"cost=${result.cost_usd:.4f}", flush=True)  # fmt: skip
        results.append(result)

    table = results_table(results, settings.model)
    print("\n" + table)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    report = settings.runs_dir / f"benchmark-{stamp}.json"
    report.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    RESULTS_FILE.write_text(f"# Benchmark results\n\nRun {stamp} UTC.\n\n{table}\n", "utf-8")
    print(f"\nSaved {report} and {RESULTS_FILE}")
    return 0 if all(result.solved for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())

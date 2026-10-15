import argparse
import sys
from pathlib import Path
from typing import Any

from devpilot.config import ConfigError, load_settings
from devpilot.github_client import GitHubError
from devpilot.pipeline import RunOutcome, run_on_github, run_task
from devpilot.planner import PlanningError
from devpilot.providers import ProviderError
from devpilot.runlog import Event
from devpilot.sandbox import SandboxError


def print_event(event: Event) -> None:
    data: dict[str, Any] = event["data"]
    if event["type"] == "started":
        print(f"Run {data['run_id']} using {data['model']}")
    elif event["type"] == "plan":
        print("\nPlan:")
        for number, step in enumerate(data["plan"], start=1):
            print(f"  {number}. {step}")
        print()
    elif event["type"] == "step":
        _print_step(data)


def _print_step(step: dict[str, Any]) -> None:
    if step["thought"]:
        first_line = step["thought"].splitlines()[0]
        print(f"[{step['step']}] {first_line[:120]}")
    for call in step["tool_calls"]:
        detail = call["input"].get("path") or call["input"].get("command") or ""
        marker = "error" if call["is_error"] else "ok"
        print(f"[{step['step']}]   {call['tool']}({detail}) -> {marker}")


def print_outcome(outcome: RunOutcome) -> None:
    print(f"\nStatus: {outcome.status}")
    print(f"Steps: {outcome.steps}   Estimated cost: ${outcome.cost_usd:.4f}")
    print(f"Tests passing: {outcome.tests_passed}")
    print(f"Log: {outcome.log_path}")
    if outcome.branch:
        print(f"Branch: {outcome.branch}")
    if outcome.pr_url:
        print(f"Pull request: {outcome.pr_url}")
    print(f"\nSummary:\n{outcome.summary}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devpilot", description="Autonomous coding agent that plans, codes and tests."
    )
    parser.add_argument("--env-file", default=".env", help="Path to the .env file.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Solve a task in a local directory or a GitHub repo.")
    run.add_argument("task", help="What the agent should do.")
    run.add_argument("--workspace", type=Path, default=Path("."), help="Local directory to edit.")
    run.add_argument("--repo", help="owner/name: clone this repo and open a pull request.")
    run.add_argument("--no-pr", action="store_true", help="With --repo, skip the pull request.")

    issue = commands.add_parser("issue", help="Solve a GitHub issue and open a pull request.")
    issue.add_argument("repo", help="owner/name")
    issue.add_argument("number", type=int)
    issue.add_argument("--no-pr", action="store_true", help="Solve it but skip the pull request.")

    web = commands.add_parser("web", help="Start the web UI.")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.env_file)
        if args.command == "web":
            from devpilot.web.app import serve

            serve(settings, host=args.host, port=args.port)
            return 0
        if args.command == "issue":
            outcome = run_on_github(
                args.repo,
                settings,
                issue_number=args.number,
                open_pr=not args.no_pr,
                listeners=[print_event],
            )
        elif args.repo:
            outcome = run_on_github(
                args.repo, settings, task=args.task, open_pr=not args.no_pr, listeners=[print_event]
            )
        else:
            outcome = run_task(args.task, args.workspace, settings, listeners=[print_event])
    except (ConfigError, GitHubError, SandboxError, PlanningError, ProviderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print_outcome(outcome)
    return 0 if outcome.status == "finished" else 1


if __name__ == "__main__":
    sys.exit(main())

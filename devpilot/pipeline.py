import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from devpilot.agent import Agent
from devpilot.config import Settings
from devpilot.github_client import (
    GitHubClient,
    GitHubError,
    GitRepo,
    IssueInfo,
    validate_repo_name,
)
from devpilot.planner import make_plan
from devpilot.providers import Provider, make_provider
from devpilot.runlog import Listener, RunLog, new_run_id
from devpilot.sandbox import DockerSandbox
from devpilot.tools import ToolExecutor

LOCAL_ARTIFACTS = [".pytest_cache/", "__pycache__/", "*.pyc"]

SandboxFactory = Callable[..., Any]


@dataclass
class RunOutcome:
    run_id: str
    status: str
    summary: str
    plan: list[str]
    steps: int
    cost_usd: float
    tests_passed: bool | None
    log_path: str
    workspace: str
    branch: str | None = None
    pr_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def issue_to_task(issue: IssueInfo) -> str:
    body = issue.body.strip() or "(no description)"
    return f"Resolve GitHub issue #{issue.number}: {issue.title}\n\n{body}"


def _slug(text: str, max_length: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "task"


def pr_title(task: str, issue: IssueInfo | None) -> str:
    if issue:
        return f"Fix #{issue.number}: {issue.title}"
    first_line = task.strip().splitlines()[0]
    return first_line if len(first_line) <= 72 else first_line[:69] + "..."


def pr_body(outcome: RunOutcome, diff_stat: str, issue: IssueInfo | None) -> str:
    plan = "\n".join(f"{i}. {step}" for i, step in enumerate(outcome.plan, start=1))
    tests = {True: "passing", False: "failing", None: "not run"}[outcome.tests_passed]
    sections = [
        f"## Summary\n\n{outcome.summary}",
        f"## Plan\n\n{plan}",
        f"## Changes\n\n```\n{diff_stat}\n```",
        f"## Verification\n\nTest suite in the DevPilot Docker sandbox: **{tests}**.",
    ]
    if issue:
        sections.append(f"Closes #{issue.number}")
    sections.append(
        f"---\nOpened by DevPilot (run `{outcome.run_id}`, {outcome.steps} steps, "
        f"estimated cost ${outcome.cost_usd:.2f})."
    )
    return "\n\n".join(sections)


def _solve(
    task: str,
    workspace: Path,
    settings: Settings,
    log: RunLog,
    provider: Provider | None,
    sandbox_factory: SandboxFactory,
) -> RunOutcome:
    log.update(workspace=str(workspace))
    try:
        provider = provider or make_provider(settings)
        with sandbox_factory(
            workspace,
            base_image=settings.sandbox_image,
            network=settings.sandbox_network,
            timeout=settings.command_timeout,
        ) as sandbox:
            tools = ToolExecutor(workspace, sandbox, command_timeout=settings.command_timeout)
            plan, plan_usage = make_plan(provider, task, tools.list_files().content)
            log.set_plan(plan)
            result = Agent(provider, settings, tools, log, usage=plan_usage).run(task, plan)
    except Exception as exc:
        log.finish("error", summary=f"{type(exc).__name__}: {exc}")
        raise
    log.finish(
        result.status,
        summary=result.summary,
        steps_taken=result.steps,
        usage=result.usage.to_dict(),
        cost_usd=round(result.cost_usd, 4),
        tests_passed=result.tests_passed,
    )
    return RunOutcome(
        run_id=log.run_id,
        status=result.status,
        summary=result.summary,
        plan=plan,
        steps=result.steps,
        cost_usd=result.cost_usd,
        tests_passed=result.tests_passed,
        log_path=str(log.path),
        workspace=str(workspace),
    )


def run_task(
    task: str,
    workspace: Path,
    settings: Settings,
    *,
    provider: Provider | None = None,
    listeners: Iterable[Listener] = (),
    sandbox_factory: SandboxFactory = DockerSandbox,
) -> RunOutcome:
    """Plan and solve a task in a local workspace directory."""
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    log = RunLog(settings.runs_dir, task=task, model=settings.model, listeners=listeners)
    return _solve(task, workspace, settings, log, provider, sandbox_factory)


def run_on_github(
    repo_name: str,
    settings: Settings,
    *,
    task: str | None = None,
    issue_number: int | None = None,
    open_pr: bool = True,
    provider: Provider | None = None,
    github: GitHubClient | None = None,
    listeners: Iterable[Listener] = (),
    sandbox_factory: SandboxFactory = DockerSandbox,
) -> RunOutcome:
    """Clone a repository, solve a task or issue on a new branch, and open a pull request."""
    if (task is None) == (issue_number is None):
        raise ValueError("Pass exactly one of task or issue_number.")
    validate_repo_name(repo_name)
    github = github or GitHubClient(settings.require_github_token())
    issue = github.get_issue(repo_name, issue_number) if issue_number is not None else None
    task_text = issue_to_task(issue) if issue else str(task)

    run_id = new_run_id()
    workspace = settings.workspace_dir.resolve() / f"{repo_name.replace('/', '-')}-{run_id}"
    repo = github.clone(repo_name, workspace)
    repo.exclude(LOCAL_ARTIFACTS)
    base_branch = repo.current_branch()
    prefix = f"issue-{issue.number}" if issue else _slug(task_text)
    branch = f"devpilot/{prefix}-{run_id[-6:]}"
    repo.create_branch(branch)

    log = RunLog(
        settings.runs_dir, task=task_text, model=settings.model, run_id=run_id, listeners=listeners
    )
    log.update(repo=repo_name, issue=issue_number, branch=branch)
    outcome = _solve(task_text, workspace, settings, log, provider, sandbox_factory)
    outcome.branch = branch

    if open_pr and outcome.status == "finished" and repo.has_changes():
        try:
            outcome.pr_url = _publish(
                repo, github, repo_name, branch, base_branch, outcome, task_text, issue
            )
        except GitHubError as exc:
            log.update(pr_error=str(exc))
            raise
        log.update(pr_url=outcome.pr_url)
    return outcome


def _publish(
    repo: GitRepo,
    github: GitHubClient,
    repo_name: str,
    branch: str,
    base_branch: str,
    outcome: RunOutcome,
    task: str,
    issue: IssueInfo | None,
) -> str:
    title = pr_title(task, issue)
    author_name, author_email = github.commit_identity()
    repo.commit_all(f"{title}\n\n{outcome.summary}", author_name, author_email)
    repo.push(branch)
    return github.create_pull_request(
        repo_name,
        head=branch,
        base=base_branch,
        title=title,
        body=pr_body(outcome, repo.last_commit_stat(), issue),
    )

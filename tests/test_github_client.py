import base64
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from github.GithubException import GithubException

from devpilot.config import Settings
from devpilot.github_client import (
    GitHubClient,
    GitHubError,
    GitRepo,
    IssueInfo,
    _auth_env,
    validate_repo_name,
)
from devpilot.pipeline import issue_to_task, pr_title, run_on_github
from tests.conftest import FakeSandbox
from tests.fakes import claude_provider, plan_response, response, tool_use


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def remote(tmp_path) -> Path:
    """A bare repository with one commit on main, standing in for GitHub."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    (seed / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    git("add", ".", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init", cwd=seed)
    bare = tmp_path / "remote.git"
    git("clone", "-q", "--bare", str(seed), str(bare), cwd=tmp_path)
    return bare


def test_validate_repo_name():
    assert validate_repo_name("octo/hello-world.py") == "octo/hello-world.py"
    for bad in ["octo", "octo/repo/extra", "https://github.com/a/b", "a/b; rm -rf"]:
        with pytest.raises(GitHubError):
            validate_repo_name(bad)


def test_auth_env_uses_header_not_url():
    env = _auth_env("secret-token")

    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    encoded = env["GIT_CONFIG_VALUE_0"].removeprefix("AUTHORIZATION: basic ")
    assert base64.b64decode(encoded).decode() == "x-access-token:secret-token"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_git_repo_branch_commit_push(tmp_path, remote):
    repo = GitRepo.clone(str(remote), tmp_path / "clone", token="secret-token")
    repo.exclude([".pytest_cache/"])
    repo.create_branch("devpilot/fix")
    (tmp_path / "clone" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "clone" / ".pytest_cache").mkdir()
    (tmp_path / "clone" / ".pytest_cache" / "x").write_text("cache")

    assert repo.has_changes()
    repo.commit_all("Fix add", "DevPilot", "bot@example.com")
    repo.push("devpilot/fix")

    assert "calc.py" in repo.last_commit_stat()
    assert ".pytest_cache" not in repo.last_commit_stat()
    assert "secret-token" not in (tmp_path / "clone" / ".git" / "config").read_text()
    log = git("log", "--format=%an <%ae> %s", "devpilot/fix", cwd=remote)
    assert log.splitlines()[0] == "DevPilot <bot@example.com> Fix add"


def test_git_errors_are_reported(tmp_path):
    with pytest.raises(GitHubError, match="git clone failed"):
        GitRepo.clone(str(tmp_path / "missing.git"), tmp_path / "clone")


def test_get_issue_maps_fields():
    github = MagicMock()
    issue = github.get_repo.return_value.get_issue.return_value
    issue.number, issue.title, issue.body = 7, "Crash on empty input", None
    issue.html_url = "https://github.com/o/r/issues/7"

    info = GitHubClient("t", github=github).get_issue("o/r", 7)

    github.get_repo.assert_called_with("o/r")
    assert info == IssueInfo(7, "Crash on empty input", "", "https://github.com/o/r/issues/7")


def test_get_issue_wraps_api_errors():
    github = MagicMock()
    github.get_repo.side_effect = GithubException(404, {"message": "Not Found"}, None)

    with pytest.raises(GitHubError, match="issue #3"):
        GitHubClient("t", github=github).get_issue("o/r", 3)


def test_create_pull_request_returns_url():
    github = MagicMock()
    github.get_repo.return_value.create_pull.return_value.html_url = "https://github.com/o/r/pull/1"

    url = GitHubClient("t", github=github).create_pull_request(
        "o/r", head="devpilot/x", base="main", title="T", body="B"
    )

    github.get_repo.return_value.create_pull.assert_called_once_with(
        title="T", body="B", head="devpilot/x", base="main"
    )
    assert url == "https://github.com/o/r/pull/1"


def test_commit_identity_uses_noreply_address():
    github = MagicMock()
    github.get_user.return_value.id = 42
    github.get_user.return_value.login = "octo"

    assert GitHubClient("t", github=github).commit_identity() == (
        "DevPilot",
        "42+octo@users.noreply.github.com",
    )


def test_issue_task_and_title():
    issue = IssueInfo(12, "Add a divide function", "It should raise on zero.", "url")

    assert issue_to_task(issue).startswith("Resolve GitHub issue #12: Add a divide function")
    assert pr_title("ignored", issue) == "Fix #12: Add a divide function"
    assert pr_title("x" * 100, None).endswith("...")


@contextmanager
def fake_sandbox_factory(workspace, **_):
    yield FakeSandbox()


def test_issue_mode_end_to_end_opens_pr(tmp_path, remote):
    github = MagicMock(spec=GitHubClient)
    github.get_issue.return_value = IssueInfo(5, "add() subtracts", "add(2, 3) returns -1", "url")
    github.clone.side_effect = lambda name, dest: GitRepo.clone(str(remote), dest)
    github.commit_identity.return_value = ("DevPilot", "bot@example.com")
    github.create_pull_request.return_value = "https://github.com/o/r/pull/9"
    provider, _ = claude_provider(
        [
            plan_response("Fix add in calc.py", "Run tests"),
            response(tool_use("edit_file", path="calc.py", old_text="a - b", new_text="a + b")),
            response(tool_use("finish", summary="add() now returns the sum.")),
        ]
    )
    settings = Settings(
        anthropic_api_key="k",
        github_token="t",
        runs_dir=tmp_path / "runs",
        workspace_dir=tmp_path / "ws",
    )

    outcome = run_on_github(
        "o/r",
        settings,
        issue_number=5,
        provider=provider,
        github=github,
        sandbox_factory=fake_sandbox_factory,
    )

    assert outcome.status == "finished"
    assert outcome.pr_url == "https://github.com/o/r/pull/9"
    assert outcome.branch.startswith("devpilot/issue-5-")
    kwargs = github.create_pull_request.call_args.kwargs
    assert kwargs["title"] == "Fix #5: add() subtracts"
    assert kwargs["base"] == "main"
    assert "Closes #5" in kwargs["body"]
    assert "calc.py" in kwargs["body"]
    assert outcome.branch in git("branch", "--list", "devpilot/*", cwd=remote)


def test_no_pr_when_nothing_changed(tmp_path, remote):
    github = MagicMock(spec=GitHubClient)
    github.clone.side_effect = lambda name, dest: GitRepo.clone(str(remote), dest)
    provider, _ = claude_provider(
        [plan_response("Look around"), response(tool_use("finish", summary="Nothing to do."))]
    )
    settings = Settings(
        anthropic_api_key="k", github_token="t", runs_dir=tmp_path / "r", workspace_dir=tmp_path
    )

    outcome = run_on_github(
        "o/r",
        settings,
        task="Check the code",
        provider=provider,
        github=github,
        sandbox_factory=fake_sandbox_factory,
    )

    assert outcome.pr_url is None
    github.create_pull_request.assert_not_called()

import base64
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from github import Auth, Github
from github.GithubException import GithubException

REPO_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


class GitHubError(Exception):
    pass


@dataclass(frozen=True)
class IssueInfo:
    number: int
    title: str
    body: str
    url: str


def validate_repo_name(name: str) -> str:
    if not REPO_NAME_PATTERN.match(name):
        raise GitHubError(f"Expected a repository in the form owner/name, got {name!r}")
    return name


def _auth_env(token: str | None) -> dict[str, str]:
    """Pass the token to git as an HTTP header through environment config.

    This keeps it out of the process arguments and out of the clone's .git/config.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
            GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}",
        )
    return env


class GitRepo:
    """A local clone driven through the git command line."""

    def __init__(self, path: Path, token: str | None = None) -> None:
        self.path = path
        self._token = token

    @classmethod
    def clone(cls, url: str, dest: Path, token: str | None = None) -> "GitRepo":
        dest.parent.mkdir(parents=True, exist_ok=True)
        repo = cls(dest, token)
        repo._git("clone", "--quiet", url, str(dest), cwd=dest.parent)
        return repo

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd or self.path,
            env=_auth_env(self._token),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise GitHubError(f"git {args[0]} failed: {completed.stderr.strip()}")
        return completed.stdout

    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def create_branch(self, name: str) -> None:
        self._git("checkout", "--quiet", "-b", name)

    def exclude(self, patterns: list[str]) -> None:
        """Ignore local artifacts (test caches) without touching the project's .gitignore."""
        exclude_file = self.path / ".git" / "info" / "exclude"
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        with exclude_file.open("a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(patterns) + "\n")

    def has_changes(self) -> bool:
        return bool(self._git("status", "--porcelain").strip())

    def commit_all(self, message: str, author_name: str, author_email: str) -> None:
        self._git("add", "--all")
        self._git(
            "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
            "commit", "--quiet", "-m", message,
        )  # fmt: skip

    def last_commit_stat(self) -> str:
        return self._git("show", "--stat", "--format=", "HEAD").strip()

    def push(self, branch: str) -> None:
        self._git("push", "--quiet", "--set-upstream", "origin", branch)


class GitHubClient:
    def __init__(self, token: str, github: Github | None = None) -> None:
        self.token = token
        self._github = github or Github(auth=Auth.Token(token))

    def get_issue(self, repo_name: str, number: int) -> IssueInfo:
        try:
            issue = self._github.get_repo(validate_repo_name(repo_name)).get_issue(number)
        except GithubException as exc:
            raise GitHubError(f"Could not load issue #{number} from {repo_name}: {exc}") from exc
        return IssueInfo(
            number=issue.number, title=issue.title, body=issue.body or "", url=issue.html_url
        )

    def clone(self, repo_name: str, dest: Path) -> GitRepo:
        url = f"https://github.com/{validate_repo_name(repo_name)}.git"
        return GitRepo.clone(url, dest, self.token)

    def commit_identity(self) -> tuple[str, str]:
        """Commit as DevPilot, using the token owner's noreply address."""
        user = self._github.get_user()
        return "DevPilot", f"{user.id}+{user.login}@users.noreply.github.com"

    def create_pull_request(
        self, repo_name: str, *, head: str, base: str, title: str, body: str
    ) -> str:
        try:
            repo = self._github.get_repo(validate_repo_name(repo_name))
            pull = repo.create_pull(title=title, body=body, head=head, base=base)
        except GithubException as exc:
            raise GitHubError(f"Could not open a pull request on {repo_name}: {exc}") from exc
        return pull.html_url

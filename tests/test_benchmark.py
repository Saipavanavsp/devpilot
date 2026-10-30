import shutil
from pathlib import Path

import pytest

from benchmarks.run_benchmark import TASKS_DIR, Attempt, grade, summarize
from devpilot.config import Settings
from tests.test_sandbox import _docker_available

TASK = TASKS_DIR / "04_median_bug"
SOLUTION = """\
def mean(values):
    if not values:
        raise ValueError("mean of empty sequence")
    return sum(values) / len(values)


def median(values):
    if not values:
        raise ValueError("median of empty sequence")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 0:
        return (ordered[middle - 1] + ordered[middle]) / 2
    return ordered[middle]
"""
SKIP_EVERYTHING = """\
import pytest

def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.skip)
"""


def attempt(status: str, solved: bool = False, steps: int = 10, cost: float = 0.1) -> Attempt:
    return Attempt("t", 1, solved, status, steps, cost, "run")


def test_api_errors_are_excluded_from_the_success_rate():
    summary = summarize(
        TASK, False, [attempt("finished", True), attempt("unverified"), attempt("error", steps=0)]
    )

    assert summary.errors == 1
    assert summary.completed == 2
    assert summary.success_rate == 0.5
    assert summary.avg_steps == 10


@pytest.fixture
def solved_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    shutil.copytree(TASK / "repo", workspace)
    (workspace / "stats.py").write_text(SOLUTION)
    return workspace


@pytest.mark.docker
@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not available")
class TestGrading:
    settings = Settings(anthropic_api_key=None, github_token=None)

    def test_starting_code_fails_and_a_real_fix_passes(self, tmp_path, solved_workspace):
        untouched = tmp_path / "untouched"
        shutil.copytree(TASK / "repo", untouched)

        assert not grade(TASK, untouched, self.settings)
        assert grade(TASK, solved_workspace, self.settings)

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("conftest.py", SKIP_EVERYTHING),
            ("pytest.ini", "[pytest]\naddopts = -k nothing_matches\n"),
            ("pytest.py", "raise SystemExit(0)\n"),
        ],
    )
    def test_agent_cannot_fake_the_hidden_tests(self, tmp_path, name, content):
        workspace = tmp_path / "ws"
        shutil.copytree(TASK / "repo", workspace)  # still the buggy code
        (workspace / name).write_text(content)

        assert not grade(TASK, workspace, self.settings)

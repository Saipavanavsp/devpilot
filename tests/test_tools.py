import pytest

from devpilot.sandbox import CommandResult
from devpilot.tools import TOOL_SPECS, ToolExecutor, truncate_middle


@pytest.fixture
def executor(tmp_path, fake_sandbox):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n")
    return ToolExecutor(tmp_path, fake_sandbox, command_timeout=60)


def test_every_spec_has_a_handler(executor):
    for spec in TOOL_SPECS:
        assert spec["name"] in executor._handlers


def test_list_files_skips_git(executor):
    result = executor.execute("list_files", {})

    assert result.content == "src/app.py"


def test_read_file_numbers_lines(executor):
    result = executor.execute("read_file", {"path": "src/app.py"})

    assert result.content == "1| def add(a, b):\n2|     return a - b"


def test_read_file_line_range(executor):
    result = executor.execute("read_file", {"path": "src/app.py", "start_line": 2})

    assert result.content == "2|     return a - b"


def test_container_paths_map_to_workspace(executor):
    result = executor.execute("read_file", {"path": "/workspace/src/app.py", "end_line": 1})

    assert result.content == "1| def add(a, b):"


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "src/../../x", ".git/config"])
def test_rejects_paths_outside_workspace(executor, path):
    result = executor.execute("write_file", {"path": path, "content": "x"})

    assert result.is_error


def test_write_file_creates_directories(executor, tmp_path):
    result = executor.execute("write_file", {"path": "pkg/new.py", "content": "x = 1\ny = 2\n"})

    assert not result.is_error
    assert result.content == "Wrote 2 lines to pkg/new.py"
    assert (tmp_path / "pkg" / "new.py").read_text() == "x = 1\ny = 2\n"


def test_edit_file_replaces_unique_match(executor, tmp_path):
    result = executor.execute(
        "edit_file", {"path": "src/app.py", "old_text": "a - b", "new_text": "a + b"}
    )

    assert not result.is_error
    assert "return a + b" in (tmp_path / "src" / "app.py").read_text()


def test_edit_file_rejects_missing_text(executor):
    result = executor.execute(
        "edit_file", {"path": "src/app.py", "old_text": "nope", "new_text": "x"}
    )

    assert result.is_error
    assert "not found" in result.content


def test_edit_file_rejects_ambiguous_text(executor, tmp_path):
    (tmp_path / "dup.py").write_text("x = 1\nx = 1\n")

    result = executor.execute("edit_file", {"path": "dup.py", "old_text": "x = 1", "new_text": ""})

    assert result.is_error
    assert "2 times" in result.content


def test_run_command_goes_through_sandbox(executor, fake_sandbox):
    fake_sandbox.results.append(CommandResult(exit_code=0, output="ok\n"))

    result = executor.execute("run_command", {"command": "echo ok"})

    assert fake_sandbox.commands == ["echo ok"]
    assert result.content == "exit code: 0\nok\n"


def test_run_command_blocks_dangerous_commands(executor, fake_sandbox):
    result = executor.execute("run_command", {"command": "sudo rm -rf /"})

    assert result.is_error
    assert "blocked" in result.content
    assert fake_sandbox.commands == []


def test_run_tests_tracks_outcome(executor, fake_sandbox):
    fake_sandbox.results.append(CommandResult(exit_code=1, output="1 failed"))

    executor.execute("run_tests", {"target": "tests/test_app.py"})

    assert fake_sandbox.commands == ["python -m pytest -q tests/test_app.py"]
    assert executor.last_tests_passed is False


def test_pytest_via_run_command_also_tracks_outcome(executor, fake_sandbox):
    fake_sandbox.results.append(CommandResult(exit_code=0, output="3 passed"))
    executor.execute("run_command", {"command": "python3 -m pytest tests/test_app.py"})
    assert executor.last_tests_passed is True

    executor.execute("run_command", {"command": "ls"})
    assert executor.last_tests_passed is True


def test_finish_returns_summary(executor):
    result = executor.execute("finish", {"summary": "Fixed add()"})

    assert not result.is_error
    assert result.content == "Fixed add()"


def test_unknown_tool_and_bad_arguments_are_errors(executor):
    assert executor.execute("delete_everything", {}).is_error
    assert executor.execute("read_file", {"wrong": "arg"}).is_error


def test_truncate_middle_keeps_head_and_tail():
    text = "a" * 100 + "b" * 100

    truncated = truncate_middle(text, limit=50)

    assert truncated.startswith("a" * 25)
    assert truncated.endswith("b" * 25)
    assert "150 characters omitted" in truncated

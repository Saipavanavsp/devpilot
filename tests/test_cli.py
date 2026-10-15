from devpilot import cli
from devpilot.pipeline import RunOutcome


def outcome(**overrides):
    values = dict(
        run_id="r1",
        status="finished",
        summary="done",
        plan=["a"],
        steps=3,
        cost_usd=0.05,
        tests_passed=True,
        log_path="runs/r1.json",
        workspace="ws",
    )
    return RunOutcome(**{**values, **overrides})


def test_run_command_uses_local_workspace(monkeypatch, tmp_path, capsys):
    calls = {}

    def fake_run_task(task, workspace, settings, listeners):
        calls.update(task=task, workspace=workspace)
        return outcome()

    monkeypatch.setattr(cli, "run_task", fake_run_task)

    code = cli.main(["--env-file", str(tmp_path / "none"), "run", "fix it", "--workspace", "w"])

    assert code == 0
    assert calls["task"] == "fix it"
    assert str(calls["workspace"]) == "w"
    assert "Status: finished" in capsys.readouterr().out


def test_issue_command_passes_repo_and_number(monkeypatch, tmp_path, capsys):
    calls = {}

    def fake_run_on_github(repo, settings, **kwargs):
        calls.update(repo=repo, **kwargs)
        return outcome(pr_url="https://github.com/o/r/pull/1", branch="devpilot/issue-12")

    monkeypatch.setattr(cli, "run_on_github", fake_run_on_github)

    code = cli.main(["--env-file", str(tmp_path / "none"), "issue", "o/r", "12"])

    assert code == 0
    assert calls["repo"] == "o/r"
    assert calls["issue_number"] == 12
    assert calls["open_pr"] is True
    assert "https://github.com/o/r/pull/1" in capsys.readouterr().out


def test_unfinished_run_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "run_task", lambda *a, **k: outcome(status="max_steps"))

    assert cli.main(["--env-file", str(tmp_path / "none"), "run", "x"]) == 1


def test_print_event_formats_steps(capsys):
    cli.print_event(
        {
            "type": "step",
            "data": {
                "step": 2,
                "thought": "Running the tests now.\nMore detail.",
                "tool_calls": [
                    {"tool": "run_tests", "input": {}, "is_error": False},
                    {"tool": "read_file", "input": {"path": "a.py"}, "is_error": True},
                ],
            },
        }
    )

    out = capsys.readouterr().out.splitlines()
    assert out == [
        "[2] Running the tests now.",
        "[2]   run_tests() -> ok",
        "[2]   read_file(a.py) -> error",
    ]

import json
from dataclasses import replace

import pytest

from devpilot.agent import CUT_OFF, MAX_UNVERIFIED_FINISHES, NOT_VERIFIED, NUDGE, Agent
from devpilot.config import Settings
from devpilot.planner import PlanningError, make_plan
from devpilot.runlog import RunLog
from devpilot.sandbox import CommandResult
from devpilot.tools import ToolExecutor
from tests.fakes import (
    claude_provider,
    plan_response,
    response,
    text,
    tool_use,
    verified_finish,
)


@pytest.fixture
def settings(tmp_path):
    return Settings(anthropic_api_key="test", github_token=None, runs_dir=tmp_path / "runs")


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    return root


def make_agent(responses, settings, workspace, sandbox):
    provider, client = claude_provider(responses)
    log = RunLog(settings.runs_dir, task="fix add", model=settings.model)
    tools = ToolExecutor(workspace, sandbox)
    return Agent(provider, settings, tools, log), client, log


def test_agent_edits_tests_and_finishes(settings, workspace, fake_sandbox):
    fake_sandbox.results.append(CommandResult(exit_code=0, output="1 passed"))
    agent, _, log = make_agent(
        [
            response(text("Reading the code."), tool_use("read_file", path="calc.py")),
            response(tool_use("edit_file", path="calc.py", old_text="a - b", new_text="a + b")),
            response(tool_use("run_tests")),
            response(tool_use("finish", summary="Fixed add to return the sum.")),
        ],
        settings,
        workspace,
        fake_sandbox,
    )

    result = agent.run("fix add", ["Fix add", "Run tests"])

    assert result.status == "finished"
    assert result.summary == "Fixed add to return the sum."
    assert result.steps == 4
    assert result.tests_passed is True
    assert "return a + b" in (workspace / "calc.py").read_text()

    saved = json.loads(log.path.read_text())
    assert [s["tool_calls"][0]["tool"] for s in saved["steps"]] == [
        "read_file",
        "edit_file",
        "run_tests",
        "finish",
    ]
    assert saved["steps"][0]["thought"] == "Reading the code."
    assert saved["steps"][0]["tokens"]["input_tokens"] == 1000


def test_tool_results_are_sent_back_with_matching_ids(settings, workspace, fake_sandbox):
    first = tool_use("list_files")
    agent, client, _ = make_agent(
        [response(first), verified_finish()],
        settings,
        workspace,
        fake_sandbox,
    )

    agent.run("task", ["step"])

    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == first.id
    assert "calc.py" in tool_result["content"]


def test_claude_request_uses_model_fallbacks_and_caching(settings, workspace, fake_sandbox):
    agent, client, _ = make_agent([verified_finish()], settings, workspace, fake_sandbox)

    agent.run("task", ["step"])

    call = client.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["fallbacks"] == "default"
    assert call["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"] == {"effort": "medium"}
    assert {tool["name"] for tool in call["tools"]} >= {"read_file", "run_tests", "finish"}


def test_agent_stops_at_max_steps(settings, workspace, fake_sandbox):
    settings = replace(settings, max_steps=2)
    agent, _, _ = make_agent(
        [response(tool_use("list_files")), response(tool_use("list_files"))],
        settings,
        workspace,
        fake_sandbox,
    )

    result = agent.run("task", ["step"])

    assert result.status == "max_steps"
    assert result.steps == 2


def test_agent_stops_when_budget_is_spent(settings, workspace, fake_sandbox):
    settings = replace(settings, max_cost_usd=0.001)
    agent, client, _ = make_agent(
        [response(tool_use("list_files")), response(tool_use("list_files"))],
        settings,
        workspace,
        fake_sandbox,
    )

    result = agent.run("task", ["step"])

    assert result.status == "budget_exceeded"
    assert len(client.calls) == 1


def test_text_answer_after_nudge_is_not_a_success(settings, workspace, fake_sandbox):
    agent, client, _ = make_agent(
        [response(text("I think it's done.")), response(text("All done."))],
        settings,
        workspace,
        fake_sandbox,
    )

    result = agent.run("task", ["step"])

    assert client.calls[1]["messages"][-1] == {"role": "user", "content": NUDGE}
    assert result.status == "stopped"
    assert result.summary == "All done."


def test_reply_cut_off_before_a_tool_call_is_not_a_stop(settings, workspace, fake_sandbox):
    agent, client, _ = make_agent(
        [
            response(text("Let me think about this at great length..."), stop_reason="max_tokens"),
            response(text("Still thinking..."), stop_reason="max_tokens"),
            verified_finish(),
        ],
        settings,
        workspace,
        fake_sandbox,
    )

    result = agent.run("task", ["step"])

    assert client.calls[1]["messages"][-1] == {"role": "user", "content": CUT_OFF}
    assert result.status == "finished"


def test_finish_is_rejected_until_tests_pass(settings, workspace, fake_sandbox):
    agent, client, _ = make_agent(
        [
            response(tool_use("edit_file", path="calc.py", old_text="a - b", new_text="a + b")),
            response(tool_use("finish", summary="skipped the tests")),
            verified_finish("Fixed add."),
        ],
        settings,
        workspace,
        fake_sandbox,
    )

    result = agent.run("fix add", ["step"])

    rejection = client.calls[2]["messages"][-1]["content"][0]
    assert rejection["is_error"] is True
    assert rejection["content"] == NOT_VERIFIED
    assert result.status == "finished"
    assert result.summary == "Fixed add."
    assert result.tests_passed is True


def test_change_after_passing_tests_needs_a_new_test_run(settings, workspace, fake_sandbox):
    agent, client, _ = make_agent(
        [
            response(tool_use("run_tests")),
            response(tool_use("edit_file", path="calc.py", old_text="a - b", new_text="a * b")),
            response(tool_use("finish", summary="done")),
            verified_finish(),
        ],
        settings,
        workspace,
        fake_sandbox,
    )

    result = agent.run("task", ["step"])

    assert client.calls[3]["messages"][-1]["content"][0]["content"] == NOT_VERIFIED
    assert result.status == "finished"


def test_targeted_test_run_does_not_verify(settings, workspace, fake_sandbox):
    agent, client, _ = make_agent(
        [
            response(tool_use("run_tests", target="tests/test_calc.py")),
            response(tool_use("finish", summary="done")),
            verified_finish(),
        ],
        settings,
        workspace,
        fake_sandbox,
    )

    assert agent.run("task", ["step"]).status == "finished"
    assert client.calls[2]["messages"][-1]["content"][0]["content"] == NOT_VERIFIED


def test_failing_tests_end_the_run_as_unverified(settings, workspace, fake_sandbox):
    fake_sandbox.results.extend(
        CommandResult(exit_code=1, output="1 failed") for _ in range(MAX_UNVERIFIED_FINISHES)
    )
    failing_finish = response(tool_use("run_tests"), tool_use("finish", summary="Can't fix it."))
    agent, client, _ = make_agent(
        [failing_finish] * MAX_UNVERIFIED_FINISHES, settings, workspace, fake_sandbox
    )

    result = agent.run("task", ["step"])

    assert result.status == "unverified"
    assert result.summary == "Can't fix it."
    assert result.tests_passed is False
    assert len(client.calls) == MAX_UNVERIFIED_FINISHES


def test_truncated_tool_call_is_not_executed(settings, workspace, fake_sandbox):
    agent, client, _ = make_agent(
        [
            response(tool_use("write_file", path="big.py", content="x"), stop_reason="max_tokens"),
            verified_finish(),
        ],
        settings,
        workspace,
        fake_sandbox,
    )

    agent.run("task", ["step"])

    assert not (workspace / "big.py").exists()
    assert client.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_refusal_ends_run(settings, workspace, fake_sandbox):
    agent, _, _ = make_agent([response(stop_reason="refusal")], settings, workspace, fake_sandbox)

    assert agent.run("task", ["step"]).status == "refused"


def test_make_plan_parses_steps():
    provider, client = claude_provider([plan_response("Read calc.py", "Fix add", "Run tests")])

    steps, usage = make_plan(provider, "fix add", "calc.py")

    assert steps == ["Read calc.py", "Fix add", "Run tests"]
    assert usage.input_tokens == 1000
    assert client.calls[0]["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.parametrize(
    "bad_response", [response(text("not json")), response(stop_reason="refusal")]
)
def test_make_plan_rejects_bad_output(bad_response):
    provider, _ = claude_provider([bad_response])

    with pytest.raises(PlanningError):
        make_plan(provider, "fix add", "calc.py")

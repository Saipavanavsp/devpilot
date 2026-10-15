import json

from fastapi.testclient import TestClient

from devpilot.config import Settings
from devpilot.pipeline import RunOutcome
from devpilot.web.app import create_app


def fake_runner(request, job_id, listener):
    listener({"type": "plan", "data": {"plan": ["Write code", "Run tests"]}})
    listener({"type": "step", "data": {"step": 1, "thought": "", "tool_calls": []}})
    return RunOutcome(
        run_id="r1",
        status="finished",
        summary=f"Did: {request.task}",
        plan=["Write code", "Run tests"],
        steps=1,
        cost_usd=0.01,
        tests_passed=True,
        log_path="runs/r1.json",
        workspace="ws",
    )


def failing_runner(request, job_id, listener):
    raise RuntimeError("sandbox exploded")


def make_client(runner):
    settings = Settings(anthropic_api_key="k", github_token=None)
    return TestClient(create_app(settings, runner=runner))


def read_events(client, job_id):
    with client.stream("GET", f"/api/runs/{job_id}/events") as stream:
        body = "".join(stream.iter_text())
    return [json.loads(line[len("data: ") :]) for line in body.splitlines() if line]


def test_index_and_config():
    client = make_client(fake_runner)

    assert "DevPilot" in client.get("/").text
    config = client.get("/api/config").json()
    assert config["model"] == "claude-sonnet-5-5"
    assert config["github_enabled"] is False


def test_run_streams_events_until_done():
    client = make_client(fake_runner)

    job = client.post("/api/runs", json={"task": "add slugify"}).json()
    events = read_events(client, job["id"])

    assert [event["type"] for event in events] == ["plan", "step", "done"]
    assert events[-1]["data"]["summary"] == "Did: add slugify"
    assert client.get("/api/runs").json()[0]["status"] == "finished"


def test_runner_errors_are_streamed():
    client = make_client(failing_runner)

    job = client.post("/api/runs", json={"task": "anything"}).json()
    events = read_events(client, job["id"])

    assert events[-1] == {"type": "error", "data": {"message": "RuntimeError: sandbox exploded"}}


def test_validation_and_unknown_run():
    client = make_client(fake_runner)

    assert client.post("/api/runs", json={"task": ""}).status_code == 422
    assert client.get("/api/runs/nope/events").status_code == 404

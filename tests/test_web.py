import json
import threading

import pytest
from fastapi.testclient import TestClient

from devpilot.config import ConfigError, Settings
from devpilot.pipeline import RunOutcome
from devpilot.web.app import create_app, server_hosts

TOKEN = "a-long-secret-token"


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


def make_client(runner, **settings_overrides):
    settings = Settings(anthropic_api_key="k", github_token=None, **settings_overrides)
    return TestClient(create_app(settings, runner=runner), base_url="http://localhost")


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


def test_foreign_host_header_is_rejected():
    """Blocks DNS rebinding: a page on evil.example resolving to 127.0.0.1."""
    client = make_client(fake_runner)

    assert client.get("/api/config", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/config", headers={"host": "127.0.0.1:8000"}).status_code == 200


def test_token_is_required_when_configured():
    client = make_client(fake_runner, web_token=TOKEN)

    assert client.get("/").status_code == 200  # the page itself asks for the token
    assert client.get("/api/config").status_code == 401
    assert client.post("/api/runs", json={"task": "x"}).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert client.get("/api/runs", headers=wrong).status_code == 401

    auth = {"Authorization": f"Bearer {TOKEN}"}
    job = client.post("/api/runs", json={"task": "x"}, headers=auth).json()
    assert client.get(f"/api/runs/{job['id']}/events").status_code == 401
    with client.stream("GET", f"/api/runs/{job['id']}/events", headers=auth) as stream:
        assert stream.status_code == 200


def test_non_ascii_token_is_rejected_not_a_server_error():
    client = make_client(fake_runner, web_token=TOKEN)

    response = client.get("/api/config", headers={"Authorization": "Bearer tést".encode()})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]
)
def test_cross_site_simple_requests_cannot_start_runs(content_type):
    """Browsers send these cross-origin without a CORS preflight; they must not start a run."""
    started = []
    client = make_client(lambda request, *_: started.append(request) or fake_runner(request, *_))

    response = client.post(
        "/api/runs",
        content='{"task": "attacker task"}',
        headers={"Content-Type": content_type, "Origin": "https://evil.example"},
    )

    assert response.status_code == 422
    assert started == []


def test_concurrent_runs_are_limited():
    release = threading.Event()

    def blocking_runner(request, job_id, listener):
        release.wait(5)
        return fake_runner(request, job_id, listener)

    client = make_client(blocking_runner, web_max_jobs=1)
    try:
        assert client.post("/api/runs", json={"task": "a"}).status_code == 201
        assert client.post("/api/runs", json={"task": "b"}).status_code == 429
    finally:
        release.set()


def test_runs_per_hour_are_limited():
    client = make_client(fake_runner, web_runs_per_hour=1)

    job = client.post("/api/runs", json={"task": "a"}).json()
    read_events(client, job["id"])

    assert client.post("/api/runs", json={"task": "b"}).status_code == 429


def test_repository_allowlist():
    client = make_client(fake_runner, web_allowed_repos=("octo/demo",))

    assert client.post("/api/runs", json={"task": "x", "repo": "octo/other"}).status_code == 403
    assert client.post("/api/runs", json={"task": "x", "repo": "Octo/Demo"}).status_code == 201


def test_server_refuses_remote_binding_without_a_token():
    settings = Settings(anthropic_api_key="k", github_token=None)
    assert server_hosts(settings, "127.0.0.1") == ["localhost", "127.0.0.1"]
    with pytest.raises(ConfigError, match="DEVPILOT_WEB_TOKEN"):
        server_hosts(settings, "0.0.0.0")
    with pytest.raises(ConfigError, match="at least"):
        server_hosts(Settings(anthropic_api_key="k", github_token=None, web_token="short"), "x")

    secured = Settings(anthropic_api_key="k", github_token=None, web_token=TOKEN)
    assert "devbox.lan" in server_hosts(secured, "devbox.lan")
    assert server_hosts(secured, "0.0.0.0") == ["*"]

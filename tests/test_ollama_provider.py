import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from devpilot.agent import Agent
from devpilot.config import Settings
from devpilot.planner import make_plan
from devpilot.providers import make_provider
from devpilot.providers.base import ProviderError, ToolCall, ToolOutput
from devpilot.providers.ollama import OllamaProvider, http_post, validate_base_url
from devpilot.runlog import RunLog
from devpilot.tools import TOOL_SPECS, ToolExecutor


def reply(content: str = "", tool_calls: list[dict] | None = None, **extra: Any) -> dict:
    message = {"role": "assistant", "content": content, "tool_calls": tool_calls or []}
    return {"message": message, "done_reason": "stop", "prompt_eval_count": 100,
            "eval_count": 20, **extra}  # fmt: skip


def call(name: str, arguments: Any, call_id: str | None = None) -> dict:
    raw = {"function": {"name": name, "arguments": arguments}}
    return {**raw, "id": call_id} if call_id else raw


class FakeOllama:
    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, path: str, body: dict) -> dict:
        self.requests.append((path, json.loads(json.dumps(body))))  # snapshot the history
        if not self.replies:
            raise AssertionError("FakeOllama ran out of scripted replies")
        return self.replies.pop(0)


def make(replies: list[dict], effort: str = "medium") -> tuple[OllamaProvider, FakeOllama]:
    fake = FakeOllama(replies)
    return OllamaProvider(fake, "qwen3:8b", effort, num_ctx=16_384), fake


class Recorder(BaseHTTPRequestHandler):
    """Answers every POST with the server's canned status and body, and records the path."""

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.paths.append(self.path)
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.server.body)))
        self.end_headers()
        self.wfile.write(self.server.body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def http_server():
    servers = []

    def start(status: int = 200, body: bytes = b'{"message": {"content": "ok"}}'):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        server.status, server.body, server.paths = status, body, []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_tool_calls_round_trip():
    provider, fake = make(
        [reply(tool_calls=[call("read_file", {"path": "a.py"}, "c1")]), reply("done")]
    )
    conversation = provider.start("system", TOOL_SPECS, "fix it")

    turn = conversation.send()
    conversation.add_tool_results(
        [ToolOutput(call=turn.tool_calls[0], content="no such file", is_error=True)]
    )
    final = conversation.send()

    assert turn.stop == "tool_use"
    assert turn.tool_calls == [ToolCall("c1", "read_file", {"path": "a.py"})]
    assert turn.usage.input_tokens == 100 and turn.usage.output_tokens == 20
    path, body = fake.requests[1]
    assert path == "/api/chat"
    assert body["stream"] is False
    assert body["options"]["num_ctx"] == 16_384
    assert body["think"] is True
    assert {t["function"]["name"] for t in body["tools"]} >= {"read_file", "finish"}
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "tool"]
    assert body["messages"][-1] == {
        "role": "tool", "content": "ERROR: no such file", "tool_name": "read_file"
    }  # fmt: skip
    assert final.stop == "end_turn" and final.text == "done"


def test_string_arguments_and_missing_ids_are_handled():
    provider, _ = make([reply(tool_calls=[call("list_files", '{"path": "src"}')])])

    turn = provider.start("s", TOOL_SPECS, "m").send()

    assert turn.tool_calls[0].arguments == {"path": "src"}
    assert turn.tool_calls[0].id  # generated


def test_unparseable_tool_call_is_malformed():
    provider, _ = make([reply(tool_calls=[call("read_file", "{not json")])])

    assert provider.start("s", TOOL_SPECS, "m").send().stop == "malformed"


def test_length_limit_and_thinking_text():
    provider, fake = make([reply("partial", done_reason="length", **{})], effort="low")
    fake.replies[0]["message"]["thinking"] = "let me think"

    turn = provider.start("s", TOOL_SPECS, "m").send()

    assert turn.stop == "max_tokens"
    assert turn.text == "let me think\npartial"
    assert fake.requests[0][1]["think"] is False  # effort low turns thinking off


def test_planner_uses_json_schema_format():
    provider, fake = make([reply(json.dumps({"steps": ["Read", "Fix", "Test"]}))])

    steps, _ = make_plan(provider, "task", "a.py")

    assert steps == ["Read", "Fix", "Test"]
    body = fake.requests[0][1]
    assert body["format"]["required"] == ["steps"]
    assert body["think"] is False and "tools" not in body


def test_agent_runs_end_to_end_on_ollama(tmp_path, fake_sandbox):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    edit = call("edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"})
    provider, _ = make(
        [
            reply(tool_calls=[edit]),
            reply(tool_calls=[call("run_tests", {}), call("finish", {"summary": "Fixed."})]),
        ]
    )
    settings = Settings(
        anthropic_api_key=None, github_token=None, provider="ollama", model="qwen3:8b",
        runs_dir=tmp_path / "runs",
    )  # fmt: skip
    log = RunLog(settings.runs_dir, task="t", model=settings.model)

    tools = ToolExecutor(tmp_path, fake_sandbox, untracked_dirs=[settings.runs_dir])
    result = Agent(provider, settings, tools, log).run("t", ["s"])

    assert result.status == "finished"
    assert result.tests_passed is True
    assert result.cost_usd == 0.0  # local models are free


@pytest.mark.parametrize(
    "url", ["http://localhost:11434", "http://127.0.0.1:11434/", "https://gpu.example.com"]
)
def test_allowed_urls(url):
    assert validate_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url", ["http://gpu.example.com:11434", "ftp://localhost", "file:///etc/passwd", "localhost"]
)
def test_rejected_urls(url):
    """Plain http to another machine would send the repository's code unencrypted."""
    with pytest.raises(ProviderError):
        validate_base_url(url)


def test_make_provider_needs_no_key_and_reports_unreachable_server():
    settings = Settings(
        anthropic_api_key=None, github_token=None, provider="ollama", model="qwen3:8b",
        ollama_url="http://127.0.0.1:9",  # nothing listens on the discard port
    )  # fmt: skip
    provider = make_provider(settings)

    with pytest.raises(ProviderError, match="Cannot reach Ollama"):
        provider.complete_json("s", "p", {"type": "object"})


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_local_requests_ignore_proxy_settings(http_server, monkeypatch, host):
    """A proxy from the environment must not see the code sent to a local server."""
    ollama = http_server(body=b'{"message": {"content": "direct"}}')
    proxy = http_server(body=b'{"message": {"content": "via proxy"}}')
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, f"http://127.0.0.1:{proxy.server_port}")
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    url = f"http://{host}:{ollama.server_port}"
    # Control: plain urllib really does send this local request through the proxy. A fresh
    # opener, because urlopen() caches the proxy settings it saw first for the whole process.
    control = urllib.request.Request(f"{url}/api/chat", data=b"{}")
    urllib.request.build_opener().open(control, timeout=10)
    assert len(proxy.paths) == 1

    result = http_post(url)("/api/chat", {"model": "m"})

    assert result["message"]["content"] == "direct"
    assert ollama.paths == ["/api/chat"]
    assert len(proxy.paths) == 1  # not used again


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (404, b'{"error": "model not found"}', r"HTTP 404: .*model not found.*ollama pull m"),
        (500, b'{"error": "token repeat limit reached"}', r"HTTP 500: .*token repeat limit"),
        (200, b"not json", "invalid JSON"),
        (200, b"\x80\x81", "invalid JSON"),
        (200, b"[1, 2]", "returned list, expected a JSON object"),
        (200, b'"text"', "returned str, expected a JSON object"),
    ],
)
def test_bad_http_responses_raise_provider_error(http_server, status, body, error):
    server = http_server(status=status, body=body)
    post = http_post(f"http://127.0.0.1:{server.server_port}")

    with pytest.raises(ProviderError, match=error):
        post("/api/chat", {"model": "m"})


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"message": "text"},
        {"message": {"content": ["a"]}},
        {"message": {"content": "", "thinking": 5}},
        {"message": {"content": "", "tool_calls": {"function": {}}}},
        {"message": {"content": ""}, "eval_count": "many"},
    ],
)
def test_malformed_response_shapes_raise_provider_error(response):
    provider, _ = make([response, response])

    with pytest.raises(ProviderError):
        provider.start("s", TOOL_SPECS, "m").send()
    with pytest.raises(ProviderError):
        provider.complete_json("s", "p", {"type": "object"})


def test_tool_call_entries_of_the_wrong_shape_are_malformed():
    bad_calls = ["read_file", {"function": "read_file"}, {"function": {"name": 5, "arguments": {}}}]
    provider, _ = make([reply(tool_calls=bad_calls)])

    assert provider.start("s", TOOL_SPECS, "m").send().stop == "malformed"

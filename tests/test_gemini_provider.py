import json
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from devpilot.providers import ProviderError, ToolCall, ToolOutput
from devpilot.providers.gemini import GeminiProvider
from devpilot.tools import TOOL_SPECS


def gemini_response(*parts: dict[str, Any], finish_reason: str = "STOP") -> Any:
    return types.GenerateContentResponse.model_validate(
        {
            "candidates": [
                {"content": {"role": "model", "parts": list(parts)}, "finish_reason": finish_reason}
            ],
            "usage_metadata": {
                "prompt_token_count": 1200,
                "cached_content_token_count": 200,
                "candidates_token_count": 50,
                "thoughts_token_count": 30,
            },
        }
    )


class FakeGenai:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model: str, contents: list, config: Any) -> Any:
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        return self.responses.pop(0)


def make_provider(responses: list[Any]) -> tuple[GeminiProvider, FakeGenai]:
    client = FakeGenai(responses)
    return GeminiProvider(client, "gemini-3.6-flash"), client  # type: ignore[arg-type]


def test_tool_call_turn_is_normalised():
    provider, client = make_provider(
        [
            gemini_response(
                {"text": "Looking at the files.", "thought": True},
                {"function_call": {"id": "fc-1", "name": "read_file", "args": {"path": "a.py"}}},
            )
        ]
    )

    turn = provider.start("system", TOOL_SPECS, "task").send()

    assert turn.stop == "tool_use"
    assert turn.text == "Looking at the files."
    assert turn.tool_calls == [ToolCall(id="fc-1", name="read_file", arguments={"path": "a.py"})]
    assert turn.usage.input_tokens == 1000
    assert turn.usage.cache_read_tokens == 200
    assert turn.usage.output_tokens == 80
    config = client.calls[0]["config"]
    assert config.automatic_function_calling.disable is True
    names = [d.name for d in config.tools[0].function_declarations]
    assert names == [spec["name"] for spec in TOOL_SPECS]


def test_history_keeps_model_content_and_tool_results():
    call = {"function_call": {"name": "list_files", "args": {}}}
    provider, client = make_provider([gemini_response(call), gemini_response({"text": "done"})])
    conversation = provider.start("system", TOOL_SPECS, "task")

    turn = conversation.send()
    conversation.add_tool_results([ToolOutput(call=turn.tool_calls[0], content="a.py")])
    final = conversation.send()

    contents = client.calls[1]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    response_part = contents[2].parts[0].function_response
    assert response_part.name == "list_files"
    assert response_part.response == {"output": "a.py"}
    # The API gave no id, so the locally generated one must not be sent back.
    assert response_part.id is None
    assert final.stop == "end_turn"


def test_errors_are_reported_under_error_key():
    provider, client = make_provider([gemini_response({"text": "ok"})])
    conversation = provider.start("system", TOOL_SPECS, "task")
    call = ToolCall(id="fc-9", name="read_file", arguments={})

    conversation.add_tool_results([ToolOutput(call=call, content="missing", is_error=True)])
    conversation.send()

    part = client.calls[0]["contents"][-1].parts[0].function_response
    assert part.response == {"error": "missing"}
    assert part.id == "fc-9"


@pytest.mark.parametrize(
    ("finish_reason", "stop"),
    [("SAFETY", "refusal"), ("MAX_TOKENS", "max_tokens"), ("MALFORMED_FUNCTION_CALL", "malformed")],
)
def test_finish_reasons_map_to_stops(finish_reason, stop):
    provider, _ = make_provider([gemini_response({"text": "x"}, finish_reason=finish_reason)])

    assert provider.start("system", TOOL_SPECS, "task").send().stop == stop


def test_complete_json_requests_schema_output():
    plan = json.dumps({"steps": ["a", "b"]})
    provider, client = make_provider([gemini_response({"text": plan})])

    text, usage = provider.complete_json("system", "prompt", {"type": "object"})

    assert json.loads(text) == {"steps": ["a", "b"]}
    assert usage.output_tokens == 80
    config = client.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == {"type": "object"}


def test_complete_json_raises_on_refusal():
    provider, _ = make_provider([gemini_response({"text": ""}, finish_reason="SAFETY")])

    with pytest.raises(ProviderError):
        provider.complete_json("system", "prompt", {"type": "object"})

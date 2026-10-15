import json
from itertools import count
from types import SimpleNamespace
from typing import Any

_ids = count(1)


def usage(input_tokens: int = 1000, output_tokens: int = 200) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def tool_use(name: str, **arguments: Any) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=arguments)


def response(*content: SimpleNamespace, stop_reason: str | None = None) -> SimpleNamespace:
    if stop_reason is None:
        has_tools = any(block.type == "tool_use" for block in content)
        stop_reason = "tool_use" if has_tools else "end_turn"
    return SimpleNamespace(content=list(content), stop_reason=stop_reason, usage=usage())


def plan_response(*steps: str) -> SimpleNamespace:
    return response(text(json.dumps({"steps": list(steps)})))


class FakeAnthropic:
    """Stands in for anthropic.Anthropic and replays scripted responses."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **params: Any) -> SimpleNamespace:
        # Copy the message list, since the agent keeps appending to the same object.
        self.calls.append({**params, "messages": list(params.get("messages", []))})
        if not self.responses:
            raise AssertionError("FakeAnthropic ran out of scripted responses")
        return self.responses.pop(0)

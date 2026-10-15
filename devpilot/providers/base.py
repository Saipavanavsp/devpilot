from dataclasses import dataclass, field
from typing import Any, Protocol

from devpilot.usage import Usage

ToolSpec = dict[str, Any]


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutput:
    call: ToolCall
    content: str
    is_error: bool = False


@dataclass
class ModelTurn:
    """One model response, normalised across providers.

    stop is one of: "tool_use", "end_turn", "max_tokens", "refusal", "malformed".
    """

    text: str
    tool_calls: list[ToolCall]
    stop: str
    usage: Usage = field(default_factory=Usage)


class Conversation(Protocol):
    """A tool-use conversation whose history is kept in the provider's native format."""

    def send(self) -> ModelTurn: ...

    def add_tool_results(self, outputs: list[ToolOutput]) -> None: ...

    def add_user_message(self, text: str) -> None: ...


class Provider(Protocol):
    def start(self, system: str, tools: list[ToolSpec], message: str) -> Conversation: ...

    def complete_json(
        self, system: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[str, Usage]: ...

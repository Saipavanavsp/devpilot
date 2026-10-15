from typing import Any

import anthropic

from devpilot.providers.base import ModelTurn, ProviderError, ToolCall, ToolOutput, ToolSpec
from devpilot.usage import Usage

FALLBACK_BETA = "server-side-fallback-2026-07-01"
PROGRESS_UPDATES_BETA = "thinking-display-updates-2026-08-18"

# Models that support server-side refusal fallbacks and progress-update thinking blocks.
MODELS_WITH_FALLBACKS = {
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-fable-5-1",
}
MODELS_WITH_PROGRESS_UPDATES = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"}

STOP_REASONS = {"tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}


def usage_from_response(usage: Any) -> Usage:
    return Usage(
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
    )


def response_text(content: list[Any]) -> str:
    """Join the visible text and any progress-update notes in a response."""
    parts: list[str] = []
    for block in content:
        if block.type == "text" and block.text.strip():
            parts.append(block.text.strip())
        elif block.type == "thinking" and getattr(block, "thinking", "").strip():
            parts.append(block.thinking.strip())
    return "\n".join(parts)


class ClaudeProvider:
    def __init__(self, client: anthropic.Anthropic, model: str, effort: str = "medium") -> None:
        self.client = client
        self.model = model
        self.effort = effort

    @classmethod
    def from_key(cls, api_key: str, model: str, effort: str) -> "ClaudeProvider":
        return cls(anthropic.Anthropic(api_key=api_key, max_retries=4), model, effort)

    def create(self, *, progress_updates: bool = False, **params: Any) -> Any:
        """Send a request with the model-specific options DevPilot relies on.

        Refusal fallbacks keep a false-positive safety refusal from ending a run, and progress
        updates surface the model's short notes between tool calls for the log and web UI.
        """
        betas: list[str] = []
        if self.model in MODELS_WITH_FALLBACKS:
            betas.append(FALLBACK_BETA)
            params["fallbacks"] = "default"
        if progress_updates and self.model in MODELS_WITH_PROGRESS_UPDATES:
            betas.append(PROGRESS_UPDATES_BETA)
            params["thinking"] = {"type": "adaptive", "display": "updates"}
        return self.client.beta.messages.create(model=self.model, betas=betas, **params)

    def start(self, system: str, tools: list[ToolSpec], message: str) -> "ClaudeConversation":
        return ClaudeConversation(self, system, tools, message)

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> tuple[str, Usage]:
        response = self.create(
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason == "refusal":
            raise ProviderError("The model declined the request.")
        text = next((block.text for block in response.content if block.type == "text"), "")
        return text, usage_from_response(response.usage)


class ClaudeConversation:
    def __init__(
        self, provider: ClaudeProvider, system: str, tools: list[ToolSpec], message: str
    ) -> None:
        self.provider = provider
        self.system = system
        self.tools = tools
        # History is append-only: newer Claude models reject edited earlier turns.
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": message}]

    def send(self) -> ModelTurn:
        response = self.provider.create(
            progress_updates=True,
            max_tokens=16_000,
            system=self.system,
            tools=self.tools,
            messages=self.messages,
            cache_control={"type": "ephemeral"},
            output_config={"effort": self.provider.effort},
        )
        self.messages.append({"role": "assistant", "content": response.content})
        calls = [
            ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
            for block in response.content
            if block.type == "tool_use"
        ]
        return ModelTurn(
            text=response_text(response.content),
            tool_calls=calls,
            stop=STOP_REASONS.get(response.stop_reason, "end_turn"),
            usage=usage_from_response(response.usage),
        )

    def add_tool_results(self, outputs: list[ToolOutput]) -> None:
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": output.call.id,
                        "content": output.content,
                        "is_error": output.is_error,
                    }
                    for output in outputs
                ],
            }
        )

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

import uuid
from typing import Any

from google import genai
from google.genai import types

from devpilot.providers.base import ModelTurn, ProviderError, ToolCall, ToolOutput, ToolSpec
from devpilot.usage import Usage

REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
MALFORMED_REASONS = {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"}
THINKING_LEVELS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}

REQUEST_TIMEOUT_MS = 180_000

# The free tier allows only a few requests per minute, so back off generously on 429s.
RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=5, initial_delay=10.0, max_delay=60.0, http_status_codes=[429, 500, 503]
)


def usage_from_response(metadata: Any) -> Usage:
    if metadata is None:
        return Usage()
    cached = metadata.cached_content_token_count or 0
    return Usage(
        input_tokens=(metadata.prompt_token_count or 0) - cached,
        # Gemini bills thinking tokens as output.
        output_tokens=(metadata.candidates_token_count or 0) + (metadata.thoughts_token_count or 0),
        cache_read_tokens=cached,
    )


def _stop_reason(response: types.GenerateContentResponse, has_calls: bool) -> str:
    if not response.candidates:
        return "refusal"
    reason = response.candidates[0].finish_reason
    name = reason.name if reason is not None else "STOP"
    if name in REFUSAL_REASONS:
        return "refusal"
    if name in MALFORMED_REASONS:
        return "malformed"
    if name == "MAX_TOKENS":
        return "max_tokens"
    return "tool_use" if has_calls else "end_turn"


class GeminiProvider:
    def __init__(self, client: genai.Client, model: str, effort: str = "medium") -> None:
        self.client = client
        self.model = model
        self.effort = effort

    @classmethod
    def from_key(cls, api_key: str, model: str, effort: str) -> "GeminiProvider":
        # Without a timeout a stalled connection blocks the run forever; the SDK value is in ms.
        options = types.HttpOptions(timeout=REQUEST_TIMEOUT_MS, retry_options=RETRY_OPTIONS)
        return cls(genai.Client(api_key=api_key, http_options=options), model, effort)

    def generate(
        self, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        return self.client.models.generate_content(
            model=self.model, contents=contents, config=config
        )

    def start(self, system: str, tools: list[ToolSpec], message: str) -> "GeminiConversation":
        return GeminiConversation(self, system, tools, message)

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> tuple[str, Usage]:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        )
        response = self.generate(
            [types.Content(role="user", parts=[types.Part(text=prompt)])], config
        )
        if _stop_reason(response, has_calls=False) == "refusal":
            raise ProviderError("The model declined the request.")
        return response.text or "", usage_from_response(response.usage_metadata)


class GeminiConversation:
    def __init__(
        self, provider: GeminiProvider, system: str, tools: list[ToolSpec], message: str
    ) -> None:
        self.provider = provider
        declarations = [
            types.FunctionDeclaration(
                name=spec["name"],
                description=spec["description"],
                parameters_json_schema=spec["input_schema"],
            )
            for spec in tools
        ]
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(
                include_thoughts=True, thinking_level=THINKING_LEVELS[provider.effort]
            ),
            max_output_tokens=16_000,
        )
        self.contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=message)])
        ]
        # The Gemini API may omit call ids; local ones are generated but never sent back.
        self._generated_ids: set[str] = set()

    def send(self) -> ModelTurn:
        response = self.provider.generate(self.contents, self.config)
        content = response.candidates[0].content if response.candidates else None
        parts = (content.parts if content else None) or []
        if content is not None and parts:
            # Appending the content unchanged keeps the thought signatures Gemini requires.
            self.contents.append(content)

        calls: list[ToolCall] = []
        notes: list[str] = []
        for part in parts:
            if part.function_call is not None:
                call = part.function_call
                call_id = call.id
                if not call_id:
                    call_id = uuid.uuid4().hex
                    self._generated_ids.add(call_id)
                calls.append(
                    ToolCall(id=call_id, name=call.name or "", arguments=dict(call.args or {}))
                )
            elif part.text and part.text.strip():
                notes.append(part.text.strip())
        return ModelTurn(
            text="\n".join(notes),
            tool_calls=calls,
            stop=_stop_reason(response, has_calls=bool(calls)),
            usage=usage_from_response(response.usage_metadata),
        )

    def add_tool_results(self, outputs: list[ToolOutput]) -> None:
        parts = []
        for output in outputs:
            key = "error" if output.is_error else "output"
            part = types.Part.from_function_response(
                name=output.call.name, response={key: output.content}
            )
            if part.function_response is not None and output.call.id not in self._generated_ids:
                part.function_response.id = output.call.id
            parts.append(part)
        self.contents.append(types.Content(role="user", parts=parts))

    def add_user_message(self, text: str) -> None:
        self.contents.append(types.Content(role="user", parts=[types.Part(text=text)]))

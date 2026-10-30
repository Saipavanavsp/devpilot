"""Local models through Ollama's chat API: free, offline, and the code never leaves the machine.

Uses the standard library only. https://github.com/ollama/ollama/blob/main/docs/api.md
"""

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from devpilot.providers.base import ModelTurn, ProviderError, ToolCall, ToolOutput, ToolSpec
from devpilot.usage import Usage

# Local generation is slow on laptops, especially the first request while the model loads.
REQUEST_TIMEOUT_SECONDS = 900
MAX_OUTPUT_TOKENS = 8_192
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

Post = Callable[[str, dict[str, Any]], dict[str, Any]]


def validate_base_url(url: str) -> str:
    """Allow plain http only to this machine; the repository's code is sent to this server."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ProviderError(f"OLLAMA_URL must be an http(s) URL, got {url!r}")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        raise ProviderError(
            f"OLLAMA_URL {url!r} would send your code unencrypted over the network; "
            "use https for a remote Ollama server."
        )
    return url.rstrip("/")


def http_post(base_url: str) -> Post:
    if urlsplit(base_url).hostname in LOCAL_HOSTS:
        # urllib otherwise honours HTTP_PROXY and the Windows proxy settings, which would send
        # the code for a local server through a proxy, in plain text if the URL is http.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    else:
        opener = urllib.request.build_opener()

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{base_url}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                parsed = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            hint = f" Run `ollama pull {body.get('model')}`." if exc.code == 404 else ""
            raise ProviderError(f"Ollama returned HTTP {exc.code}: {detail}{hint}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(
                f"Cannot reach Ollama at {base_url} ({exc}). Is it running (`ollama serve`)?"
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderError(f"Ollama returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ProviderError(f"Ollama returned {type(parsed).__name__}, expected a JSON object")
        return parsed

    return post


def usage_from_response(response: dict[str, Any]) -> Usage:
    counts = (response.get("prompt_eval_count") or 0, response.get("eval_count") or 0)
    if not all(isinstance(count, int) for count in counts):
        raise ProviderError(f"Ollama returned invalid token counts: {counts!r}")
    return Usage(input_tokens=counts[0], output_tokens=counts[1])


def _message(response: dict[str, Any]) -> dict[str, Any]:
    """The reply message, with its shape checked so a bad response can't crash the agent."""
    message = response.get("message")
    if not isinstance(message, dict):
        raise ProviderError("Ollama response has no message object")
    for key in ("content", "thinking"):
        if not isinstance(message.get(key) or "", str):
            raise ProviderError(f"Ollama message field {key!r} is not text")
    if not isinstance(message.get("tool_calls") or [], list):
        raise ProviderError("Ollama message field 'tool_calls' is not a list")
    return message


class OllamaProvider:
    def __init__(self, post: Post, model: str, effort: str = "medium", num_ctx: int = 32_768):
        self.post = post
        self.model = model
        # Thinking makes small models noticeably better at multi-step work, but slower.
        self.think = effort != "low"
        self.num_ctx = num_ctx

    @classmethod
    def from_url(cls, base_url: str, model: str, effort: str, num_ctx: int) -> "OllamaProvider":
        return cls(http_post(validate_base_url(base_url)), model, effort, num_ctx)

    def chat(self, messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return self.post(
            "/api/chat",
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                # Ollama's default context is small and it silently drops the oldest tokens.
                "options": {"num_ctx": self.num_ctx, "num_predict": MAX_OUTPUT_TOKENS},
                **extra,
            },
        )

    def start(self, system: str, tools: list[ToolSpec], message: str) -> "OllamaConversation":
        return OllamaConversation(self, system, tools, message)

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> tuple[str, Usage]:
        response = self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            format=schema,
            think=False,
        )
        return _message(response).get("content") or "", usage_from_response(response)


def _arguments(raw: Any) -> dict[str, Any] | None:
    """Tool arguments arrive as an object, or from some models as a JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


class OllamaConversation:
    def __init__(
        self, provider: OllamaProvider, system: str, tools: list[ToolSpec], message: str
    ) -> None:
        self.provider = provider
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["input_schema"],
                },
            }
            for spec in tools
        ]
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ]

    def send(self) -> ModelTurn:
        response = self.provider.chat(self.messages, tools=self.tools, think=self.provider.think)
        message = _message(response)
        self.messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": message.get("tool_calls") or [],
            }
        )

        calls: list[ToolCall] = []
        malformed = False
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict):
                malformed = True
                continue
            name = function.get("name")
            arguments = _arguments(function.get("arguments"))
            if not name or not isinstance(name, str) or arguments is None:
                malformed = True
                continue
            call_id = str(raw.get("id") or uuid.uuid4().hex)
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))

        if malformed and not calls:
            stop = "malformed"
        elif response.get("done_reason") == "length":
            stop = "max_tokens"
        else:
            stop = "tool_use" if calls else "end_turn"
        notes = [(message.get(key) or "").strip() for key in ("thinking", "content")]
        return ModelTurn(
            text="\n".join(note for note in notes if note),
            tool_calls=calls,
            stop=stop,
            usage=usage_from_response(response),
        )

    def add_tool_results(self, outputs: list[ToolOutput]) -> None:
        for output in outputs:
            content = f"ERROR: {output.content}" if output.is_error else output.content
            self.messages.append(
                {"role": "tool", "content": content, "tool_name": output.call.name}
            )

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

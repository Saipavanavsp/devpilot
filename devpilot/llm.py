from typing import Any

import anthropic

from devpilot.config import Settings

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


def make_client(settings: Settings) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.require_anthropic_key(), max_retries=4)


def create_message(
    client: anthropic.Anthropic,
    settings: Settings,
    *,
    progress_updates: bool = False,
    **params: Any,
) -> Any:
    """Send a Messages API request with the model-specific options DevPilot relies on.

    Refusal fallbacks keep a false-positive safety refusal from ending a run, and progress
    updates surface the model's short notes between tool calls for the log and web UI.
    """
    betas: list[str] = []
    if settings.model in MODELS_WITH_FALLBACKS:
        betas.append(FALLBACK_BETA)
        params["fallbacks"] = "default"
    if progress_updates and settings.model in MODELS_WITH_PROGRESS_UPDATES:
        betas.append(PROGRESS_UPDATES_BETA)
        params["thinking"] = {"type": "adaptive", "display": "updates"}
    return client.beta.messages.create(model=settings.model, betas=betas, **params)


def response_text(content: list[Any]) -> str:
    """Join the visible text and any progress-update notes in a response."""
    parts: list[str] = []
    for block in content:
        if block.type == "text" and block.text.strip():
            parts.append(block.text.strip())
        elif block.type == "thinking" and getattr(block, "thinking", "").strip():
            parts.append(block.thinking.strip())
    return "\n".join(parts)

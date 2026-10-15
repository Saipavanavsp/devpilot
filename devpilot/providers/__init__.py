from devpilot.config import Settings
from devpilot.providers.base import (
    Conversation,
    ModelTurn,
    Provider,
    ProviderError,
    ToolCall,
    ToolOutput,
)

__all__ = [
    "Conversation",
    "ModelTurn",
    "Provider",
    "ProviderError",
    "ToolCall",
    "ToolOutput",
    "make_provider",
]


def make_provider(settings: Settings) -> Provider:
    api_key = settings.require_api_key()
    # Imported lazily so only the SDK for the configured provider has to load.
    if settings.provider == "gemini":
        from devpilot.providers.gemini import GeminiProvider

        return GeminiProvider.from_key(api_key, settings.model, settings.effort)
    from devpilot.providers.claude import ClaudeProvider

    return ClaudeProvider.from_key(api_key, settings.model, settings.effort)

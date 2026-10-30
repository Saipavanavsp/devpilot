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
    # Imported lazily so only the SDK for the configured provider has to load.
    if settings.provider == "ollama":
        from devpilot.providers.ollama import OllamaProvider

        return OllamaProvider.from_url(
            settings.ollama_url, settings.model, settings.effort, settings.ollama_num_ctx
        )
    api_key = settings.require_api_key()
    if settings.provider == "gemini":
        from devpilot.providers.gemini import GeminiProvider

        return GeminiProvider.from_key(api_key, settings.model, settings.effort)
    from devpilot.providers.claude import ClaudeProvider

    return ClaudeProvider.from_key(api_key, settings.model, settings.effort)

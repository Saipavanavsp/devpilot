import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROVIDERS = {"anthropic", "gemini"}
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "gemini": "gemini-3.6-flash"}
API_KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GOOGLE_API_KEY"}


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


MODEL_PRICES: dict[str, ModelPrice] = {
    "claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    "claude-opus-5-5": ModelPrice(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
    "claude-haiku-5-5": ModelPrice(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-fable-5-1": ModelPrice(input=10.00, output=50.00, cache_read=0.25, cache_write=12.50),
    "gemini-3.6-flash": ModelPrice(input=1.50, output=7.50, cache_read=0.15, cache_write=0.0),
}

# Unknown models are priced at the most expensive tier so the cost limit errs on the safe side.
FALLBACK_PRICE = max(MODEL_PRICES.values(), key=lambda price: price.output)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    github_token: str | None
    google_api_key: str | None = None
    provider: str = "anthropic"
    model: str = DEFAULT_MODELS["anthropic"]
    effort: str = "medium"
    max_steps: int = 40
    max_cost_usd: float = 2.0
    max_tokens: int = 1_500_000
    command_timeout: int = 120
    sandbox_image: str = "python:3.12-slim"
    sandbox_network: bool = False
    runs_dir: Path = Path("runs")
    workspace_dir: Path = Path("workspace")

    def require_api_key(self) -> str:
        key = self.google_api_key if self.provider == "gemini" else self.anthropic_api_key
        if not key:
            raise ConfigError(
                f"{API_KEY_VARS[self.provider]} is not set. Add it to your .env file."
            )
        return key

    def require_github_token(self) -> str:
        if not self.github_token:
            raise ConfigError("GITHUB_TOKEN is not set. Add it to your .env file.")
        return self.github_token

    @property
    def price(self) -> ModelPrice:
        return MODEL_PRICES.get(self.model, FALLBACK_PRICE)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _choice(name: str, default: str, allowed: set[str]) -> str:
    value = (os.environ.get(name) or default).strip().lower()
    if value not in allowed:
        raise ConfigError(f"{name} must be one of {', '.join(sorted(allowed))}, got {value!r}")
    return value


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Build settings from the environment, after loading values from env_file if it exists.

    Variables already set in the process environment win over the file.
    """
    if env_file is not None and Path(env_file).is_file():
        load_dotenv(env_file, override=False)

    provider = _choice("DEVPILOT_PROVIDER", "anthropic", PROVIDERS)
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        google_api_key=os.environ.get("GOOGLE_API_KEY") or None,
        provider=provider,
        model=os.environ.get("DEVPILOT_MODEL") or DEFAULT_MODELS[provider],
        effort=_choice("DEVPILOT_EFFORT", "medium", {"low", "medium", "high", "xhigh", "max"}),
        max_steps=_int("DEVPILOT_MAX_STEPS", 40),
        max_cost_usd=_float("DEVPILOT_MAX_COST_USD", 2.0),
        max_tokens=_int("DEVPILOT_MAX_TOKENS", 1_500_000),
        command_timeout=_int("DEVPILOT_COMMAND_TIMEOUT", 120),
        sandbox_image=os.environ.get("DEVPILOT_SANDBOX_IMAGE") or "python:3.12-slim",
        sandbox_network=_bool("DEVPILOT_SANDBOX_NETWORK", False),
        runs_dir=Path(os.environ.get("DEVPILOT_RUNS_DIR") or "runs"),
        workspace_dir=Path(os.environ.get("DEVPILOT_WORKSPACE_DIR") or "workspace"),
    )

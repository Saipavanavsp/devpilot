import pytest

from devpilot.config import (
    DEFAULT_MODEL,
    FALLBACK_PRICE,
    MODEL_PRICES,
    ConfigError,
    load_settings,
)

ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "DEVPILOT_MODEL",
    "DEVPILOT_EFFORT",
    "DEVPILOT_MAX_STEPS",
    "DEVPILOT_MAX_COST_USD",
    "DEVPILOT_SANDBOX_NETWORK",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_env_file(tmp_path):
    settings = load_settings(tmp_path / "missing.env")

    assert settings.model == DEFAULT_MODEL
    assert settings.anthropic_api_key is None
    assert settings.max_steps == 40
    assert settings.sandbox_network is False


def test_reads_values_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_API_KEY=test-key\n"
        "DEVPILOT_MODEL=claude-opus-5-5\n"
        "DEVPILOT_MAX_STEPS=12\n"
        "DEVPILOT_MAX_COST_USD=0.5\n"
        "DEVPILOT_SANDBOX_NETWORK=true\n"
    )

    settings = load_settings(env_file)

    assert settings.anthropic_api_key == "test-key"
    assert settings.model == "claude-opus-5-5"
    assert settings.max_steps == 12
    assert settings.max_cost_usd == 0.5
    assert settings.sandbox_network is True


def test_process_environment_overrides_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DEVPILOT_MODEL=claude-opus-5-5\n")
    monkeypatch.setenv("DEVPILOT_MODEL", "claude-haiku-5-5")

    assert load_settings(env_file).model == "claude-haiku-5-5"


def test_invalid_integer_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVPILOT_MAX_STEPS", "lots")

    with pytest.raises(ConfigError, match="DEVPILOT_MAX_STEPS"):
        load_settings(None)


def test_invalid_effort_raises(monkeypatch):
    monkeypatch.setenv("DEVPILOT_EFFORT", "extreme")

    with pytest.raises(ConfigError, match="DEVPILOT_EFFORT"):
        load_settings(None)


def test_require_keys_raise_when_missing():
    settings = load_settings(None)

    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        settings.require_anthropic_key()
    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        settings.require_github_token()


def test_unknown_model_uses_most_expensive_price(monkeypatch):
    monkeypatch.setenv("DEVPILOT_MODEL", "some-future-model")

    assert load_settings(None).price == FALLBACK_PRICE
    assert all(FALLBACK_PRICE.output >= price.output for price in MODEL_PRICES.values())

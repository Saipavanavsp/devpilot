# Contributing

## Development setup

```bash
git clone https://github.com/Saipavanavsp/devpilot.git
cd devpilot
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pip install pre-commit && pre-commit install
cp .env.example .env               # then add your own keys
```

## Checks

Run these before opening a pull request; CI runs the same ones.

```bash
ruff check .
ruff format --check .
pytest
```

Unit tests mock the model APIs and GitHub, so they need no keys. `test_real_container_isolation` needs Docker and is skipped without it.

## Guidelines

- Keep functions short and typed, and add tests for new behaviour.
- New tools go in `devpilot/tools.py` with a JSON schema in `TOOL_SPECS` and a handler; anything that executes code must go through the sandbox.
- New model providers implement the `Provider` and `Conversation` protocols in `devpilot/providers/base.py`.
- Write commit messages in the imperative mood ("Add retry to Gemini provider").
- Never commit `.env`, keys, tokens or run logs.

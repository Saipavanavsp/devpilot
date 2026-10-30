# Security

## Secrets

DevPilot never stores keys in the repository.

- API keys (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) and `GITHUB_TOKEN` are read at runtime from a local `.env` file or the environment. `.env` is in `.gitignore`; only `.env.example` with placeholder values is committed.
- The GitHub token is handed to git as an HTTP header through `GIT_CONFIG_*` environment variables. It is never written to a clone's `.git/config` and never appears in process arguments, logs or pull request text.
- Run logs (`runs/`) and agent workspaces (`workspace/`) are local only and gitignored.
- Every push and pull request is scanned with [gitleaks](https://github.com/gitleaks/gitleaks) in CI, and the optional pre-commit hooks run gitleaks and a private-key check before each commit.
- GitHub secret scanning and push protection are enabled on the repository.
- CI runs [pip-audit](https://github.com/pypa/pip-audit) on every push and fails if an installed dependency has a known vulnerability.

## Local models

With `DEVPILOT_PROVIDER=ollama`, prompts, including the repository's code, go only to the Ollama server at `OLLAMA_URL`. DevPilot refuses plain http to any host other than localhost, and connects to a local server directly, ignoring `HTTP_PROXY` and the system proxy settings, so a proxy never sees that traffic. Ollama listens on 127.0.0.1 by default; don't set `OLLAMA_HOST=0.0.0.0` unless you mean to share the model with your network, because the Ollama API has no authentication.

If you fork the project, keep your keys in `.env` or your CI provider's secret store, and rotate any key that is ever committed by mistake, even if the commit is removed afterwards.

## Agent sandbox

Code written by the model runs only inside a Docker container with no network, a read-only root filesystem, dropped capabilities, resource limits and a per-command timeout. Repository dependencies are installed beforehand by a separate setup container, which is the only step with network access. Note that this runs the target repository's own install-time code (for example package build scripts); disable it with `DEVPILOT_SANDBOX_INSTALL_DEPS=false` for repositories you don't trust. See the safety design section of the [README](README.md#safety-design).

Git is the one tool that runs on the host against files the agent can influence. The container sees `.git` read-only, and DevPilot runs host git with hooks and `core.fsmonitor` disabled. Before this was fixed (30 October 2026), a command in the sandbox could write `.git/hooks/pre-commit`, which the host's `git commit` would then run outside the sandbox.

Known residual risks are listed under "Known limitations" in the [README](README.md#known-limitations).

## Web UI

`devpilot web` starts runs that spend model credits and execute code, so it is local-only by default: it binds to 127.0.0.1 and rejects requests with any Host header other than `localhost`/`127.0.0.1`, which protects against DNS rebinding. It refuses to bind to any other address unless `DEVPILOT_WEB_TOKEN` is set, and then every API call needs that token. If you expose it, also set `DEVPILOT_WEB_ALLOWED_REPOS`, and put it behind HTTPS (for example a reverse proxy), since the token is sent as a bearer header.

## Reporting a vulnerability

Please open a private security advisory on GitHub (Security > Report a vulnerability) rather than a public issue.

# Security

## Secrets

DevPilot never stores keys in the repository.

- API keys (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) and `GITHUB_TOKEN` are read at runtime from a local `.env` file or the environment. `.env` is in `.gitignore`; only `.env.example` with placeholder values is committed.
- The GitHub token is handed to git as an HTTP header through `GIT_CONFIG_*` environment variables. It is never written to a clone's `.git/config` and never appears in process arguments, logs or pull request text.
- Run logs (`runs/`) and agent workspaces (`workspace/`) are local only and gitignored.
- Every push and pull request is scanned with [gitleaks](https://github.com/gitleaks/gitleaks) in CI, and the optional pre-commit hooks run gitleaks and a private-key check before each commit.
- GitHub secret scanning and push protection are enabled on the repository.

If you fork the project, keep your keys in `.env` or your CI provider's secret store, and rotate any key that is ever committed by mistake, even if the commit is removed afterwards.

## Agent sandbox

Code written by the model runs only inside a Docker container with no network, a read-only root filesystem, dropped capabilities, resource limits and a per-command timeout. See the safety design section of the [README](README.md#safety-design).

## Reporting a vulnerability

Please open a private security advisory on GitHub (Security > Report a vulnerability) rather than a public issue.

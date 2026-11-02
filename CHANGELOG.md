# Changelog

## 0.1.0

- Skeleton: `kit.py apply`, layer mechanism (files, check.d, ci-steps), `.starter-kit.json` record.
- Base layer with `scripts/check.sh` and the `.githooks/pre-push` hook.
- Base layer: `check.yml` workflow (docs skip, concurrency, time limits, cache summary, gitleaks job), local gitleaks check, `renovate.json`, `.gitleaks.toml`, Claude guards (`block-risky-git`, `block-secrets`, `main-is-read-only`).
- Python layer: pinned ruff lint and format, pyright, and pytest through uv; cached setup-uv CI step. Works on an existing pyproject.toml without rewriting it.

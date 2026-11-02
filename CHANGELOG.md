# Changelog

## 0.1.0

- Skeleton: `kit.py apply`, layer mechanism (files, check.d, ci-steps), `.starter-kit.json` record.
- Base layer with `scripts/check.sh` and the `.githooks/pre-push` hook.
- Base layer: `check.yml` workflow (docs skip, concurrency, time limits, cache summary, gitleaks job), local gitleaks check, `renovate.json`, `.gitleaks.toml`, the `block-secrets` Claude guard (the owner's global hooks already cover merges, force pushes and resets).
- Python layer: pinned ruff lint and format, pyright, and pytest through uv; cached setup-uv CI step. Works on an existing pyproject.toml without rewriting it.
- Shell layer: shellcheck on every script found by extension or shebang (a missing shellcheck fails with an install hint), pinned shellcheck install in CI.
- Review fixes: the gitleaks job has its own workflow with no path filter, so docs-only pull requests are scanned.
- Review fixes: the local gitleaks scan covers only files git would save (tracked plus untracked and not ignored), so a key in a git-ignored `.env` does not fail the push.
- Review fixes: `apply` refuses to overwrite files that exist with different content (`--force` overrides) and leaves a `core.hooksPath` other than `.githooks` alone, with a note.
- Review fixes: `renovate.json` extends `config:recommended` and no longer automerges.
- Review fixes: a Claude SessionStart hook sets `core.hooksPath .githooks` on a fresh clone when it is unset.
- Review fixes: ruff uses `E,F,W,I,B,UP` when the project has no ruff config; setup-uv saves its cache only on push events; `cancel-in-progress` applies to pull requests only.
- Review fixes: `timings` reads 100 jobs per page and counts dependency installs (`pnpm install`, `npm ci`, `uv sync`) as setup.
- Optional `gitleaks-ci` layer installs a pinned gitleaks in the CI check job.

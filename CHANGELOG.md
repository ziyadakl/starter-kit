# Changelog

## 0.3.0

- New `typescript` layer for single-package projects (a Next.js app, a Cloudflare Worker, plain TypeScript): `scripts/check.d/20-typescript.sh` runs typecheck (`tsc`, TypeScript 7), lint (oxlint with type-aware rules), format check (oxfmt) and tests (the project's `test` script, else vitest when it is configured). The package manager comes from the lockfile (pnpm, npm, yarn or bun). A tool the project has installed is used as it is; otherwise the kit's pinned version runs through the manager's one-off runner (`npx -y`, `pnpm dlx`, `yarn dlx`, `bunx`), so `package.json` needs no edit and a kit update changes the versions in one place. The layer ships `.oxlintrc.json`, `.oxfmtrc.json` and `.nvmrc` (22), `scripts/ts-pm.sh` and the CI setup steps; `apply` refuses to overwrite one of these files that already exists with other content, as for every kit file. The config guard protects both oxc configs. A project's eslint and prettier setup is left alone and not run.
- TypeScript canaries: the typecheck and the linter each run on a planted mistake first and the check fails if they do not report it, and a typecheck, lint or format run that looked at zero files fails.
- `scripts/check.sh` now runs every `check.d` script even after one fails and ends with one `CHECK FAILED: <script>` line per failure (still `CHECK PASSED` on success). Order is the script names, so give cheap checks low numbers (`10-` seconds, `20-` language tools, `30-` and up slow suites). The TypeScript check does the same across its own steps.
- The `check.yml` workflow skips draft pull requests (pushes to main are unaffected) and runs when a draft is marked ready (`ready_for_review`). The key scan in `gitleaks.yml` still runs on drafts.
- TypeScript CI setup: Node from `.nvmrc`, no package-manager cache (a restore took longer than a cold install), dependencies installed from the lockfile without changing it.
- The pre-push hook was checked from a linked git worktree and runs the worktree's own check; a test now pins it.

## 0.2.0

- The `.env` guard's folder walk skips build output (`target`, `.next`, `dist`, `build`, …) and stops descending past 20 levels instead of blocking, so content searches in deep Java, Rust or Next.js trees with no `.env` are allowed.

- Session status: `scripts/session-status.sh` runs at session start and prints the branch, uncommitted files, open pull requests, open issue count and free disk. The GitHub lines are left out when gh is missing, signed out or slow; the script always exits 0.
- `.env` guard (`env-guard.py`, on Read, Grep and Bash): refuses to print the values of `.env` and `.env.<name>` files; `.env.example` and listing the names only stay allowed.
- Merge guard (`merge-guard.py`, on Bash): refuses a pull request merge (the gh merge command or the merge API call) unless every check on the PR's head commit passed. Where `<git common dir>/review-stamps/` exists, the head commit must also be stamped there; repos without that folder are never asked for a stamp. `scripts/check.sh` stays the local CI; there is no separate script.
- Config guard (`config-guard.sh`, on Edit, Write, MultiEdit and NotebookEdit): refuses edits to lint, format and scan configuration with "Fix the code, don't weaken the config." Each layer ships its own list in `.claude/protect.d/<layer>.txt` (base: `.gitleaks.toml`; python: `ruff.toml`, `.ruff.toml`, `pyrightconfig.json`; shell: `.shellcheckrc`). The owner overrides by starting Claude with `ALLOW_CONFIG_EDIT=1`, or by editing by hand. `pyproject.toml` is not covered.
- A project whose `.claude/settings.json` has its own edits keeps them; `kit.py update` reports the file as left alone, and the new hook entries have to be copied in by hand.
- Review fixes: the `.env` guard now looks for a real `.env` anywhere under the searched folder (bounded walk that skips `.git`, `node_modules`, `.venv` and similar; a search too big to check is refused) and no longer blocks searches in projects that have no `.env`; its refusal says how to narrow the search. `apply` skips `__pycache__` and `.pyc`, and the Python hooks no longer write bytecode. The merge guard gives each gh call 20 seconds and refuses the merge on a timeout.

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

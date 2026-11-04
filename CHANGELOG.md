# Changelog

## 0.3.0

- New `typescript` layer for single-package projects (a Next.js app, a Cloudflare Worker, plain TypeScript), one `check.d` script per step: `20-ts-typecheck.sh`, `21-ts-lint.sh`, `22-ts-format.sh` and `30-ts-tests.sh` (the project's `test` script, else vitest when it is configured), sharing `scripts/ts-common.sh`. The package manager comes from the lockfile (pnpm, npm, yarn, or bun with `bun.lock` or `bun.lockb`).
- The typecheck always runs TypeScript 7: the project's `node_modules/.bin/tsc` (or a `node_modules/typescript7` alias) only when it is version 7 or newer, otherwise the pinned TypeScript 7.0.2 through the manager's one-off runner (`npx -y`, `pnpm dlx`, `yarn dlx`, `bunx`). The first line of the step names the compiler that ran. A `tsconfig.json` with `references` is checked with `tsc -b --noEmit`; a `tsconfig.json` that does not parse is reported as tsc's own error.
- Lint (oxlint, type-aware) and format (oxfmt) use the project's own binary only when every package the kit pins for that tool is installed (oxlint needs oxlint-tsgolint beside it); otherwise the pinned version runs one-off. `package.json` needs no edit. The versions live in `scripts/ts-common.sh` under `# renovate:` comments, and `renovate.json` (base layer) has a regex manager that bumps them.
- The layer ships `.oxlintrc.json` (a copy of a finished monorepo config: categories off, about a hundred named rules including the type-aware ones, React hooks rules on `.tsx`/`.jsx` only), `.oxfmtrc.json` (the same build-folder ignores; lint alone also skips config files and non-`.ts`/`.tsx` sources, as the monorepo does), `scripts/ts-common.sh`, `scripts/ts-pm.sh` and the CI setup steps; `apply` refuses to overwrite one of these files that already exists with other content, as for every kit file. The config guard protects both oxc configs. A project's eslint and prettier setup is left alone and not run.
- TypeScript canaries: the typecheck runs on a planted type error and the linter on a planted floating promise and `debugger` statement, and the check fails unless each is reported (the lint ones as errors, not warnings). A lint or format run that looked at zero files, or whose file count cannot be read, fails.
- `scripts/check.sh` now runs every `check.d` script even after one fails and ends with one `CHECK FAILED: <script>` line per failure (still `CHECK PASSED` on success). Order is the script names, so give cheap checks low numbers (`10-` seconds, `20-` language tools, `30-` and up slow suites). The TypeScript check does the same across its own steps.
- The `check.yml` workflow skips draft pull requests (pushes to main are unaffected) and runs when a draft is marked ready (`ready_for_review`). The key scan in `gitleaks.yml` still runs on drafts. The README now says to open pull requests as drafts while working and mark them ready once.
- TypeScript CI setup: Node from the project's `.nvmrc`, else its `.node-version`, else Node 22 (the kit ships no `.nvmrc`); no package-manager cache (`package-manager-cache: false`; the one measurement, a monorepo with a ~395 MB pnpm store, had a restore slower than a cold install); `actions/setup-node` and `oven-sh/setup-bun` pinned to commit SHAs; dependencies installed from the lockfile without changing it.
- Existing TypeScript projects can adopt the lint rules without weeks of cleanup. The rules moved to a kit-owned `.oxlint-kit.json`; `.oxlintrc.json` extends it, then an optional `.oxlint-project.json` (never touched by `kit.py update`, protected by the config guard). Three rule groups: locked rules that are always errors (`no-floating-promises`, `no-misused-promises`, `await-thenable`, `no-debugger`), a short list of type-hygiene rules a project may lower to warnings, and every other rule as an error. `options.maxWarnings` in the project file caps the warnings; `bash scripts/lint-cap.sh` records the baseline and lowers the cap, and refuses to raise it.
- New check step `23-ts-cap-raise.sh`: on a pull request it fails when the cap in `.oxlint-project.json` is higher than on the base branch (or the file or cap was removed); a file new in the pull request passes, and outside a pull request it skips. It fetches the base branch itself, so `check.yml`'s shallow checkout needs no change.
- README: the three rule groups, the cap, the lower-cap command and a five-step adoption recipe.
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

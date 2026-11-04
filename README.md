# starter-kit
Shared starter kit: checks, hooks, CI and guards per project type

## What a project gets

`python3 kit.py apply [--layer python] [--layer shell] [--layer typescript] <project>` copies the base layer and the layers you name into a git project. `kit.py update <project>` brings it up to the current kit and keeps the project's own edits. Python 3.11 or newer, standard library only; the tests run with `./tests.sh`.

### Local CI

`bash scripts/check.sh` is the local CI: it runs every `scripts/check.d/NN-*.sh` step in name order, keeps going after a failure, and ends with `CHECK PASSED`, or with one `CHECK FAILED: <script>` line per failed step. The number is the cost: `10-` for seconds, `20-` for the language tools, `30-` and up for slow suites. The pre-push hook runs it before every push and the GitHub workflow runs the same script, so there is no second local-CI script to keep in step. The workflow skips draft pull requests and runs once when a draft is marked ready; pushes to main always run it. The key scan (`gitleaks.yml`) also runs on drafts.

### Layers

| Layer | Applied | What it adds |
| --- | --- | --- |
| base | always | `scripts/check.sh`, the pre-push hook, the CI workflows, the key scan, the Claude guards, `renovate.json` |
| python | `--layer python` | ruff lint and format, pyright, pytest (through uv); cached setup-uv CI step |
| shell | `--layer shell` | shellcheck on every shell script; pinned shellcheck install in CI |
| typescript | `--layer typescript` | typecheck, oxlint, oxfmt and tests for single-package projects; see below |
| gitleaks-ci | `--layer gitleaks-ci` | pinned gitleaks in the CI check job, so the local key scan runs there too |

### TypeScript layer

For a single-package project (a Next.js app, a Cloudflare Worker, plain TypeScript) with a lockfile at the repo root. Install the dependencies first. The layer adds one `check.d` script per step, in this order; each runs even when an earlier one failed, and `check.sh` lists every failed one:

| Script | Step | Command | Fails when |
| --- | --- | --- | --- |
| `20-ts-typecheck.sh` | typecheck | `tsc --noEmit` with TypeScript 7 (see below) | any type error; no `tsconfig.json`; a `tsconfig.json` that does not parse (tsc's own error is shown); the config reaches zero source files |
| `21-ts-lint.sh` | lint | `oxlint --type-aware` (oxlint 1.90.0, oxlint-tsgolint 7.0.2005) | any error-level finding; zero files linted |
| `22-ts-format.sh` | format | `oxfmt --check` on `.ts .tsx .js .jsx .mjs .cjs .mts .cts` (oxfmt 0.75.0) | any file that would change; zero files checked |
| `30-ts-tests.sh` | tests | the project's `test` script, else `vitest run` when vitest is configured | a failing test. No test script (or npm's placeholder) and no vitest: skipped with a line saying so |

The steps share `scripts/ts-common.sh`, which holds the pinned versions (Renovate reads them) and the helpers.

**Which TypeScript runs.** Always TypeScript 7, and the first line of the typecheck names it. The project's `node_modules/.bin/tsc` is used only when `tsc --version` reports 7 or newer (so is a `node_modules/typescript7` alias). Any older project `tsc` (TypeScript 5 or 6) is not used, and the pinned TypeScript 7.0.2 runs through the package manager's one-off runner instead. A `tsconfig.json` with `references` (the Vite layout, `"files": []`) is checked with `tsc -b --noEmit`, which builds every referenced project; that may write `.tsbuildinfo` files where the referenced tsconfigs say (Vite: `node_modules/.tmp`).

**Which oxlint and oxfmt run.** The package manager is read from the lockfile: `pnpm-lock.yaml`, `package-lock.json`, `yarn.lock`, `bun.lock` or the older binary `bun.lockb` (first match wins, in that order). A tool the project already has in `node_modules/.bin` is used as it is, provided every package the kit pins for it is installed too (oxlint counts only with oxlint-tsgolint next to it). Otherwise the kit runs its pinned version through the manager's one-off runner (`pnpm dlx`, `npx -y`, `yarn dlx`, `bunx`; yarn 1 and multi-package runs under bun use `npx`), so `package.json` stays untouched and the versions live in the one helper file.

Two canaries keep a green result honest. Before the real run, `tsc` is run on a planted type error, and `oxlint` on a planted floating promise and `debugger` statement, under the project's own `.oxlintrc.json`; the check fails with `CANARY FAILED` unless each is reported, and the lint ones must be reported as errors, not warnings (a rule switched off or left at warning, or type information not reaching the linter, shows up here). Each real run must also have looked at more than zero files, and a count that cannot be read fails the step too.

`apply` ships `.oxlintrc.json`, `.oxfmtrc.json`, `scripts/ts-common.sh`, `scripts/ts-pm.sh` and `.claude/protect.d/typescript.txt`. The lint rules are a copy of a finished monorepo config: oxlint's categories off, about a hundred named rules (type-aware ones included), and the React hooks rules on `.tsx` and `.jsx` files only. Like every kit file, one that already exists with other content stops `apply` before anything is written (use `--force` to overwrite). A project that already uses eslint or prettier keeps both untouched; the kit's check does not run them, and `oxfmt` prints with Prettier's defaults at 80 columns, so set the same options in `.oxfmtrc.json` by hand if your Prettier config differs. Monorepos with a package per folder are not covered.

CI: Node comes from the project's `.nvmrc` if it has one, else its `.node-version`, else Node 22 (setup-node, three steps gated on which file exists). pnpm and yarn come through corepack (`packageManager` in `package.json` picks the version), bun through setup-bun when the project has `bun.lock` or `bun.lockb`, then `bash scripts/ts-pm.sh install` installs from the lockfile without changing it. There is no package-manager cache: the one measurement (a monorepo with a ~395 MB pnpm store) had a restore slower than a cold install; a small project was not measured. The setup actions are pinned to commit SHAs.

Open pull requests as drafts while you work and mark them ready once: the `check` job skips drafts, so CI does not run on every push while you work and runs when you mark the pull request ready.

### Claude Code guards (base layer)

All are wired in `.claude/settings.json`. Each guard blocks with exit code 2 and a reason Claude can read. A crash or unreadable input in a guard lets the tool call through, so a broken guard never stops all work; a clear match is always refused.

| File | Runs on | What it does |
| --- | --- | --- |
| `scripts/session-status.sh` | session start | Prints the branch, uncommitted files, open pull requests, open issue count and free disk. GitHub lines are left out when gh is missing, signed out or slow. Always exits 0. |
| `.claude/hooks/env-guard.py` | Read, Grep, Bash | Refuses to print the values of `.env` and `.env.<name>` files. `.env.example` and listing names only (`cut -d= -f1 .env`) are allowed. Does not cover a program that loads `.env` and prints its own environment. |
| `.claude/hooks/merge-guard.py` | Bash | Refuses a pull request merge (the gh merge command or its API call) unless every check on the PR's head commit passed. If `$(git rev-parse --git-common-dir)/review-stamps/` exists, the head commit must also have a file there named after its full SHA; a repo without that folder is never asked for a stamp. |
| `.claude/hooks/config-guard.sh` | Edit, Write, MultiEdit, NotebookEdit | Refuses edits to lint, format and scan configuration: "Fix the code, don't weaken the config." |
| `.claude/hooks/block-secrets.sh` | Edit, Write, Bash | Refuses a key, token or password in a file git saves. |

The `.env` and merge guards need `python3` on the PATH; without it they do nothing.

When a private repo runs out of GitHub Actions minutes, its checks never run, so the merge guard refuses every merge. That is on purpose: the owner merges by hand on GitHub until the minutes come back. A local `scripts/check.sh` pass does not count, because the agent could fake its result.

#### Which files the config guard protects

Each layer ships a list in `.claude/protect.d/<layer>.txt`, one pattern per line (`#` starts a comment). A pattern with no slash matches that file name in any folder; one with a slash matches the path from the project root, with `*` as a glob. A project is protected for exactly the layers it applied:

- base: `.gitleaks.toml`, and the lists themselves
- python: `ruff.toml`, `.ruff.toml`, `pyrightconfig.json`. `pyproject.toml` is not covered, because it also holds dependencies; a weakened `[tool.ruff]` or `[tool.pyright]` section there gets through.
- shell: `.shellcheckrc`
- typescript: `.oxlintrc.json`, `.oxfmtrc.json`. `tsconfig.json` and `package.json` are not covered, because they also hold compiler paths, scripts and dependencies.

Add your own patterns in a new file such as `.claude/protect.d/local.txt`. To change a protected file on purpose, edit it by hand outside Claude, or start Claude with `ALLOW_CONFIG_EDIT=1` in its environment. Claude's own shell commands cannot set that variable for the hook.

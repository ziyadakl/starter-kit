# starter-kit
Shared starter kit: checks, hooks, CI and guards per project type

## What a project gets

`python3 kit.py apply [--layer python] [--layer shell] <project>` copies the base layer and the layers you name into a git project. `kit.py update <project>` brings it up to the current kit and keeps the project's own edits. Python 3.11 or newer, standard library only; the tests run with `./tests.sh`.

### Local CI

`bash scripts/check.sh` is the local CI: it runs every `scripts/check.d/NN-*.sh` step in order and ends with `CHECK PASSED` or `CHECK FAILED`. The pre-push hook runs it before every push and the GitHub workflow runs the same script, so there is no second local-CI script to keep in step.

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

Add your own patterns in a new file such as `.claude/protect.d/local.txt`. To change a protected file on purpose, edit it by hand outside Claude, or start Claude with `ALLOW_CONFIG_EDIT=1` in its environment. Claude's own shell commands cannot set that variable for the hook.

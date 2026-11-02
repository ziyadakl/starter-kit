#!/usr/bin/env bash
# Python checks: ruff lint, ruff format, pyright, pytest.
# The linters run pinned through uvx, so the project's pyproject.toml needs no tool entries; a
# [tool.ruff] or [tool.pyright] table there still wins. pytest runs in the project's own
# environment (uv run) when it has a [project] table, and a project with no tests passes.
set -euo pipefail

RUFF_VERSION=0.17.3
PYRIGHT_VERSION=1.1.414

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed (https://docs.astral.sh/uv/)" >&2
  exit 1
fi

uv_run=(uv run)
if ! grep -qE '^\[project\]' pyproject.toml 2>/dev/null; then
  uv_run=(uv run --no-project)
elif [ -f uv.lock ]; then
  uv_run=(uv run --locked)
fi

echo "-- lint (ruff $RUFF_VERSION)"
uvx "ruff@$RUFF_VERSION" check .

echo "-- format (ruff $RUFF_VERSION)"
uvx "ruff@$RUFF_VERSION" format --check .

echo "-- types (pyright $PYRIGHT_VERSION)"
pyright=(uvx "pyright@$PYRIGHT_VERSION")
if grep -qE '^\[project\]' pyproject.toml 2>/dev/null; then
  # Point pyright at the project's environment so its imports resolve.
  py=$("${uv_run[@]}" python -c 'import sys; print(sys.executable)')
  pyright+=(--pythonpath "$py")
fi
"${pyright[@]}"

echo "-- tests (pytest)"
status=0
"${uv_run[@]}" --with pytest pytest -q -p no:cacheprovider || status=$?
if [ "$status" -eq 5 ]; then
  echo "no tests collected: skipping"
elif [ "$status" -ne 0 ]; then
  exit "$status"
fi

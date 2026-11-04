#!/usr/bin/env bash
# Tests: the project's own `test` script wins (it carries its flags); otherwise vitest when the project has it configured.
# No test script (or npm's placeholder) and no vitest: skipped with a line saying so.
# shellcheck disable=SC2154  # pm, canary_dir and the version pins come from ts-common.sh
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../ts-common.sh"

echo "-- tests ($pm)"
export CI=1

script=$(node -e 'const s = (require("./package.json").scripts || {}).test; if (s) process.stdout.write(s)' 2>/dev/null)
case "$script" in
  "" | *"no test specified"*) ;;
  *)
    "$pm" run test
    exit $?
    ;;
esac

configured=0
for f in vitest.config.* vitest.workspace.*; do
  [ -e "$f" ] && configured=1
done
if node -e 'const p = require("./package.json"); process.exit({ ...p.dependencies, ...p.devDependencies }.vitest ? 0 : 1)' 2>/dev/null; then
  configured=1
fi
if [ "$configured" -eq 0 ]; then
  echo "no tests configured: skipping (no test script and no vitest)"
  exit 0
fi
if [ ! -x node_modules/.bin/vitest ]; then
  echo "vitest is configured but not installed: run the dependency install" >&2
  exit 1
fi
node_modules/.bin/vitest run

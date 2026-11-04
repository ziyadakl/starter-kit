#!/usr/bin/env bash
# Lint with oxlint, type-aware (needs oxlint-tsgolint next to it), under .oxlintrc.json: the kit's rules
# (.oxlint-kit.json) then the project's optional .oxlint-project.json.
#
# Policy: the merged config must keep the locked rules as errors and may lower only the allowed rules (see ts-common.sh);
# the project file's options.maxWarnings caps the warnings: over the cap fails, under it prints the lower-cap command.
#
# Canary: oxlint first runs on a planted floating promise (type-aware) and a debugger statement and must report both
# as errors under the merged config, so a rule switched off, a rule left at warning, or type information not
# reaching the linter fails here instead of passing silently. The real run must also have looked at more than zero files.
# shellcheck disable=SC2154  # pm, canary_dir and the version pins come from ts-common.sh
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../ts-common.sh"

oxlint_pkgs=("oxlint@$OXLINT_VERSION" "oxlint-tsgolint@$OXLINT_TSGOLINT_VERSION")
config=()
cap=""

echo "-- lint (oxlint $OXLINT_VERSION, type-aware)"

new_canary_dir
if [ -f .oxlintrc.json ]; then
  config_file=.oxlintrc.json
  if [ ! -f "$LINT_PROJECT_FILE" ]; then
    # oxlint refuses an extends entry that points at a missing file: lint under a copy of the root config without it.
    config_file="$canary_dir/oxlintrc.json"
    sed -E "s#^( *)\"\\./$LINT_PROJECT_FILE\"#\\1\"./$LINT_KIT_FILE\"#; s#\"\\./#\"$PWD/#" .oxlintrc.json >"$config_file"
  fi
  config=(-c "$config_file")

  if ! { tool oxlint "${oxlint_pkgs[@]}" -- -c "$LINT_KIT_FILE" --print-config >"$canary_dir/kit.json" 2>&1 &&
    tool oxlint "${oxlint_pkgs[@]}" -- -c "$config_file" --print-config >"$canary_dir/merged.json" 2>&1; }; then
    cat "$canary_dir/kit.json" "$canary_dir/merged.json" 2>/dev/null
    echo "lint: could not read the merged oxlint config" >&2
    exit 1
  fi
  # Prints the cap (nothing when there is none); violations go to stderr, one line each, with exit status 1.
  cap=$(node - "$canary_dir/kit.json" "$canary_dir/merged.json" <<'JS'
const fs = require("fs");
const [kitPath, mergedPath] = process.argv.slice(2);
const kit = JSON.parse(fs.readFileSync(kitPath, "utf8"));
const merged = JSON.parse(fs.readFileSync(mergedPath, "utf8"));
const words = (v) => (process.env[v] || "").split(/\s+/).filter(Boolean);
const locked = words("LINT_LOCKED_RULES");
const lowerable = words("LINT_LOWERABLE_RULES");
const norm = (n) => n.replace(/^@typescript-eslint\//, "typescript/");
const level = (v) => {
  const w = String(Array.isArray(v) ? v[0] : v).toLowerCase();
  return ["deny", "error", "2"].includes(w) ? 2 : ["warn", "warning", "1"].includes(w) ? 1 : 0;
};
const name = (l) => (l === 2 ? "error" : l === 1 ? "warn" : "off");
const isLowerable = (n) => lowerable.some((p) => (p.endsWith("*") ? n.startsWith(p.slice(0, -1)) : n === p));
const rules = (o) => Object.fromEntries(Object.entries((o && o.rules) || {}).map(([k, v]) => [norm(k), v]));
const kitRules = rules(kit);
const mergedRules = rules(merged);
const key = (o) => JSON.stringify(o.files);
const bad = [];
const lockedHit = (n, l, where) =>
  bad.push(`lint: locked rule ${n} is ${name(l)}${where}, not error: a project cannot lower it (${process.env.LINT_PROJECT_FILE})`);
for (const n of locked) if (level(mergedRules[n]) !== 2) lockedHit(n, level(mergedRules[n]), "");
for (const [n, v] of Object.entries(mergedRules)) {
  if (kitRules[n] !== undefined && level(v) < level(kitRules[n]) && !locked.includes(n) && !isLowerable(n))
    bad.push(`lint: rule ${n} is lowered to ${name(level(v))}, but only the allowed rules may be (${process.env.LINT_PROJECT_FILE})`);
}
for (const o of merged.overrides || []) {
  const base = ((kit.overrides || []).find((k) => key(k) === key(o)) || {}).rules || {};
  const baseRules = Object.fromEntries(Object.entries(base).map(([k, v]) => [norm(k), v]));
  for (const [n, v] of Object.entries(rules(o))) {
    if (locked.includes(n)) {
      if (level(v) !== 2) lockedHit(n, level(v), ` for ${key(o)}`);
      continue;
    }
    const ref = baseRules[n] !== undefined ? baseRules[n] : kitRules[n];
    if (ref !== undefined && level(v) < level(ref) && !isLowerable(n) && n !== "no-var")
      bad.push(`lint: rule ${n} is lowered to ${name(level(v))} for ${key(o)}, but only the allowed rules may be (${process.env.LINT_PROJECT_FILE})`);
  }
}
bad.forEach((m) => console.error(m));
if (bad.length) process.exit(1);
const cap = merged.options && merged.options[process.env.LINT_CAP_KEY];
if (Number.isInteger(cap)) console.log(cap);
JS
  ) || exit 1
fi

printf 'async function f() {\n  return 1;\n}\nf();\ndebugger;\n' >"$canary_dir/canary.ts"
# The unix format ends every finding with [Error/<rule>] or [Warning/<rule>].
out=$(tool oxlint "${oxlint_pkgs[@]}" -- --type-aware -f unix ${config[@]+"${config[@]}"} "$canary_dir" 2>&1)
status=$?
missing=""
for rule in no-debugger no-floating-promises; do
  printf '%s\n' "$out" | grep -Eq "\[Error/[a-z-]+\($rule\)\]" || missing="$missing $rule"
done
if [ "$status" = 0 ] || [ -n "$missing" ]; then
  printf '%s\n' "$out"
  echo "CANARY FAILED: lint: oxlint did not report${missing:- the planted mistakes} as errors: the rules are off or only warn in the config, or type information is not reaching it" >&2
  exit 1
fi

out=$(tool oxlint "${oxlint_pkgs[@]}" -- --type-aware -f default ${config[@]+"${config[@]}"} . 2>&1)
status=$?
printf '%s\n' "$out"
require_files lint "$out" "$status" || exit 1

warnings=$(printf '%s\n' "$out" | sed -nE 's/^Found ([0-9]+) warnings? and [0-9]+ errors?\..*/\1/p' | tail -n 1)
if [ -n "$cap" ] && [ -n "$warnings" ]; then
  if printf '%s\n' "$out" | grep -q 'Exceeded maximum number of warnings'; then
    echo "lint: $warnings warnings, over the cap of $cap ($LINT_CAP_KEY in $LINT_PROJECT_FILE): fix the new warnings. Only the owner raises the cap" >&2
  elif [ "$status" = 0 ] && [ "$warnings" -lt "$cap" ]; then
    echo "lint: $warnings warnings, below the cap of $cap: lower the cap with: $LINT_CAP_COMMAND"
  fi
fi
exit "$status"

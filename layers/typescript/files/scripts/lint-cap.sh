#!/usr/bin/env bash
# The lower-cap command: measures the project's lint warnings and writes (or lowers) the project lint file, .oxlint-project.json.
#   bash scripts/lint-cap.sh
# What it does, in order:
#   1. Files outside the tsconfig that have findings (untyped browser scripts, say) get an override that turns off the
#      typescript/no-unsafe-* family and no-var there. The promise rules stay on.
#   2. Each allowed rule (LINT_LOWERABLE_RULES in ts-common.sh) that still fires is set to "warn". Locked rules and every
#      other rule stay errors.
#   3. options.maxWarnings is set to the warning count measured now.
# It refuses to raise an existing cap: if the count is above it, nothing is written. Only the owner raises the cap.
# A project with no warnings and nothing to override gets no project file. Findings that stay errors are listed by rule:
# fix those, they are what the check still fails on.
# shellcheck disable=SC2154  # pm, canary_dir and the version pins come from ts-common.sh
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/ts-common.sh"

oxlint_pkgs=("oxlint@$OXLINT_VERSION" "oxlint-tsgolint@$OXLINT_TSGOLINT_VERSION")

if [ ! -f .oxlintrc.json ] || [ ! -f "$LINT_KIT_FILE" ]; then
  echo "lint-cap: .oxlintrc.json and $LINT_KIT_FILE are not both here: apply the typescript layer first (kit.py apply --layer typescript)" >&2
  exit 1
fi
if [ ! -f tsconfig.json ]; then
  echo "lint-cap: no tsconfig.json at the repo root: the type-aware rules need it" >&2
  exit 1
fi

new_canary_dir
work=$canary_dir
read -r -d '' LC_JS <<'JS'
const fs = require("fs");
const path = require("path");
const [mode, ...a] = process.argv.slice(1);
const words = (v) => (process.env[v] || "").split(/\s+/).filter(Boolean);
const lowerable = words("LINT_LOWERABLE_RULES");
const capKey = process.env.LINT_CAP_KEY;
const isLowerable = (n) => lowerable.some((p) => (p.endsWith("*") ? n.startsWith(p.slice(0, -1)) : n === p));
// A rule's name as the config spells it, from a diagnostic code such as typescript(no-unsafe-assignment).
const ruleOf = (code) => {
  const m = /^([a-z-]+)\((.+)\)$/.exec(code || "");
  if (!m) return code || "";
  return m[1] === "eslint" ? m[2] : `${m[1]}/${m[2]}`;
};
const readJson = (f) => JSON.parse(fs.readFileSync(f, "utf8").replace(/^\s*\/\/.*$/gm, ""));
const writeJson = (f, o) => fs.writeFileSync(f, JSON.stringify(o, null, 2) + "\n");
const existing = (f) => (f !== "-" && fs.existsSync(f) ? readJson(f) : {});
const diags = (f) => readJson(f).diagnostics || [];
// Paths from tsc may go through a symlink (/tmp, /var on macOS): compare real paths.
const real = (p) => { try { return fs.realpathSync(p); } catch { return p; } };
const rel = (p) => path.relative(real(process.cwd()), real(p)).split(path.sep).join("/");

if (mode === "candidate") {
  // <existing|-> <out>: the existing project file without its cap, to measure under.
  const cfg = existing(a[0]);
  if (cfg.options) delete cfg.options[capKey];
  writeJson(a[1], cfg);
} else if (mode === "refs") {
  // <tsconfig show-config output file>: the tsconfig files it references, one per line.
  const cfg = JSON.parse(fs.readFileSync(a[0], "utf8"));
  for (const r of cfg.references || []) {
    let p = path.resolve(r.path);
    if (fs.existsSync(p) && fs.statSync(p).isDirectory()) p = path.join(p, "tsconfig.json");
    console.log(rel(p));
  }
} else if (mode === "outside") {
  // <diagnostics json> <ts files list> <kit print-config json> <candidate>: adds the override for files with findings
  // that the tsconfig does not reach.
  const inTs = new Set(fs.readFileSync(a[1], "utf8").split("\n").filter(Boolean).map((l) => rel(path.resolve(l))));
  const outside = [...new Set(diags(a[0]).map((d) => d.filename))].filter((f) => f && !inTs.has(f));
  const globs = new Set();
  for (const f of outside) {
    const dir = path.posix.dirname(f);
    const tsInside = [...inTs].some((t) => dir !== "." && t.startsWith(dir + "/"));
    globs.add(dir === "." || tsInside ? f : `${dir}/**`);
  }
  if (!globs.size) process.exit(0);
  const kitRules = Object.keys(JSON.parse(fs.readFileSync(a[2], "utf8")).rules || {}).map((n) => n.replace(/^@typescript-eslint\//, "typescript/"));
  const family = kitRules.filter((n) => lowerable.some((p) => p.endsWith("*") && n.startsWith(p.slice(0, -1))));
  const rules = {};
  for (const n of [...family].sort()) rules[n] = "off";
  rules["no-var"] = "off";
  const cfg = readJson(a[3]);
  // A folder glob already covers every glob and file inside it.
  const dirs = [...globs].filter((g) => g.endsWith("/**")).map((g) => g.slice(0, -2));
  const files = [...globs].filter((g) => !dirs.some((d) => g.startsWith(d) && g !== d + "**")).sort();
  cfg.overrides = cfg.overrides || [];
  if (!cfg.overrides.some((o) => JSON.stringify(o.files) === JSON.stringify(files))) cfg.overrides.push({ files, rules });
  writeJson(a[3], cfg);
} else if (mode === "final") {
  // <diagnostics json> <candidate> <existing|-> <out>: sets the firing allowed rules to warn, sets the cap, writes the file.
  const all = diags(a[0]);
  const cfg = readJson(a[1]);
  const old = existing(a[2]);
  const oldCap = old.options && Number.isInteger(old.options[capKey]) ? old.options[capKey] : null;
  const firing = new Set(all.map((d) => ruleOf(d.code)).filter(isLowerable));
  // A rule keeps its entry unless it is an allowed rule at "warn": those are re-derived from what fires now.
  const rules = {};
  for (const [n, v] of Object.entries(cfg.rules || {})) if (!(isLowerable(n) && String(v) === "warn")) rules[n] = v;
  for (const n of [...firing].sort()) rules[n] = "warn";
  const warnings = all.filter((d) => d.severity === "warning" || firing.has(ruleOf(d.code))).length;
  const errors = all.filter((d) => d.severity !== "warning" && !firing.has(ruleOf(d.code)));
  const left = {};
  for (const d of errors) left[ruleOf(d.code)] = (left[ruleOf(d.code)] || 0) + 1;
  const leftText = Object.entries(left).sort((x, y) => y[1] - x[1]).map(([n, c]) => `  ${c}  ${n}`).join("\n");
  if (oldCap !== null && warnings > oldCap) {
    console.error(`lint-cap: ${warnings} warnings, above the cap of ${oldCap} in ${process.env.LINT_PROJECT_FILE}: nothing written. Fix the new warnings; only the owner raises the cap`);
    process.exit(1);
  }
  const hasOverrides = (cfg.overrides || []).length > 0;
  const out = { ...cfg };
  if (Object.keys(rules).length) out.rules = rules;
  else delete out.rules;
  if (warnings > 0 || oldCap !== null || hasOverrides || Object.keys(rules).length) {
    out.options = { ...(cfg.options || {}), [capKey]: warnings };
    const header =
      `// The project's own lint settings; the kit's rules are in ${process.env.LINT_KIT_FILE}. Written by \`${process.env.LINT_CAP_COMMAND}\`.\n` +
      `// rules: allowed rules that fire in old code, set to warn. overrides: files outside the tsconfig skip the type-hygiene rules.\n` +
      `// ${capKey}: the warning count measured then; the check fails above it. Only the owner raises it.\n`;
    fs.writeFileSync(a[3], header + JSON.stringify(out, null, 2) + "\n");
    console.log(`lint-cap: ${oldCap === null ? "wrote" : "updated"} ${process.env.LINT_PROJECT_FILE}: cap ${warnings}${oldCap !== null && oldCap !== warnings ? ` (was ${oldCap})` : ""}`);
    console.log(`lint-cap: rules set to warn: ${[...firing].sort().join(", ") || "none"}`);
    for (const o of out.overrides || []) console.log(`lint-cap: override for ${o.files.join(", ")}: ${Object.keys(o.rules).length} rules off`);
  } else {
    console.log("lint-cap: no warnings and nothing to override: no project file is needed");
  }
  console.log(`lint-cap: errors left, by rule (${errors.length} in all):${leftText ? "\n" + leftText : " none"}`);
}
JS
lc_node() { node -e "$LC_JS" "$@"; }

# lint_json <project file to lint under> <out.json>: the type-aware findings as JSON, under the kit config plus that project file.
lint_json() {
  local root=$lint_root
  sed -E "s#\"\\./$LINT_PROJECT_FILE\"#\"$1\"#; s#\"\\./#\"$PWD/#" .oxlintrc.json >"$root"
  tool oxlint "${oxlint_pkgs[@]}" -- --type-aware -f json -c "$root" . >"$2" 2>"$work/lint.err"
  if ! node -e 'JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))' "$2" 2>/dev/null; then
    cat "$2" "$work/lint.err" 2>/dev/null
    echo "lint-cap: oxlint did not produce a report" >&2
    exit 1
  fi
}

# ts_files: the files the tsconfig reaches (its references too), absolute or relative, one per line.
ts_files() {
  local ts=(tool tsc "typescript@$TYPESCRIPT_VERSION" --) ref
  "${ts[@]}" --noEmit --listFilesOnly || return 1
  "${ts[@]}" --showConfig >"$work/showconfig.json" 2>/dev/null || return 0
  for ref in $(lc_node refs "$work/showconfig.json" 2>/dev/null); do
    "${ts[@]}" --noEmit --listFilesOnly -p "$ref" || return 1
  done
}

echo "-- lint-cap (oxlint $OXLINT_VERSION, type-aware)"
existing=-
[ -f "$LINT_PROJECT_FILE" ] && existing=$LINT_PROJECT_FILE
# The candidate project file and the root config copy sit in the repo root: oxlint reads an override's globs relative to
# the root config it was given, so a copy elsewhere would match the override's files against the wrong folder.
cand="$PWD/.oxlint-project.lint-cap-$$.json"
lint_root=".oxlintrc.lint-cap-$$.json" # relative: oxlint matches the globs against the real path of the folder, which an absolute path through a symlink (/tmp on macOS) would miss
trap 'cleanup; rm -f "$cand" "$lint_root"' EXIT

tool oxlint "${oxlint_pkgs[@]}" -- -c "$LINT_KIT_FILE" --print-config >"$work/kit.json" 2>"$work/kit.err" || {
  cat "$work/kit.err"
  echo "lint-cap: could not read $LINT_KIT_FILE" >&2
  exit 1
}

lc_node candidate "$existing" "$cand" || exit 1
lint_json "$cand" "$work/pass1.json"
ts_files >"$work/tsfiles.txt" 2>"$work/ts.err" || {
  cat "$work/ts.err"
  echo "lint-cap: tsc could not list the files the tsconfig reaches" >&2
  exit 1
}
lc_node outside "$work/pass1.json" "$work/tsfiles.txt" "$work/kit.json" "$cand" || exit 1
lint_json "$cand" "$work/pass2.json"
lc_node final "$work/pass2.json" "$cand" "$existing" "$LINT_PROJECT_FILE"

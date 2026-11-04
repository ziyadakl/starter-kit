// The lint-file policy of the typescript layer, in one place. Plain Node, no dependencies. Run it, do not import it:
//   node scripts/lint-policy.mjs <command> [args]
// Its users are scripts/check.d/21-ts-lint.sh, scripts/check.d/23-ts-cap-raise.sh and scripts/lint-cap.sh.
// The rule lists and file names come from the environment (scripts/ts-common.sh exports them), so there is one copy.
//
// Commands (a violation or a bad file prints one message on stderr, with no stack trace):
//   cap <file>                         prints the file's warning cap, or "none". Exit 2 when the file is not JSON.
//   check <kit.json> <merged.json> [--ts-files <list>]
//                                      the merged config (oxlint --print-config) against the kit's. Exit 1 on a violation.
//                                      Prints the cap when there is one.
//   root-config <out> [<project file>] the root .oxlintrc.json with its project-file entry replaced by <project file>, or
//                                      dropped when none is given (oxlint refuses an extends entry that points nowhere).
//   candidate <existing|-> <out>       the project file without its cap, to measure under.   (lint-cap.sh)
//   refs <showConfig.json>             the tsconfig files a tsconfig references, one per line.   (lint-cap.sh)
//   outside <diagnostics> <ts files> <kit.json> <candidate> <existing|->
//                                      adds the override for files outside the tsconfig.   (lint-cap.sh)
//   final <diagnostics> <candidate> <existing|-> <out>
//                                      sets the allowed rules that fire to warn, sets the cap, writes <out>.   (lint-cap.sh)
//
// The policy. Locked rules are errors, always. Lowerable rules may be "warn" and never "off". The rules that may be
// turned off at all (OVERRIDE_OFF) may be so only in an override whose files the tsconfig does not check: that is how the
// override lint-cap.sh writes for untyped browser scripts is told from one written to hide findings in typed code. Any
// other rule lowered is refused, and a level that is not off, warn or error is invalid (never read as "off"). A project
// file that lowers anything must also set options.maxWarnings.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

class PolicyError extends Error {}

// Reads JSON that may carry // line comments, trailing // comments and /* */ blocks (all outside strings).
export function stripComments(text) {
  let out = "";
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      let j = i + 1;
      while (j < text.length && text[j] !== '"') j += text[j] === "\\" ? 2 : 1;
      out += text.slice(i, j + 1);
      i = j;
    } else if (c === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n") i++;
      out += "\n";
    } else if (c === "/" && text[i + 1] === "*") {
      const end = text.indexOf("*/", i + 2);
      i = end === -1 ? text.length : end + 1;
      out += " ";
    } else {
      out += c;
    }
  }
  return out;
}

export function parseLoose(text, label) {
  try {
    return JSON.parse(stripComments(text));
  } catch (e) {
    throw new PolicyError(
      `${label}: not valid JSON after removing comments (${e.message})`,
    );
  }
}

export function readLoose(file) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch (e) {
    throw new PolicyError(`${file}: cannot be read (${e.code || e.message})`);
  }
  return parseLoose(text, file);
}

const words = (name, env) => (env[name] || "").split(/\s+/).filter(Boolean);
const inList = (list, n) =>
  list.some((p) => (p.endsWith("*") ? n.startsWith(p.slice(0, -1)) : n === p));
const normName = (n) => n.replace(/^@typescript-eslint\//, "typescript/");
const rulesOf = (o) =>
  Object.fromEntries(
    Object.entries((o && o.rules) || {}).map(([k, v]) => [normName(k), v]),
  );

export function policyFromEnv(env = process.env) {
  return {
    locked: words("LINT_LOCKED_RULES", env),
    lowerable: words("LINT_LOWERABLE_RULES", env),
    overrideOff: words("LINT_OVERRIDE_OFF_RULES", env),
    capKey: env.LINT_CAP_KEY,
    projectFile: env.LINT_PROJECT_FILE,
    capCommand: env.LINT_CAP_COMMAND,
    kitFile: env.LINT_KIT_FILE,
  };
}

const LEVELS = {
  off: 0,
  allow: 0,
  0: 0,
  warn: 1,
  1: 1,
  error: 2,
  deny: 2,
  2: 2,
};
const LEVEL_NAMES = ["off", "warn", "error"];
// 0 off, 1 warn, 2 error; null for anything else (a typo is not "off").
export function level(v) {
  const raw = Array.isArray(v) ? v[0] : v;
  const k = String(raw).toLowerCase();
  return Object.hasOwn(LEVELS, k) ? LEVELS[k] : null;
}

export const capOf = (cfg, capKey) => {
  const c = cfg && cfg.options && cfg.options[capKey];
  return Number.isInteger(c) && c >= 0 ? c : null;
};

// Paths from tsc may go through a symlink (/tmp, /var on macOS): compare real paths.
const real = (p) => {
  try {
    return fs.realpathSync(p);
  } catch {
    return p;
  }
};
const rel = (p) =>
  path.relative(real(process.cwd()), real(p)).split(path.sep).join("/");

// A glob of an oxlint override as a regular expression: **, *, ?, {a,b} and [classes].
export function globToRegExp(glob) {
  let re = "";
  const g = glob.replace(/^\.\//, "");
  for (let i = 0; i < g.length; i++) {
    const c = g[i];
    if (c === "*" && g[i + 1] === "*") {
      if (g[i + 2] === "/") {
        re += "(?:.*/)?";
        i += 2;
      } else {
        re += ".*";
        i += 1;
      }
    } else if (c === "[" && classEnd(g, i) !== -1) {
      // A character class, read the way oxlint's glob matcher reads it: "[!x]" negates, and a "]" right after the
      // opening "[" (or "[!") is a member, not the end.
      const end = classEnd(g, i);
      let body = g.slice(i + 1, end);
      const neg = body.startsWith("!");
      if (neg) body = body.slice(1);
      re += `[${neg ? "^" : ""}${body.replace(/[\\\]^]/g, "\\$&")}]`;
      i = end;
    } else if (c === "*") re += "[^/]*";
    else if (c === "?") re += "[^/]";
    else if (c === "{") re += "(?:";
    else if (c === "}") re += ")";
    else if (c === ",") re += "|";
    else re += c.replace(/[.+^$()|[\]\\]/g, "\\$&");
  }
  // Like oxlint (and .gitignore), a glob with no slash matches a file name at any depth.
  return new RegExp(`^${g.includes("/") ? "" : "(?:.*/)?"}${re}$`);
}

// The index of the "]" that closes the class opened at i, or -1 when it is not closed.
function classEnd(g, i) {
  let j = i + 1;
  if (g[j] === "!") j++;
  if (g[j] === "]") j++;
  return g.indexOf("]", j);
}

// The policy check. Returns { violations, cap }. tsFiles: the files the tsconfig reaches (relative), or null.
export function checkPolicy(kit, merged, policy, tsFiles) {
  const { locked, lowerable, overrideOff, capKey, projectFile } = policy;
  const bad = [];
  let lowered = false;
  const where = ` (${projectFile})`;
  const kitRules = rulesOf(kit);

  const judge = (n, v, ref, scope, files) => {
    const l = level(v);
    const at = scope ? ` for ${JSON.stringify(files)}` : "";
    if (l === null) {
      bad.push(
        `lint: rule ${n} has the level ${JSON.stringify(v)}${at}, which is not off, warn or error${where}`,
      );
      return;
    }
    if (locked.includes(n)) {
      if (l !== 2)
        bad.push(
          `lint: locked rule ${n} is ${LEVEL_NAMES[l]}${at}, not error: a project cannot lower it${where}`,
        );
      return;
    }
    const refLevel = ref === undefined ? null : level(ref);
    if (refLevel === null || l >= refLevel) return;
    lowered = true;
    if (scope && inList(overrideOff, n)) {
      const globs = (files || []).map((f) => globToRegExp(f));
      const hit = (tsFiles || []).filter((f) => globs.some((re) => re.test(f)));
      if (tsFiles === null)
        bad.push(
          `lint: rule ${n} is turned off${at} but the tsconfig's files were not listed, so it cannot be told from typed code${where}`,
        );
      else if (hit.length)
        bad.push(
          `lint: rule ${n} is turned off${at}, which covers ${hit[0]}${hit.length > 1 ? ` and ${hit.length - 1} more` : ""} in the tsconfig: only files outside the tsconfig may skip it${where}`,
        );
    } else if (inList(lowerable, n)) {
      if (l === 0)
        bad.push(
          `lint: rule ${n} is switched off${at}, but a project may only lower it to warn: off hides its findings from the cap${where}`,
        );
    } else {
      bad.push(
        `lint: rule ${n} is lowered to ${LEVEL_NAMES[l]}${at}, but only the allowed rules may be${where}`,
      );
    }
  };

  const mergedRules = rulesOf(merged);
  for (const n of locked)
    if (!(n in mergedRules))
      bad.push(
        `lint: locked rule ${n} is not set to error in the merged config${where}`,
      );
  for (const [n, v] of Object.entries(mergedRules))
    judge(n, v, kitRules[n], false);

  const key = (o) => JSON.stringify(o.files);
  for (const o of merged.overrides || []) {
    const mine = (kit.overrides || []).find((k) => key(k) === key(o));
    const base = rulesOf(mine);
    for (const [n, v] of Object.entries(rulesOf(o)))
      judge(n, v, base[n] !== undefined ? base[n] : kitRules[n], true, o.files);
  }

  const cap = capOf(merged, capKey);
  const given = merged.options && merged.options[capKey];
  if (given !== undefined && cap === null)
    bad.push(
      `lint: options.${capKey} is ${JSON.stringify(given)}: it must be a whole number, 0 or more${where}`,
    );
  else if (lowered && cap === null)
    bad.push(
      `lint: ${projectFile} lowers rules but sets no options.${capKey}: lowered rules need a cap on the warnings (${policy.capCommand} writes one)`,
    );
  return { violations: bad, cap };
}

// A rule's name as the config spells it, from a diagnostic code such as typescript(no-unsafe-assignment).
const ruleOf = (code) => {
  const m = /^([a-z-]+)\((.+)\)$/.exec(code || "");
  if (!m) return code || "";
  return m[1] === "eslint" ? m[2] : `${m[1]}/${m[2]}`;
};
const writeJson = (f, o) =>
  fs.writeFileSync(f, JSON.stringify(o, null, 2) + "\n");
const existing = (f) =>
  f && f !== "-" && fs.existsSync(f) ? readLoose(f) : {};
const diagnostics = (f) => readLoose(f).diagnostics || [];

function rootConfig(out, projectPath) {
  const policy = policyFromEnv();
  const root = readLoose(".oxlintrc.json");
  const mine = `./${policy.projectFile}`;
  root.extends = (root.extends || []).flatMap((e) =>
    e === mine ? (projectPath ? [`./${projectPath}`] : []) : [e],
  );
  writeJson(out, root);
}

function outside(diagFile, tsFile, kitFile, candFile, oldFile) {
  const policy = policyFromEnv();
  // A re-run only lowers the cap: it adds no override.
  if (capOf(existing(oldFile), policy.capKey) !== null) return;
  const inTs = new Set(
    fs
      .readFileSync(tsFile, "utf8")
      .split("\n")
      .filter(Boolean)
      .map((l) => rel(path.resolve(l))),
  );
  const outsideFiles = [
    ...new Set(diagnostics(diagFile).map((d) => d.filename)),
  ].filter((f) => f && !inTs.has(f));
  const globs = new Set();
  for (const f of outsideFiles) {
    const dir = path.posix.dirname(f);
    const tsInside = [...inTs].some(
      (t) => dir !== "." && t.startsWith(dir + "/"),
    );
    globs.add(dir === "." || tsInside ? f : `${dir}/**`);
  }
  if (!globs.size) return;
  const kitRules = Object.keys(readLoose(kitFile).rules || {}).map(normName);
  const names = new Set([
    ...kitRules.filter((n) => inList(policy.overrideOff, n)),
    ...policy.overrideOff.filter((p) => !p.endsWith("*")),
  ]);
  const rules = {};
  for (const n of [...names].sort()) rules[n] = "off";
  const cfg = readLoose(candFile);
  // A folder glob already covers every glob and file inside it.
  const dirs = [...globs]
    .filter((g) => g.endsWith("/**"))
    .map((g) => g.slice(0, -2));
  const files = [...globs]
    .filter((g) => !dirs.some((d) => g.startsWith(d) && g !== d + "**"))
    .sort();
  cfg.overrides = cfg.overrides || [];
  if (
    !cfg.overrides.some(
      (o) => JSON.stringify(o.files) === JSON.stringify(files),
    )
  )
    cfg.overrides.push({ files, rules });
  writeJson(candFile, cfg);
}

function final(diagFile, candFile, oldFile, outFile) {
  const policy = policyFromEnv();
  const { capKey, lowerable } = policy;
  const isLowerable = (n) => inList(lowerable, n);
  const all = diagnostics(diagFile);
  const cfg = readLoose(candFile);
  const old = existing(oldFile);
  const oldCap = capOf(old, capKey);
  // Measured under the candidate, which holds the existing project file: with a cap, its warn rules already show as warnings.
  const rerun = oldCap !== null;
  const fires = new Set(all.map((d) => ruleOf(d.code)).filter(isLowerable));
  const warned = new Set(
    all.filter((d) => d.severity === "warning").map((d) => ruleOf(d.code)),
  );
  const firing = rerun
    ? new Set([...fires].filter((n) => warned.has(n)))
    : fires;
  // A rule keeps its entry unless it is an allowed rule at "warn": those are re-derived from what fires now. A first run
  // sets every allowed rule that fires to warn; a re-run only keeps the ones already at warn, so it never adds a rule.
  const rules = {};
  for (const [n, v] of Object.entries(cfg.rules || {}))
    if (!(isLowerable(n) && String(v) === "warn")) rules[n] = v;
  for (const n of [...firing].sort()) rules[n] = "warn";
  const isWarning = (d) =>
    d.severity === "warning" || (!rerun && firing.has(ruleOf(d.code)));
  const warnings = all.filter(isWarning).length;
  const errors = all.filter((d) => !isWarning(d));
  const left = {};
  for (const d of errors)
    left[ruleOf(d.code)] = (left[ruleOf(d.code)] || 0) + 1;
  const leftText = Object.entries(left)
    .sort((x, y) => y[1] - x[1])
    .map(([n, c]) => `  ${c}  ${n}`)
    .join("\n");
  if (rerun && warnings > oldCap) {
    console.error(
      `lint-cap: ${warnings} warnings, above the cap of ${oldCap} in ${policy.projectFile}: nothing written. Fix the new warnings; only the owner raises the cap`,
    );
    process.exit(1);
  }
  const hasOverrides = (cfg.overrides || []).length > 0;
  const out = { ...cfg };
  if (Object.keys(rules).length) out.rules = rules;
  else delete out.rules;
  if (warnings > 0 || rerun || hasOverrides || Object.keys(rules).length) {
    out.options = { ...(cfg.options || {}), [capKey]: warnings };
    const header =
      `// The project's own lint settings; the kit's rules are in ${policy.kitFile}. Written by \`${policy.capCommand}\`.\n` +
      `// rules: allowed rules that fire in old code, set to warn. overrides: files outside the tsconfig skip the type-hygiene rules.\n` +
      `// ${capKey}: the warning count measured then; the check fails above it. Only the owner raises it.\n`;
    fs.writeFileSync(outFile, header + JSON.stringify(out, null, 2) + "\n");
    const was = rerun && oldCap !== warnings ? ` (was ${oldCap})` : "";
    console.log(
      `lint-cap: ${rerun ? "updated" : "wrote"} ${policy.projectFile}: cap ${warnings}${was}`,
    );
    console.log(
      `lint-cap: rules set to warn: ${[...firing].sort().join(", ") || "none"}`,
    );
    for (const o of out.overrides || [])
      console.log(
        `lint-cap: override for ${o.files.join(", ")}: ${Object.keys(o.rules).length} rules off`,
      );
  } else {
    console.log(
      "lint-cap: no warnings and nothing to override: no project file is needed",
    );
  }
  console.log(
    `lint-cap: errors left, by rule (${errors.length} in all):${leftText ? "\n" + leftText : " none"}`,
  );
}

function main(argv) {
  const [cmd, ...a] = argv;
  if (cmd === "cap") {
    const c = capOf(readLoose(a[0]), process.env.LINT_CAP_KEY);
    console.log(c === null ? "none" : c);
  } else if (cmd === "check") {
    const i = a.indexOf("--ts-files");
    const tsFiles =
      i === -1
        ? null
        : fs
            .readFileSync(a[i + 1], "utf8")
            .split("\n")
            .filter(Boolean)
            .map((l) => rel(path.resolve(l)));
    const { violations, cap } = checkPolicy(
      readLoose(a[0]),
      readLoose(a[1]),
      policyFromEnv(),
      tsFiles,
    );
    violations.forEach((m) => console.error(m));
    if (violations.length) process.exit(1);
    if (cap !== null) console.log(cap);
  } else if (cmd === "root-config") rootConfig(a[0], a[1]);
  else if (cmd === "candidate") {
    const cfg = existing(a[0]);
    if (cfg.options) delete cfg.options[process.env.LINT_CAP_KEY];
    writeJson(a[1], cfg);
  } else if (cmd === "refs") {
    const cfg = readLoose(a[0]);
    for (const r of cfg.references || []) {
      let p = path.resolve(r.path);
      if (fs.existsSync(p) && fs.statSync(p).isDirectory())
        p = path.join(p, "tsconfig.json");
      console.log(rel(p));
    }
  } else if (cmd === "outside") outside(...a);
  else if (cmd === "final") final(...a);
  else throw new PolicyError(`lint-policy: unknown command ${cmd}`);
}

if (
  process.argv[1] &&
  real(process.argv[1]) === real(fileURLToPath(import.meta.url))
) {
  try {
    main(process.argv.slice(2));
  } catch (e) {
    if (!(e instanceof PolicyError)) throw e;
    console.error(`lint-policy: ${e.message}`);
    process.exit(2);
  }
}

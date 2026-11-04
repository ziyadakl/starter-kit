"""TypeScript layer behaviour: what apply writes, package-manager detection, the check steps and their canaries.

Most tests run against fake tools in node_modules/.bin, so they need no network. TestRealTools runs the
kit's pinned tools through npx and is skipped where npx is missing.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import unittest

from tests.helpers import KIT_ROOT, clean_env, new_project, run_kit

# The seam the other lint-adoption slices build on: the project's own lint file, the kit's rules file, the cap key, and
# the command the lint check prints when the warning count drops below the cap.
PROJECT_FILE = ".oxlint-project.json"
KIT_RULES_FILE = ".oxlint-kit.json"
CAP_KEY = "maxWarnings"
LOWER_CAP_COMMAND = "bash scripts/lint-cap.sh"

PACKAGE_JSON = {"name": "demo", "version": "1.0.0", "private": True}
TSCONFIG = {
    "compilerOptions": {
        "strict": True,
        "noEmit": True,
        "target": "ES2022",
        "module": "ESNext",
        "moduleResolution": "Bundler",
        "types": [],
    },
    "include": ["src"],
}

# Fake tools: each reads FAKE_* from the environment so a test can make one of them misbehave.
# FAKE_TSC modes: ok, typeerror, blind (canary unseen), zero (no source files), parseerror (broken tsconfig),
# refs (tsconfig lists references, so the check must build them with -b), refs-typeerror.
# FAKE_TSC_VERSION is what `--version` prints.
FAKE_TSC = r"""#!/usr/bin/env bash
mode="${FAKE_TSC:-ok}"
case " $* " in
  *" --version "*) echo "Version ${FAKE_TSC_VERSION:-7.0.2}"; exit 0 ;;
  *" --listFilesOnly "*)
    [ "$mode" = parseerror ] && { echo "tsconfig.json(1,2): error TS1136: Property assignment expected."; exit 1; }
    [ "$mode" = zero ] && exit 0
    echo "$PWD/src/a.ts"; exit 0 ;;
  *" --showConfig "*)
    case "$mode" in refs*) echo '{ "files": [], "references": [{ "path": "./tsconfig.app.json" }] }' ;; *) echo '{ "files": ["./src/a.ts"] }' ;; esac
    exit 0 ;;
  *" -b "*)
    echo "tsc -b ran"
    [ "$mode" = refs-typeerror ] && { echo "src/a.ts(1,7): error TS2322: planted"; exit 2; }
    exit 0 ;;
  *" -p "*)
    [ "$mode" = blind ] && exit 0
    echo "canary.ts(1,7): error TS2322: Type 'string' is not assignable to type 'number'."; exit 2 ;;
esac
if [ "$mode" = typeerror ]; then echo "src/a.ts(1,7): error TS2322: planted"; exit 2; fi
exit 0
"""
# FAKE_OXLINT modes: ok, blind (canary finds nothing), norule (canary misses one rule), warn (canary finds both as
# warnings), zero (lints no files), lint (a finding in the project), unreadable (no summary line).
FAKE_OXLINT = r"""#!/usr/bin/env bash
mode="${FAKE_OXLINT:-ok}"
summary() { echo "Found 0 warnings and $1 errors."; echo "Finished in 1ms on ${2:-1} file with 90 rules using 1 threads."; }
case " $* " in
  *" --print-config "*)
    echo '{ "rules": { "no-debugger": "deny", "typescript/no-floating-promises": "deny", "typescript/no-misused-promises": "deny", "typescript/await-thenable": "deny" }, "overrides": [], "options": {} }'
    exit 0 ;;
esac
for a in "$@"; do
  if [ -f "$a/canary.ts" ]; then
    sev=Error
    [ "$mode" = warn ] && sev=Warning
    case "$mode" in
      blind) summary 0; exit 0 ;;
      norule) echo "canary.ts:4:1: planted [$sev/typescript(no-floating-promises)]"; summary 1; exit 1 ;;
    esac
    echo "canary.ts:5:1: planted [$sev/eslint(no-debugger)]"
    echo "canary.ts:4:1: planted [$sev/typescript(no-floating-promises)]"
    summary 2
    [ "$mode" = warn ] && exit 0
    exit 1
  fi
done
case "$mode" in
  zero) summary 0 0; exit 0 ;;
  unreadable) echo "all fine"; exit 0 ;;
  lint) echo "src/a.ts:1:1: planted [Error/eslint(no-debugger)]"; summary 1; exit 1 ;;
esac
summary 0
exit 0
"""
FAKE_OXFMT = r"""#!/usr/bin/env bash
case "${FAKE_OXFMT:-ok}" in
  zero) echo "Finished in 1ms on 0 files using 1 threads."; exit 0 ;;
  unreadable) echo "All matched files use the correct format."; exit 0 ;;
  bad) echo "src/a.ts"; echo "Format issues found in above 1 files."; exit 1 ;;
esac
echo "All matched files use the correct format."; echo "Finished in 1ms on 1 files using 1 threads."; exit 0
"""
FAKE_VITEST = r"""#!/usr/bin/env bash
echo "vitest $*"
[ "${FAKE_VITEST:-ok}" = fail ] && exit 1
exit 0
"""


def write_exec(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run_script(project, *cmd, env=None, path_prepend=None):
    e = clean_env()
    e.update(env or {})
    if path_prepend:
        e["PATH"] = f"{path_prepend}{os.pathsep}{e['PATH']}"
    return subprocess.run(
        list(map(str, cmd)),
        capture_output=True,
        text=True,
        cwd=project,
        env=e,
        check=False,
    )


class TypeScriptProject(unittest.TestCase):
    """A TypeScript project with a lockfile, fake installed tools, and the layer applied."""

    fake_tools = True

    def setUp(self):
        self.tmp, self.project = new_project()
        self.addCleanup(self.tmp.cleanup)
        p = self.project
        (p / "package.json").write_text(json.dumps(PACKAGE_JSON))
        (p / "package-lock.json").write_text("{}\n")
        (p / "tsconfig.json").write_text(json.dumps(TSCONFIG))
        (p / "src").mkdir()
        (p / "src/a.ts").write_text("export const a = 1;\n")
        (p / ".gitignore").write_text("node_modules/\n")
        if self.fake_tools:
            bin_dir = p / "node_modules/.bin"
            write_exec(bin_dir / "tsc", FAKE_TSC)
            write_exec(bin_dir / "oxlint", FAKE_OXLINT)
            for package in ("oxlint", "oxlint-tsgolint", "oxfmt"):
                (p / "node_modules" / package).mkdir()
            write_exec(bin_dir / "oxfmt", FAKE_OXFMT)
        else:
            (p / "node_modules").mkdir()
        r = run_kit("apply", "--layer", "typescript", p)
        self.assertEqual(r.returncode, 0, r.stderr)

    def check(self, **env):
        c = run_script(self.project, "bash", "scripts/check.sh", env=env)
        return c, c.stdout + c.stderr


class TestApply(TypeScriptProject):
    def test_layer_writes_its_files(self):
        for rel in (
            "scripts/check.d/20-ts-typecheck.sh",
            "scripts/check.d/21-ts-lint.sh",
            "scripts/check.d/22-ts-format.sh",
            "scripts/check.d/23-ts-cap-raise.sh",
            "scripts/check.d/30-ts-tests.sh",
            "scripts/ts-common.sh",
            "scripts/ts-pm.sh",
            ".oxlintrc.json",
            ".oxlint-kit.json",
            ".oxfmtrc.json",
            ".claude/protect.d/typescript.txt",
        ):
            self.assertTrue((self.project / rel).is_file(), rel)
        # The project file is the project's own: apply never writes it.
        self.assertFalse((self.project / PROJECT_FILE).exists())
        # The kit no longer ships a Node version file: CI reads the project's own, else uses Node 22.
        self.assertFalse((self.project / ".nvmrc").exists())
        self.assertFalse((self.project / "scripts/check.d/20-typescript.sh").exists())
        record = json.loads((self.project / ".starter-kit.json").read_text())
        self.assertIn("typescript", record["layers"])
        self.assertIn(".oxlintrc.json", record["files"])

    def test_package_json_and_eslint_prettier_configs_are_left_alone(self):
        self.assertEqual(
            json.loads((self.project / "package.json").read_text()), PACKAGE_JSON
        )
        tmp, p = new_project()
        self.addCleanup(tmp.cleanup)
        (p / "eslint.config.mjs").write_text("export default [];\n")
        (p / ".prettierrc").write_text("{}\n")
        r = run_kit("apply", "--layer", "typescript", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((p / "eslint.config.mjs").read_text(), "export default [];\n")
        self.assertEqual((p / ".prettierrc").read_text(), "{}\n")

    def test_existing_config_with_other_content_is_not_overwritten(self):
        tmp, p = new_project()
        self.addCleanup(tmp.cleanup)
        (p / ".oxlintrc.json").write_text('{"rules": {}}\n')
        r = run_kit("apply", "--layer", "typescript", p)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".oxlintrc.json", r.stderr)
        self.assertEqual((p / ".oxlintrc.json").read_text(), '{"rules": {}}\n')
        self.assertFalse((p / ".oxfmtrc.json").exists())  # nothing was written

    def test_existing_identical_config_is_accepted(self):
        tmp, p = new_project()
        self.addCleanup(tmp.cleanup)
        shutil.copy(self.project / ".oxlintrc.json", p / ".oxlintrc.json")
        r = run_kit("apply", "--layer", "typescript", p)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_config_guard_protects_the_oxc_configs(self):
        for name in (".oxlintrc.json", KIT_RULES_FILE, PROJECT_FILE, ".oxfmtrc.json"):
            payload = {"tool_input": {"file_path": str(self.project / name)}}
            env = {"CLAUDE_PROJECT_DIR": str(self.project)}
            r = subprocess.run(
                ["bash", str(self.project / ".claude/hooks/config-guard.sh")],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                cwd=self.project,
                env={**os.environ, **env},
                check=False,
            )
            self.assertEqual(r.returncode, 2, name + r.stderr)

    def config(self, name):
        """A shipped config as a dict: the // comment lines stripped, the rest parsed as JSON."""
        lines = (self.project / name).read_text().splitlines()
        return json.loads(
            "\n".join(ln for ln in lines if not ln.lstrip().startswith("//"))
        )

    def test_root_config_extends_the_kit_rules_then_the_optional_project_file(self):
        cfg = self.config(".oxlintrc.json")
        self.assertEqual(cfg["extends"], [f"./{KIT_RULES_FILE}", f"./{PROJECT_FILE}"])
        self.assertNotIn(
            "rules", cfg
        )  # ignored folders stay here: oxlint drops them from an extended file
        self.assertIn("ignorePatterns", cfg)
        self.assertNotIn("ignorePatterns", self.config(KIT_RULES_FILE))

    def test_lint_config_is_the_monorepos_finished_rule_set(self):
        cfg = self.config(KIT_RULES_FILE)
        self.assertEqual(cfg["categories"], {"correctness": "off"})
        self.assertTrue(cfg["options"]["typeAware"])
        self.assertEqual(cfg["plugins"], ["typescript", "import"])
        rules = cfg["rules"]
        self.assertGreaterEqual(len(rules), 100)
        for rule in (
            "typescript/no-explicit-any",
            "typescript/no-floating-promises",
            "typescript/no-unsafe-assignment",
            "typescript/unbound-method",
            "typescript/no-unnecessary-condition",
            "no-debugger",
            "prefer-const",
            "import/consistent-type-specifier-style",
        ):
            self.assertIn(rule, rules)
        # The React overrides are scoped to component files and carry the hooks rules.
        react = [o for o in cfg["overrides"] if "react" in o.get("plugins", [])]
        self.assertTrue(react)
        self.assertEqual(sorted(react[0]["files"]), ["**/*.jsx", "**/*.tsx"])
        self.assertEqual(react[0]["rules"]["react/rules-of-hooks"], "error")

    def test_lint_config_holds_nothing_specific_to_the_monorepo(self):
        text = (self.project / KIT_RULES_FILE).read_text() + (
            self.project / ".oxlintrc.json"
        ).read_text()
        for word in (
            "apps/",
            "packages/",
            "acme",
            "deals",
            "supabase",
            "tracker",
            "nextjs/",
        ):
            self.assertNotIn(word, text)
        cfg = self.config(KIT_RULES_FILE)
        # The unbound-method override for tests stays (it is the kit's own, measured on a real project).
        off = [
            o["files"]
            for o in cfg["overrides"]
            if o["rules"].get("typescript/unbound-method") == "off"
        ]
        self.assertTrue(any("**/*.test.*" in f for f in off))

    def test_lint_and_format_share_the_build_folder_ignores(self):
        lint = self.config(".oxlintrc.json")["ignorePatterns"]
        fmt = self.config(".oxfmtrc.json")["ignorePatterns"]
        self.assertTrue(set(fmt) <= set(lint))
        for name in (".wrangler", ".open-next", ".turbo", ".next", "node_modules"):
            self.assertTrue(any(name in pat for pat in fmt), name)

    def test_lint_skips_what_the_monorepo_skips(self):
        # Config files and non-.ts/.tsx sources sit outside a tsconfig, so the type-aware rules misfire on them.
        lint = self.config(".oxlintrc.json")["ignorePatterns"]
        self.assertIn("**/*.config.*", lint)
        self.assertIn("**/*.{jsx,mjs,cjs,mts,cts}", lint)

    def test_never_overwritten_comment_matches_apply(self):
        text = (self.project / KIT_RULES_FILE).read_text()
        self.assertNotIn("never overwritten", text)
        self.assertIn("--force", text)


class TestPackageManagerDetection(unittest.TestCase):
    def setUp(self):
        self.tmp, self.project = new_project()
        self.addCleanup(self.tmp.cleanup)
        r = run_kit("apply", "--layer", "typescript", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)

    def detect(self, *lockfiles):
        for f in self.project.glob("*.lock*"):
            f.unlink()
        for name in ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lock"):
            (self.project / name).unlink(missing_ok=True)
        for name in lockfiles:
            (self.project / name).write_text("x\n")
        return run_script(self.project, "bash", "scripts/ts-pm.sh", "detect")

    def test_each_lockfile_names_its_package_manager(self):
        for lock, pm in (
            ("pnpm-lock.yaml", "pnpm"),
            ("package-lock.json", "npm"),
            ("yarn.lock", "yarn"),
            ("bun.lock", "bun"),
            ("bun.lockb", "bun"),
        ):
            r = self.detect(lock)
            self.assertEqual(r.returncode, 0, lock + r.stderr)
            self.assertEqual(r.stdout.strip(), pm, lock)

    def test_no_lockfile_fails_and_says_to_commit_one(self):
        r = self.detect()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("lockfile", r.stderr)

    def test_two_lockfiles_pick_pnpm_first(self):
        self.assertEqual(
            self.detect("package-lock.json", "pnpm-lock.yaml").stdout.strip(), "pnpm"
        )

    def test_install_is_frozen_for_every_manager(self):
        shim = self.project / "shims"
        for pm in ("pnpm", "npm", "yarn", "bun"):
            write_exec(shim / pm, '#!/usr/bin/env bash\necho "$(basename "$0") $*"\n')
        cases = [
            (("pnpm-lock.yaml",), "pnpm install --frozen-lockfile"),
            (("package-lock.json",), "npm ci"),
            (("bun.lock",), "bun install --frozen-lockfile"),
            (("yarn.lock",), "yarn install --frozen-lockfile"),
        ]
        for locks, expected in cases:
            self.detect(*locks)
            r = run_script(
                self.project,
                "bash",
                "scripts/ts-pm.sh",
                "install",
                path_prepend=shim,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), expected)
        # Yarn 2 and newer is told apart by the __metadata block in its lockfile.
        self.detect()
        (self.project / "yarn.lock").write_text("__metadata:\n  version: 8\n")
        r = run_script(
            self.project, "bash", "scripts/ts-pm.sh", "install", path_prepend=shim
        )
        self.assertEqual(r.stdout.strip(), "yarn install --immutable")

    def test_one_off_runner_per_manager(self):
        shim = self.project / "shims"
        for pm in ("pnpm", "npx", "yarn", "bunx"):
            write_exec(shim / pm, '#!/usr/bin/env bash\necho "$(basename "$0") $*"\n')
        script = 'source scripts/ts-pm.sh; PM=$(ts_detect_pm); ts_oneoff "$PM" tool a@1 b@2 -- --x'
        cases = [
            (("pnpm-lock.yaml",), "pnpm --package=a@1 --package=b@2 dlx tool --x"),
            (("package-lock.json",), "npx -y -p a@1 -p b@2 tool --x"),
            (("yarn.lock",), "npx -y -p a@1 -p b@2 tool --x"),
            (("bun.lock",), "npx -y -p a@1 -p b@2 tool --x"),
        ]
        for locks, expected in cases:
            self.detect(*locks)
            r = run_script(self.project, "bash", "-c", script, path_prepend=shim)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), expected)
        # One package: bun uses bunx, yarn 2+ uses yarn dlx.
        self.detect("bun.lock")
        r = run_script(
            self.project,
            "bash",
            "-c",
            "source scripts/ts-pm.sh; ts_oneoff bun tool a@1 --",
            path_prepend=shim,
        )
        self.assertEqual(r.stdout.strip(), "bunx -p a@1 tool")
        (self.project / "yarn.lock").write_text("__metadata:\n  version: 8\n")
        r = run_script(
            self.project,
            "bash",
            "-c",
            "source scripts/ts-pm.sh; ts_oneoff yarn tool a@1 b@2 --",
            path_prepend=shim,
        )
        self.assertEqual(r.stdout.strip(), "yarn dlx -p a@1 -p b@2 tool")


class TestTypeScriptCheck(TypeScriptProject):
    def test_healthy_project_passes_and_runs_every_step(self):
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("CHECK PASSED", c.stdout)
        for step in ("typecheck", "lint", "format", "tests"):
            self.assertIn(f"-- {step}", out)

    def test_type_error_fails_and_other_steps_still_run(self):
        c, out = self.check(FAKE_TSC="typeerror", FAKE_OXLINT="lint")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("TS2322", out)
        self.assertIn("no-debugger", out)  # lint ran after typecheck failed
        self.assertIn("-- format", out)
        self.assertIn("CHECK FAILED: scripts/check.d/20-ts-typecheck.sh", out)
        self.assertIn("CHECK FAILED: scripts/check.d/21-ts-lint.sh", out)
        self.assertNotIn("CHECK FAILED: scripts/check.d/22-ts-format.sh", out)

    def test_format_violation_fails(self):
        c, out = self.check(FAKE_OXFMT="bad")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("Format issues found", out)
        self.assertIn("CHECK FAILED: scripts/check.d/22-ts-format.sh", out)

    def test_test_script_runs_through_the_package_manager(self):
        shim = self.project / "shims"
        write_exec(shim / "npm", '#!/usr/bin/env bash\necho "npm $*"\n')
        pkg = dict(PACKAGE_JSON, scripts={"test": "vitest run --silent"})
        (self.project / "package.json").write_text(json.dumps(pkg))
        c = run_script(
            self.project,
            "bash",
            "scripts/check.sh",
            path_prepend=shim,
        )
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertIn("npm run test", c.stdout)

    def test_npm_init_placeholder_test_script_counts_as_no_tests(self):
        pkg = dict(
            PACKAGE_JSON,
            scripts={"test": 'echo "Error: no test specified" && exit 1'},
        )
        (self.project / "package.json").write_text(json.dumps(pkg))
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("no tests configured", out)

    def test_vitest_config_without_test_script_runs_vitest(self):
        (self.project / "vitest.config.ts").write_text("export default {};\n")
        write_exec(self.project / "node_modules/.bin/vitest", FAKE_VITEST)
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("vitest run", out)
        c, out = self.check(FAKE_VITEST="fail")
        self.assertNotEqual(c.returncode, 0, out)

    def test_missing_node_modules_says_to_install(self):
        shutil.rmtree(self.project / "node_modules")
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("npm ci", out)

    def test_missing_tsconfig_fails_with_a_hint(self):
        (self.project / "tsconfig.json").unlink()
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("tsconfig.json", out)


class TestCanaries(TypeScriptProject):
    def test_typecheck_canary_fails_when_the_checker_reports_nothing(self):
        c, out = self.check(FAKE_TSC="blind")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("CANARY FAILED", out)
        self.assertIn("typecheck", out)

    def test_typecheck_fails_when_no_source_file_is_checked(self):
        c, out = self.check(FAKE_TSC="zero")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("zero source files", out)

    def test_lint_canary_fails_when_rules_are_off(self):
        c, out = self.check(FAKE_OXLINT="blind")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("CANARY FAILED", out)
        self.assertIn("no-debugger", out)

    def test_lint_canary_fails_when_a_planted_rule_is_missing(self):
        c, out = self.check(FAKE_OXLINT="norule")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("CANARY FAILED", out)

    def test_lint_canary_fails_when_the_planted_findings_are_only_warnings(self):
        c, out = self.check(FAKE_OXLINT="warn")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("CANARY FAILED", out)
        self.assertIn("error", out)

    def test_lint_fails_when_the_file_count_cannot_be_read(self):
        c, out = self.check(FAKE_OXLINT="unreadable")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("cannot be trusted", out)

    def test_format_fails_when_the_file_count_cannot_be_read(self):
        c, out = self.check(FAKE_OXFMT="unreadable")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("cannot be trusted", out)

    def test_tsconfig_parse_error_is_reported_as_that_error(self):
        c, out = self.check(FAKE_TSC="parseerror")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("TS1136", out)
        self.assertNotIn("zero source files", out)

    def test_tsconfig_with_references_is_built_with_tsc_b(self):
        c, out = self.check(FAKE_TSC="refs")
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("tsc -b ran", out)
        self.assertNotIn("zero source files", out)

    def test_type_error_in_a_referenced_project_fails(self):
        c, out = self.check(FAKE_TSC="refs-typeerror")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("TS2322", out)
        self.assertIn("CHECK FAILED: scripts/check.d/20-ts-typecheck.sh", out)

    def test_lint_fails_when_it_checked_zero_files(self):
        c, out = self.check(FAKE_OXLINT="zero")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("zero files", out)

    def test_format_fails_when_it_checked_zero_files(self):
        c, out = self.check(FAKE_OXFMT="zero")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("zero files", out)

    def test_canary_leaves_no_files_in_the_project(self):
        before = sorted(p.name for p in self.project.iterdir())
        self.check()
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), before)


class TestWorkflow(TypeScriptProject):
    def setUp(self):
        super().setUp()
        self.text = (self.project / ".github/workflows/check.yml").read_text()
        self.check_job = self.text.split("  gitleaks:")[0]

    def test_node_version_comes_from_nvmrc_then_node_version_then_22(self):
        steps = re.split(r"(?m)^      - ", self.text)
        node_steps = [st for st in steps if "actions/setup-node@" in st]
        self.assertEqual(len(node_steps), 3, node_steps)
        by_file = {}
        for st in node_steps:
            for key in (
                "node-version-file: .nvmrc",
                "node-version-file: .node-version",
                "node-version: 22",
            ):
                if key in st:
                    by_file[key] = st
        self.assertEqual(len(by_file), 3)
        self.assertIn("hashFiles('.nvmrc') != ''", by_file["node-version-file: .nvmrc"])
        node_version = by_file["node-version-file: .node-version"]
        self.assertIn("hashFiles('.nvmrc') == ''", node_version)
        self.assertIn("hashFiles('.node-version') != ''", node_version)
        fallback = by_file["node-version: 22"]
        self.assertIn("hashFiles('.nvmrc') == ''", fallback)
        self.assertIn("hashFiles('.node-version') == ''", fallback)

    def test_every_setup_node_step_turns_the_package_manager_cache_off(self):
        steps = [
            st
            for st in re.split(r"(?m)^      - ", self.text)
            if "actions/setup-node@" in st
        ]
        self.assertEqual(len(steps), 3)
        for st in steps:
            self.assertIn("package-manager-cache: false", st)
        self.assertNotRegex(self.text, r"(?m)^\s+cache:")

    def test_actions_are_pinned_to_commit_shas_with_a_version_comment(self):
        for action in ("actions/setup-node", "oven-sh/setup-bun"):
            uses = re.findall(rf"uses: {re.escape(action)}@(\S+)(.*)", self.text)
            self.assertTrue(uses, action)
            for ref, rest in uses:
                self.assertRegex(ref, r"^[0-9a-f]{40}$", action)
                self.assertRegex(rest, r"#\s*v\d+\.\d+\.\d+", action)

    def test_dependencies_install_from_the_lockfile_before_the_check(self):
        self.assertIn("bash scripts/ts-pm.sh install", self.text)
        self.assertLess(
            self.text.index("setup-node"), self.text.index("scripts/ts-pm.sh install")
        )
        self.assertLess(
            self.text.index("scripts/ts-pm.sh install"),
            self.text.index("run: bash scripts/check.sh"),
        )

    def test_bun_is_set_up_only_when_the_project_has_a_bun_lockfile(self):
        self.assertIn("oven-sh/setup-bun@", self.text)
        self.assertIn("hashFiles('bun.lock', 'bun.lockb')", self.text)

    def test_corepack_is_enabled_for_pnpm_and_yarn(self):
        self.assertIn("corepack enable", self.text)


# A stand-in for npx that logs its arguments and runs the fake tool the pinned package would have provided.
STAND_IN_NPX = r"""#!/usr/bin/env bash
echo "npx $*" >> "$NPX_LOG"
args=("$@")
i=0
[ "${args[0]:-}" = -y ] && i=1
while [ "${args[$i]:-}" = -p ]; do i=$((i + 2)); done
exec "$FAKE_BIN_DIR/${args[$i]}" "${args[@]:$((i + 1))}"
"""

COMMON_SH = KIT_ROOT / "layers/typescript/files/scripts/ts-common.sh"


def pinned_versions():
    """{VARIABLE: version} read from the shared helper, so a Renovate bump does not break these tests."""
    return dict(re.findall(r"(?m)^([A-Z_]+_VERSION)=(\S+)$", COMMON_SH.read_text()))


class TestPinnedPath(TypeScriptProject):
    """No tool installed in node_modules: every step goes through the package manager's one-off runner, with the pinned versions."""

    fake_tools = False

    def setUp(self):
        super().setUp()
        self.shims = self.project / "shims"
        self.fakes = self.project / "fakes"
        self.log = self.project / "npx.log"
        write_exec(self.shims / "npx", STAND_IN_NPX)
        write_exec(self.fakes / "tsc", FAKE_TSC)
        write_exec(self.fakes / "oxlint", FAKE_OXLINT)
        write_exec(self.fakes / "oxfmt", FAKE_OXFMT)
        self.v = pinned_versions()

    def run_check(self, **env):
        env.update(NPX_LOG=str(self.log), FAKE_BIN_DIR=str(self.fakes))
        c = run_script(
            self.project, "bash", "scripts/check.sh", env=env, path_prepend=self.shims
        )
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return c, c.stdout + c.stderr, calls

    def install(self, rel, text):
        write_exec(self.project / "node_modules" / rel, text)

    def test_each_tool_runs_pinned_through_the_one_off_runner(self):
        c, out, calls = self.run_check()
        self.assertEqual(c.returncode, 0, out)
        v = self.v
        self.assertTrue(
            any(
                call.startswith(f"npx -y -p typescript@{v['TYPESCRIPT_VERSION']} tsc ")
                for call in calls
            ),
            calls,
        )
        self.assertTrue(
            any(
                call.startswith(
                    f"npx -y -p oxlint@{v['OXLINT_VERSION']} -p oxlint-tsgolint@{v['OXLINT_TSGOLINT_VERSION']} oxlint "
                )
                for call in calls
            ),
            calls,
        )
        self.assertTrue(
            any(
                call.startswith(f"npx -y -p oxfmt@{v['OXFMT_VERSION']} oxfmt ")
                for call in calls
            ),
            calls,
        )

    def test_a_project_tsc_older_than_7_is_not_used(self):
        self.install(".bin/tsc", FAKE_TSC)
        c, out, calls = self.run_check(FAKE_TSC_VERSION="5.9.2")
        self.assertEqual(c.returncode, 0, out)
        self.assertTrue(
            any(
                f"typescript@{self.v['TYPESCRIPT_VERSION']} tsc" in call
                for call in calls
            ),
            calls,
        )
        self.assertRegex(
            out, rf"tsc {re.escape(self.v['TYPESCRIPT_VERSION'])} \(pinned"
        )
        self.assertIn("5.9.2", out)

    def test_a_project_tsc_7_or_newer_is_used(self):
        self.install(".bin/tsc", FAKE_TSC)
        c, out, calls = self.run_check(FAKE_TSC_VERSION="7.1.0")
        self.assertEqual(c.returncode, 0, out)
        self.assertFalse(any(" tsc " in call for call in calls), calls)
        self.assertRegex(out, r"tsc 7\.1\.0 \(the project's own")

    def test_a_typescript7_alias_package_is_used_when_the_default_tsc_is_older(self):
        self.install("typescript7/bin/tsc", FAKE_TSC)
        (self.project / "node_modules/typescript7/package.json").write_text(
            json.dumps({"name": "typescript", "version": "7.0.2"})
        )
        # .bin/tsc reports 5.x; the alias reports 7.0.2 (FAKE_TSC_VERSION default).
        write_exec(
            self.project / "node_modules/.bin/tsc",
            FAKE_TSC.replace("${FAKE_TSC_VERSION:-7.0.2}", "5.9.2"),
        )
        c, out, calls = self.run_check()
        self.assertEqual(c.returncode, 0, out)
        self.assertFalse(any(" tsc " in call for call in calls), calls)
        self.assertIn("typescript7", out)

    def test_project_oxlint_is_used_only_with_its_type_aware_partner_installed(self):
        self.install(".bin/oxlint", FAKE_OXLINT)
        (self.project / "node_modules/oxlint").mkdir()
        c, out, calls = self.run_check()
        self.assertEqual(c.returncode, 0, out)
        self.assertTrue(any("-p oxlint-tsgolint@" in call for call in calls), calls)
        (self.project / "node_modules/oxlint-tsgolint").mkdir()
        self.log.unlink()
        c, out, calls = self.run_check()
        self.assertEqual(c.returncode, 0, out)
        self.assertFalse(any(" oxlint " in call for call in calls), calls)

    def test_project_oxfmt_is_used_when_installed(self):
        self.install(".bin/oxfmt", FAKE_OXFMT)
        (self.project / "node_modules/oxfmt").mkdir()
        c, out, calls = self.run_check()
        self.assertEqual(c.returncode, 0, out)
        self.assertFalse(any(" oxfmt " in call for call in calls), calls)


class TestRenovate(unittest.TestCase):
    def test_a_regex_manager_reads_every_version_pin_in_the_helper(self):
        cfg = json.loads((KIT_ROOT / "layers/base/files/renovate.json").read_text())
        managers = [m for m in cfg["customManagers"] if m["customType"] == "regex"]
        self.assertEqual(len(managers), 1)
        manager = managers[0]
        patterns = [re.compile(p.strip("/")) for p in manager["managerFilePatterns"]]
        for path in (
            "scripts/ts-common.sh",
            "layers/typescript/files/scripts/ts-common.sh",
        ):
            self.assertTrue(any(p.search(path) for p in patterns), path)
        # Renovate's named groups are (?<name>...); Python spells them (?P<name>...).
        regex = re.compile(
            re.sub(r"\(\?<([A-Za-z])", r"(?P<\1", manager["matchStrings"][0])
        )
        found = {
            m["depName"]: m["currentValue"]
            for m in regex.finditer(COMMON_SH.read_text())
        }
        v = pinned_versions()
        self.assertEqual(
            found,
            {
                "typescript": v["TYPESCRIPT_VERSION"],
                "oxlint": v["OXLINT_VERSION"],
                "oxlint-tsgolint": v["OXLINT_TSGOLINT_VERSION"],
                "oxfmt": v["OXFMT_VERSION"],
            },
        )
        self.assertEqual(manager["datasourceTemplate"], "npm")


class TestReadme(unittest.TestCase):
    def setUp(self):
        self.text = (KIT_ROOT / "README.md").read_text()
        self.changelog = (KIT_ROOT / "CHANGELOG.md").read_text()

    def test_documents_bun_lockb_node_version_files_and_drafts(self):
        self.assertIn("bun.lockb", self.text)
        self.assertIn(".node-version", self.text)
        self.assertNotIn(".nvmrc` (`22`)", self.text)
        self.assertRegex(self.text, r"(?i)drafts? while (you )?work")

    def test_says_exactly_which_compiler_runs(self):
        for text in (self.text, self.changelog):
            self.assertIn("TypeScript 7", text)
        self.assertIn("node_modules/.bin/tsc", self.text)
        self.assertIn("tsc -b", self.text)


class TestLintAdoptionDocs(unittest.TestCase):
    def setUp(self):
        self.text = (KIT_ROOT / "README.md").read_text()
        self.changelog = (KIT_ROOT / "CHANGELOG.md").read_text()

    def test_readme_explains_the_rule_groups_the_cap_and_the_lower_cap_command(self):
        for needle in (
            "no-floating-promises",
            "no-misused-promises",
            "await-thenable",
            "no-debugger",
            "no-non-null-assertion",
            PROJECT_FILE,
            "maxWarnings",
            LOWER_CAP_COMMAND,
            "23-ts-cap-raise.sh",
            "base branch",
        ):
            self.assertIn(needle, self.text)

    def test_readme_gives_the_adoption_recipe_in_order(self):
        recipe = self.text[self.text.index("Adopting the kit in an existing project") :]
        positions = [
            recipe.index(w)
            for w in (
                "Apply the kit",
                "Mechanical commit",
                "linter in fix mode",
                "formatter in write mode",
                "repair by hand",
                LOWER_CAP_COMMAND,
                "remaining error",
                "push and open the pull request",
            )
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("void", recipe)
        # The pre-push hook runs the full check, so a lint-red mechanical pull request could never be pushed.
        self.assertIn("One pull request per project", recipe)

    def test_changelog_names_the_cap_check_under_0_3_0(self):
        section = self.changelog.split("## 0.2.0")[0]
        self.assertIn("23-ts-cap-raise.sh", section)
        self.assertIn(PROJECT_FILE, section)


@unittest.skipUnless(shutil.which("npx") and shutil.which("node"), "needs node and npx")
class TestRealTools(TypeScriptProject):
    """The kit's pinned tools, fetched by npx, on a real project: no fakes, no edit to package.json."""

    fake_tools = False

    def plant(self, name, text):
        (self.project / name).write_text(text)

    def test_clean_project_passes(self):
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("CHECK PASSED", c.stdout)

    def test_each_kind_of_mistake_fails_the_check(self):
        self.plant("src/type.ts", 'export const t: number = "text";\n')
        self.plant("src/lint.ts", "debugger;\n")
        self.plant("src/fmt.ts", "export const   f =  1\n")
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("TS2322", out)
        self.assertIn("no-debugger", out)
        self.assertIn("src/fmt.ts", out)
        for script in ("20-ts-typecheck", "21-ts-lint", "22-ts-format"):
            self.assertIn(f"CHECK FAILED: scripts/check.d/{script}.sh", out)

    def test_type_aware_rule_fires_through_the_one_off_runner(self):
        self.plant("src/p.ts", "async function f() {\n  return 1;\n}\nf();\n")
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("no-floating-promises", out)

    def test_the_pinned_typescript_7_runs_and_is_named(self):
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertRegex(out, r"tsc 7\.\d+\.\d+ \(pinned")

    def test_project_references_are_built_with_tsc_b(self):
        (self.project / "tsconfig.json").write_text(
            json.dumps({"files": [], "references": [{"path": "./tsconfig.app.json"}]})
        )
        app = dict(TSCONFIG)
        app["compilerOptions"] = dict(
            TSCONFIG["compilerOptions"],
            composite=True,
            tsBuildInfoFile="./node_modules/.tmp/app.tsbuildinfo",
        )
        (self.project / "tsconfig.app.json").write_text(json.dumps(app))
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.plant("src/type.ts", 'export const t: number = "text";\n')
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("TS2322", out)
        self.assertNotIn("zero source files", out)

    def test_broken_tsconfig_is_reported_as_its_own_error(self):
        self.plant("tsconfig.json", "{ bad")
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertRegex(out, r"error TS\d+")
        self.assertNotIn("zero source files", out)

    def test_package_json_is_untouched(self):
        self.check()
        self.assertEqual(
            json.loads((self.project / "package.json").read_text()), PACKAGE_JSON
        )


PROMISE_BUG = "async function f() {\n  return 1;\n}\nf();\n"
NON_NULL = (
    'const m = new Map<string, number>();\nexport const v: number = m.get("a")!;\n'
)
UNSAFE = "export const u: number = JSON.parse('1');\n"


@unittest.skipUnless(shutil.which("npx") and shutil.which("node"), "needs node and npx")
class TestLintAdoption(TypeScriptProject):
    """The locked rules, the project file and the warning cap, on the kit's pinned oxlint through npx."""

    fake_tools = False

    def plant(self, name, text):
        p = self.project / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def project_file(self, rules=None, cap=None, overrides=None):
        cfg = {}
        if rules is not None:
            cfg["rules"] = rules
        if overrides is not None:
            cfg["overrides"] = overrides
        if cap is not None:
            cfg["options"] = {CAP_KEY: cap}
        (self.project / PROJECT_FILE).write_text(json.dumps(cfg))

    def lint_fails(self):
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("CHECK FAILED: scripts/check.d/21-ts-lint.sh", out)
        return out

    def test_no_project_file_means_the_full_rule_set(self):
        self.plant("src/n.ts", NON_NULL)
        out = self.lint_fails()
        self.assertIn("no-non-null-assertion", out)
        self.assertNotIn(LOWER_CAP_COMMAND, out)

    def test_a_locked_rule_lowered_in_the_project_file_fails_naming_it(self):
        for rule in (
            "typescript/no-floating-promises",
            "typescript/no-misused-promises",
            "typescript/await-thenable",
            "no-debugger",
        ):
            for level in ("warn", "off"):
                self.project_file(rules={rule: level})
                out = self.lint_fails()
                self.assertIn(f"locked rule {rule}", out, (rule, level))

    def test_a_locked_rule_lowered_in_a_project_override_fails_naming_it(self):
        self.project_file(
            overrides=[
                {
                    "files": ["public/**"],
                    "rules": {"typescript/no-floating-promises": "off"},
                }
            ]
        )
        self.assertIn("locked rule typescript/no-floating-promises", self.lint_fails())

    def test_a_rule_outside_the_allowed_list_cannot_be_lowered(self):
        self.project_file(rules={"typescript/no-explicit-any": "warn"}, cap=5)
        self.assertIn("typescript/no-explicit-any", self.lint_fails())
        self.project_file(rules={"prefer-const": "off"})
        self.assertIn("prefer-const", self.lint_fails())

    def test_an_allowed_rule_lowered_to_warn_passes_at_or_under_the_cap(self):
        self.plant("src/n.ts", NON_NULL)
        self.project_file(rules={"typescript/no-non-null-assertion": "warn"}, cap=1)
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("CHECK PASSED", c.stdout)
        self.assertNotIn(LOWER_CAP_COMMAND, out)  # at the cap: nothing to lower

    def test_warnings_over_the_cap_fail_with_the_cap_and_the_count(self):
        self.plant("src/n.ts", NON_NULL)
        self.project_file(rules={"typescript/no-non-null-assertion": "warn"}, cap=0)
        out = self.lint_fails()
        self.assertIn("over the cap of 0", out)
        self.assertIn("1 warning", out)
        self.assertIn(PROJECT_FILE, out)

    def test_a_count_below_the_cap_prints_the_lower_cap_command(self):
        self.plant("src/n.ts", NON_NULL)
        self.project_file(rules={"typescript/no-non-null-assertion": "warn"}, cap=3)
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("below the cap of 3", out)
        self.assertIn(LOWER_CAP_COMMAND, out)

    def test_browser_files_override_skips_unsafe_rules_but_keeps_promise_rules(self):
        self.plant("src/browser/b.ts", UNSAFE + PROMISE_BUG)
        c, out = self.check()  # control: with no override both fire
        self.assertIn("no-unsafe-assignment", out)
        self.assertIn("no-floating-promises", out)
        self.project_file(
            overrides=[
                {
                    "files": ["src/browser/**"],
                    "rules": {
                        "typescript/no-unsafe-assignment": "off",
                        "no-var": "off",
                    },
                }
            ]
        )
        out = self.lint_fails()
        self.assertNotIn("no-unsafe-assignment", out)
        self.assertIn("no-floating-promises", out)
        self.assertNotIn("locked rule", out)  # lowering an allowed rule is no violation

    def test_the_lint_canary_still_runs_under_a_project_file(self):
        self.project_file(rules={"typescript/no-non-null-assertion": "warn"}, cap=0)
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertNotIn("CANARY FAILED", out)


@unittest.skipUnless(shutil.which("npx") and shutil.which("node"), "needs node and npx")
class TestLowerCap(TypeScriptProject):
    """The lower-cap command (scripts/lint-cap.sh) on the kit's pinned oxlint through npx."""

    fake_tools = False

    def plant(self, name, text):
        p = self.project / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def lint_cap(self):
        c = run_script(self.project, "bash", "scripts/lint-cap.sh")
        return c, c.stdout + c.stderr

    def project_cfg(self):
        text = (self.project / PROJECT_FILE).read_text()
        return json.loads(re.sub(r"(?m)^\s*//.*$", "", text))

    def write_cfg(self, cfg):
        (self.project / PROJECT_FILE).write_text(json.dumps(cfg))

    def test_first_run_warns_the_allowed_rules_that_fire_and_sets_the_cap(self):
        self.plant("src/n.ts", NON_NULL + UNSAFE.replace("'1'", '"1"'))
        c, out = self.lint_cap()
        self.assertEqual(c.returncode, 0, out)
        cfg = self.project_cfg()
        self.assertEqual(
            cfg["rules"],
            {
                "typescript/no-non-null-assertion": "warn",
                "typescript/no-unsafe-assignment": "warn",
            },
        )
        self.assertEqual(cfg["options"][CAP_KEY], 2)
        c, out = self.check()  # the check is green, at the cap
        self.assertEqual(c.returncode, 0, out)
        self.assertNotIn(LOWER_CAP_COMMAND, out)

    def test_a_project_without_warnings_gets_no_project_file(self):
        c, out = self.lint_cap()
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("no project file is needed", out)
        self.assertFalse((self.project / PROJECT_FILE).exists())

    def test_a_second_run_changes_nothing(self):
        self.plant("src/n.ts", NON_NULL)
        self.lint_cap()
        first = (self.project / PROJECT_FILE).read_text()
        c, out = self.lint_cap()
        self.assertEqual(c.returncode, 0, out)
        self.assertEqual((self.project / PROJECT_FILE).read_text(), first)

    def test_it_lowers_an_existing_cap_and_drops_rules_that_no_longer_fire(self):
        self.plant("src/n.ts", NON_NULL)
        self.write_cfg(
            {
                "rules": {
                    "typescript/no-non-null-assertion": "warn",
                    "typescript/no-unsafe-assignment": "warn",
                },
                "options": {CAP_KEY: 9},
            }
        )
        c, out = self.lint_cap()
        self.assertEqual(c.returncode, 0, out)
        cfg = self.project_cfg()
        self.assertEqual(cfg["rules"], {"typescript/no-non-null-assertion": "warn"})
        self.assertEqual(cfg["options"][CAP_KEY], 1)
        self.assertIn("was 9", out)

    def test_it_refuses_to_raise_an_existing_cap(self):
        self.plant(
            "src/n.ts",
            NON_NULL
            + NON_NULL.replace("const m", "const m2")
            .replace("m.get", "m2.get")
            .replace("v:", "v2:"),
        )
        self.write_cfg(
            {
                "rules": {"typescript/no-non-null-assertion": "warn"},
                "options": {CAP_KEY: 1},
            }
        )
        before = (self.project / PROJECT_FILE).read_text()
        c, out = self.lint_cap()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("above the cap of 1", out)
        self.assertEqual((self.project / PROJECT_FILE).read_text(), before)

    def test_a_new_allowed_rule_that_fires_cannot_raise_a_zero_cap(self):
        self.plant("src/n.ts", NON_NULL)
        self.write_cfg({"options": {CAP_KEY: 0}})
        c, out = self.lint_cap()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertEqual(self.project_cfg(), {"options": {CAP_KEY: 0}})

    def test_files_outside_the_tsconfig_skip_unsafe_rules_but_keep_promise_rules(self):
        self.plant(
            "public/b.js",
            PROMISE_BUG + "var x = JSON.parse('1');\nexport const y = x.foo;\n",
        )
        self.plant(
            "public/sub/c.js", "var z = JSON.parse('1');\nexport const w = z.bar;\n"
        )  # nested: one glob covers both
        self.plant("src/n.ts", UNSAFE.replace("'1'", '"1"'))
        c, out = self.lint_cap()
        self.assertEqual(c.returncode, 0, out)
        cfg = self.project_cfg()
        # The typed file's unsafe-assignment and the browser file's require-await (not in the skipped family) warn.
        self.assertEqual(
            cfg["rules"],
            {
                "typescript/no-unsafe-assignment": "warn",
                "typescript/require-await": "warn",
            },
        )
        self.assertEqual(cfg["options"][CAP_KEY], 2)
        override = cfg["overrides"][0]
        self.assertEqual(override["files"], ["public/**"])
        self.assertEqual(override["rules"]["no-var"], "off")
        self.assertEqual(override["rules"]["typescript/no-unsafe-member-access"], "off")
        self.assertNotIn("typescript/no-floating-promises", override["rules"])
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertRegex(
            out,
            r"public/b\.js.*no-floating-promises|no-floating-promises[\s\S]*public/b\.js",
        )
        self.assertNotIn("no-unsafe-member-access", out)
        self.assertNotIn("no-var", out)
        self.assertNotIn("locked rule", out)
        self.assertIn("errors left, by rule", self.lint_cap()[1])

    def test_it_measures_the_same_through_a_symlinked_folder(self):
        link = self.project.parent / (self.project.name + "-link")
        link.symlink_to(self.project, target_is_directory=True)
        self.addCleanup(link.unlink)
        self.plant("public/b.js", "var x = JSON.parse('1');\nexport const y = x.foo;\n")
        c = run_script(link, "bash", "scripts/lint-cap.sh", env={"PWD": str(link)})
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertNotIn(
            "typescript/no-unsafe-member-access", self.project_cfg().get("rules", {})
        )

    def test_locked_rule_findings_stay_errors_and_are_never_lowered(self):
        self.plant("src/p.ts", "Promise.resolve(1);\ndebugger;\n")
        c, out = self.lint_cap()
        self.assertEqual(c.returncode, 0, out)
        self.assertFalse((self.project / PROJECT_FILE).exists())
        self.assertIn("typescript/no-floating-promises", out)
        self.assertIn("no-debugger", out)


class TestCapRaiseCheck(TypeScriptProject):
    """CI on a pull request fails when the project file's cap is higher than on the base branch."""

    STEP = "scripts/check.d/23-ts-cap-raise.sh"

    def git(self, *args):
        r = subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            capture_output=True,
            text=True,
            cwd=self.project,
            env=clean_env(),
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def set_cap(self, cap):
        (self.project / PROJECT_FILE).write_text(
            json.dumps({"options": {"maxWarnings": cap}})
        )

    def land_on_base(self):
        """Commit the project as the base branch and push it to a local bare 'origin'."""
        remote = self.project.parent / (self.project.name + "-origin.git")
        subprocess.run(
            ["git", "init", "-q", "--bare", str(remote)], check=True, env=clean_env()
        )
        self.addCleanup(shutil.rmtree, remote, True)
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.git("branch", "-M", "main")
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "origin", "main")

    def in_pull_request(self, base="main"):
        r = run_script(self.project, "bash", self.STEP, env={"GITHUB_BASE_REF": base})
        return r, r.stdout + r.stderr

    def test_outside_a_pull_request_it_skips_cleanly(self):
        self.set_cap(5)
        r = run_script(self.project, "bash", self.STEP)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("skip", (r.stdout + r.stderr).lower())

    def test_a_project_file_new_in_the_pull_request_passes(self):
        self.land_on_base()
        self.set_cap(40)
        r, out = self.in_pull_request()
        self.assertEqual(r.returncode, 0, out)

    def test_a_raised_cap_fails_naming_both_caps(self):
        self.set_cap(10)
        self.land_on_base()
        self.set_cap(11)
        r, out = self.in_pull_request()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("11", out)
        self.assertIn("10", out)
        self.assertIn("maxWarnings", out)

    def test_an_equal_or_lower_cap_passes(self):
        self.set_cap(10)
        self.land_on_base()
        for cap in (10, 3):
            self.set_cap(cap)
            r, out = self.in_pull_request()
            self.assertEqual(r.returncode, 0, out)

    def test_deleting_the_project_file_or_its_cap_fails(self):
        self.set_cap(10)
        self.land_on_base()
        (self.project / PROJECT_FILE).write_text("{}\n")
        r, out = self.in_pull_request()
        self.assertNotEqual(r.returncode, 0, out)
        (self.project / PROJECT_FILE).unlink()
        r, out = self.in_pull_request()
        self.assertNotEqual(r.returncode, 0, out)

    def test_a_base_branch_that_cannot_be_fetched_fails(self):
        self.land_on_base()
        self.set_cap(5)
        r, out = self.in_pull_request(base="no-such-branch")
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("no-such-branch", out)

    def test_check_sh_runs_it_and_still_passes_outside_a_pull_request(self):
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("-- cap raise", out)
        self.assertIn("CHECK PASSED", c.stdout)


class TestKitUpdate(TypeScriptProject):
    def test_update_leaves_the_project_file_alone_and_updates_the_kit_rules_file(self):
        layers = self.project.parent / (self.project.name + "-layers")
        shutil.copytree(KIT_ROOT / "layers", layers)
        self.addCleanup(shutil.rmtree, layers, True)
        tmp, p = new_project()
        self.addCleanup(tmp.cleanup)
        r = run_kit("apply", "--layers-dir", layers, "--layer", "typescript", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        mine = '{"options": {"maxWarnings": 7}}\n'
        (p / PROJECT_FILE).write_text(mine)
        kit_file = layers / "typescript/files" / KIT_RULES_FILE
        kit_file.write_text(kit_file.read_text() + "\n// a later kit release\n")
        r = run_kit("update", "--layers-dir", layers, p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((p / PROJECT_FILE).read_text(), mine)
        self.assertIn("a later kit release", (p / KIT_RULES_FILE).read_text())
        record = json.loads((p / ".starter-kit.json").read_text())
        self.assertNotIn(PROJECT_FILE, record["files"])


if __name__ == "__main__":
    unittest.main()

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
            "scripts/check.d/30-ts-tests.sh",
            "scripts/ts-common.sh",
            "scripts/ts-pm.sh",
            ".oxlintrc.json",
            ".oxfmtrc.json",
            ".claude/protect.d/typescript.txt",
        ):
            self.assertTrue((self.project / rel).is_file(), rel)
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
        for name in (".oxlintrc.json", ".oxfmtrc.json"):
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

    def test_lint_config_is_the_monorepos_finished_rule_set(self):
        cfg = self.config(".oxlintrc.json")
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
        text = (self.project / ".oxlintrc.json").read_text()
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
        cfg = self.config(".oxlintrc.json")
        # The unbound-method override for tests stays (it is the kit's own, measured on a real project).
        off = [
            o["files"]
            for o in cfg["overrides"]
            if o["rules"].get("typescript/unbound-method") == "off"
        ]
        self.assertTrue(any("**/*.test.*" in f for f in off))

    def test_lint_and_format_share_one_ignore_list(self):
        lint = self.config(".oxlintrc.json")["ignorePatterns"]
        fmt = self.config(".oxfmtrc.json")["ignorePatterns"]
        self.assertEqual(lint, fmt)
        for name in (".wrangler", ".open-next", ".turbo", ".next", "node_modules"):
            self.assertTrue(any(name in pat for pat in lint), name)

    def test_never_overwritten_comment_matches_apply(self):
        text = (self.project / ".oxlintrc.json").read_text()
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


if __name__ == "__main__":
    unittest.main()

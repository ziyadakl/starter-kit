"""TypeScript layer behaviour: what apply writes, package-manager detection, the check steps and their canaries.

Most tests run against fake tools in node_modules/.bin, so they need no network. TestRealTools runs the
kit's pinned tools through npx and is skipped where npx is missing.
"""

import json
import os
import shutil
import stat
import subprocess
import unittest

from tests.helpers import new_project, run_kit

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
FAKE_TSC = r"""#!/usr/bin/env bash
mode="${FAKE_TSC:-ok}"
case " $* " in
  *" --version "*) echo "Version 7.0.2"; exit 0 ;;
  *" --listFilesOnly "*)
    [ "$mode" = zero ] && exit 0
    echo "$PWD/src/a.ts"; exit 0 ;;
  *" -p "*)
    [ "$mode" = blind ] && exit 0
    echo "canary.ts(1,7): error TS2322: Type 'string' is not assignable to type 'number'."; exit 2 ;;
esac
if [ "$mode" = typeerror ]; then echo "src/a.ts(1,7): error TS2322: planted"; exit 2; fi
exit 0
"""
FAKE_OXLINT = r"""#!/usr/bin/env bash
mode="${FAKE_OXLINT:-ok}"
for a in "$@"; do
  if [ -f "$a/canary.ts" ]; then
    case "$mode" in
      blind) echo "Found 0 warnings and 0 errors."; echo "Finished in 1ms on 1 file with 90 rules using 1 threads."; exit 0 ;;
      norule) echo "typescript(no-floating-promises): planted"; echo "Found 0 warnings and 1 errors."; echo "Finished in 1ms on 1 file with 90 rules using 1 threads."; exit 1 ;;
    esac
    echo "eslint(no-debugger): planted"; echo "typescript(no-floating-promises): planted"
    echo "Found 0 warnings and 2 errors."; echo "Finished in 1ms on 1 file with 90 rules using 1 threads."; exit 1
  fi
done
case "$mode" in
  zero) echo "Found 0 warnings and 0 errors."; echo "Finished in 1ms on 0 files with 90 rules using 1 threads."; exit 0 ;;
  lint) echo "src/a.ts:1:1: error eslint(no-debugger): planted"; echo "Found 0 warnings and 1 errors."; echo "Finished in 1ms on 1 file with 90 rules using 1 threads."; exit 1 ;;
esac
echo "Found 0 warnings and 0 errors."; echo "Finished in 1ms on 1 file with 90 rules using 1 threads."; exit 0
"""
FAKE_OXFMT = r"""#!/usr/bin/env bash
case "${FAKE_OXFMT:-ok}" in
  zero) echo "Finished in 1ms on 0 files using 1 threads."; exit 0 ;;
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
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
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
        (p / "src/a.ts").write_text("export const a: number = 1;\n")
        (p / ".gitignore").write_text("node_modules/\n")
        if self.fake_tools:
            bin_dir = p / "node_modules/.bin"
            write_exec(bin_dir / "tsc", FAKE_TSC)
            write_exec(bin_dir / "oxlint", FAKE_OXLINT)
            (p / "node_modules/oxlint-tsgolint").mkdir()
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
            "scripts/check.d/20-typescript.sh",
            "scripts/ts-pm.sh",
            ".oxlintrc.json",
            ".oxfmtrc.json",
            ".nvmrc",
            ".claude/protect.d/typescript.txt",
        ):
            self.assertTrue((self.project / rel).is_file(), rel)
        self.assertEqual((self.project / ".nvmrc").read_text().strip(), "22")
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
        (p / ".nvmrc").write_text("20\n")
        r = run_kit("apply", "--layer", "typescript", p)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".oxlintrc.json", r.stderr)
        self.assertIn(".nvmrc", r.stderr)
        self.assertEqual((p / ".oxlintrc.json").read_text(), '{"rules": {}}\n')
        self.assertEqual((p / ".nvmrc").read_text(), "20\n")
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

    def test_shipped_configs_are_valid_json_with_comments_stripped(self):
        for name in (".oxlintrc.json", ".oxfmtrc.json"):
            lines = (self.project / name).read_text().splitlines()
            body = "\n".join(ln for ln in lines if not ln.lstrip().startswith("//"))
            cfg = json.loads(body)
            if name == ".oxlintrc.json":
                off = [
                    o["files"]
                    for o in cfg["overrides"]
                    if o["rules"].get("typescript/unbound-method") == "off"
                ]
                self.assertTrue(any("**/*.test.*" in f for f in off))


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
        script = 'source scripts/ts-pm.sh; PM=$(ts_detect_pm); ts_oneoff "$PM" "a@1 b@2" tool --x'
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
            'source scripts/ts-pm.sh; ts_oneoff bun "a@1" tool',
            path_prepend=shim,
        )
        self.assertEqual(r.stdout.strip(), "bunx -p a@1 tool")
        (self.project / "yarn.lock").write_text("__metadata:\n  version: 8\n")
        r = run_script(
            self.project,
            "bash",
            "-c",
            'source scripts/ts-pm.sh; ts_oneoff yarn "a@1 b@2" tool',
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
        self.assertIn("CHECK FAILED: scripts/check.d/20-typescript.sh", out)
        self.assertRegex(out, r"failed: typecheck, lint")

    def test_format_violation_fails(self):
        c, out = self.check(FAKE_OXFMT="bad")
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("Format issues found", out)

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

    def test_node_comes_from_nvmrc_with_no_package_manager_cache(self):
        self.assertIn("actions/setup-node@", self.text)
        self.assertIn("node-version-file: .nvmrc", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s+cache:")
        self.assertNotIn("cache: pnpm", self.text)
        self.assertNotIn("cache: npm", self.text)

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
        self.assertIn("failed: typecheck, lint, format", out)

    def test_type_aware_rule_fires_through_the_one_off_runner(self):
        self.plant("src/p.ts", "async function f() {\n  return 1;\n}\nf();\n")
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("no-floating-promises", out)

    def test_package_json_is_untouched(self):
        self.check()
        self.assertEqual(
            json.loads((self.project / "package.json").read_text()), PACKAGE_JSON
        )


if __name__ == "__main__":
    unittest.main()

"""Lint adoption in the typescript layer: the locked and lowerable rules, the project file and its warning cap, the
lower-cap command (scripts/lint-cap.sh), the CI cap-raise step and the shared policy module (scripts/lint-policy.mjs).

The policy module's unit tests run on JSON fixtures (fast, no npx). The classes that run oxlint use the kit's pinned
tools through npx and are skipped where npx is missing.
"""

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import KIT_ROOT, clean_env
from tests.test_typescript_layer import (
    CAP_KEY,
    LOWER_CAP_COMMAND,
    PROJECT_FILE,
    TypeScriptProject,
    run_script,
)

SCRIPTS = KIT_ROOT / "layers/typescript/files/scripts"
POLICY = SCRIPTS / "lint-policy.mjs"
NODE = shutil.which("node") or "node"


def common_var(name):
    """A variable of ts-common.sh, the one copy of the policy's data."""
    m = re.search(
        rf'^{name}="?([^"\n]*)"?$', (SCRIPTS / "ts-common.sh").read_text(), re.M
    )
    assert m, name
    return m.group(1)


POLICY_ENV = {
    name: common_var(name)
    for name in (
        "LINT_LOCKED_RULES",
        "LINT_LOWERABLE_RULES",
        "LINT_OVERRIDE_OFF_RULES",
        "LINT_CAP_KEY",
        "LINT_PROJECT_FILE",
        "LINT_KIT_FILE",
        "LINT_CAP_COMMAND",
    )
}

KIT = {
    "rules": {
        "no-debugger": "deny",
        "typescript/no-floating-promises": "deny",
        "typescript/no-misused-promises": "deny",
        "typescript/await-thenable": "deny",
        "typescript/no-unsafe-assignment": "deny",
        "typescript/no-unsafe-member-access": "deny",
        "typescript/no-non-null-assertion": "deny",
        "typescript/no-explicit-any": "deny",
        "no-var": "deny",
        "prefer-const": "deny",
    },
    "overrides": [
        {"files": ["**/*.tsx"], "rules": {"react-hooks/rules-of-hooks": "deny"}}
    ],
    "options": {},
}


@unittest.skipUnless(shutil.which("node"), "needs node")
class TestLintPolicy(unittest.TestCase):
    """scripts/lint-policy.mjs on JSON fixtures: no oxlint, no npx."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def node(self, *args, cwd=None):
        r = subprocess.run(
            [NODE, str(POLICY), *map(str, args)],
            capture_output=True,
            text=True,
            cwd=cwd or self.dir,
            env={**os.environ, **POLICY_ENV},
            check=False,
        )
        return r, r.stdout + r.stderr

    def merged(self, rules=None, overrides=None, cap=None):
        m = copy.deepcopy(KIT)
        m["rules"].update(rules or {})
        m["overrides"] += overrides or []
        if cap is not None:
            m["options"] = {CAP_KEY: cap}
        return m

    def check(self, merged, ts_files=None):
        (self.dir / "kit.json").write_text(json.dumps(KIT))
        (self.dir / "merged.json").write_text(json.dumps(merged))
        args = ["check", "kit.json", "merged.json"]
        if ts_files is not None:
            (self.dir / "ts.txt").write_text("\n".join(ts_files) + "\n")
            args += ["--ts-files", "ts.txt"]
        return self.node(*args)

    def refused(self, merged, *needles, ts_files=None):
        r, out = self.check(merged, ts_files)
        self.assertEqual(r.returncode, 1, out)
        for n in needles:
            self.assertIn(n, out)
        return out

    def accepted(self, merged, ts_files=None):
        r, out = self.check(merged, ts_files)
        self.assertEqual(r.returncode, 0, out)
        return r.stdout.strip()

    def test_the_kit_config_alone_is_accepted_with_no_cap(self):
        self.assertEqual(self.accepted(self.merged()), "")

    def test_a_locked_rule_below_error_is_refused_by_name(self):
        for rule in POLICY_ENV["LINT_LOCKED_RULES"].split():
            for level in ("warn", "off", "allow"):
                self.refused(self.merged({rule: level}, cap=0), f"locked rule {rule}")

    def test_a_locked_rule_missing_from_the_merged_config_is_refused(self):
        m = self.merged(cap=0)
        del m["rules"]["no-debugger"]
        self.refused(m, "locked rule no-debugger")

    def test_a_locked_rule_lowered_in_an_override_is_refused(self):
        o = {
            "files": ["public/**"],
            "rules": {"typescript/no-floating-promises": "off"},
        }
        self.refused(
            self.merged(overrides=[o], cap=0),
            "locked rule typescript/no-floating-promises",
        )

    def test_an_allowed_rule_may_be_warn_but_never_off(self):
        self.assertEqual(
            self.accepted(
                self.merged({"typescript/no-non-null-assertion": "warn"}, cap=4)
            ),
            "4",
        )
        self.refused(
            self.merged({"typescript/no-non-null-assertion": "off"}, cap=4),
            "typescript/no-non-null-assertion",
            "switched off",
        )
        self.refused(
            self.merged({"typescript/no-unsafe-assignment": "off"}, cap=4),
            "typescript/no-unsafe-assignment",
        )

    def test_a_rule_outside_the_lists_cannot_be_lowered(self):
        for level in ("warn", "off"):
            self.refused(
                self.merged({"typescript/no-explicit-any": level}, cap=2),
                "typescript/no-explicit-any",
                "only the allowed rules",
            )
        self.refused(self.merged({"no-var": "warn"}, cap=2), "no-var")

    def test_an_unknown_level_is_invalid_and_not_read_as_off(self):
        for rule in (
            "typescript/no-non-null-assertion",
            "typescript/no-explicit-any",
            "no-debugger",
        ):
            self.refused(
                self.merged({rule: "eror"}, cap=2), rule, "not off, warn or error"
            )

    def test_every_spelling_of_the_three_levels_is_understood(self):
        for level in ("error", "deny", 2, "2", ["error", {"x": 1}]):
            self.assertEqual(self.accepted(self.merged({"no-var": level})), "")
        for level in ("warn", 1, ["warn"]):
            self.accepted(
                self.merged({"typescript/no-non-null-assertion": level}, cap=1)
            )

    def test_the_off_list_may_be_off_only_for_files_outside_the_tsconfig(self):
        rules = {"typescript/no-unsafe-member-access": "off", "no-var": "off"}
        o = {"files": ["public/**"], "rules": rules}
        typed = ["src/a.ts", "src/sub/b.ts"]
        self.assertEqual(self.accepted(self.merged(overrides=[o], cap=3), typed), "3")
        covering = {"files": ["src/**"], "rules": rules}
        out = self.refused(
            self.merged(overrides=[covering], cap=3),
            "no-var",
            "src/a.ts",
            ts_files=typed,
        )
        self.assertIn("only files outside the tsconfig", out)
        # a single file glob, a nested glob and a brace glob are matched too
        for glob in (
            "src/a.ts",
            "**/b.ts",
            "{lib,src}/**/*.ts",
            "sr[c]/**",
            "[s]rc/sub/b.ts",
        ):
            self.refused(
                self.merged(overrides=[{"files": [glob], "rules": rules}], cap=3),
                "tsconfig",
                ts_files=typed,
            )

    def test_the_off_list_in_an_override_cannot_be_checked_without_the_tsconfig_files(
        self,
    ):
        o = {"files": ["public/**"], "rules": {"no-var": "off"}}
        self.refused(self.merged(overrides=[o], cap=1), "no-var", "tsconfig")

    def test_an_override_may_not_switch_off_anything_outside_the_off_list(self):
        for rule in (
            "typescript/no-non-null-assertion",
            "prefer-const",
            "typescript/no-explicit-any",
        ):
            o = {"files": ["public/**"], "rules": {rule: "off"}}
            self.refused(self.merged(overrides=[o], cap=1), rule, ts_files=[])
        o = {
            "files": ["public/**"],
            "rules": {"typescript/no-non-null-assertion": "warn"},
        }
        self.accepted(self.merged(overrides=[o], cap=1), ts_files=[])

    def test_lowering_anything_requires_the_cap(self):
        out = self.refused(
            self.merged({"typescript/no-non-null-assertion": "warn"}),
            CAP_KEY,
            "lowers rules",
        )
        self.assertIn(LOWER_CAP_COMMAND, out)
        o = {"files": ["public/**"], "rules": {"no-var": "off"}}
        self.refused(self.merged(overrides=[o]), CAP_KEY, ts_files=[])

    def test_the_cap_must_be_a_whole_number_of_zero_or_more(self):
        for bad in (-1, "3", 1.5, None):
            m = self.merged({"typescript/no-non-null-assertion": "warn"})
            m["options"] = {CAP_KEY: bad}
            self.refused(m, CAP_KEY)

    def test_a_cap_of_zero_is_a_cap(self):
        self.assertEqual(
            self.accepted(
                self.merged({"typescript/no-non-null-assertion": "warn"}, cap=0)
            ),
            "0",
        )

    def test_the_cap_command_reads_a_file_with_every_kind_of_comment(self):
        text = (
            "// header line\n"
            '{ /* block\n spanning */\n  "note": "a // not a comment, nor /* this */",\n'
            '  "options": { "maxWarnings": 12 } // trailing\n}\n'
        )
        (self.dir / "p.json").write_text(text)
        r, out = self.node("cap", "p.json")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "12"), out)
        (self.dir / "p.json").write_text("{}")
        self.assertEqual(self.node("cap", "p.json")[0].stdout.strip(), "none")

    def test_bad_json_gives_one_clear_line_and_no_stack_trace(self):
        (self.dir / "p.json").write_text('{ "options": { "maxWarnings": 3 }, ')
        r, out = self.node("cap", "p.json")
        self.assertEqual(r.returncode, 2, out)
        self.assertIn("p.json", out)
        self.assertIn("not valid JSON", out)
        self.assertEqual(len(out.strip().splitlines()), 1, out)
        self.assertNotIn("    at ", out)
        r, out = self.node("cap", "missing.json")
        self.assertEqual(r.returncode, 2, out)
        self.assertNotIn("    at ", out)

    def test_the_root_config_copy_swaps_or_drops_the_project_entry(self):
        (self.dir / ".oxlintrc.json").write_text(
            '// c\n{\n  "extends": [\n    "./.oxlint-kit.json",\n    "./.oxlint-project.json"\n  ],\n  "ignorePatterns": ["**/dist/**"]\n}\n'
        )
        self.node("root-config", "out.json")
        out = json.loads((self.dir / "out.json").read_text())
        self.assertEqual(out["extends"], ["./.oxlint-kit.json"])
        self.assertEqual(out["ignorePatterns"], ["**/dist/**"])
        self.node("root-config", "out.json", ".oxlint-project.lint-cap-1.json")
        out = json.loads((self.dir / "out.json").read_text())
        self.assertEqual(
            out["extends"], ["./.oxlint-kit.json", "./.oxlint-project.lint-cap-1.json"]
        )

    def test_the_root_config_copy_survives_a_path_with_sed_special_characters(self):
        odd = self.dir / "a#b&c d"
        odd.mkdir()
        (odd / ".oxlintrc.json").write_text(
            '{"extends": ["./.oxlint-kit.json", "./.oxlint-project.json"]}'
        )
        r, out = self.node("root-config", "out.json", cwd=odd)
        self.assertEqual(r.returncode, 0, out)
        self.assertEqual(
            json.loads((odd / "out.json").read_text())["extends"],
            ["./.oxlint-kit.json"],
        )


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

    def test_readme_lists_the_lint_scripts_among_the_files_apply_ships(self):
        line = self.text[self.text.index("`apply` ships") :].splitlines()[0]
        for name in ("scripts/lint-cap.sh", "scripts/lint-policy.mjs"):
            self.assertIn(name, line)

    def test_readme_gives_the_adoption_recipe_in_order(self):
        recipe = self.text[self.text.index("Adopting the kit in an existing project") :]
        positions = [
            recipe.index(w)
            for w in (
                "Apply the kit",
                "Mechanical commit",
                "scripts/ts-fix.sh",
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


PROMISE_BUG = "async function f() {\n  return 1;\n}\nf();\n"
NON_NULL = (
    'const m = new Map<string, number>();\nexport const v: number = m.get("a")!;\n'
)
UNSAFE = "export const u: number = JSON.parse('1');\n"


@unittest.skipUnless(shutil.which("npx") and shutil.which("node"), "needs node and npx")
class TestLintAdoption(TypeScriptProject):
    """The locked rules, the project file and the warning cap, on the kit's pinned oxlint through npx."""

    fake_tools = False

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
        self.plant(
            "public/b.js",
            PROMISE_BUG + "var x = JSON.parse('1');\nexport const y = x.foo;\n",
        )
        c, out = self.check()  # control: with no override all three fire
        self.assertIn("no-unsafe-member-access", out)
        self.assertIn("no-var", out)
        self.assertIn("no-floating-promises", out)
        self.project_file(
            overrides=[
                {
                    "files": ["public/**"],
                    "rules": {
                        "typescript/no-unsafe-member-access": "off",
                        "typescript/no-unsafe-assignment": "off",
                        "no-var": "off",
                    },
                }
            ],
            cap=0,
        )
        out = self.lint_fails()
        self.assertNotIn("no-unsafe-member-access", out)
        self.assertNotIn("no-var", out)
        self.assertIn("no-floating-promises", out)
        self.assertNotIn("locked rule", out)  # lowering an allowed rule is no violation

    def test_no_project_file_ignores_build_folders_and_config_files_and_leaves_no_file(
        self,
    ):
        self.plant("dist/x.ts", "debugger;\n")
        self.plant("vite.config.ts", "debugger;\n")
        before = sorted(p.name for p in self.project.iterdir())
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertNotIn("dist/x.ts", out)
        self.assertNotIn("vite.config.ts", out)
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), before)

    def test_an_allowed_rule_switched_off_is_refused_because_off_hides_the_findings(
        self,
    ):
        self.plant("src/n.ts", NON_NULL)
        self.project_file(rules={"typescript/no-non-null-assertion": "off"}, cap=0)
        out = self.lint_fails()
        self.assertIn("typescript/no-non-null-assertion", out)
        self.assertIn("off", out)

    def test_no_var_cannot_be_switched_off_for_files_the_tsconfig_checks(self):
        self.plant("src/v.ts", "export var v = 1;\n")
        self.project_file(
            overrides=[{"files": ["src/**"], "rules": {"no-var": "off"}}], cap=0
        )
        self.assertIn("no-var", self.lint_fails())

    def test_a_project_file_that_lowers_a_rule_must_set_the_cap(self):
        self.plant("src/n.ts", NON_NULL)
        self.project_file(rules={"typescript/no-non-null-assertion": "warn"})
        out = self.lint_fails()
        self.assertIn(CAP_KEY, out)

    def test_the_lint_canary_still_runs_under_a_project_file(self):
        self.project_file(rules={"typescript/no-non-null-assertion": "warn"}, cap=0)
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertNotIn("CANARY FAILED", out)


@unittest.skipUnless(shutil.which("npx") and shutil.which("node"), "needs node and npx")
class TestLowerCap(TypeScriptProject):
    """The lower-cap command (scripts/lint-cap.sh) on the kit's pinned oxlint through npx."""

    fake_tools = False

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

    def test_a_rerun_only_lowers_the_cap_and_never_warns_another_rule(self):
        self.plant("src/n.ts", NON_NULL)
        self.write_cfg({"options": {CAP_KEY: 0}})
        c, out = self.lint_cap()
        self.assertEqual(c.returncode, 0, out)
        self.assertEqual(self.project_cfg(), {"options": {CAP_KEY: 0}})
        self.assertIn("typescript/no-non-null-assertion", out)  # left as an error
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)

    def test_a_rerun_with_a_warning_rule_in_place_keeps_other_firing_rules_errors(self):
        self.plant("src/n.ts", NON_NULL)
        self.lint_cap()
        before = self.project_cfg()
        self.plant("src/u.ts", UNSAFE.replace("'1'", '"1"'))
        c, out = self.lint_cap()
        self.assertEqual(c.returncode, 0, out)
        self.assertEqual(self.project_cfg(), before)
        self.assertIn("typescript/no-unsafe-assignment", out)

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


class BaseBranch(TypeScriptProject):
    """A project committed as the base branch of a local bare 'origin', and the cap-raise step run as CI would."""

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


class TestCapRaiseCheck(BaseBranch):
    """CI on a pull request fails when the project file's cap is higher than on the base branch."""

    def set_cap(self, cap):
        (self.project / PROJECT_FILE).write_text(
            json.dumps({"options": {"maxWarnings": cap}})
        )

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


@unittest.skipUnless(shutil.which("npx") and shutil.which("node"), "needs node and npx")
class TestCapRaiseEndToEnd(BaseBranch):
    """The file lint-cap.sh writes, committed as the base branch, through the CI cap-raise step."""

    fake_tools = False

    def test_the_file_lint_cap_writes_passes_unchanged_and_fails_when_raised(self):
        self.plant("src/n.ts", NON_NULL)
        c = run_script(self.project, "bash", "scripts/lint-cap.sh")
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.land_on_base()
        r, out = self.in_pull_request()
        self.assertEqual(r.returncode, 0, out)
        path = self.project / PROJECT_FILE
        path.write_text(
            path.read_text().replace('"maxWarnings": 1', '"maxWarnings": 2')
        )
        r, out = self.in_pull_request()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("only goes down", out)


@unittest.skipUnless(shutil.which("npx") and shutil.which("node"), "needs node and npx")
class TestFixCommand(TypeScriptProject):
    """The adoption fixer (scripts/ts-fix.sh): the linter's fix mode, then the formatter, on the kit's pinned tools."""

    fake_tools = False

    def test_fixes_and_formats_before_any_project_file_exists(self):
        # No .oxlint-project.json yet: oxlint itself refuses .oxlintrc.json's extends entry for a missing file.
        self.plant(
            "src/a.ts", "export function f(): number {\n  var  x = 1\n    return x\n}\n"
        )
        c = run_script(self.project, "bash", "scripts/ts-fix.sh")
        out = c.stdout + c.stderr
        self.assertEqual(c.returncode, 0, out)
        text = (self.project / "src/a.ts").read_text()
        self.assertNotIn("var ", text)  # the linter's fix ran (no-var)
        self.assertIn("  return x;\n", text)  # the formatter ran
        fmt = run_script(self.project, "bash", "scripts/check.d/22-ts-format.sh")
        self.assertEqual(fmt.returncode, 0, fmt.stdout + fmt.stderr)
        left = [
            p.name
            for p in self.project.iterdir()
            if re.match(r"\.oxlintrc\..+\.json$", p.name)
        ]
        self.assertEqual(left, [])
        self.assertFalse((self.project / PROJECT_FILE).exists())

    def test_leaves_unfixable_findings_and_still_formats_inside_a_claude_session(self):
        # Inside a Claude session oxlint picks another output format; the fixer must not read that as "did not run".
        self.plant(
            "src/b.ts", "async function g() {\n  return 1\n}\n   g()\nexport {}\n"
        )
        c = run_script(
            self.project,
            "bash",
            "scripts/ts-fix.sh",
            env={"CLAUDECODE": "1", "AI_AGENT": "claude-code"},
        )
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertIn("formatted", c.stdout)
        self.assertIn("\ng();\n", (self.project / "src/b.ts").read_text())


@unittest.skipUnless(shutil.which("node"), "needs node")
class TestPolicyPathWithSpace(unittest.TestCase):
    def test_the_policy_runs_from_a_folder_whose_path_has_a_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "my projects"
            d.mkdir()
            shutil.copy(POLICY, d / "lint-policy.mjs")
            (d / "p.json").write_text('// header\n{"options": {"maxWarnings": 7}}\n')
            env = {**clean_env(), **POLICY_ENV}
            c = subprocess.run(
                [NODE, str(d / "lint-policy.mjs"), "cap", str(d / "p.json")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual((c.returncode, c.stdout.strip()), (0, "7"), c.stderr)

import json
import unittest
from pathlib import Path

from tests.helpers import (
    KIT_ROOT,
    git_config,
    make_layers,
    new_project,
    run_check,
    run_kit,
)


def plant(project, name, body):
    d = project / "scripts/check.d"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)


class ApplyBase(unittest.TestCase):
    def setUp(self):
        self.tmp, self.project = new_project()
        self.addCleanup(self.tmp.cleanup)


class TestBaseLayer(ApplyBase):
    def test_apply_with_no_layers_still_applies_base_and_check_passes(self):
        r = run_kit("apply", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        c = run_check(self.project)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertIn("CHECK PASSED", c.stdout)

    def test_failing_check_script_fails_check_and_is_named(self):
        run_kit("apply", "--layer", "base", self.project)
        plant(self.project, "50-broken.sh", "#!/usr/bin/env bash\nexit 1\n")
        c = run_check(self.project)
        self.assertNotEqual(c.returncode, 0)
        self.assertIn("50-broken.sh", c.stdout + c.stderr)

    def test_check_stops_at_first_failure(self):
        run_kit("apply", self.project)
        plant(self.project, "50-a.sh", "#!/usr/bin/env bash\nexit 1\n")
        plant(self.project, "60-b.sh", "#!/usr/bin/env bash\necho SECOND-RAN\n")
        c = run_check(self.project)
        self.assertNotEqual(c.returncode, 0)
        self.assertNotIn("SECOND-RAN", c.stdout)

    def test_bytecode_in_a_layer_is_not_copied(self):
        layers = Path(self.tmp.name) / "layers"
        make_layers(
            layers,
            {
                "base": {
                    "files/keep.txt": "k\n",
                    "files/hooks/__pycache__/x.cpython-314.pyc": "junk",
                    "files/hooks/y.pyc": "junk",
                }
            },
        )
        r = run_kit("apply", "--layers-dir", layers, self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.project / "keep.txt").exists())
        self.assertFalse((self.project / "hooks/__pycache__").exists())
        self.assertFalse((self.project / "hooks/y.pyc").exists())
        self.assertNotIn("pyc", (self.project / ".starter-kit.json").read_text())

    def test_apply_sets_hooks_path(self):
        run_kit("apply", self.project)
        self.assertEqual(git_config(self.project, "core.hooksPath"), ".githooks")

    def test_check_self_installs_hooks_path_when_unset(self):
        run_kit("apply", self.project)
        import subprocess

        subprocess.run(
            ["git", "config", "--unset", "core.hooksPath"], cwd=self.project, check=True
        )
        run_check(self.project)
        self.assertEqual(git_config(self.project, "core.hooksPath"), ".githooks")

    def test_pre_push_hook_runs_check(self):
        run_kit("apply", self.project)
        plant(self.project, "50-broken.sh", "#!/usr/bin/env bash\nexit 1\n")
        import os
        import subprocess

        hook = self.project / ".githooks/pre-push"
        self.assertTrue(os.access(hook, os.X_OK))
        r = subprocess.run(
            [str(hook)], cwd=self.project, capture_output=True, text=True, check=False
        )
        self.assertNotEqual(r.returncode, 0)

    def test_apply_is_idempotent(self):
        run_kit("apply", self.project)
        first = (self.project / ".starter-kit.json").read_text()
        r = run_kit("apply", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.project / ".starter-kit.json").read_text(), first)

    def test_rejects_non_git_target(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            r = run_kit("apply", d)
            self.assertNotEqual(r.returncode, 0)

    def test_unknown_layer_is_an_error(self):
        r = run_kit("apply", "--layer", "nope", self.project)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope", r.stderr)


class TestRecord(ApplyBase):
    def test_record_has_version_layers_and_hashes(self):
        run_kit("apply", "--layer", "base", self.project)
        rec = json.loads((self.project / ".starter-kit.json").read_text())
        self.assertEqual(rec["kit_version"], (KIT_ROOT / "VERSION").read_text().strip())
        self.assertEqual(rec["layers"], ["base"])
        self.assertIn("scripts/check.sh", rec["files"])
        self.assertEqual(len(rec["files"]["scripts/check.sh"]), 64)

    def test_hash_is_sha256_of_written_file(self):
        import hashlib

        run_kit("apply", self.project)
        rec = json.loads((self.project / ".starter-kit.json").read_text())
        data = (self.project / ".githooks/pre-push").read_bytes()
        self.assertEqual(
            rec["files"][".githooks/pre-push"], hashlib.sha256(data).hexdigest()
        )


class TestLayerMechanism(ApplyBase):
    def setUp(self):
        super().setUp()
        import tempfile

        self.ltmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.ltmp.cleanup)
        self.layers = Path(self.ltmp.name)
        real = KIT_ROOT / "layers"
        import shutil

        shutil.copytree(real / "base", self.layers / "base")

    def test_layer_files_and_check_d_are_copied(self):
        make_layers(
            self.layers,
            {
                "extra": {
                    "files/docs/note.txt": "hello\n",
                    "check.d/20-extra.sh": "#!/usr/bin/env bash\necho EXTRA-RAN\n",
                }
            },
        )
        r = run_kit(
            "apply", "--layers-dir", self.layers, "--layer", "extra", self.project
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.project / "docs/note.txt").read_text(), "hello\n")
        c = run_check(self.project)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertIn("EXTRA-RAN", c.stdout)
        rec = json.loads((self.project / ".starter-kit.json").read_text())
        self.assertEqual(rec["layers"], ["base", "extra"])
        self.assertIn("scripts/check.d/20-extra.sh", rec["files"])

    def test_check_d_script_from_layer_can_fail_check(self):
        make_layers(
            self.layers, {"bad": {"check.d/30-bad.sh": "#!/usr/bin/env bash\nexit 3\n"}}
        )
        run_kit("apply", "--layers-dir", self.layers, "--layer", "bad", self.project)
        c = run_check(self.project)
        self.assertNotEqual(c.returncode, 0)
        self.assertIn("30-bad.sh", c.stdout + c.stderr)

    def test_ci_steps_inserted_at_marker_with_indentation(self):
        make_layers(
            self.layers,
            {
                "base": {
                    "files/.github/workflows/check.yml": "jobs:\n  check:\n    steps:\n      - uses: a\n      # starter-kit: layer setup steps\n      - run: z\n"
                },
                "py": {"ci-steps.yml": "- name: py\n  run: echo py\n"},
                "js": {"ci-steps.yml": "- name: js\n  run: echo js\n"},
            },
        )
        run_kit(
            "apply",
            "--layers-dir",
            self.layers,
            "--layer",
            "py",
            "--layer",
            "js",
            self.project,
        )
        text = (self.project / ".github/workflows/check.yml").read_text()
        self.assertIn(
            "      - name: py\n        run: echo py\n      - name: js\n        run: echo js\n",
            text,
        )
        self.assertLess(text.index("name: py"), text.index("- run: z"))
        self.assertIn("# starter-kit: layer setup steps", text)

    def test_ci_steps_without_marker_is_an_error(self):
        (self.layers / "base/files/.github/workflows/check.yml").unlink()
        make_layers(self.layers, {"py": {"ci-steps.yml": "- run: x\n"}})
        r = run_kit("apply", "--layers-dir", self.layers, "--layer", "py", self.project)
        self.assertNotEqual(r.returncode, 0)

    def test_recorded_hash_for_workflow_is_of_final_content(self):
        import hashlib

        make_layers(
            self.layers,
            {
                "base": {
                    "files/.github/workflows/check.yml": "    # starter-kit: layer setup steps\n"
                },
                "py": {"ci-steps.yml": "- run: x\n"},
            },
        )
        run_kit("apply", "--layers-dir", self.layers, "--layer", "py", self.project)
        rec = json.loads((self.project / ".starter-kit.json").read_text())
        data = (self.project / ".github/workflows/check.yml").read_bytes()
        self.assertEqual(
            rec["files"][".github/workflows/check.yml"],
            hashlib.sha256(data).hexdigest(),
        )


class TestGitleaksCiLayer(ApplyBase):
    def test_layer_installs_a_pinned_gitleaks_before_the_check_runs(self):
        r = run_kit("apply", "--layer", "gitleaks-ci", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.project / ".github/workflows/check.yml").read_text()
        self.assertRegex(text, r"install gitleaks \d+\.\d+\.\d+")
        self.assertRegex(text, r"gitleaks/releases/download/v\d+\.\d+\.\d+/")
        self.assertLess(
            text.index("install gitleaks"), text.index("run: bash scripts/check.sh")
        )


class TestKitMeta(unittest.TestCase):
    def test_version_is_semver_and_changelog_has_heading(self):
        import re

        v = (KIT_ROOT / "VERSION").read_text().strip()
        self.assertRegex(v, r"^\d+\.\d+\.\d+$")
        self.assertRegex(
            (KIT_ROOT / "CHANGELOG.md").read_text(), rf"(?m)^## {re.escape(v)}\b"
        )


if __name__ == "__main__":
    unittest.main()


class TestExistingProjectFiles(ApplyBase):
    def test_differing_existing_file_is_refused_and_left_alone(self):
        (self.project / "renovate.json").write_text('{"mine": true}\n')
        r = run_kit("apply", self.project)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("renovate.json", r.stdout + r.stderr)
        self.assertEqual(
            (self.project / "renovate.json").read_text(), '{"mine": true}\n'
        )
        self.assertFalse((self.project / ".starter-kit.json").exists())

    def test_force_overwrites_the_differing_file(self):
        (self.project / "renovate.json").write_text('{"mine": true}\n')
        r = run_kit("apply", "--force", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('"mine"', (self.project / "renovate.json").read_text())

    def test_another_hooks_path_is_kept(self):
        import subprocess

        subprocess.run(
            ["git", "config", "core.hooksPath", ".husky/_"],
            cwd=self.project,
            check=True,
        )
        r = run_kit("apply", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git_config(self.project, "core.hooksPath"), ".husky/_")

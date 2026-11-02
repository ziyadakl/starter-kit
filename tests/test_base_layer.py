"""Base layer behaviour: CI workflow, local gitleaks check, Renovate config, Claude guards."""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests.helpers import new_project, run_check, run_kit

HAVE_GITLEAKS = shutil.which("gitleaks") is not None


def fake_key():
    # Built at run time so no key-shaped string sits in this file.
    return "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ0bC2eF4hJ6"


def clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class Applied(unittest.TestCase):
    def setUp(self):
        self.tmp, self.project = new_project()
        self.addCleanup(self.tmp.cleanup)
        r = run_kit("apply", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)

    def path(self, rel):
        return self.project / rel

    def checkout(self, branch):
        subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=self.project, check=True)


class TestWorkflow(Applied):
    def setUp(self):
        super().setUp()
        self.text = self.path(".github/workflows/check.yml").read_text()

    def jobs(self):
        """Split the text under the top-level `jobs:` key into {name: block}."""
        body = self.text.split("\njobs:\n", 1)[1]
        parts = re.split(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", body)
        return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}

    def test_triggers_pull_request_ignoring_docs_and_push_to_main(self):
        self.assertRegex(self.text, r"(?m)^  pull_request:")
        self.assertIn("paths-ignore", self.text)
        self.assertIn("'**/*.md'", self.text)
        self.assertIn("'docs/**'", self.text)
        self.assertRegex(self.text, r"(?s)push:\s*\n\s*branches: \[main\]")

    def test_concurrency_cancels_superseded_runs(self):
        self.assertIn("group: ${{ github.workflow }}-${{ github.ref }}", self.text)
        self.assertIn("cancel-in-progress: true", self.text)

    def test_every_job_has_a_time_limit(self):
        jobs = self.jobs()
        self.assertIn("check", jobs)
        self.assertIn("gitleaks", jobs)
        for name, block in jobs.items():
            self.assertRegex(block, r"(?m)^    timeout-minutes: \d+", name)

    def test_check_job_runs_check_script_and_reports_cache(self):
        block = self.jobs()["check"]
        self.assertIn("scripts/check.sh", block)
        self.assertIn("# starter-kit: layer setup steps", block)
        self.assertIn("GITHUB_STEP_SUMMARY", block)
        self.assertIn("cache: ", block)

    def test_gitleaks_job_scans_full_history(self):
        block = self.jobs()["gitleaks"]
        self.assertIn("fetch-depth: 0", block)
        self.assertIn("gitleaks/gitleaks-action", block)

    def test_no_tab_characters(self):
        self.assertNotIn("\t", self.text)


class TestGitleaksCheck(Applied):
    def test_clean_project_passes(self):
        c = run_check(self.project)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertIn("CHECK PASSED", c.stdout)

    def test_planted_fake_key_fails_check(self):
        if not HAVE_GITLEAKS:
            self.skipTest("gitleaks not installed")
        self.path("config.txt").write_text(f"github_token = {fake_key()}\n")
        c = run_check(self.project)
        self.assertNotEqual(c.returncode, 0, c.stdout)
        self.assertIn("gitleaks", c.stdout + c.stderr)

    def test_missing_gitleaks_prints_one_line_skip_and_passes(self):
        # Run with a PATH holding only the tools the check needs, so gitleaks is hidden.
        bin_dir = self.project / ".fakebin"
        bin_dir.mkdir()
        for tool in ("bash", "git", "env", "dirname", "sh", "cat", "grep", "sed"):
            src = shutil.which(tool)
            if src:
                (bin_dir / tool).symlink_to(src)
        env = clean_env()
        env["PATH"] = str(bin_dir)
        c = subprocess.run([shutil.which("bash"), "scripts/check.sh"], capture_output=True,
                           text=True, cwd=self.project, env=env)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertRegex(c.stdout, r"(?i)gitleaks not installed")


class TestRenovate(Applied):
    def test_renovate_config_is_json_with_preset_and_no_project_rules(self):
        cfg = json.loads(self.path("renovate.json").read_text())
        self.assertIn("extends", cfg)
        self.assertNotIn("packageRules", cfg)


def hook(project, name, payload, *args):
    env = clean_env()
    env["CLAUDE_PROJECT_DIR"] = str(project)
    return subprocess.run(
        ["bash", str(project / ".claude/hooks" / name), *args],
        input=json.dumps(payload), capture_output=True, text=True, cwd=project, env=env,
    )


def bash_payload(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def edit_payload(content):
    return {"tool_input": {"file_path": "/p/app.py", "content": content}}


class TestClaudeGuards(Applied):
    def test_settings_is_json_and_every_hook_file_exists_and_is_executable(self):
        cfg = json.loads(self.path(".claude/settings.json").read_text())
        cmds = [h["command"] for ev in cfg["hooks"].values() for g in ev for h in g["hooks"]]
        self.assertTrue(cmds)
        for cmd in cmds:
            for rel in re.findall(r"\.claude/hooks/[\w.-]+\.sh", cmd):
                p = self.path(rel)
                self.assertTrue(p.is_file(), rel)
                self.assertTrue(os.access(p, os.X_OK), rel)

    def test_does_not_duplicate_env_write_blocking(self):
        self.assertFalse(self.path(".claude/hooks/block-env-write.sh").exists())

    def test_blocks_destructive_git(self):
        for cmd in ("git reset " + "--hard HEAD~1", "git push --force origin main", "git branch -D x",
                    "git checkout -- .", "git clean -fd"):
            r = hook(self.project, "block-risky-git.sh", bash_payload(cmd))
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn("BLOCKED", r.stderr)

    def test_allows_ordinary_git_on_a_branch(self):
        self.checkout("work")
        for cmd in ("git status", "git commit -m x", "git push origin work", "git restore --staged f"):
            r = hook(self.project, "block-risky-git.sh", bash_payload(cmd))
            self.assertEqual(r.returncode, 0, cmd + r.stderr)

    def test_blocks_committing_on_main(self):
        self.checkout("main")
        r = hook(self.project, "block-risky-git.sh", bash_payload("git commit -m x"))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_secret_in_edit_is_blocked_and_placeholder_allowed(self):
        r = hook(self.project, "block-secrets.sh", edit_payload(f"token = '{fake_key()}'\n"), "edit")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn(fake_key(), r.stderr)
        ok = hook(self.project, "block-secrets.sh", edit_payload("token = 'your-key-here'\n"), "edit")
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_secret_in_pending_commit_is_blocked(self):
        self.path("app.txt").write_text(f"key = {fake_key()}\n")
        r = hook(self.project, "block-secrets.sh", bash_payload("git commit -m x"), "bash")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_main_is_read_only_for_edits_and_history_changes(self):
        self.checkout("main")
        self.assertEqual(hook(self.project, "main-is-read-only.sh", edit_payload("x = 1\n"), "edit").returncode, 2)
        r = hook(self.project, "main-is-read-only.sh", bash_payload("git reset HEAD~1"), "bash")
        self.assertEqual(r.returncode, 2)
        ff = hook(self.project, "main-is-read-only.sh", bash_payload("git merge --ff-only origin/main"), "bash")
        self.assertEqual(ff.returncode, 0)

    def test_main_read_only_allows_work_on_a_branch(self):
        self.checkout("work")
        self.assertEqual(hook(self.project, "main-is-read-only.sh", edit_payload("x = 1\n"), "edit").returncode, 0)

    def test_hooks_carry_no_personal_names(self):
        for f in (self.project / ".claude").rglob("*"):
            if f.is_file():
                self.assertNotRegex(f.read_text(), r"(?i)ziyad|miranda|affinity")


if __name__ == "__main__":
    unittest.main()

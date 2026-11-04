"""Base layer behaviour: CI workflow, local gitleaks check, Renovate config, Claude guards."""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests.helpers import clean_env, git_config, new_project, run_check, run_kit

HAVE_GITLEAKS = shutil.which("gitleaks") is not None


def fake_key():
    # Built at run time so no key-shaped string sits in this file.
    return "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ0bC2eF4hJ6"


class Applied(unittest.TestCase):
    def setUp(self):
        self.tmp, self.project = new_project()
        self.addCleanup(self.tmp.cleanup)
        r = run_kit("apply", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)

    def path(self, rel):
        return self.project / rel

    def checkout(self, branch):
        subprocess.run(
            ["git", "checkout", "-q", "-b", branch], cwd=self.project, check=True
        )


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

    def test_concurrency_cancels_only_superseded_pull_request_runs(self):
        for rel in (".github/workflows/check.yml", ".github/workflows/gitleaks.yml"):
            text = self.path(rel).read_text()
            self.assertIn("group: ${{ github.workflow }}-${{ github.ref }}", text, rel)
            self.assertIn(
                "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
                text,
                rel,
            )
            self.assertNotRegex(text, r"cancel-in-progress: true", rel)

    def test_every_job_has_a_time_limit(self):
        jobs = self.jobs()
        self.assertIn("check", jobs)
        for name, block in jobs.items():
            self.assertRegex(block, r"(?m)^    timeout-minutes: \d+", name)
        gl = self.path(".github/workflows/gitleaks.yml").read_text()
        self.assertRegex(gl, r"(?m)^    timeout-minutes: \d+")

    def test_check_job_runs_check_script_and_reports_cache(self):
        block = self.jobs()["check"]
        self.assertIn("scripts/check.sh", block)
        self.assertIn("# starter-kit: layer setup steps", block)
        self.assertIn("GITHUB_STEP_SUMMARY", block)
        self.assertIn("cache: ", block)

    def test_gitleaks_has_its_own_workflow_that_docs_only_changes_do_not_skip(self):
        gl = self.path(".github/workflows/gitleaks.yml").read_text()
        self.assertNotIn("paths-ignore", gl)
        self.assertNotIn("paths:", gl)
        self.assertRegex(gl, r"(?m)^  pull_request:")
        self.assertRegex(gl, r"(?s)push:\s*\n\s*branches: \[main\]")
        self.assertIn("fetch-depth: 0", gl)
        self.assertIn("gitleaks/gitleaks-action", gl)
        self.assertNotIn("gitleaks-action", self.text)

    def test_pull_request_trigger_includes_ready_for_review(self):
        m = re.search(r"(?m)^  pull_request:\n(?:    .*\n)+", self.text)
        assert m is not None
        self.assertRegex(
            m.group(0),
            r"types: \[opened, synchronize, reopened, ready_for_review\]",
        )

    def test_check_job_skips_draft_pull_requests_but_not_pushes(self):
        block = self.jobs()["check"]
        self.assertIn(
            "if: github.event_name != 'pull_request' || github.event.pull_request.draft == false",
            block,
        )

    def test_gitleaks_scan_still_runs_on_drafts(self):
        gl = self.path(".github/workflows/gitleaks.yml").read_text()
        self.assertNotIn("draft", gl)
        # No `if:` on any job (4 spaces) or on the workflow's jobs section that could skip a draft or anything else.
        self.assertNotRegex(gl, r"(?m)^    if:")

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
        self.assertIn("leaks found", c.stdout + c.stderr)
        self.assertIn("CHECK FAILED: scripts/check.d/10-gitleaks.sh", c.stderr)

    def test_key_in_git_ignored_env_passes_but_in_tracked_file_fails(self):
        if not HAVE_GITLEAKS:
            self.skipTest("gitleaks not installed")
        self.path(".gitignore").write_text(".env\n.venv/\n")
        self.path(".env").write_text(f"GITHUB_TOKEN={fake_key()}\n")
        (self.path(".venv")).mkdir()
        self.path(".venv/secret.txt").write_text(f"token = {fake_key()}\n")
        c = run_check(self.project)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.path("tracked.txt").write_text(f"GITHUB_TOKEN={fake_key()}\n")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.project, check=True)
        c = run_check(self.project)
        self.assertNotEqual(c.returncode, 0, c.stdout)
        self.assertIn("leaks found", c.stdout + c.stderr)

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
        c = subprocess.run(
            [str(shutil.which("bash")), "scripts/check.sh"],
            capture_output=True,
            text=True,
            cwd=self.project,
            env=env,
            check=False,
        )
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertRegex(c.stdout, r"(?i)gitleaks not installed")


class TestPrePushInWorktree(Applied):
    """The pre-push hook finds the check in a linked worktree, not in the main checkout."""

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=cwd or self.project,
            capture_output=True,
            text=True,
            env=clean_env(),
            check=False,
        )

    def setUp(self):
        super().setUp()
        d = self.path("scripts/check.d")
        (d / "50-marker.sh").write_text(
            "#!/usr/bin/env bash\n"
            'if [ -f "$PWD/FAIL" ]; then echo planted failure; exit 1; fi\n'
            'touch "$PWD/ran-here"\n'
        )
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        self.git("branch", "-M", "main")
        self.remote = self.project.parent / (self.project.name + "-remote.git")
        self.addCleanup(shutil.rmtree, self.remote, True)
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.git("remote", "add", "origin", str(self.remote))
        self.wt = self.project.parent / (self.project.name + "-wt")
        self.addCleanup(shutil.rmtree, self.wt, True)
        r = self.git("worktree", "add", "-q", "-b", "feature", str(self.wt))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_push_from_a_worktree_runs_the_check_in_that_worktree(self):
        r = self.git("push", "-q", "origin", "feature", cwd=self.wt)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.wt / "ran-here").exists())
        self.assertFalse((self.project / "ran-here").exists())

    def test_failing_check_blocks_a_push_from_a_worktree(self):
        (self.wt / "FAIL").write_text("x\n")
        r = self.git("push", "-q", "origin", "feature", cwd=self.wt)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("planted failure", r.stdout + r.stderr)


class TestRenovate(Applied):
    def test_renovate_config_proposes_updates_and_never_automerges(self):
        cfg = json.loads(self.path("renovate.json").read_text())
        self.assertEqual(cfg["extends"], ["config:recommended"])
        self.assertNotIn("packageRules", cfg)
        self.assertNotIn("automerge", self.path("renovate.json").read_text())


def hook(project, name, payload, *args):
    env = clean_env()
    env["CLAUDE_PROJECT_DIR"] = str(project)
    return subprocess.run(
        ["bash", str(project / ".claude/hooks" / name), *args],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=project,
        env=env,
        check=False,
    )


def bash_payload(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def edit_payload(content):
    return {"tool_input": {"file_path": "/p/app.py", "content": content}}


class TestClaudeGuards(Applied):
    def test_settings_is_json_and_every_hook_file_exists_and_is_executable(self):
        cfg = json.loads(self.path(".claude/settings.json").read_text())
        cmds = [
            h["command"] for ev in cfg["hooks"].values() for g in ev for h in g["hooks"]
        ]
        self.assertTrue(cmds)
        for cmd in cmds:
            for rel in re.findall(
                r"(?:\.claude/hooks|scripts)/[\w.-]+\.(?:sh|py)", cmd
            ):
                p = self.path(rel)
                self.assertTrue(p.is_file(), rel)
                self.assertTrue(os.access(p, os.X_OK), rel)

    def test_does_not_duplicate_env_write_blocking(self):
        self.assertFalse(self.path(".claude/hooks/block-env-write.sh").exists())

    def test_the_shipped_hooks(self):
        names = sorted(p.name for p in self.path(".claude/hooks").iterdir())
        self.assertEqual(
            names,
            [
                "block-secrets.sh",
                "config-guard.sh",
                "env-guard.py",
                "install-hooks-path.sh",
                "lib.sh",
                "merge-guard.py",
                "shell_words.py",
            ],
        )

    def test_secret_in_edit_is_blocked_and_placeholder_allowed(self):
        r = hook(
            self.project,
            "block-secrets.sh",
            edit_payload(f"token = '{fake_key()}'\n"),
            "edit",
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn(fake_key(), r.stderr)
        ok = hook(
            self.project,
            "block-secrets.sh",
            edit_payload("token = 'your-key-here'\n"),
            "edit",
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_secret_in_pending_commit_is_blocked(self):
        self.path("app.txt").write_text(f"key = {fake_key()}\n")
        r = hook(
            self.project, "block-secrets.sh", bash_payload("git commit -m x"), "bash"
        )
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_session_start_hook_is_registered(self):
        cfg = json.loads(self.path(".claude/settings.json").read_text())
        cmds = [h["command"] for g in cfg["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertTrue(any("install-hooks-path.sh" in c for c in cmds))

    def test_session_start_hook_installs_hooks_path_on_a_fresh_clone(self):
        subprocess.run(
            ["git", "config", "--unset", "core.hooksPath"], cwd=self.project, check=True
        )
        r = hook(self.project, "install-hooks-path.sh", {})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git_config(self.project, "core.hooksPath"), ".githooks")

    def test_session_start_hook_leaves_a_chosen_hooks_path_alone(self):
        subprocess.run(
            ["git", "config", "core.hooksPath", "my-hooks"],
            cwd=self.project,
            check=True,
        )
        r = hook(self.project, "install-hooks-path.sh", {})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git_config(self.project, "core.hooksPath"), "my-hooks")


if __name__ == "__main__":
    unittest.main()

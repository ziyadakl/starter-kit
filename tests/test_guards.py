"""Session status, .env guard, merge guard and config guard: each fed a tool call on stdin, through its public interface."""

import json
import os
import shutil
import stat
import subprocess
import time
import unittest
from pathlib import Path

from tests.helpers import new_project, run_kit

# Tools the session-status script may use. gh is left out on purpose.
CORE_TOOLS = (
    "bash env git dirname df awk wc tr date mktemp sleep kill rm cat head sed sort uniq"
).split()

GREEN = [{"status": "COMPLETED", "conclusion": "SUCCESS", "name": "check"}]


def clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(project, *args):
    subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)


def path_without_gh(directory):
    """A bin dir holding links to the core tools only, so `gh` cannot be found."""
    directory.mkdir(exist_ok=True)
    for tool in CORE_TOOLS:
        src = shutil.which(tool)
        if src and not (directory / tool).exists():
            (directory / tool).symlink_to(src)
    return str(directory)


def write_exe(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def payload(tool: str, cwd: object = ".", **tool_input: object):
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)}


def wired_matchers(project, event, script):
    cfg = json.loads((project / ".claude/settings.json").read_text())
    return [
        g.get("matcher")
        for g in cfg["hooks"][event]
        for h in g["hooks"]
        if script in h["command"]
    ]


class Applied(unittest.TestCase):
    layers = ()

    def setUp(self):
        self.tmp, self.project = new_project()
        self.addCleanup(self.tmp.cleanup)
        args = [a for layer in self.layers for a in ("--layer", layer)]
        r = run_kit("apply", *args, self.project)
        self.assertEqual(r.returncode, 0, r.stderr)

    def run_hook(self, rel, data, env_extra=None, path=None, raw=None):
        env = clean_env()
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        if path is not None:
            env["PATH"] = path
        env.update(env_extra or {})
        cmd = "python3" if rel.endswith(".py") else "bash"
        return subprocess.run(
            [cmd, str(self.project / rel)],
            input=raw if raw is not None else json.dumps(data),
            capture_output=True,
            text=True,
            cwd=self.project,
            env=env,
            check=False,
        )


class TestSessionStatus(Applied):
    def commit_on_branch(self, branch):
        git(self.project, "checkout", "-q", "-b", branch)
        git(
            self.project,
            "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "--allow-empty", "-m", "first",
        )  # fmt: skip

    def status(self, path, cwd=None):
        env = clean_env()
        env["PATH"] = path
        return subprocess.run(
            ["bash", str(self.project / "scripts/session-status.sh")],
            capture_output=True,
            text=True,
            cwd=cwd or self.project,
            env=env,
            check=False,
        )

    def test_without_gh_prints_branch_dirty_count_and_disk_and_exits_0(self):
        self.commit_on_branch("feature-x")
        (self.project / "new.txt").write_text("x\n")
        r = self.status(path_without_gh(Path(self.tmp.name) / "bin-no-gh"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("feature-x", r.stdout)
        self.assertIn("Uncommitted: ", r.stdout)
        self.assertRegex(r.stdout, r"Free disk: \d+ GB")
        self.assertNotIn("Open PRs", r.stdout)

    def test_with_gh_prints_open_prs_and_issue_count(self):
        self.commit_on_branch("main")
        bin_dir = Path(self.tmp.name) / "bin-fake-gh"
        path = path_without_gh(bin_dir)
        write_exe(
            bin_dir / "gh",
            '#!/bin/sh\ncase "$1" in\n'
            '  pr) echo "  #7 my-branch -> main: Add thing" ;;\n'
            "  issue) echo 3 ;;\nesac\n",
        )
        r = self.status(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("#7 my-branch -> main: Add thing", r.stdout)
        self.assertIn("Open issues: 3", r.stdout)

    def test_outside_a_git_repo_still_exits_0(self):
        outside = Path(self.tmp.name) / "plain"
        outside.mkdir()
        r = self.status(path_without_gh(Path(self.tmp.name) / "bin-x"), cwd=outside)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wired_as_a_second_session_start_hook(self):
        for script in ("install-hooks-path.sh", "session-status.sh"):
            self.assertTrue(
                wired_matchers(self.project, "SessionStart", script), script
            )


class TestEnvGuard(Applied):
    hook = ".claude/hooks/env-guard.py"

    def verdict(self, tool, env_extra=None, **tool_input):
        return self.run_hook(
            self.hook, payload(tool, self.project, **tool_input), env_extra=env_extra
        )

    def test_reading_env_files_is_blocked(self):
        for path in (".env", "/p/.env", ".env.local", ".env.production"):
            r = self.verdict("Read", file_path=path)
            self.assertEqual(r.returncode, 2, path)
            self.assertIn("cut -d= -f1 .env", r.stderr)

    def test_env_example_and_other_files_are_allowed(self):
        for path in (".env.example", "/p/src/app.py", "environment.md"):
            self.assertEqual(self.verdict("Read", file_path=path).returncode, 0, path)

    def test_bash_commands_that_print_values_are_blocked(self):
        for cmd in (
            "cat .env",
            "head -3 .env.local",
            "grep KEY .env",
            "cat < .env",
            "bash -c 'cat .env'",
            "cp .env /tmp/copy",
            "git show HEAD:.env",
            "python3 -c \"print(open('.env').read())\"",
            "echo hi && sed -n 1p .env",
        ):
            self.assertEqual(self.verdict("Bash", command=cmd).returncode, 2, cmd)

    def test_names_only_listing_and_unrelated_commands_are_allowed(self):
        for cmd in (
            "cut -d= -f1 .env",
            "grep -c KEY .env",
            "grep -q KEY .env",
            "cat .env.example",
            "cp .env.example .env",
            "ls -a",
            "test -f .env",
            "echo $ENVIRONMENT",
            "cat README.md",
        ):
            self.assertEqual(self.verdict("Bash", command=cmd).returncode, 0, cmd)

    def test_grep_tool(self):
        self.assertEqual(self.verdict("Grep", pattern="K", path=".env").returncode, 2)
        self.assertEqual(self.verdict("Grep", pattern="K", glob=".env*").returncode, 2)
        (self.project / ".env").write_text("KEY=1\n")
        r = self.verdict("Grep", pattern="K", output_mode="content")
        self.assertEqual(r.returncode, 2)
        r = self.verdict("Grep", pattern="K", output_mode="files_with_matches")
        self.assertEqual(r.returncode, 0)

    def test_recursive_grep_over_a_folder_holding_env_is_blocked(self):
        (self.project / ".env").write_text("KEY=1\n")
        self.assertEqual(self.verdict("Bash", command="grep -r KEY .").returncode, 2)
        self.assertEqual(self.verdict("Bash", command="grep -rl KEY .").returncode, 0)

    def test_content_search_in_a_project_without_env_is_allowed(self):
        (self.project / "app.py").write_text("x = 1\n")
        r = self.verdict("Grep", pattern="x", output_mode="content")
        self.assertEqual(r.returncode, 0, r.stderr)
        for cmd in ("grep -rn x .", "rg --hidden x", "grep -rn x ."):
            self.assertEqual(self.verdict("Bash", command=cmd).returncode, 0, cmd)

    def test_nested_env_is_found_by_recursive_searches(self):
        web = self.project / "apps/web"
        web.mkdir(parents=True)
        (web / ".env").write_text("SECRET=1\n")
        for cmd in (
            "grep -rn SECRET apps",
            "rg --hidden SECRET apps",
            "grep -rn SECRET .",
        ):
            r = self.verdict("Bash", command=cmd)
            self.assertEqual(r.returncode, 2, cmd)
        r = self.verdict("Grep", pattern="SECRET", path="apps", output_mode="content")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--exclude", r.stderr)

    def test_nested_env_example_alone_does_not_block(self):
        web = self.project / "apps/web"
        web.mkdir(parents=True)
        (web / ".env.example").write_text("SECRET=\n")
        r = self.verdict("Bash", command="grep -rn SECRET apps")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_searches_that_leave_env_out_are_allowed(self):
        (self.project / ".env").write_text("KEY=1\n")
        for cmd in ("grep -rn x --exclude='.env*' .", "rg --hidden -g '!.env*' x ."):
            self.assertEqual(self.verdict("Bash", command=cmd).returncode, 0, cmd)
        r = self.verdict("Grep", pattern="K", glob="*.py", output_mode="content")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_env_deep_in_the_tree_is_found_and_heavy_folders_are_skipped(self):
        deep = self.project / "a/b/c/d"
        deep.mkdir(parents=True)
        (deep / ".env").write_text("K=1\n")
        self.assertEqual(self.verdict("Bash", command="grep -rn K .").returncode, 2)
        (deep / ".env").unlink()
        nm = self.project / "node_modules/pkg"
        nm.mkdir(parents=True)
        (nm / ".env").write_text("K=1\n")
        self.assertEqual(self.verdict("Bash", command="grep -rn K .").returncode, 0)

    def test_deep_source_tree_without_env_is_allowed(self):
        deep = self.project / "src"
        for part in "com example app service impl internal util core more".split():
            deep = deep / part
        deep.mkdir(parents=True)
        (deep / "Main.java").write_text("class Main {}\n")
        (self.project / "target" / "a" / "b").mkdir(parents=True)
        self.assertEqual(self.verdict("Bash", command="grep -rn x .").returncode, 0)
        self.assertEqual(self.verdict("Bash", command="grep -rn x src").returncode, 0)

    def test_search_too_big_to_check_is_blocked(self):
        big = self.project / "big"
        big.mkdir()
        for i in range(30):
            (big / f"d{i}").mkdir()
        r = self.verdict(
            "Bash", command="grep -rn x big", env_extra={"KIT_ENV_WALK_LIMIT": "10"}
        )
        self.assertEqual(r.returncode, 2)

    def test_fails_open_on_garbage_input(self):
        for raw in ("not json", '{"tool_input": "oops"}', "[]", ""):
            r = self.run_hook(self.hook, None, raw=raw)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_wired_for_read_grep_and_bash(self):
        wired = wired_matchers(self.project, "PreToolUse", "env-guard.py")
        self.assertEqual(sorted(wired), ["Bash", "Read|Grep"])


class TestMergeGuard(Applied):
    hook = ".claude/hooks/merge-guard.py"

    def setUp(self):
        super().setUp()
        self.bin = Path(self.tmp.name) / "bin-merge"
        self.path = path_without_gh(self.bin) + os.pathsep + os.environ["PATH"]
        self.head = "a" * 40
        git(self.project, "checkout", "-q", "-b", "feature")

    def fake_gh(self, rollup=None, exit_code=0):
        """A gh that answers `pr view --json ...` with one PR whose head checks are `rollup`."""
        pr = {"number": 7, "headRefOid": self.head, "statusCheckRollup": rollup}
        (self.bin / "pr.json").write_text(json.dumps(pr))
        write_exe(
            self.bin / "gh",
            f'#!/bin/sh\n[ {exit_code} -eq 0 ] || {{ echo "gh: boom" >&2; exit {exit_code}; }}\n'
            f'cat "{self.bin}/pr.json"\n',
        )

    def merge(self, cmd="gh pr merge 7 --squash"):
        return self.run_hook(
            self.hook, payload("Bash", self.project, command=cmd), path=self.path
        )

    def test_merge_with_green_checks_is_allowed(self):
        self.fake_gh(
            [
                *GREEN,
                {"status": "COMPLETED", "conclusion": "SKIPPED", "name": "docs"},
                {"state": "SUCCESS", "context": "legacy"},
            ]
        )
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_merge_with_a_failing_or_pending_check_or_none_is_blocked(self):
        failing = [{"status": "COMPLETED", "conclusion": "FAILURE", "name": "check"}]
        pending = [{"status": "IN_PROGRESS", "conclusion": "", "name": "check"}]
        for rollup in (failing, pending, [], None):
            self.fake_gh(rollup)
            r = self.merge()
            self.assertEqual(r.returncode, 2, rollup)
            self.assertIn("BLOCKED", r.stderr)

    def test_a_gh_that_hangs_blocks_the_merge(self):
        write_exe(self.bin / "gh", "#!/bin/sh\nsleep 5\n")
        start = time.monotonic()
        r = self.run_hook(
            self.hook,
            payload("Bash", self.project, command="gh pr merge 7"),
            env_extra={"KIT_GH_TIMEOUT": "0.5"},
            path=self.path,
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("timed out", r.stderr)
        self.assertLess(time.monotonic() - start, 4)

    def test_every_way_to_name_a_merge_is_covered(self):
        self.fake_gh([{"status": "COMPLETED", "conclusion": "FAILURE", "name": "c"}])
        for cmd in (
            "gh pr merge",
            "gh pr merge --auto 7",
            "gh -R o/r pr merge 7",
            "echo ok && gh pr merge 7 -d",
            "bash -c 'gh pr merge 7'",
            "gh api repos/o/r/pulls/7/merge -X PUT",
        ):
            self.assertEqual(self.merge(cmd).returncode, 2, cmd)

    def test_other_commands_are_allowed_without_calling_gh(self):
        self.fake_gh([], exit_code=9)
        for cmd in ("gh pr view 7", "gh pr list", "git merge main", "echo gh pr merge"):
            self.assertEqual(self.merge(cmd).returncode, 0, cmd)

    def test_a_merge_gh_cannot_verify_is_blocked(self):
        self.fake_gh([], exit_code=1)
        r = self.merge()
        self.assertEqual(r.returncode, 2)
        self.assertIn("boom", r.stderr)

    def test_stamp_is_required_only_when_the_stamps_folder_exists(self):
        self.fake_gh(GREEN)
        self.assertEqual(self.merge().returncode, 0)
        stamps = self.project / ".git" / "review-stamps"
        stamps.mkdir()
        r = self.merge()
        self.assertEqual(r.returncode, 2)
        self.assertIn(self.head[:12], r.stderr)
        (stamps / self.head).write_text("pr=7\n")
        self.assertEqual(self.merge().returncode, 0)

    def test_fails_open_on_garbage_input(self):
        r = self.run_hook(self.hook, None, raw="not json", path=self.path)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wired_for_bash(self):
        wired = wired_matchers(self.project, "PreToolUse", "merge-guard.py")
        self.assertEqual(wired, ["Bash"])


class TestConfigGuard(Applied):
    layers = ("python", "shell")
    hook = ".claude/hooks/config-guard.sh"

    def edit(self, rel, tool="Edit", env_extra=None, raw=None):
        data = {"tool_name": tool, "tool_input": {"file_path": str(self.project / rel)}}
        return self.run_hook(self.hook, data, env_extra=env_extra, raw=raw)

    def test_each_layers_config_files_are_blocked_with_the_reason_and_the_way_out(self):
        for rel in (
            "ruff.toml",
            ".ruff.toml",
            "pyrightconfig.json",
            "pkg/ruff.toml",
            ".shellcheckrc",
            ".gitleaks.toml",
        ):
            r = self.edit(rel)
            self.assertEqual(r.returncode, 2, rel)
            self.assertIn("Fix the code, don't weaken the config.", r.stderr, rel)
            self.assertIn("ALLOW_CONFIG_EDIT=1", r.stderr, rel)

    def test_the_protected_lists_are_themselves_protected(self):
        self.assertEqual(self.edit(".claude/protect.d/python.txt").returncode, 2)

    def test_ordinary_files_are_allowed(self):
        for rel in ("src/app.py", "pyproject.toml", "README.md", "scripts/run.sh"):
            self.assertEqual(self.edit(rel).returncode, 0, rel)

    def test_write_and_notebook_tools_are_covered_too(self):
        self.assertEqual(self.edit("ruff.toml", tool="Write").returncode, 2)
        data = {"tool_input": {"notebook_path": str(self.project / "ruff.toml")}}
        self.assertEqual(self.run_hook(self.hook, data).returncode, 2)

    def test_owner_override_lets_the_edit_through(self):
        r = self.edit("ruff.toml", env_extra={"ALLOW_CONFIG_EDIT": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_fails_open_on_garbage_input(self):
        self.assertEqual(self.edit("ruff.toml", raw="not json").returncode, 0)
        self.assertEqual(self.edit("ruff.toml", raw="").returncode, 0)

    def test_wired_for_edit_and_write_tools(self):
        wired = wired_matchers(self.project, "PreToolUse", "config-guard.sh")
        self.assertEqual(wired, ["Edit|Write|MultiEdit|NotebookEdit"])


class TestConfigGuardWithBaseOnly(Applied):
    hook = ".claude/hooks/config-guard.sh"

    def edit(self, rel):
        data = {"tool_input": {"file_path": str(self.project / rel)}}
        return self.run_hook(self.hook, data)

    def test_a_layer_not_applied_contributes_nothing(self):
        self.assertEqual(self.edit("ruff.toml").returncode, 0)
        self.assertEqual(self.edit(".gitleaks.toml").returncode, 2)


if __name__ == "__main__":
    unittest.main()

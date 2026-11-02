"""Shell layer behaviour: shellcheck runs on every script found by extension or shebang."""

import os
import shutil
import subprocess
import unittest

from tests.helpers import new_project, run_check, run_kit

HAVE_SHELLCHECK = shutil.which("shellcheck") is not None

CLEAN = (
    '#!/usr/bin/env bash\nset -euo pipefail\nname="${1:-world}"\necho "hello $name"\n'
)
# SC2086: unquoted variable.
BROKEN = "#!/usr/bin/env bash\nname=$1\necho $name\n"


class Applied(unittest.TestCase):
    def setUp(self):
        self.tmp, self.project = new_project()
        self.addCleanup(self.tmp.cleanup)
        r = run_kit("apply", "--layer", "shell", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)

    def add(self, rel, body, track=True):
        p = self.project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        if track:
            subprocess.run(["git", "add", rel], cwd=self.project, check=True)


@unittest.skipUnless(HAVE_SHELLCHECK, "shellcheck not installed")
class TestShellcheckCheck(Applied):
    def test_clean_script_passes(self):
        self.add("bin/tool.sh", CLEAN)
        c = run_check(self.project)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertIn("CHECK PASSED", c.stdout)

    def test_planted_error_fails_and_is_named(self):
        self.add("bin/tool.sh", BROKEN)
        c = run_check(self.project)
        self.assertNotEqual(c.returncode, 0, c.stdout)
        self.assertIn("bin/tool.sh", c.stdout + c.stderr)
        self.assertIn("SC2086", c.stdout + c.stderr)

    def test_script_found_by_shebang_without_extension(self):
        self.add("bin/tool", BROKEN)
        c = run_check(self.project)
        self.assertNotEqual(c.returncode, 0)
        self.assertIn("SC2086", c.stdout + c.stderr)

    def test_non_shell_file_is_ignored(self):
        self.add("notes.txt", "echo $unquoted\n")
        self.add("tool.py", "#!/usr/bin/env python3\nprint('x')\n")
        self.assertEqual(run_check(self.project).returncode, 0)

    def test_zsh_script_is_ignored(self):
        self.add("bin/z", "#!/usr/bin/env zsh\nprint -r -- $1\n")
        self.assertEqual(run_check(self.project).returncode, 0)


class TestMissingShellcheck(Applied):
    def test_missing_shellcheck_fails_with_install_hint(self):
        bin_dir = self.project / ".fakebin"
        bin_dir.mkdir()
        for tool in (
            "bash",
            "git",
            "env",
            "dirname",
            "sh",
            "cat",
            "grep",
            "sed",
            "head",
        ):
            src = shutil.which(tool)
            if src:
                (bin_dir / tool).symlink_to(src)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["PATH"] = str(bin_dir)
        c = subprocess.run(
            [str(shutil.which("bash")), "scripts/check.sh"],
            capture_output=True,
            text=True,
            cwd=self.project,
            env=env,
            check=False,
        )
        self.assertNotEqual(c.returncode, 0, c.stdout)
        self.assertRegex(c.stdout + c.stderr, r"(?i)install shellcheck")


class TestWorkflowStep(Applied):
    def test_workflow_installs_pinned_shellcheck(self):
        text = (self.project / ".github/workflows/check.yml").read_text()
        self.assertRegex(text, r"install shellcheck \d+\.\d+\.\d+")
        self.assertRegex(text, r"shellcheck/releases/download/v\d+\.\d+\.\d+/")
        self.assertLess(
            text.index("install shellcheck"), text.index("run: bash scripts/check.sh")
        )


if __name__ == "__main__":
    unittest.main()

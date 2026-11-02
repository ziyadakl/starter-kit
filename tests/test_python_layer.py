"""Python layer behaviour: a fresh project passes check; each planted mistake makes check fail."""

import unittest

from tests.helpers import new_project, run_check, run_kit

PYPROJECT = """[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[tool.pytest.ini_options]
pythonpath = ["."]
"""
MODULE = """def add(a: int, b: int) -> int:
    return a + b
"""
TEST = """from demo import add


def test_add():
    assert add(1, 2) == 3
"""


class PythonProject(unittest.TestCase):
    def setUp(self):
        self.tmp, self.project = new_project()
        self.addCleanup(self.tmp.cleanup)
        (self.project / "pyproject.toml").write_text(PYPROJECT)
        (self.project / "demo.py").write_text(MODULE)
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_demo.py").write_text(TEST)
        r = run_kit("apply", "--layer", "base", "--layer", "python", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)

    def check(self):
        c = run_check(self.project)
        return c, c.stdout + c.stderr

    def plant(self, name, text):
        (self.project / name).write_text(text)


class TestPythonCheck(PythonProject):
    def test_fresh_project_passes(self):
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)
        self.assertIn("CHECK PASSED", c.stdout)

    def test_pyproject_is_left_alone(self):
        self.assertEqual((self.project / "pyproject.toml").read_text(), PYPROJECT)

    def test_project_without_tests_passes(self):
        (self.project / "tests" / "test_demo.py").unlink()
        (self.project / "tests").rmdir()
        c, out = self.check()
        self.assertEqual(c.returncode, 0, out)

    def test_lint_error_fails(self):
        self.plant("lint.py", "import os\n")
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)
        self.assertIn("CHECK FAILED", out)

    def test_format_violation_fails(self):
        self.plant("fmt.py", "x = {  'a':1 }\n")
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)

    def test_type_error_fails(self):
        self.plant("typed.py", 'x: int = "text"\n')
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)

    def test_failing_test_fails(self):
        self.plant("tests/test_bad.py", "def test_bad():\n    assert False\n")
        c, out = self.check()
        self.assertNotEqual(c.returncode, 0, out)


class TestPythonWorkflow(PythonProject):
    def test_workflow_sets_up_uv_with_cache_inside_check_job(self):
        text = (self.project / ".github/workflows/check.yml").read_text()
        check_job = text.split("  gitleaks:")[0]
        self.assertIn("astral-sh/setup-uv@", check_job)
        self.assertIn("enable-cache: true", check_job)
        self.assertRegex(
            check_job, r"(?m)^        id: \S+"
        )  # the summary step reads its cache-hit output
        self.assertLess(
            check_job.index("setup-uv"), check_job.index("run: bash scripts/check.sh")
        )
        self.assertNotIn("\t", text)


if __name__ == "__main__":
    unittest.main()

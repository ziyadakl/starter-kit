import json
import tempfile
import unittest
from pathlib import Path

from starterkit import apply as apply_cmd
from tests.helpers import make_layers, new_project, run_kit

CHANGELOG = """# Changelog

## 0.2.0

- second change

## 0.1.1

- small fix

## 0.1.0

- first release
"""


class FakeKit:
    """A kit root + layers dir in a temp dir, so tests never depend on the real layers."""

    def __init__(self, test, version, files, changelog=CHANGELOG):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.layers = self.root / "layers"
        self.set(version, files, changelog)

    def set(self, version, files, changelog=CHANGELOG):
        import shutil
        shutil.rmtree(self.layers, ignore_errors=True)
        make_layers(self.layers, {"base": {f"files/{k}": v for k, v in files.items()}})
        (self.root / "VERSION").write_text(version + "\n")
        (self.root / "CHANGELOG.md").write_text(changelog)

    def apply(self, project):
        apply_cmd.run(self.root, self.layers, [], project)

    def update(self, project):
        return run_kit("update", "--kit-root", self.root, "--layers-dir", self.layers, project)


def record(project):
    return json.loads((project / ".starter-kit.json").read_text())


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp, self.project = new_project()
        self.addCleanup(self.tmp.cleanup)
        self.kit = FakeKit(self, "0.1.0", {"a.txt": "a1\n", "b.txt": "b1\n"})
        self.kit.apply(self.project)

    def test_seam_edit_one_file_bump_version_update(self):
        (self.project / "a.txt").write_text("mine\n")
        self.kit.set("0.2.0", {"a.txt": "a2\n", "b.txt": "b2\n"})
        r = self.kit.update(self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.project / "a.txt").read_text(), "mine\n")
        self.assertEqual((self.project / "b.txt").read_text(), "b2\n")
        self.assertIn("a.txt", r.stdout)
        self.assertNotIn("b.txt", r.stdout.split("edited")[-1])
        self.assertEqual(record(self.project)["kit_version"], "0.2.0")

    def test_prints_only_changelog_entries_newer_than_recorded(self):
        self.kit.set("0.2.0", {"a.txt": "a1\n", "b.txt": "b1\n"})
        r = self.kit.update(self.project)
        self.assertIn("second change", r.stdout)
        self.assertIn("small fix", r.stdout)
        self.assertNotIn("first release", r.stdout)

    def test_edited_file_stays_listed_on_later_updates(self):
        (self.project / "a.txt").write_text("mine\n")
        self.kit.set("0.2.0", {"a.txt": "a2\n", "b.txt": "b1\n"})
        self.kit.update(self.project)
        r = self.kit.update(self.project)
        self.assertIn("a.txt", r.stdout)
        self.assertEqual((self.project / "a.txt").read_text(), "mine\n")

    def test_file_new_in_kit_is_added_and_recorded(self):
        self.kit.set("0.2.0", {"a.txt": "a1\n", "b.txt": "b1\n", "sub/c.txt": "c\n"})
        self.kit.update(self.project)
        self.assertEqual((self.project / "sub/c.txt").read_text(), "c\n")
        self.assertIn("sub/c.txt", record(self.project)["files"])

    def test_new_kit_file_that_project_already_has_is_not_overwritten(self):
        (self.project / "c.txt").write_text("mine\n")
        self.kit.set("0.2.0", {"a.txt": "a1\n", "b.txt": "b1\n", "c.txt": "c\n"})
        r = self.kit.update(self.project)
        self.assertEqual((self.project / "c.txt").read_text(), "mine\n")
        self.assertIn("c.txt", r.stdout)

    def test_file_removed_from_kit_is_kept_and_listed(self):
        self.kit.set("0.2.0", {"a.txt": "a1\n"})
        r = self.kit.update(self.project)
        self.assertTrue((self.project / "b.txt").exists())
        self.assertIn("b.txt", r.stdout)
        self.assertNotIn("b.txt", record(self.project)["files"])

    def test_file_deleted_by_project_is_not_brought_back(self):
        (self.project / "b.txt").unlink()
        self.kit.set("0.2.0", {"a.txt": "a1\n", "b.txt": "b2\n"})
        r = self.kit.update(self.project)
        self.assertFalse((self.project / "b.txt").exists())
        self.assertIn("b.txt", r.stdout)

    def test_edit_matching_new_kit_content_is_recorded_as_current(self):
        (self.project / "a.txt").write_text("a2\n")
        self.kit.set("0.2.0", {"a.txt": "a2\n", "b.txt": "b1\n"})
        self.kit.update(self.project)
        self.assertEqual(record(self.project)["files"]["a.txt"], apply_cmd.sha256(b"a2\n"))

    def test_update_without_record_fails_clearly(self):
        tmp, bare = new_project()
        self.addCleanup(tmp.cleanup)
        r = self.kit.update(bare)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("apply", r.stderr)

    def test_already_current_says_so(self):
        self.kit.set("0.1.0", {"a.txt": "a1\n", "b.txt": "b1\n"}, "# Changelog\n\n## 0.1.0\n\n- first release\n")
        r = self.kit.update(self.project)
        self.assertEqual(r.returncode, 0)
        self.assertIn("up to date", r.stdout)


if __name__ == "__main__":
    unittest.main()

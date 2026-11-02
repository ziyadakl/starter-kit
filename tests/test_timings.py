import json
import unittest
from pathlib import Path

from starterkit import timings
from tests.helpers import run_kit

FIX = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIX / f"timings_{name}.json").read_text())


def fake_gh(calls=None):
    data = {
        "repos/example/repo/actions/runs/1/jobs?per_page=100": load("jobs_1"),
        "repos/example/repo/actions/runs/2/jobs?per_page=100": load("jobs_2"),
    }

    def gh(path):
        if calls is not None:
            calls.append(path)
        if path.startswith("repos/example/repo/actions/runs?"):
            return load("runs") if "page=1" in path else {"workflow_runs": []}
        return data[path]

    return gh


class TimingsTest(unittest.TestCase):
    def test_per_job_medians_and_totals(self):
        stats = timings.collect("example/repo", 10, gh=fake_gh())
        check = stats["check"]
        self.assertEqual(check["count"], 2)
        self.assertEqual((check["queue"]["median"], check["queue"]["total"]), (20, 40))
        self.assertEqual((check["setup"]["median"], check["setup"]["total"]), (30, 60))
        self.assertEqual((check["run"]["median"], check["run"]["total"]), (120, 240))
        lint = stats["lint"]
        self.assertEqual(lint["count"], 1)
        self.assertEqual(
            (lint["queue"]["total"], lint["setup"]["total"], lint["run"]["total"]),
            (5, 10, 30),
        )

    def test_table_lists_each_job(self):
        out = timings.report("example/repo", 10, gh=fake_gh())
        lines = out.splitlines()
        self.assertIn("job", lines[0])
        self.assertIn("queue", lines[0])
        check = next(l for l in lines if l.startswith("check"))
        self.assertIn("20s", check)
        self.assertIn("2m00s", check)
        self.assertTrue(any(l.startswith("lint") for l in lines))

    def test_runs_limit_is_requested_and_respected(self):
        calls = []
        timings.collect("example/repo", 1, gh=fake_gh(calls))
        self.assertIn("per_page=1", calls[0])
        self.assertFalse(any("/runs/2/jobs" in c for c in calls))

    def test_jobs_are_requested_100_per_page(self):
        calls = []
        timings.collect("example/repo", 1, gh=fake_gh(calls))
        self.assertIn("repos/example/repo/actions/runs/1/jobs?per_page=100", calls)

    def test_dependency_install_steps_count_as_setup(self):
        def step(name, a, b):
            return {
                "name": name,
                "started_at": f"2025-01-01T10:00:{a:02d}Z",
                "completed_at": f"2025-01-01T10:00:{b:02d}Z",
            }

        for install in ("Run pnpm install", "Run npm ci", "Run uv sync --locked"):
            job = {
                "name": "x",
                "conclusion": "success",
                "created_at": "2025-01-01T10:00:00Z",
                "started_at": "2025-01-01T10:00:00Z",
                "completed_at": "2025-01-01T10:00:50Z",
                "steps": [
                    step("Set up job", 0, 2),
                    step("Run actions/checkout@v4", 2, 10),
                    step(install, 10, 30),
                    step("Run tests", 30, 50),
                ],
            }
            _, setup, run = timings._split(job)
            self.assertEqual((setup, run), (30, 20), install)

    def test_no_runs(self):
        out = timings.report("example/repo", 5, gh=lambda p: {"workflow_runs": []})
        self.assertIn("no workflow runs", out)

    def test_unfinished_jobs_are_skipped(self):
        jobs = {
            "jobs": [
                {
                    "name": "x",
                    "created_at": "2025-01-01T10:00:00Z",
                    "started_at": None,
                    "completed_at": None,
                    "steps": [],
                }
            ]
        }
        gh = lambda p: (
            {"workflow_runs": [{"id": 1}]}
            if "page=1" in p
            else (jobs if "/jobs" in p else {"workflow_runs": []})
        )
        self.assertEqual(timings.collect("example/repo", 5, gh=gh), {})

    def test_skipped_jobs_are_ignored(self):
        jobs = {
            "jobs": [
                {
                    "name": "x",
                    "conclusion": "skipped",
                    "created_at": "2025-01-01T10:00:00Z",
                    "started_at": "2025-01-01T10:00:05Z",
                    "completed_at": "2025-01-01T10:00:04Z",
                    "steps": [],
                }
            ]
        }
        gh = lambda p: (
            {"workflow_runs": [{"id": 1}]}
            if "page=1" in p
            else (jobs if "/jobs" in p else {"workflow_runs": []})
        )
        self.assertEqual(timings.collect("example/repo", 5, gh=gh), {})

    def test_cli_wired(self):
        r = run_kit("timings", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--runs", r.stdout)


if __name__ == "__main__":
    unittest.main()

"""`kit.py timings`: where a repository's CI time goes, per job.

Per job: queue (created -> started), setup (started -> first step that is not
setup-like), run (that step -> completed). Reads the GitHub API through `gh`.
"""

import json
import re
import statistics
import subprocess
from datetime import datetime

MAX_PAGE = 100
SETUP_STEP = re.compile(
    r"^(set up job|run actions/(checkout|cache|setup-\w+)|run \S*/?(setup|install|cache|restore)\S*|"
    r"(set up|setup|install|restore|cache|checkout|initialize)\b.*|"
    r"(run )?(pnpm|npm|yarn|bun) (install|i|ci)\b.*|"
    r"(run )?uv (sync|pip install)\b.*|"
    r"(run )?(pip|poetry|pipenv|bundle) install\b.*)",
    re.IGNORECASE,
)


class TimingsError(Exception):
    pass


def gh_api(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise TimingsError(f"gh api {path} failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def _ts(s):
    return datetime.fromisoformat(s).timestamp()


def _split(job):
    """Return (queue, setup, run) seconds, or None if the job did not finish."""
    created, started, done = (
        job.get("created_at"),
        job.get("started_at"),
        job.get("completed_at"),
    )
    if not (created and started and done) or job.get("conclusion") == "skipped":
        return None
    t_created, t_started, t_done = _ts(created), _ts(started), _ts(done)
    if t_done < t_started:
        return None
    boundary = t_started
    for step in job.get("steps") or []:
        if SETUP_STEP.match(step.get("name", "")):
            if step.get("completed_at"):
                boundary = max(boundary, _ts(step["completed_at"]))
        else:
            break
    boundary = min(boundary, t_done)
    return (max(t_started - t_created, 0), boundary - t_started, t_done - boundary)


def _list_runs(repo, limit, gh):
    runs, page = [], 1
    while len(runs) < limit:
        per_page = min(limit, MAX_PAGE)
        got = gh(f"repos/{repo}/actions/runs?per_page={per_page}&page={page}").get(
            "workflow_runs", []
        )
        runs.extend(got)
        if len(got) < per_page:
            break
        page += 1
    return runs[:limit]


def collect(repo, runs, gh=gh_api):
    """Return {job name: {"count": n, "queue"|"setup"|"run": {"median", "total"}}}."""
    samples = {}
    for run in _list_runs(repo, runs, gh):
        for job in gh(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page={MAX_PAGE}").get("jobs", []):
            parts = _split(job)
            if parts:
                samples.setdefault(job["name"], []).append(parts)
    stats = {}
    for name, rows in samples.items():
        entry: dict = {"count": len(rows)}
        for i, key in enumerate(("queue", "setup", "run")):
            col = [r[i] for r in rows]
            entry[key] = {"median": statistics.median(col), "total": sum(col)}
        stats[name] = entry
    return stats


def fmt(seconds):
    s = round(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


def report(repo, runs, gh=gh_api):
    stats = collect(repo, runs, gh)
    if not stats:
        return f"no workflow runs with finished jobs found for {repo}"
    head = [
        "job",
        "runs",
        "queue med",
        "setup med",
        "run med",
        "queue tot",
        "setup tot",
        "run tot",
    ]
    rows = [head]
    for name, s in sorted(stats.items()):
        rows.append(
            [name, str(s["count"])]
            + [fmt(s[k]["median"]) for k in ("queue", "setup", "run")]
            + [fmt(s[k]["total"]) for k in ("queue", "setup", "run")]
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(head))]
    return "\n".join(
        "  ".join(
            c.ljust(widths[i]) if i == 0 else c.rjust(widths[i])
            for i, c in enumerate(r)
        ).rstrip()
        for r in rows
    )


def run(repo, runs):
    print(report(repo, runs))

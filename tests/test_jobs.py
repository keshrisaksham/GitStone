"""Tests for the web UI's background job runner, against a real local
HTTP server (same pattern as test_gitstone.py) so a job actually dumps
something real rather than being mocked away."""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "GitStone-Packages"))

from gitstone_webui import jobs as jobmod  # noqa: E402
# Importing the fixture function itself (not just calling it) registers it
# with pytest for this module too, so test_jobs.py gets the same no-listing
# HTTP test server as test_gitstone.py without redefining it.
from test_gitstone import server  # noqa: E402,F401


def test_parse_lines_strips_blanks_and_comments():
    assert jobmod._parse_lines("a\n# comment\n\n  b  \n") == ["a", "b"]


def test_parse_headers_builds_dict_with_user_agent():
    headers = jobmod._parse_headers("X-Foo=bar\nX-Baz = qux \n", "my-ua")
    assert headers == {"User-Agent": "my-ua", "X-Foo": "bar", "X-Baz": "qux"}


def test_parse_headers_rejects_bad_line():
    with pytest.raises(ValueError):
        jobmod._parse_headers("not-a-header-line", "ua")


def test_job_id_regex():
    job_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), "0" * 8)
    assert jobmod.JOB_ID_RE.match(job_id)
    assert not jobmod.JOB_ID_RE.match("../../etc/passwd")
    assert not jobmod.JOB_ID_RE.match("")


@pytest.fixture
def manager(tmp_path):
    return jobmod.JobManager(str(tmp_path / "workspace"))


def test_manager_rejects_unsafe_job_id(manager):
    assert manager.get("../../etc/passwd") is None
    assert manager.get("") is None
    assert manager.get(None) is None


def test_submit_runs_and_persists_a_dump(manager, server):
    job = manager.submit(server.url, jobmod.JobOptions(jobs=2, timeout=5))

    for _ in range(100):
        if job.status in ("done", "failed"):
            break
        time.sleep(0.1)

    assert job.status == "done", job.log_tail()
    assert job.stats["fetched"] > 0
    assert os.path.isfile(os.path.join(job.repo_dir, "flag.txt"))
    assert "Fetching" in job.log_tail()

    # metadata survives a fresh manager (simulating an app restart)
    reloaded = jobmod.JobManager(manager.workspace)
    same_job = reloaded.get(job.id)
    assert same_job is not None
    assert same_job.status == "done"
    assert same_job.stats["fetched"] == job.stats["fetched"]


def test_submit_bad_proxy_fails_cleanly(manager, server):
    job = manager.submit(server.url, jobmod.JobOptions(proxy="not-a-proxy"))
    for _ in range(50):
        if job.status in ("done", "failed"):
            break
        time.sleep(0.1)
    assert job.status == "failed"
    assert "proxy" in job.log_tail()


def test_jobs_run_one_at_a_time(manager, server):
    """ Two jobs submitted back to back must not interleave their log
    output: the second one's log must not contain any lines until the
    first is finished. """
    job1 = manager.submit(server.url, jobmod.JobOptions(delay=0.05, jobs=1))
    job2 = manager.submit(server.url, jobmod.JobOptions(jobs=2))

    time.sleep(0.2)  # job1 should be well underway, job2 still queued
    assert job2.status == "queued"
    assert job1.status == "running"

    for _ in range(100):
        if job2.status in ("done", "failed"):
            break
        time.sleep(0.1)

    assert job1.status == "done"
    assert job2.status == "done"


def test_delete_removes_job_directory(manager, server):
    job = manager.submit(server.url, jobmod.JobOptions())
    for _ in range(50):
        if job.status in ("done", "failed"):
            break
        time.sleep(0.1)

    assert manager.delete(job.id) is True
    assert not os.path.isdir(job.dir)
    assert manager.get(job.id) is None


def test_delete_refuses_while_running(manager, server):
    job = manager.submit(server.url, jobmod.JobOptions(delay=0.2))
    time.sleep(0.05)
    assert job.status == "running"
    assert manager.delete(job.id) is False

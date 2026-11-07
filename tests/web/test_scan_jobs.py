"""Background scans and searches."""
import time
import threading

from tests.helpers import import_dump, post, stage_password_edit
from gitstone.analysis import walk


def _wait_job(app, client, dump_id, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get("/dumps/%s/scans/%s/status.json" % (dump_id, job_id)).get_json()
        if not status["active"]:
            return status
        time.sleep(0.05)
    raise AssertionError("scan job still running")


def test_background_secrets_scan_runs_reports_and_renders_the_results(app, client, repo):
    stage_password_edit(repo)
    dump_id = import_dump(app, client, repo)
    resp = client.get("/dumps/%s/secrets?run=1&bg=1" % dump_id)
    assert resp.status_code == 302
    job_id = resp.headers["Location"].rstrip("/").split("/")[-1]
    status = _wait_job(app, client, dump_id, job_id)
    assert status["state"] == "done" and status["phase"] == "Done"
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "OctopiFociPilfer45" in page and "uncommitted (staged)" in page


def test_background_search_runs_and_renders_the_results(app, client, repo):
    dump_id = import_dump(app, client, repo)
    resp = client.get("/dumps/%s/search/master?scope=blobs&q=secret&bg=1" % dump_id)
    assert resp.status_code == 302
    job_id = resp.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait_job(app, client, dump_id, job_id)["state"] == "done"
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "flag.txt" in page and "match" in page


def test_a_running_job_page_shows_progress_and_can_be_cancelled(app, client, repo):
    dump_id = import_dump(app, client, repo)
    started = threading.Event()

    def forever(job):
        job.progress("Scanning files", 3, 10)
        started.set()
        while not job.should_stop():
            time.sleep(0.01)
        raise walk.ScanCancelled()

    job = app.scans.start(dump_id, "secrets", {"custom": "", "meta": False, "entropy": False, "bg": True}, forever)
    assert started.wait(5)
    page = client.get("/dumps/%s/scans/%s" % (dump_id, job.id)).get_data(as_text=True)
    assert "data-scan-status" in page and "Scanning files" in page and "Cancel" in page
    status = client.get("/dumps/%s/scans/%s/status.json" % (dump_id, job.id)).get_json()
    assert status["active"] and (status["done"], status["total"]) == (3, 10)

    csrf = client.get("/dumps/%s/scans/%s" % (dump_id, job.id)).get_data(as_text=True)
    token = csrf.split('name="csrf" value="')[1].split('"')[0]
    assert client.post("/dumps/%s/scans/%s/cancel" % (dump_id, job.id), data={}).status_code == 400  # CSRF
    assert client.post("/dumps/%s/scans/%s/cancel" % (dump_id, job.id), data={"csrf": token}).status_code == 302
    assert _wait_job(app, client, dump_id, job.id)["state"] == "cancelled"
    assert "was cancelled" in client.get("/dumps/%s/scans/%s" % (dump_id, job.id)).get_data(as_text=True)


def test_a_failing_job_reports_the_error_and_other_dumps_cannot_see_it(app, client, repo):
    dump_id = import_dump(app, client, repo)

    def boom(job):
        raise RuntimeError("kaput")

    job = app.scans.start(dump_id, "secrets", {"custom": "", "meta": False, "entropy": False, "bg": True}, boom)
    assert _wait_job(app, client, dump_id, job.id)["state"] == "failed"
    assert "kaput" in client.get("/dumps/%s/scans/%s" % (dump_id, job.id)).get_data(as_text=True)
    assert client.get("/dumps/%s/scans/nosuchjob/status.json" % dump_id).status_code == 404
    other = import_dump(app, client, repo)
    assert client.get("/dumps/%s/scans/%s" % (other, job.id)).status_code == 404

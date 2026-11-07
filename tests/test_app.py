"""Flask-level tests for the web UI: the HTTP surface the manual curl session
exercised earlier, now automated, plus the import-from-disk and client-cert
features."""
import os
import re
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "GitStone-Packages"))

from gitstone_webui.app import create_app  # noqa: E402
from test_gitstone import server  # noqa: E402,F401 (shared no-listing HTTP server)
from conftest import git  # noqa: E402


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "workspace"))


@pytest.fixture
def client(app):
    return app.test_client()


def get_csrf(client, path="/new"):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "no csrf token found on %s" % path
    return m.group(1)


def wait_for(app, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = app.jobs.get(job_id)
        if job.status in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job %s did not finish in time" % job_id)


def job_id_from_redirect(response):
    assert response.status_code == 302, response.get_data(as_text=True)
    return response.headers["Location"].rsplit("/", 1)[-1]


# ------------------------------------------------------------- dashboard/new


def test_dashboard_empty(client):
    assert b"No dumps yet" in client.get("/").get_data()


def test_new_job_requires_csrf(client):
    resp = client.post("/new", data={"url": "http://x/.git"})
    assert resp.status_code == 400


def test_new_job_requires_url(client):
    csrf = get_csrf(client)
    resp = client.post("/new", data={"csrf": csrf, "url": ""})
    assert resp.status_code == 400
    assert b"URL is required" in resp.get_data()


def test_new_job_submits_and_dumps(app, client, server):
    csrf = get_csrf(client)
    resp = client.post("/new", data={
        "csrf": csrf, "url": server.url, "jobs": "3", "retry": "3",
        "timeout": "5", "delay": "0", "method": "GET",
        "user_agent": "test-agent", "headers": "", "cookie": "",
        "branches": "", "proxy": "",
    })
    job_id = job_id_from_redirect(resp)
    job = wait_for(app, job_id)
    assert job.status == "done", job.log_tail()
    assert job.kind == "dump"
    assert job.stats["fetched"] > 0

    tree = client.get("/jobs/%s/tree/master/" % job_id).get_data(as_text=True)
    assert "flag.txt" in tree


def test_new_job_bad_client_cert_fails_before_any_network_call(app, client, server):
    csrf = get_csrf(client)
    resp = client.post("/new", data={
        "csrf": csrf, "url": server.url, "jobs": "1", "retry": "1",
        "timeout": "5", "delay": "0", "method": "GET",
        "user_agent": "ua", "headers": "", "cookie": "", "branches": "",
        "proxy": "", "client_cert_p12": "/no/such/cert.p12",
        "client_cert_p12_password": "hunter2",
    })
    job_id = job_id_from_redirect(resp)
    job = wait_for(app, job_id)
    assert job.status == "failed"
    assert "does not exist" in job.log_tail()
    assert not server.requests  # never touched the target


def test_client_cert_password_never_written_to_meta(app, client, server):
    csrf = get_csrf(client)
    resp = client.post("/new", data={
        "csrf": csrf, "url": server.url, "jobs": "1", "retry": "1",
        "timeout": "5", "delay": "0", "method": "GET",
        "user_agent": "ua", "headers": "", "cookie": "", "branches": "",
        "proxy": "", "client_cert_p12": "/no/such/cert.p12",
        "client_cert_p12_password": "hunter2-super-secret",
    })
    job_id = job_id_from_redirect(resp)
    wait_for(app, job_id)
    meta_path = os.path.join(app.jobs.workspace, job_id, "meta.json")
    with open(meta_path) as f:
        raw = f.read()
    assert "hunter2-super-secret" not in raw


def test_new_job_invalid_option_shows_error(client):
    csrf = get_csrf(client)
    resp = client.post("/new", data={"csrf": csrf, "url": "http://x/.git", "jobs": "not-a-number"})
    assert resp.status_code == 400
    assert b"invalid option" in resp.get_data()


# ------------------------------------------------------------------- import


def test_import_requires_csrf(client, repo):
    resp = client.post("/import", data={"path": str(repo)})
    assert resp.status_code == 400


def test_import_requires_existing_directory(client):
    csrf = get_csrf(client, "/import")
    resp = client.post("/import", data={"csrf": csrf, "path": "/no/such/directory"})
    assert resp.status_code == 400
    assert b"is not a directory" in resp.get_data()


def test_import_copies_and_is_immediately_browsable_offline(app, client, repo):
    """ The whole point: no HTTP server involved anywhere in this test. """
    csrf = get_csrf(client, "/import")
    resp = client.post("/import", data={"csrf": csrf, "path": str(repo)})
    job_id = job_id_from_redirect(resp)
    job = wait_for(app, job_id)

    assert job.status == "done", job.log_tail()
    assert job.kind == "import"
    assert job.stats["fetched"] > 0

    blob = client.get("/jobs/%s/blob/master/flag.txt" % job_id).get_data(as_text=True)
    assert "secret v2" in blob

    # the operator's original directory must still be intact and untouched
    assert (repo / "flag.txt").read_text() == "secret v2\n"


def test_import_of_non_repo_directory_fails_cleanly(app, client, tmp_path):
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    (empty / "readme.txt").write_text("hi")

    csrf = get_csrf(client, "/import")
    resp = client.post("/import", data={"csrf": csrf, "path": str(empty)})
    job_id = job_id_from_redirect(resp)
    job = wait_for(app, job_id)

    assert job.status == "failed"
    assert "RepoError" not in job.log_tail()  # message is human text, not a repr
    assert os.path.isfile(os.path.join(job.repo_dir, "readme.txt"))  # left for inspection


def test_deleting_an_import_does_not_touch_the_original(app, client, repo):
    csrf = get_csrf(client, "/import")
    resp = client.post("/import", data={"csrf": csrf, "path": str(repo)})
    job_id = job_id_from_redirect(resp)
    wait_for(app, job_id)

    csrf2 = get_csrf(client, "/")
    client.post("/jobs/%s/delete" % job_id, data={"csrf": csrf2})
    assert app.jobs.get(job_id) is None
    assert (repo / "flag.txt").is_file()  # original untouched


# -------------------------------------------------------- security surface


def test_raw_download_is_forced_attachment_with_nosniff(app, client, server):
    csrf = get_csrf(client)
    resp = client.post("/new", data={
        "csrf": csrf, "url": server.url, "jobs": "2", "retry": "3",
        "timeout": "5", "delay": "0", "method": "GET", "user_agent": "ua",
        "headers": "", "cookie": "", "branches": "", "proxy": "",
    })
    job_id = job_id_from_redirect(resp)
    wait_for(app, job_id)

    resp = client.get("/jobs/%s/raw/master/flag.txt" % job_id)
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in resp.headers["Content-Disposition"]


@pytest.mark.parametrize("job_id", [
    "not-a-real-id",            # wrong format entirely
    "00000000-000000-00000000", # right format, but no such job
])
def test_bad_job_id_is_404(client, job_id):
    assert client.get("/jobs/%s/tree/master/" % job_id).status_code == 404


def test_unsafe_ref_is_404(app, client, server):
    """ End-to-end check that an unsafe ref never even reaches gitview --
    the single-segment case (no URL-encoding ambiguity to worry about).
    Path-traversal-via-subpath is exercised directly against gitview in
    test_gitview.py instead, where there's no WSGI-layer decoding in the way
    of proving what the validation actually does. """
    csrf = get_csrf(client)
    resp = client.post("/new", data={
        "csrf": csrf, "url": server.url, "jobs": "2", "retry": "3",
        "timeout": "5", "delay": "0", "method": "GET", "user_agent": "ua",
        "headers": "", "cookie": "", "branches": "", "proxy": "",
    })
    job_id = job_id_from_redirect(resp)
    wait_for(app, job_id)

    assert client.get("/jobs/%s/tree/-rf/" % job_id).status_code == 404
    assert client.get("/jobs/%s/commit/not-a-sha" % job_id).status_code == 404


def test_secrets_scan_finds_leaked_and_later_removed_secret(app, client, repo):
    (repo / "config.env").write_text('AWS_SECRET_ACCESS_KEY: "AKIAABCDEFGHIJKLMNOP"\n')
    git(repo, "add", "config.env")
    git(repo, "commit", "-q", "-m", "oops")
    (repo / "config.env").unlink()
    git(repo, "rm", "-q", "config.env")
    git(repo, "commit", "-q", "-m", "remove it")

    csrf = get_csrf(client, "/import")
    resp = client.post("/import", data={"csrf": csrf, "path": str(repo)})
    job_id = job_id_from_redirect(resp)
    wait_for(app, job_id)

    page = client.get("/jobs/%s/secrets?run=1" % job_id).get_data(as_text=True)
    assert "AKIAABCDEFGHIJKLMNOP" in page


def test_python_dash_m_entrypoint_runs():
    """ `cd GitStone-Packages && python3 -m gitstone_webui` has to work with nothing
    pip-installed -- that's the whole point of documenting it as a no-install
    option. -I isolated mode is deliberately NOT used here: besides ignoring
    PYTHONPATH, -I also stops `-m` from prepending the current directory to
    sys.path at all, which is exactly the mechanism this command relies on to
    find `gitstone_webui` in the first place. Using -I previously let this
    test pass for the wrong reason -- only because whatever venv happened to
    be running pytest already had the package pip-installed, which papered
    over `-m`'s cwd resolution never actually being exercised. """
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "gitstone_webui", "--help"],
        cwd=os.path.join(ROOT, "GitStone-Packages"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout
    assert "gitstone-ui" in result.stdout

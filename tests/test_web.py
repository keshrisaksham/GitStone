"""Flask-level tests for the web UI, through the test client."""
import io
import os
import re
import time
import zipfile

import pytest

from conftest import git
from gitstone import actions
from gitstone.web import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "workspace"))


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_of(client, path="/new"):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "no csrf token found on %s" % path
    return m.group(1)


def post(client, path, data=None, csrf_from="/new", **kw):
    data = dict(data or {})
    data.setdefault("csrf", csrf_of(client, csrf_from))
    return client.post(path, data=data, **kw)


def ids_from(response):
    """(dump_id, run_id) from the redirect to /dumps/<id>[/runs/<n>]."""
    assert response.status_code == 302, response.get_data(as_text=True)
    parts = response.headers["Location"].split("/")
    dump_id = parts[parts.index("dumps") + 1]
    run_id = int(parts[-1]) if "runs" in parts else None
    return dump_id, run_id


def wait_run(app, dump_id, run_id, timeout=20):
    dump = app.store.get(dump_id)
    run = dump.get_run(run_id)
    deadline = time.time() + timeout
    while time.time() < deadline and run.active:
        time.sleep(0.05)
    assert not run.active, "run still %s" % run.status
    return run


def dump_it(app, client, server, **extra):
    data = {"url": server.url, "jobs": "3", "timeout": "5", "start_now": "on", **extra}
    dump_id, run_id = ids_from(post(client, "/new", data))
    return dump_id, wait_run(app, dump_id, run_id)


# ------------------------------------------------------------- pages


def test_dashboard_empty(client):
    assert b"No dumps yet" in client.get("/").get_data()


def test_functions_page_lists_every_action(client):
    html = client.get("/functions").get_data(as_text=True)
    for a in actions.REGISTRY.values():
        assert a.title in html


def test_new_dump_page_renders_the_dump_actions_params(client):
    html = client.get("/new").get_data(as_text=True)
    for name in ("url", "jobs", "retry", "timeout", "proxy", "headers", "branches",
                 "client_cert_p12_password"):
        assert 'name="%s"' % name in html


# ---------------------------------------------------------- creating dumps


def test_new_dump_requires_csrf(client):
    assert client.post("/new", data={"url": "http://x/.git"}).status_code == 400


def test_new_dump_requires_url(client):
    resp = post(client, "/new", {"url": ""}, csrf_from="/new")
    assert resp.status_code == 400 and b"URL is required" in resp.get_data()


def test_new_dump_invalid_option_shows_error_and_keeps_the_input(client):
    resp = post(client, "/new", {"url": "http://x/.git", "jobs": "many"}, csrf_from="/new")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "must be a number" in body
    assert "http://x/.git" in body


def test_new_dump_starts_a_full_dump_and_is_browsable(app, client, server):
    dump_id, run = dump_it(app, client, server)
    assert run.status == "done", app.store.read_log(app.store.get(dump_id), run)[0]
    assert run.stats["fetched"] > 0

    tree = client.get("/dumps/%s/tree/master/" % dump_id).get_data(as_text=True)
    assert "flag.txt" in tree
    page = client.get("/dumps/%s" % dump_id).get_data(as_text=True)
    assert "Browse repository" in page and "download.zip" in page


def test_new_dump_without_start_now_creates_an_empty_dump(app, client):
    resp = post(client, "/new", {"url": "http://x/.git", "timeout": "5"}, csrf_from="/new")
    dump_id, run_id = ids_from(resp)
    assert run_id is None and app.store.get(dump_id).runs == []
    page = client.get("/dumps/%s" % dump_id).get_data(as_text=True)
    assert "Nothing has been run" in page and "Probe target" in page


def test_dump_settings_prefill_the_functions_forms(app, client):
    resp = post(client, "/new", {"url": "http://x/.git", "jobs": "7", "cookie": "sid=1"}, csrf_from="/new")
    dump_id, _ = ids_from(resp)
    page = client.get("/dumps/%s" % dump_id).get_data(as_text=True)
    assert 'value="7"' in page and "sid=1" in page


# ------------------------------------------------------------- functions


def test_run_a_single_function_on_a_dump(app, client, server):
    dump_id, _ = ids_from(post(client, "/new", {"url": server.url, "timeout": "5"}, csrf_from="/new"))

    dump_id, run_id = ids_from(post(client, "/dumps/%s/run/probe" % dump_id, {"timeout": "5"}))
    run = wait_run(app, dump_id, run_id)
    assert run.status == "done"

    page = client.get("/dumps/%s/runs/%d" % (dump_id, run_id)).get_data(as_text=True)
    assert "exposed git repository" in page and "Probe target" in page


def test_functions_can_be_chained_on_one_dump(app, client, server):
    dump_id, _ = ids_from(post(client, "/new", {"url": server.url, "timeout": "5"}, csrf_from="/new"))
    for step in ("fetch_common", "find_refs", "fetch_objects", "checkout", "verify"):
        _, run_id = ids_from(post(client, "/dumps/%s/run/%s" % (dump_id, step),
                                  {"jobs": "2", "timeout": "5"}))
        assert wait_run(app, dump_id, run_id).status == "done", step
    assert os.path.isfile(os.path.join(app.store.get(dump_id).repo_dir, "flag.txt"))
    assert "Nothing missing" in app.store.read_log(
        app.store.get(dump_id), app.store.get(dump_id).latest_run)[0]


def test_unknown_function_or_wrong_kind_is_404(app, client, repo):
    dump_id, _ = ids_from(post(client, "/new", {"url": "http://x/.git"}, csrf_from="/new"))
    assert post(client, "/dumps/%s/run/nope" % dump_id).status_code == 404
    assert post(client, "/dumps/%s/run/import_local" % dump_id).status_code == 404  # local-only


def test_run_requires_csrf(app, client):
    dump_id, _ = ids_from(post(client, "/new", {"url": "http://x/.git"}, csrf_from="/new"))
    assert client.post("/dumps/%s/run/probe" % dump_id).status_code == 400


def test_bad_function_input_is_shown_on_the_dump_page(app, client):
    dump_id, _ = ids_from(post(client, "/new", {"url": "http://x/.git"}, csrf_from="/new"))
    resp = post(client, "/dumps/%s/run/probe" % dump_id, {"retry": "0"})
    assert resp.status_code == 400 and b"Attempts per request must be at least 1" in resp.get_data()
    assert app.store.get(dump_id).runs == []


def test_a_bad_client_cert_fails_the_run_before_any_request(app, client, server):
    dump_id, run = dump_it(app, client, server, client_cert_p12="/no/such/cert.p12",
                           client_cert_p12_password="hunter2-super-secret")
    assert run.status == "failed"
    assert "does not exist" in app.store.read_log(app.store.get(dump_id), run)[0]
    assert not server.requests
    with open(app.store.get(dump_id).meta_path) as f:
        assert "hunter2-super-secret" not in f.read()


def test_run_status_json_streams_the_log_incrementally(app, client, server):
    dump_id, run = dump_it(app, client, server)
    first = client.get("/dumps/%s/runs/%d/status.json?offset=0" % (dump_id, run.id)).get_json()
    assert first["status"] == "done" and not first["active"]
    assert "Fetching" in first["log"] and first["stats"]["fetched"] > 0
    again = client.get("/dumps/%s/runs/%d/status.json?offset=%d" % (dump_id, run.id, first["offset"])).get_json()
    assert again["log"] == ""


def test_stop_button(app, client, server):
    data = {"url": server.url, "jobs": "1", "delay": "0.3", "timeout": "5", "start_now": "on"}
    dump_id, run_id = ids_from(post(client, "/new", data))
    time.sleep(0.5)
    resp = post(client, "/dumps/%s/runs/%d/cancel" % (dump_id, run_id))
    assert resp.status_code == 302
    assert wait_run(app, dump_id, run_id).status == "cancelled"


def test_delete_dump(app, client, server):
    dump_id, _ = dump_it(app, client, server)
    assert post(client, "/dumps/%s/delete" % dump_id).status_code == 302
    assert app.store.get(dump_id) is None
    assert client.get("/dumps/%s" % dump_id).status_code == 404


# ---------------------------------------------------------------- import


def test_import_requires_csrf(client, repo):
    assert client.post("/import", data={"path": str(repo)}).status_code == 400


def test_import_requires_a_repository_directory(client, tmp_path):
    for path, message in (("", b"path is required"), ("/no/such/dir", b"has no .git directory"),
                          (str(tmp_path), b"has no .git directory")):
        resp = post(client, "/import", {"path": path}, csrf_from="/import")
        assert resp.status_code == 400 and message in resp.get_data()


def test_import_copies_and_is_browsable_offline(app, client, repo):
    """No HTTP server is involved anywhere in this test."""
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    run = wait_run(app, dump_id, run_id)
    assert run.status == "done", app.store.read_log(app.store.get(dump_id), run)[0]
    assert app.store.get(dump_id).kind == "local"

    blob = client.get("/dumps/%s/blob/master/flag.txt" % dump_id).get_data(as_text=True)
    assert "secret v2" in blob
    page = client.get("/dumps/%s" % dump_id).get_data(as_text=True)
    assert "Import from disk" in page and "Full dump" not in page

    post(client, "/dumps/%s/delete" % dump_id)
    assert (repo / "flag.txt").is_file()  # the original is untouched


def test_import_roots(tmp_path, repo):
    inside = tmp_path / "allowed"
    inside.mkdir()
    import shutil
    shutil.copytree(repo, inside / "site")

    disabled = create_app(str(tmp_path / "w1"), import_roots=[]).test_client()
    assert b"disabled" in disabled.get("/import").get_data()
    assert post(disabled, "/import", {"path": str(repo)}).status_code == 403  # no form, but a crafted POST

    limited = create_app(str(tmp_path / "w2"), import_roots=[str(inside)]).test_client()
    outside = post(limited, "/import", {"path": str(repo)}, csrf_from="/import")
    assert outside.status_code == 400 and b"outside the allowed" in outside.get_data()
    sneaky = post(limited, "/import", {"path": str(inside / ".." / "site")}, csrf_from="/import")
    assert sneaky.status_code == 400
    assert post(limited, "/import", {"path": str(inside / "site")}, csrf_from="/import").status_code == 302


# ------------------------------------------------------------- browsing


def test_raw_download_is_forced_attachment_with_nosniff(app, client, server):
    dump_id, _ = dump_it(app, client, server)
    resp = client.get("/dumps/%s/raw/master/flag.txt" % dump_id)
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in resp.headers["Content-Disposition"]


@pytest.mark.parametrize("dump_id", ["not-a-real-id", "00000000-000000-00000000"])
def test_bad_dump_id_is_404(client, dump_id):
    assert client.get("/dumps/%s/tree/master/" % dump_id).status_code == 404
    assert client.get("/dumps/%s" % dump_id).status_code == 404


def test_unsafe_ref_is_404(app, client, server):
    dump_id, _ = dump_it(app, client, server)
    assert client.get("/dumps/%s/tree/-rf/" % dump_id).status_code == 404
    assert client.get("/dumps/%s/commit/not-a-sha" % dump_id).status_code == 404


def test_browsing_an_empty_dump_is_a_clean_404(app, client):
    dump_id, _ = ids_from(post(client, "/new", {"url": "http://x/.git"}, csrf_from="/new"))
    assert client.get("/dumps/%s/refs" % dump_id).status_code in (404,)


def test_secrets_scan_finds_leaked_and_later_removed_secret(app, client, repo):
    (repo / "config.env").write_text('AWS_SECRET_ACCESS_KEY: "AKIAABCDEFGHIJKLMNOP"\n')
    git(repo, "add", "config.env")
    git(repo, "commit", "-q", "-m", "oops")
    git(repo, "rm", "-q", "config.env")
    git(repo, "commit", "-q", "-m", "remove it")

    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    page = client.get("/dumps/%s/secrets?run=1" % dump_id).get_data(as_text=True)
    assert "AKIAABCDEFGHIJKLMNOP" in page


def test_recovered_content_is_escaped(app, client, repo):
    (repo / "evil.html").write_text("<script>alert(1)</script>\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "<img src=x onerror=alert(2)>")
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    for path in ("/blob/master/evil.html", "/commits/master"):
        body = client.get("/dumps/%s%s" % (dump_id, path)).get_data(as_text=True)
        assert "<script>alert" not in body and "<img src=x" not in body


def test_download_zip_has_the_files_and_never_follows_symlinks(app, client, server, tmp_path):
    dump_id, _ = dump_it(app, client, server)
    dump = app.store.get(dump_id)
    secret = tmp_path / "server-secret.txt"
    secret.write_text("TOP SECRET")
    os.symlink(secret, os.path.join(dump.repo_dir, "innocent.txt"))  # as a hostile repo might

    resp = client.get("/dumps/%s/download.zip" % dump_id)
    assert resp.status_code == 200 and resp.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.get_data()))
    names = zf.namelist()
    assert "flag.txt" in names and ".git/HEAD" in names
    assert "innocent.txt" not in names
    assert b"TOP SECRET" not in resp.get_data()

    bare = zipfile.ZipFile(io.BytesIO(client.get("/dumps/%s/download.zip?git=0" % dump_id).get_data()))
    assert "flag.txt" in bare.namelist() and not any(n.startswith(".git/") for n in bare.namelist())

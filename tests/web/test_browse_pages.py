"""Browsing: files, blobs, back links."""

from tests.helpers import git, ids_from, import_dump, post, stage_password_edit, wait_run
from gitstone.viewer import history, reader


def test_browsing_a_blob_at_a_commit_sha(app, client, repo, opened):
    commits, _ = reader.log_commits(opened, reader.resolve_commit(opened, "master")[1])
    first = commits[-1].sha
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    page = client.get("/dumps/%s/blob/%s/flag.txt" % (dump_id, first)).get_data(as_text=True)
    assert "as of version" in page and ">secret<" in page.replace("</span>", "<").replace("<span", "<")
    page = client.get("/dumps/%s/commit/%s" % (dump_id, first)).get_data(as_text=True)
    assert "Browse files at this version" in page


def test_blob_page_is_highlighted_and_has_line_anchors(app, client, repo):
    (repo / "app.py").write_text("import os\nprint('hi')\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "py")
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    page = client.get("/dumps/%s/blob/master/app.py?line=2" % dump_id).get_data(as_text=True)
    assert 'class="k' in page and 'id="L2"' in page and 'href="#L2"' in page
    assert 'class="hl-line"' in page and "Python" in page


def test_back_link_hookpoint_is_on_every_destination_page(app, client, repo):
    """data-back-link is what common.js fills in from document.referrer (a same-
    origin navigation only shows it, see Referrer-Policy: same-origin in
    test_security.py); here we just check every page that can be reached from
    search/secrets/commits renders the hook so the JS has something to find."""
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    commit_sha = reader.resolve_commit(reader.open_repo(str(repo)), "master")[1]
    for url in (
        "/tree/master/", "/blob/master/flag.txt", "/commits/master",
        "/commit/%s" % commit_sha, "/compare?base=%s&head=master" % commit_sha,
    ):
        body = client.get("/dumps/%s%s" % (dump_id, url)).get_data(as_text=True)
        assert 'data-back-link hidden' in body, url


def test_blob_sha_page_defaults_to_secrets_scan_without_js(app, client, repo):
    (repo / "creds.env").write_text("PASSWORD=Sup3rS3cretPw\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "creds")
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    sha = reader.read_blob(reader.open_repo(str(repo)), reader.resolve_commit(reader.open_repo(str(repo)), "master")[0], "creds.env")[0].sha
    body = client.get("/dumps/%s/blob-by-sha/%s" % (dump_id, sha)).get_data(as_text=True)
    assert 'data-back-fallback="/dumps/%s/secrets"' % dump_id in body
    assert '&larr; Secrets scan' in body  # rendered server-side: works with JS off too


def test_blob_by_sha_uses_the_name_hint_for_language_and_title(app, client, repo):
    stage_password_edit(repo)
    dump_id = import_dump(app, client, repo)
    r = reader.open_repo(str(repo))
    sha = history.index_entries(r)["auth.js"][1].decode()
    r.close()
    base = "/dumps/%s/blob-by-sha/%s" % (dump_id, sha)
    hinted = client.get(base + "?name=auth.js").get_data(as_text=True)
    assert "auth.js" in hinted and "lang-javascript" in hinted and 'class="kd"' in hinted
    bare = client.get(base).get_data(as_text=True)  # no hint: still highlighted, by content
    assert "lang-javascript" in bare and 'class="kd"' in bare

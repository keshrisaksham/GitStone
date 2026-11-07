"""The secrets scan page."""
import re

from tests.helpers import git, git_config_with_credentials, ids_from, import_dump, post, wait_run


def test_secrets_page_offers_hide_duplicates_when_there_are_any(app, client, repo):
    (repo / "a.env").write_text("APP=a\nAPI_TOKEN=abcdefghijklmnop\n")
    (repo / "b.env").write_text("APP=b\nAPI_TOKEN=abcdefghijklmnop\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "dup token")
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    page = client.get("/dumps/%s/secrets?run=1" % dump_id).get_data(as_text=True)
    assert "data-dedupe-filter" in page and "hide duplicates" in page
    assert 'data-dup="1"' in page and 'data-dup="0"' in page


def test_secrets_page_links_open_in_the_side_pane(app, client, repo):
    (repo / "creds.env").write_text("x=1\nPASSWORD=Sup3rS3cretPw\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "creds")
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)

    page = client.get("/dumps/%s/secrets?run=1" % dump_id).get_data(as_text=True)
    assert 'id="blob-pane"' in page and "data-pane" in page and "creds.env:2" in page

    import re
    href = re.search(r'data-pane href="([^"]+)"', page).group(1).replace("&amp;", "&")
    frag = client.get(href + "&partial=1")
    body = frag.get_data(as_text=True)
    assert frag.status_code == 200 and "<html" not in body       # just the fragment
    assert "Sup3rS3cretPw" in body and 'class="hl-line"' in body  # flagged line highlighted
    assert "creds.env" in body

    full = client.get(href).get_data(as_text=True)               # no-JS fallback
    assert "<html" in full and "Sup3rS3cretPw" in full

    assert client.get("/dumps/%s/blob-by-sha/%s" % (dump_id, "0" * 40)).status_code == 404
    assert client.get("/dumps/%s/blob-by-sha/nothex" % dump_id).status_code == 404


def test_secrets_page_bad_custom_regex(app, client, repo):
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    resp = client.get("/dumps/%s/secrets?run=1&custom=(" % dump_id)
    assert resp.status_code == 200 and b"invalid regular expression" in resp.data


def test_secrets_page_options_and_links_for_metadata_findings(app, client, repo):
    git_config_with_credentials(repo)
    dump_id = import_dump(app, client, repo)
    form = client.get("/dumps/%s/secrets" % dump_id).get_data(as_text=True)
    for name in ('name="meta"', 'name="entropy"', 'name="bg"'):
        assert name in form
    page = client.get("/dumps/%s/secrets?run=1&meta=1" % dump_id).get_data(as_text=True)
    assert "/gitfile/config" in page and "git file" in page
    assert 'name="meta" value="1" checked' in page

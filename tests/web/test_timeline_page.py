"""The timeline page."""
import re

from tests.helpers import commit_all, git, ids_from, import_dump, post, wait_run


def test_timeline_page_shows_gone_files_and_links_to_their_last_content(app, client, repo):
    (repo / "secret.env").write_text("TOKEN=abc\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "add secret.env")
    git(repo, "rm", "-q", "secret.env")
    git(repo, "commit", "-q", "-m", "remove secret.env")
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)

    unrun = client.get("/dumps/%s/timeline" % dump_id).get_data(as_text=True)
    assert "Build timeline" in unrun and "secret.env" not in unrun

    page = client.get("/dumps/%s/timeline?run=1" % dump_id).get_data(as_text=True)
    assert "Gone now" in page and "secret.env" in page and "remove secret.env" in page
    assert 'data-pane' in page and 'id="blob-pane"' in page

    import re
    href = re.search(r'data-pane href="([^"]+)"', page).group(1).replace("&amp;", "&")
    frag = client.get(href + "&partial=1").get_data(as_text=True)
    assert "TOKEN=abc" in frag


def test_timeline_in_sidebar(app, client, repo):
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    page = client.get("/dumps/%s/tree/master/" % dump_id).get_data(as_text=True)
    assert 'href="/dumps/%s/timeline"' % dump_id in page


def test_timeline_page_has_a_hide_duplicates_switch(app, client, repo):
    (repo / "one.env").write_text("TOKEN=copy\n")
    (repo / "two.env").write_text("TOKEN=copy\n")
    commit_all(repo, "same content twice")
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/timeline?run=1" % dump_id).get_data(as_text=True)
    assert "data-dedupe-filter" in page and 'data-dup="1"' in page
    assert ".timeline-events tbody tr" in page


def test_timeline_page_highlights_missing_references(app, client, repo):
    (repo / "Dockerfile").write_text("COPY config.production.json /app/\nCOPY dist/ /app/\n")
    commit_all(repo, "docker")
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/timeline?run=1" % dump_id).get_data(as_text=True)
    assert "Referenced but missing" in page and "config.production.json" in page
    assert "never in this repository" in page and "sensitive" in page
    assert "build output" in page  # dist/ is expected to be absent, so it is marked down
    assert page.index("config.production.json") < page.index("dist/")
    clean = client.get("/dumps/%s/timeline?run=1" % dump_id)
    assert clean.status_code == 200

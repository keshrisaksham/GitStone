"""The History analysis pages, through the Flask test client."""
import csv
import io
import json

import pytest

from tests.helpers import SECRET, import_dump


@pytest.fixture
def dump(app, client, story):
    return import_dump(app, client, story)


def get(client, dump, path="", **kw):
    return client.get("/dumps/%s/history%s" % (dump, path), **kw)


def test_overview_runs_by_itself_on_a_small_history(client, dump):
    html = get(client, dump).get_data(as_text=True)
    assert "Commits analysed" in html and "Alice" in html and "Bob" in html
    assert "sensitive-deleted" in html and "Hotspots" in html and "README.md" in html
    assert "<svg" in html and "chart-bar" in html and "Table view" in html


def test_overview_is_on_request_for_a_long_history(client, dump, monkeypatch):
    from gitstone.web.blueprints import history
    monkeypatch.setattr(history, "AUTO_RUN_MAX", 3)
    html = get(client, dump).get_data(as_text=True)
    assert "analysed on request" in html and "Analyze history" in html and "Hotspots" not in html
    assert "Hotspots" in get(client, dump, "?run=1").get_data(as_text=True)


def test_overview_of_an_empty_dump_says_so(app, client, tmp_path):
    import subprocess
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
    dump_id = import_dump(app, client, empty)
    html = client.get("/dumps/%s/history" % dump_id).get_data(as_text=True)
    assert "No commits could be read" in html


def test_page_titles_follow_the_gitstone_pipe_page_pattern(client, dump):
    for path, name in (("", "History"), ("/pickaxe", "Pickaxe"), ("/secrets", "Secret lifecycle")):
        title = get(client, dump, path).get_data(as_text=True).split("<title>")[1].split("</title>")[0]
        assert title.startswith("GitStone | %s &middot; " % name), title


def test_json_export(client, dump):
    resp = get(client, dump, "/export.json")
    assert resp.status_code == 200 and "attachment" in resp.headers["Content-Disposition"]
    data = json.loads(resp.data)
    assert data["commits_analyzed"] == 7 and {a["name"] for a in data["authors"]} == {"Alice", "Bob"}
    assert any(f["code"] == "sensitive-added" for c in data["commits"] for f in c["flags"])


def test_csv_export_cannot_inject_formulas(app, client, tmp_path):
    import subprocess
    from tests.helpers import commit
    evil = tmp_path / "evil"
    evil.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=evil, check=True)
    (evil / "f").write_text("x\n")
    commit(evil, ("=HYPERLINK(\"http://x\")", "e@x"), "2023-01-01T10:00:00", "=1+1")
    dump_id = import_dump(app, client, evil)
    resp = client.get("/dumps/%s/history/export.csv" % dump_id)
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True))))
    assert rows[0][0] == "sha" and rows[1][2].startswith("'=") and rows[1][4] == "'=1+1"


def test_unknown_export_format_is_404(client, dump):
    assert get(client, dump, "/export.xml").status_code == 404


# ---------------------------------------------------------------- pickaxe


def test_pickaxe_page_shows_the_form_then_the_commits(client, dump):
    assert "Search history" in get(client, dump, "/pickaxe").get_data(as_text=True)
    html = get(client, dump, "/pickaxe?q=" + SECRET).get_data(as_text=True)
    assert "stop committing secrets" in html and "add config" in html
    assert "diff-add" in html and "diff-del" in html and "config.py" in html


def test_pickaxe_filters_and_bad_input_are_handled(client, dump):
    only_added = get(client, dump, "/pickaxe?q=%s&change=added" % SECRET).get_data(as_text=True)
    assert "add config" in only_added and "stop committing secrets" not in only_added
    bad_regex = get(client, dump, "/pickaxe?q=(&regex=1")
    assert bad_regex.status_code == 200 and "invalid regular expression" in bad_regex.get_data(as_text=True)
    bad_date = get(client, dump, "/pickaxe?q=x&since=yesterday")
    assert bad_date.status_code == 200 and "dates look like" in bad_date.get_data(as_text=True)
    none = get(client, dump, "/pickaxe?q=zzzzzz-not-there").get_data(as_text=True)
    assert "No commit added or removed" in none


def test_pickaxe_output_is_escaped(app, client, tmp_path):
    import subprocess
    from tests.helpers import commit
    repo = tmp_path / "xss"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    (repo / "a.html").write_text("<script>alert(1)</script>\n")
    commit(repo, ("<b>x</b>", "e@x"), "2023-01-01T10:00:00", "<img src=x>")
    dump_id = import_dump(app, client, repo)
    html = client.get("/dumps/%s/history/pickaxe?q=alert" % dump_id).get_data(as_text=True)
    assert "&lt;script&gt;alert(1)" in html and "<script>alert" not in html and "<img src=x>" not in html


# -------------------------------------------------------------- lifecycle


def test_lifecycle_is_on_request_and_traces_the_secret(client, dump):
    assert "Trace credentials" in get(client, dump, "/secrets").get_data(as_text=True)
    html = get(client, dump, "/secrets?run=1").get_data(as_text=True)
    assert SECRET in html and "later removed" in html and "Bob" in html


# ---------------------------------------------------- file history and blame


def test_file_history_and_blame_pages(client, dump):
    html = get(client, dump, "/file/master/README.md").get_data(as_text=True)
    assert "docs 2" in html and "initial" in html and "Blame" in html
    blame = get(client, dump, "/blame/master/config.py").get_data(as_text=True)
    assert "APP_PASSWORD" in blame and "Bob" in blame


def test_blame_of_a_missing_file_is_a_message_not_a_crash(client, dump):
    resp = get(client, dump, "/blame/master/nothing.txt")
    assert resp.status_code == 200 and "does not exist" in resp.get_data(as_text=True)


def test_unknown_ref_or_dump_gives_the_error_page(client, dump):
    assert get(client, dump, "/file/nope/README.md").status_code == 404
    assert client.get("/dumps/20240101-000000-deadbeef/history").status_code == 404


def test_blob_page_links_to_history_and_blame(client, dump):
    html = client.get("/dumps/%s/blob/master/README.md" % dump).get_data(as_text=True)
    assert "/history/file/master/README.md" in html and "/history/blame/master/README.md" in html


# ------------------------------------------------------- commit summary


def test_commit_page_has_a_what_changed_summary(client, dump, story_repo):
    from gitstone.viewer import reader
    sha = next(reader.commit_info(c).sha for c in reader.iter_commits(story_repo, [story_repo.head()])
               if reader.commit_info(c).summary == "rename app to main")
    html = client.get("/dumps/%s/commit/%s" % (dump, sha)).get_data(as_text=True)
    assert "What changed" in html and "1 renamed" in html and "app.py" in html and "main.py" in html


def test_sidebar_offers_the_history_tools(client, dump):
    html = client.get("/dumps/%s/tree/master/" % dump).get_data(as_text=True)
    for label in ("History analysis", "Pickaxe search", "Secret lifecycle"):
        assert label in html


def test_history_pages_need_login_when_a_token_is_set(tmp_path):
    from gitstone.web import create_app
    c = create_app(str(tmp_path / "ws"), token="t" * 24).test_client()
    for path in ("/dumps/20240101-000000-deadbeef/history", "/dumps/20240101-000000-deadbeef/history/export.csv"):
        assert c.get(path).status_code == 302


# --------------------------------------------------------- author lists


def test_authors_tile_has_a_menu_with_show_and_downloads(client, dump):
    html = get(client, dump).get_data(as_text=True)
    tile = html.split("<dt>Authors")[1].split("</div>")[0]
    assert 'class="dl-menu"' in tile and "#contributors" in tile
    for kind in ("names", "usernames", "emails"):
        assert "/history/authors.txt?kind=%s" % kind in tile
    assert "/history/authors.csv" in tile and 'id="contributors"' in html


def test_authors_text_lists_are_unique_and_most_active_first(client, dump):
    names = get(client, dump, "/authors.txt?kind=names")
    assert names.status_code == 200 and names.mimetype == "text/plain"
    assert "attachment" in names.headers["Content-Disposition"] and "-names.txt" in names.headers["Content-Disposition"]
    assert names.get_data(as_text=True) == "Alice\nBob\n"
    assert get(client, dump, "/authors.txt?kind=usernames").get_data(as_text=True) == "alice\nbob\n"
    assert get(client, dump, "/authors.txt?kind=emails").get_data(as_text=True) == "alice@example.com\nbob@example.com\n"
    assert get(client, dump, "/authors.txt").get_data(as_text=True) == "Alice\nBob\n"  # names by default


def test_authors_csv_has_one_row_per_person(client, dump):
    resp = get(client, dump, "/authors.csv")
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True))))
    assert rows[0] == ["name", "username", "email", "commits", "first_commit_utc", "last_commit_utc"]
    assert rows[1][:4] == ["Alice", "alice", "alice@example.com", "5"]
    assert rows[2][:4] == ["Bob", "bob", "bob@example.com", "2"]
    assert rows[1][4].startswith("2023-01-10")


def test_authors_export_rejects_bad_requests(client, dump):
    assert get(client, dump, "/authors.xml").status_code == 404
    assert get(client, dump, "/authors.txt?kind=passwords").status_code == 400
    assert client.get("/dumps/20240101-000000-deadbeef/history/authors.txt").status_code == 404


def test_authors_csv_cannot_inject_formulas(app, client, tmp_path):
    import subprocess
    from tests.helpers import commit
    evil = tmp_path / "evil2"
    evil.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=evil, check=True)
    (evil / "f").write_text("x\n")
    commit(evil, ("=cmd|' /C calc'!A0", "e@x"), "2023-01-01T10:00:00", "c")
    dump_id = import_dump(app, client, evil)
    body = client.get("/dumps/%s/history/authors.csv" % dump_id).get_data(as_text=True)
    assert "'=cmd" in body and "\n=cmd" not in body


def test_username_from_email_understands_noreply_addresses():
    from gitstone.analysis.changes import clean_line, username_from_email
    assert username_from_email("12345+octocat@users.noreply.github.com") == "octocat"
    assert username_from_email("octocat@users.noreply.github.com") == "octocat"
    assert username_from_email("77-someone@users.noreply.gitlab.com") == "someone"
    assert username_from_email("jane.doe@corp.example") == "jane.doe"
    assert username_from_email("") == "" and username_from_email("no-at-sign") == ""
    assert clean_line("Jo\nhn\x00 \r") == "John"


def test_the_menu_script_closes_it_on_outside_click_and_escape(client):
    js = client.get("/static/js/common.js").get_data(as_text=True)
    assert ".dl-menu[open]" in js and "Escape" in js


def test_the_two_activity_charts_share_a_scale_and_so_a_baseline(client, dump):
    import re
    html = get(client, dump).get_data(as_text=True)
    boxes = re.findall(r'<svg class="chart" viewBox="0 0 (\d+) (\d+)"', html)
    assert len(boxes) == 2
    (w1, h1), (w2, h2) = ((int(a), int(b)) for a, b in boxes)
    assert h1 == h2 and w1 == 2 * w2  # columns are 2fr : 1fr, so this keeps both at the same scale
    bases = re.findall(r'class="chart-axis" x1="[\d.]+" x2="[\d.]+" y1="([\d.]+)" y2="([\d.]+)"', html)
    assert len(bases) == 2 and bases[0] == bases[1]


# ------------------------------------------------- pickaxe and staged changes


def stage_something(story):
    import subprocess
    (story / "staged.txt").write_text("api_token = 'StagedOnlyValue77'\n")
    subprocess.run(["git", "add", "staged.txt"], cwd=story, check=True)


def test_pickaxe_form_offers_staged_search_and_defaults_it_on(client, dump):
    fresh = get(client, dump, "/pickaxe").get_data(as_text=True)
    assert 'name="staged" value="1" checked' in fresh and "Include staged changes" in fresh
    after = get(client, dump, "/pickaxe?q=%s" % SECRET).get_data(as_text=True)
    assert 'name="staged" value="1" checked' not in after  # submitted without it: it stays off


def test_pickaxe_page_shows_matches_in_staged_changes(app, client, story):
    stage_something(story)
    dump_id = import_dump(app, client, story)
    html = client.get("/dumps/%s/history/pickaxe?q=StagedOnlyValue77&staged=1" % dump_id).get_data(as_text=True)
    assert "Staged changes (git add, not committed yet)" in html and "not in any commit" in html
    assert "staged.txt" in html and "StagedOnlyValue77" in html and "see all staged changes" in html
    assert "and the staged changes touch this text" in html
    off = client.get("/dumps/%s/history/pickaxe?q=StagedOnlyValue77" % dump_id).get_data(as_text=True)
    assert "not in any commit" not in off and "No commit added or removed" in off


def test_pickaxe_page_explains_why_staged_was_skipped_with_an_author_filter(app, client, story):
    stage_something(story)
    dump_id = import_dump(app, client, story)
    html = client.get("/dumps/%s/history/pickaxe?q=Sup3r&staged=1&author=bob" % dump_id).get_data(as_text=True)
    assert "Staged changes were not searched" in html and "not in any commit" not in html


def test_pickaxe_staged_output_is_escaped(app, client, tmp_path):
    import subprocess
    from tests.helpers import commit
    repo = tmp_path / "xss2"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    (repo / "a").write_text("x\n")
    commit(repo, ("a", "a@a"), "2023-01-01T10:00:00", "c")
    (repo / "b.html").write_text("<script>alert(1)</script>\n")
    subprocess.run(["git", "add", "b.html"], cwd=repo, check=True)
    dump_id = import_dump(app, client, repo)
    html = client.get("/dumps/%s/history/pickaxe?q=alert&staged=1" % dump_id).get_data(as_text=True)
    assert "&lt;script&gt;alert(1)" in html and "<script>alert" not in html

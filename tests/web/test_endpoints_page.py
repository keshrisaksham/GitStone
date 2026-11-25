"""The endpoint extraction page."""
from tests.helpers import git, import_dump


def test_endpoints_page_lists_urls_and_routes(app, client, repo):
    (repo / "server.py").write_text("@app.route('/api/v1/users')\ndef users(): pass\n")
    (repo / "client.js").write_text("fetch('/api/v1/users');\n")
    (repo / "config.py").write_text("API_BASE = 'https://api.example.com/v1'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "api")
    dump_id = import_dump(app, client, repo)

    page = client.get("/dumps/%s/endpoints?run=1" % dump_id).get_data(as_text=True)
    assert "/api/v1/users" in page and "api.example.com/v1" in page
    assert "Route definition" in page and "Client call" in page and ">URL<" in page


def test_endpoints_page_offers_hide_duplicates_when_there_are_any(app, client, repo):
    (repo / "a.py").write_text("URL = 'https://api.example.com/dup'  # primary\n")
    (repo / "b.py").write_text("BACKUP_URL = 'https://api.example.com/dup'  # mirror\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "dup url")
    dump_id = import_dump(app, client, repo)

    page = client.get("/dumps/%s/endpoints?run=1" % dump_id).get_data(as_text=True)
    assert "data-dedupe-filter" in page and "hide duplicates" in page
    assert 'data-dup="1"' in page and 'data-dup="0"' in page


def test_endpoints_page_links_open_in_the_side_pane(app, client, repo):
    (repo / "config.py").write_text("URL = 'https://api.example.com/pane'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "config")
    dump_id = import_dump(app, client, repo)

    page = client.get("/dumps/%s/endpoints?run=1" % dump_id).get_data(as_text=True)
    assert 'id="blob-pane"' in page and "data-pane" in page and "config.py:1" in page


def test_endpoints_page_has_sortable_kind_and_endpoint_columns(app, client, repo):
    (repo / "a.py").write_text("URL = 'https://b.example.com/x'\n")
    (repo / "b.py").write_text("OTHER = 'https://a.example.com/y'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "two urls")
    dump_id = import_dump(app, client, repo)

    page = client.get("/dumps/%s/endpoints?run=1" % dump_id).get_data(as_text=True)
    assert 'data-sort-key="kind"' in page and 'data-sort-key="value"' in page
    assert 'data-sort-value="https://a.example.com/y"' in page
    assert 'data-sort-value="https://b.example.com/x"' in page
    assert 'aria-sort="none"' in page


def test_sort_js_is_served(client):
    resp = client.get("/static/js/sort.js")
    assert resp.status_code == 200 and b"data-sort-key" in resp.data


def test_endpoints_page_offers_load_more_once_the_limit_is_hit(app, client, repo, monkeypatch):
    from gitstone.analysis import endpoints as endpoint_scan
    from gitstone.web.blueprints import tools
    monkeypatch.setattr(endpoint_scan, "MAX_SCAN_HITS", 1)
    monkeypatch.setattr(tools, "ENDPOINTS_LIMIT_CEILING", 5)
    for i in range(3):
        (repo / ("a%d.py" % i)).write_text("URL%d = 'https://api.example.com/%d'\n" % (i, i))
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "three urls")
    dump_id = import_dump(app, client, repo)

    capped = client.get("/dumps/%s/endpoints?run=1" % dump_id).get_data(as_text=True)
    assert "1 result." in capped
    assert "limit reached" in capped and "Load up to 2" in capped

    more = client.get("/dumps/%s/endpoints?run=1&limit=5" % dump_id).get_data(as_text=True)
    assert "3 results." in more
    assert "limit reached" not in more and "Load up to" not in more


def test_endpoints_page_empty_when_nothing_matches(app, client, repo):
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/endpoints?run=1" % dump_id).get_data(as_text=True)
    assert "No matches." in page

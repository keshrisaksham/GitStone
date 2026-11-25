"""Endpoint extraction: URLs, route definitions, and client calls."""
import pytest

from tests.helpers import git
from gitstone.analysis import endpoints


def test_endpoints_scan_finds_a_url_in_a_blob(repo, opened):
    (repo / "config.py").write_text("API_BASE = 'https://api.internal.example.com/v1'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "config")

    hits, scanned, truncated = endpoints.endpoints_scan(opened)
    assert scanned > 0 and not truncated
    assert any(h.kind == "url" and h.value == "https://api.internal.example.com/v1" for h in hits)


def test_endpoints_scan_finds_a_url_even_after_it_is_removed(repo, opened):
    """The whole point of GitStone: an endpoint deleted in a later commit is
    still sitting in the object store."""
    (repo / "legacy.py").write_text("OLD_ENDPOINT = 'https://old.example.com/v0/admin'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "add legacy endpoint")
    (repo / "legacy.py").unlink()
    git(repo, "rm", "-q", "legacy.py")
    git(repo, "commit", "-q", "-m", "remove legacy endpoint")

    hits, _, _ = endpoints.endpoints_scan(opened)
    assert any(h.value == "https://old.example.com/v0/admin" for h in hits)


def test_endpoints_scan_finds_route_definitions_and_client_calls(repo, opened):
    (repo / "server.py").write_text(
        "@app.route('/api/v1/users')\n"
        "def users():\n"
        "    return []\n"
    )
    (repo / "client.js").write_text("fetch('/api/v1/users').then(r => r.json());\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "api")

    hits, _, _ = endpoints.endpoints_scan(opened)
    kinds = {(h.kind, h.value) for h in hits}
    assert ("route", "/api/v1/users") in kinds
    assert ("call", "/api/v1/users") in kinds


@pytest.mark.parametrize("line,kind,value", [
    ("https://example.com/path and some text", "url", "https://example.com/path"),
    ("url = 'ftp://files.example.com/drop'", "url", "ftp://files.example.com/drop"),
    ("@app.route(\"/health\")", "route", "/health"),
    ("router.get('/users/:id', handler)", "route", "/users/:id"),
    ("@app.put(\"/items/{item_id}\")", "route", "/items/{item_id}"),
    ("axios.get('/api/data')", "call", "/api/data"),
    ("requests.post('/api/login', data=d)", "call", "/api/login"),
])
def test_endpoint_patterns(line, kind, value):
    matches = {(k, v) for k, v, _, _ in endpoints._iter_matches(line)}
    assert (kind, value) in matches, matches


def test_a_client_call_is_not_also_reported_as_a_route():
    """axios.get(...)/requests.post(...) match the generic <x>.<method>(
    shape a server route registration has -- without the exclusion this
    reports every client call twice, once as "call" and once as "route"."""
    for line in ("axios.get('/api/data')", "requests.post('/api/login')", "axios.delete('/api/x')"):
        kinds = {k for k, _, _, _ in endpoints._iter_matches(line)}
        assert kinds == {"call"}, (line, kinds)


def test_endpoint_patterns_ignore_ordinary_code():
    for line in ("d.get('key')", "self.config.get('name')", "# see the docs", "price = 9.99"):
        assert not list(endpoints._iter_matches(line)), line


def test_endpoints_scan_marks_repeated_hits_as_duplicates(repo, opened):
    (repo / "a.py").write_text("URL = 'https://api.example.com/x'  # primary\n")
    (repo / "b.py").write_text("BACKUP_URL = 'https://api.example.com/x'  # mirror\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "two files, same url")

    hits, _, _ = endpoints.endpoints_scan(opened)
    url_hits = [h for h in hits if h.kind == "url"]
    assert len(url_hits) == 2
    assert sorted(h.dup_of_first for h in url_hits) == [False, True]
    assert [h.dup_count for h in url_hits if not h.dup_of_first] == [2]


def test_endpoints_scan_locates_hits_by_path(repo, opened):
    (repo / "config.py").write_text("URL = 'https://api.example.com/locate-me'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "config")

    hits, _, _ = endpoints.endpoints_scan(opened)
    hit = next(h for h in hits if "locate-me" in h.value)
    assert hit.locations and hit.locations[0][1] == "config.py"
    assert hit.path == "config.py"

"""Unit tests for the dumper's building blocks (no network, no processes)."""
import types

import dulwich.objects
import pytest

from gitstone.core import Config, Logger, build_http_headers, parse_lines, parse_proxy
from gitstone.core import http as core_http
from gitstone.core import paths
from gitstone.core.git_objects import GITLINK_MODE, get_referenced_sha1
from gitstone.core.proxy import git_environment, requests_proxies
from gitstone.core.stages import merge_branches, normalize_url, ref_candidates


def response(status=200, headers=None):
    return types.SimpleNamespace(status_code=status, headers=headers or {}, close=lambda: None)


# ------------------------------------------------------------------ http


def test_verify_response():
    ok = core_http.verify_response
    assert ok(response(headers={"Content-Length": "5"})) == (True, "")
    assert ok(response())[0]
    assert ok(response(404)) == (False, "responded with status code 404")
    assert ok(response(500), any_status=True)[0]
    assert not ok(response(headers={"Content-Length": "0"}))[0]
    assert ok(response(headers={"Content-Type": "text/html; charset=utf-8"})) == (
        False, "responded with HTML")


def test_backoff_seconds():
    backoff = core_http.backoff_seconds
    assert backoff(response(429, {"Retry-After": "2"}), 0) == 2
    assert backoff(response(503), 0) == 0.5
    assert backoff(response(503), 2) == 2.0
    # capped, whatever the server asks for
    assert backoff(response(429, {"Retry-After": "86400"}), 0) == core_http.MAX_BACKOFF


class FakeSession:
    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        return response(self.statuses.pop(0))


def make_cfg(**overrides):
    values = dict(url="http://x", directory=".", log=Logger(quiet=True))
    values.update(overrides)
    return Config(**values)


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(core_http.time, "sleep", calls.append)
    return calls


def test_request_retries_slow_down_responses(sleeps):
    session = FakeSession(503, 429, 200)
    assert core_http.request(session, make_cfg(), "http://x/a").status_code == 200
    assert session.calls == 3 and len(sleeps) == 2


def test_request_gives_up_after_retry_attempts(sleeps):
    session = FakeSession(503, 503, 503)
    assert core_http.request(session, make_cfg(), "http://x/a").status_code == 503
    assert session.calls == 3


def test_request_any_status_does_not_retry(sleeps):
    session = FakeSession(503)
    assert core_http.request(session, make_cfg(any_status=True), "http://x/a").status_code == 503
    assert session.calls == 1


def test_request_applies_delay(sleeps):
    core_http.request(FakeSession(200), make_cfg(delay=0.25), "http://x/a")
    assert sleeps == [0.25]


# ----------------------------------------------------------------- objects


def test_referenced_sha1_follows_tags_and_skips_submodules():
    tag = dulwich.objects.Tag()
    tag.object = (dulwich.objects.Commit, b"b" * 40)
    assert get_referenced_sha1(tag) == ["b" * 40]

    tree = dulwich.objects.Tree()
    tree.add(b"file", 0o100644, b"a" * 40)
    tree.add(b"sub", GITLINK_MODE, b"c" * 40)
    assert get_referenced_sha1(tree) == ["a" * 40]


def test_unknown_object_raises_instead_of_exiting():
    with pytest.raises(ValueError):
        get_referenced_sha1(object())


# ------------------------------------------------------------------- paths


@pytest.mark.parametrize("path", [
    b"../evil", b"a/../../evil", b"/etc/passwd", b".git/hooks/post-checkout",
    b"sub/.GIT/config", b"", b"a//b",
])
def test_unsafe_index_paths_are_rejected(tmp_path, path):
    assert paths.safe_worktree_path(str(tmp_path.resolve()), path) is None


def test_safe_index_path_accepted(tmp_path):
    root = str(tmp_path.resolve())
    assert paths.safe_worktree_path(root, b"src/app.py") == f"{root}/src/app.py"


def test_save_file_is_atomic(tmp_path):
    target = tmp_path / "a" / "b"

    def chunks():
        yield b"he"
        raise RuntimeError("connection dropped")

    with pytest.raises(RuntimeError):
        paths.save_file(str(target), chunks())
    assert not target.exists()  # no half-written file mistaken for a download

    paths.save_file(str(target), [b"hello"])
    assert target.read_bytes() == b"hello"


# ----------------------------------------------------------------- options


def test_parse_lines_strips_blanks_and_comments():
    assert parse_lines("a\n# comment\n\n  b  \n") == ["a", "b"]


def test_build_http_headers():
    headers = build_http_headers("my-ua", ["X-Foo=bar", "X-Baz = qux "], " sid=1 ")
    assert headers == {"User-Agent": "my-ua", "X-Foo": "bar", "X-Baz": "qux", "Cookie": "sid=1"}


def test_build_http_headers_rejects_bad_line():
    with pytest.raises(ValueError, match="NAME=VALUE"):
        build_http_headers("ua", ["not-a-header-line"])


def test_config_validation():
    make_cfg().validate(jobs=1)
    for bad in (dict(retry=0), dict(timeout=0), dict(delay=-1)):
        with pytest.raises(ValueError):
            make_cfg(**bad).validate()
    with pytest.raises(ValueError):
        make_cfg().validate(jobs=0)


# ------------------------------------------------------------------- proxy


@pytest.mark.parametrize("spec,url,git_url", [
    ("socks5:127.0.0.1:9050", "socks5h://127.0.0.1:9050", "socks5h://127.0.0.1:9050"),
    ("socks4:10.0.0.1:1080", "socks4a://10.0.0.1:1080", "socks4h://10.0.0.1:1080"),
    ("http://proxy.local:3128", "http://proxy.local:3128", "http://proxy.local:3128"),
    ("localhost:9050", "socks5h://localhost:9050", "socks5h://localhost:9050"),
])
def test_parse_proxy(spec, url, git_url):
    proxy = parse_proxy(spec)
    assert (proxy.requests_url, proxy.git_url) == (url, git_url)


@pytest.mark.parametrize("spec", ["not-a-proxy", "ftp://x:1", "host:port"])
def test_parse_proxy_rejects_garbage(spec):
    with pytest.raises(ValueError, match="invalid proxy"):
        parse_proxy(spec)


def test_proxy_is_per_session_not_global(monkeypatch):
    import socket
    before = socket.socket
    session = core_http.make_session(make_cfg(proxy="socks5:127.0.0.1:9050"))
    assert session.proxies["https"] == "socks5h://127.0.0.1:9050"
    assert socket.socket is before  # the old implementation replaced this
    assert core_http.make_session(make_cfg()).proxies == {}
    assert requests_proxies(None) == {}


def test_git_environment_carries_the_proxy():
    assert git_environment("socks5:h:1")["ALL_PROXY"] == "socks5h://h:1"


# ------------------------------------------------------------------ stages


@pytest.mark.parametrize("given", [
    "http://x", "http://x/", "http://x/.git", "http://x/.git/", "http://x/.git/HEAD",
])
def test_normalize_url(given):
    assert normalize_url(given) == "http://x"


def test_normalize_url_keeps_subpaths():
    assert normalize_url("http://x/app/.git/") == "http://x/app"


def test_merge_branches_dedupes_and_skips_invalid():
    out = []
    log = Logger(stream=types.SimpleNamespace(write=out.append, flush=lambda: None))
    branches = merge_branches(["dev", "master", "bad branch!", "dev"], log)
    assert branches.count("dev") == 1 and branches.count("master") == 1
    assert "bad branch!" not in branches
    assert "ignoring invalid branch" in "".join(out)


def test_ref_candidates_cover_each_branch():
    tasks = ref_candidates(["dev"])
    assert ".git/refs/heads/dev" in tasks and ".git/logs/refs/heads/dev" in tasks

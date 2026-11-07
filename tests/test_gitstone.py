"""Tests for GitStone.

The end-to-end tests serve a real git repository over HTTP *without* directory
listings (so GitStone takes its "blind" path) and run the CLI against it.
"""
import http.server
import json
import os
import subprocess
import sys
import threading
import types

import dulwich.objects
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "GitStone-Packages"))

import gitstone  # noqa: E402
from conftest import git  # noqa: E402


def response(status=200, headers=None):
    return types.SimpleNamespace(
        status_code=status, headers=headers or {}, close=lambda: None
    )


# ---------------------------------------------------------------- unit tests


def test_verify_response():
    assert gitstone.verify_response(response(headers={"Content-Length": "5"}))[0]
    assert gitstone.verify_response(response())[0]
    assert not gitstone.verify_response(response(404))[0]
    assert gitstone.verify_response(response(500), any_status=True)[0]
    assert not gitstone.verify_response(
        response(headers={"Content-Length": "0"})
    )[0]
    assert not gitstone.verify_response(
        response(headers={"Content-Type": "text/html; charset=utf-8"})
    )[0]


def test_referenced_sha1_follows_tags_and_skips_submodules():
    tag = dulwich.objects.Tag()
    tag.object = (dulwich.objects.Commit, b"b" * 40)
    assert gitstone.get_referenced_sha1(tag) == ["b" * 40]

    tree = dulwich.objects.Tree()
    tree.add(b"file", 0o100644, b"a" * 40)
    tree.add(b"sub", gitstone.GITLINK_MODE, b"c" * 40)
    assert gitstone.get_referenced_sha1(tree) == ["a" * 40]


def test_unknown_object_raises_instead_of_exiting():
    with pytest.raises(ValueError):
        gitstone.get_referenced_sha1(object())


def test_backoff_seconds():
    assert gitstone.backoff_seconds(response(429, {"Retry-After": "2"}), 0) == 2
    assert gitstone.backoff_seconds(response(503), 0) == 0.5
    assert gitstone.backoff_seconds(response(503), 2) == 2.0
    # capped, whatever the server asks for
    big = response(429, {"Retry-After": "86400"})
    assert gitstone.backoff_seconds(big, 0) == gitstone.MAX_BACKOFF


class FakeSession:
    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        return response(self.statuses.pop(0))


def make_cfg(**overrides):
    values = dict(url="http://x", directory=".", retry=3, timeout=3,
                  http_headers={})
    values.update(overrides)
    return gitstone.Config(**values)


def test_request_retries_slow_down_responses(monkeypatch):
    monkeypatch.setattr(gitstone.time, "sleep", lambda s: None)
    session = FakeSession(503, 429, 200)
    assert gitstone.request(session, make_cfg(), "http://x/a").status_code == 200
    assert session.calls == 3


def test_request_gives_up_after_retry_attempts(monkeypatch):
    monkeypatch.setattr(gitstone.time, "sleep", lambda s: None)
    session = FakeSession(503, 503, 503)
    assert gitstone.request(session, make_cfg(), "http://x/a").status_code == 503
    assert session.calls == 3


def test_request_any_status_does_not_retry(monkeypatch):
    monkeypatch.setattr(gitstone.time, "sleep", lambda s: None)
    session = FakeSession(503)
    cfg = make_cfg(any_status=True)
    assert gitstone.request(session, cfg, "http://x/a").status_code == 503
    assert session.calls == 1


def test_request_applies_delay(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gitstone.time, "sleep", sleeps.append)
    gitstone.request(FakeSession(200), make_cfg(delay=0.25), "http://x/a")
    assert sleeps == [0.25]


@pytest.mark.parametrize(
    "path",
    [b"../evil", b"a/../../evil", b"/etc/passwd", b".git/hooks/post-checkout",
     b"sub/.GIT/config", b"", b"a//b"],
)
def test_unsafe_index_paths_are_rejected(tmp_path, path):
    assert gitstone.safe_worktree_path(str(tmp_path.resolve()), path) is None


def test_safe_index_path_accepted(tmp_path):
    root = str(tmp_path.resolve())
    assert gitstone.safe_worktree_path(root, b"src/app.py") == os.path.join(
        root, "src", "app.py"
    )


def test_save_file_is_atomic(tmp_path):
    target = tmp_path / "a" / "b"

    def chunks():
        yield b"he"
        raise RuntimeError("connection dropped")

    with pytest.raises(RuntimeError):
        gitstone.save_file(str(target), chunks())
    assert not target.exists()  # no half-written file mistaken for a download

    gitstone.save_file(str(target), [b"hello"])
    assert target.read_bytes() == b"hello"


# ------------------------------------------------------------ end-to-end


class Server:
    """Serves a directory with no listings; can inject failures."""

    def __init__(self, root):
        self.requests = []  # (path, headers)
        self.fail_once = {}  # path substring -> (status, headers)
        outer = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(root), **k)

            def send_head(self):
                outer.requests.append((self.path, dict(self.headers)))
                for needle, (status, headers) in list(outer.fail_once.items()):
                    if needle in self.path:
                        del outer.fail_once[needle]
                        self.send_response(status)
                        for k, v in headers.items():
                            self.send_header(k, v)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return None
                if os.path.isdir(self.translate_path(self.path)):
                    self.send_error(403)
                    return None
                return super().send_head()

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/.git" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(repo):
    s = Server(repo)
    yield s
    s.close()


def dump(url, out, *extra):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "GitStone-Packages", "gitstone.py"), *extra, url, str(out)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        timeout=120,
    )


def test_blind_dump_recovers_the_tree(server, tmp_path):
    out = tmp_path / "out"
    result = dump(server.url, out, "--json")
    assert result.returncode == 0, result.stderr

    assert (out / "flag.txt").read_text() == "secret v2\n"
    assert os.access(out / "src" / "run.sh", os.X_OK)

    # --json: the summary is the only thing on stdout, progress is on stderr
    summary = json.loads(result.stdout)
    assert summary["exit_code"] == 0
    assert summary["fetched"] > 0
    assert summary["missing_objects"] == 0
    assert "Fetching" in result.stderr


def test_history_is_recovered_too(server, tmp_path):
    out = tmp_path / "out"
    assert dump(server.url, out).returncode == 0
    log = subprocess.run(["git", "-C", str(out), "log", "--format=%s"],
                         stdout=subprocess.PIPE, universal_newlines=True)
    assert log.stdout.split() == ["two", "one"]


def test_all_zero_sha_is_not_requested(server, tmp_path):
    dump(server.url, tmp_path / "out")
    assert not any("0000000000000000000000000000000000000" in p
                   for p, _ in server.requests)


def test_second_run_resumes_instead_of_refetching(server, tmp_path):
    out = tmp_path / "out"
    first = json.loads(dump(server.url, out, "--json").stdout)
    second = json.loads(dump(server.url, out, "--json").stdout)

    assert second["skipped"] > 0
    assert second["fetched"] < first["fetched"]
    assert not list(out.rglob("*.part"))


def test_quiet_hides_progress_but_keeps_summary(server, tmp_path):
    result = dump(server.url, tmp_path / "out", "-q")
    assert "Fetching" not in result.stdout
    assert "Done in" in result.stdout


def test_survives_503_with_retry_after(server, tmp_path):
    server.fail_once["/.git/refs/heads/master"] = (503, {"Retry-After": "0"})
    out = tmp_path / "out"
    result = dump(server.url, out)
    assert result.returncode == 0, result.stderr
    assert "waiting" in result.stdout
    assert (out / "flag.txt").read_text() == "secret v2\n"


def test_cookie_and_header_file_are_sent(server, tmp_path):
    headers = tmp_path / "headers.txt"
    headers.write_text("# comment\nX-Token=abc\n\n")
    dump(server.url, tmp_path / "out", "-c", "sid=42", "--header-file", str(headers))
    sent = server.requests[0][1]
    assert sent["Cookie"] == "sid=42"
    assert sent["X-Token"] == "abc"


def test_branch_file_adds_branches(server, tmp_path):
    branches = tmp_path / "branches.txt"
    branches.write_text("# extra\nfeature-x\n")
    dump(server.url, tmp_path / "out", "--branch-file", str(branches))
    assert any(p.endswith("/refs/heads/feature-x") for p, _ in server.requests)


def test_not_a_git_repo_exits_nonzero(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    s = Server(empty)
    try:
        result = dump(s.url, tmp_path / "out")
    finally:
        s.close()
    assert result.returncode == 1


def test_recover_from_index_rebuilds_missing_files(repo, tmp_path):
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(repo), str(clone))
    (clone / "flag.txt").unlink()
    (clone / "src" / "run.sh").unlink()
    (clone / "extra.txt").write_text("keep me")  # untouched

    stats = gitstone.Stats()
    recovered, unavailable = gitstone.recover_from_index(str(clone), stats)

    assert (recovered, unavailable) == (2, 0)
    assert (clone / "flag.txt").read_text() == "secret v2\n"
    assert os.access(clone / "src" / "run.sh", os.X_OK)
    assert (clone / "extra.txt").read_text() == "keep me"
    assert stats.snapshot()["recovered"] == 2


def test_recover_from_index_counts_missing_objects(repo, tmp_path):
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(repo), str(clone))
    git(clone, "gc", "-q", "--prune=now")
    (clone / "flag.txt").unlink()
    for pack in (clone / ".git" / "objects" / "pack").iterdir():
        pack.unlink()

    recovered, unavailable = gitstone.recover_from_index(str(clone))
    assert recovered == 0 and unavailable >= 1

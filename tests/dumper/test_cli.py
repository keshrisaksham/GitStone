"""End-to-end tests: the ``gitstone`` command against a real repository served
over HTTP without directory listings (so the dumper takes its "blind" path)."""
import json
import os
import subprocess
import sys

from tests.helpers import REPO_ROOT, Server

ROOT = REPO_ROOT
ENV = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))


def dump(url, out, *extra):
    return subprocess.run(
        [sys.executable, "-m", "gitstone", *extra, url, str(out)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        timeout=120, env=ENV,
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
    assert not any("0000000000000000000000000000000000000" in p for p, _ in server.requests)


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


def test_bad_proxy_is_a_usage_error(server, tmp_path):
    result = dump(server.url, tmp_path / "out", "--proxy", "not-a-proxy")
    assert result.returncode == 2
    assert "invalid proxy" in result.stderr


def test_bad_header_is_a_usage_error(server, tmp_path):
    result = dump(server.url, tmp_path / "out", "-H", "nonsense")
    assert result.returncode == 2
    assert "NAME=VALUE" in result.stderr


def test_dump_through_an_http_proxy(server, tmp_path):
    from tests.helpers import ForwardProxy
    proxy = ForwardProxy()
    try:
        # a hostname that only resolves through the proxy proves traffic used it
        url = server.url.replace("127.0.0.1", "localhost")
        result = dump(url, tmp_path / "out", "--proxy", proxy.spec)
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "out" / "flag.txt").read_text() == "secret v2\n"
        assert any("/.git/HEAD" in p for p in proxy.seen)
    finally:
        proxy.close()

"""Cloning a regular remote: the URL checks, the hardened git invocation, and
a real clone/update against a local HTTP server."""
import base64

import pytest

from gitstone import actions
from gitstone.core import Cancelled, Logger, Stats, clone_or_update, validate_clone_url
from gitstone.viewer import reader
from tests.helpers import commit_all, git


class Sink:
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s

    def flush(self):
        pass


def run_clone(url, directory, **kw):
    sink = Sink()
    stats = Stats()
    code = clone_or_update(url, str(directory), Logger(sink, sink), stats, **kw)
    return code, sink.text, stats


# --------------------------------------------------------------- URL checks


@pytest.mark.parametrize("url", [
    "https://github.com/owner/repo.git",
    "http://127.0.0.1:8000/.git",
    "ssh://git@host.example/owner/repo.git",
    "git://host.example/repo.git",
    "git@github.com:owner/repo.git",
    "https://user@gitlab.example.com/group/repo",
])
def test_accepts_ordinary_remote_urls(url):
    assert validate_clone_url("  " + url + "  ") == url


@pytest.mark.parametrize("url", [
    "", "   ", "/etc/passwd", "../elsewhere", "file:///etc", "ext::sh -c 'id'",
    "--upload-pack=touch /tmp/x", "-oProxyCommand=id", "ftp://host/repo.git",
    "ssh://-oProxyCommand=id@host/repo.git", "ssh://git@-oProxyCommand=id/repo.git",
    "https://", "https://user:secret@host/repo.git", "https://host/a b", "not a url",
    "https://host/x\nhttps://evil",
])
def test_rejects_urls_that_could_read_this_machine_or_run_something(url):
    with pytest.raises(ValueError):
        validate_clone_url(url)


def test_a_password_in_the_url_is_refused_with_a_pointer_to_the_token_field():
    with pytest.raises(ValueError, match="token"):
        validate_clone_url("https://me:hunter2@host/repo.git")


# ------------------------------------------------------------- real clones


def test_clone_then_update_picks_up_new_commits(repo, server, tmp_path):
    dest = tmp_path / "clone"
    dest.mkdir()
    code, log, stats = run_clone(server.url, dest)
    assert code == 0, log
    assert (dest / "flag.txt").read_text() == "secret v2\n"  # working files written
    r = reader.open_repo(str(dest))
    try:
        assert len(list(reader.iter_commits(r, [r.head()]))) == 2
    finally:
        r.close()
    assert stats.snapshot()["fetched"] > 0

    (repo / "new.txt").write_text("more\n")
    commit_all(repo, "three")
    git(repo, "update-server-info")
    code, log, _ = run_clone(server.url, dest)
    assert code == 0 and "Already cloned" in log, log
    r = reader.open_repo(str(dest))
    try:
        shas = [s for ref in r.refs.allkeys() for s in [r.refs[ref]]]
        assert len(list(reader.iter_commits(r, shas))) == 3
    finally:
        r.close()


def test_history_only_clone_writes_no_working_files(repo, server, tmp_path):
    dest = tmp_path / "clone"
    dest.mkdir()
    code, log, _ = run_clone(server.url, dest, checkout=False)
    assert code == 0, log
    assert not (dest / "flag.txt").exists() and (dest / ".git").is_dir()


def test_token_goes_in_a_header_and_never_into_the_repository(repo, server, tmp_path):
    dest = tmp_path / "clone"
    dest.mkdir()
    code, log, _ = run_clone(server.url, dest, token="s3cr3t-token", username="alice")
    assert code == 0, log
    sent = {h.get("Authorization") for _, h in server.requests}
    assert "Basic " + base64.b64encode(b"alice:s3cr3t-token").decode() in sent
    assert "s3cr3t-token" not in (dest / ".git" / "config").read_text()
    assert "s3cr3t-token" not in log


def test_an_unreachable_remote_fails_cleanly(tmp_path):
    dest = tmp_path / "clone"
    dest.mkdir()
    code, log, _ = run_clone("http://127.0.0.1:9/nothing.git", dest)
    assert code == 1 and "git exited" in log


def test_cancel_stops_the_clone(server, tmp_path):
    dest = tmp_path / "clone"
    dest.mkdir()
    with pytest.raises(Cancelled):
        run_clone(server.url, dest, should_stop=lambda: True)


def test_bad_options_are_rejected_before_git_runs(tmp_path):
    with pytest.raises(ValueError):
        run_clone("https://host/r.git", tmp_path, depth=-1)
    with pytest.raises(ValueError):
        run_clone("https://host/r.git", tmp_path, branch="--evil")


def test_clone_action_is_registered_for_cloned_dumps_only():
    a = actions.get("clone")
    assert a.network and a.kinds == ("cloned",) and not a.dump_summary
    assert {"checkout", "verify", "recover_index"} <= {x.id for x in actions.for_kind("cloned")}
    assert "token" in {p.name for p in a.params if p.secret}

"""Shared fixtures: a small real git repository, and an HTTP server that
exposes it the way a misconfigured website would (no directory listings)."""
import os
import subprocess

import pytest

from helpers import Server

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git(cwd, *args):
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    """A small repo with two commits, an annotated tag, and server-info
    generated (so the no-listing test server can serve it)."""
    path = tmp_path / "site"
    path.mkdir()
    git(path, "init", "-q", "-b", "master")
    (path / "flag.txt").write_text("secret\n")
    (path / "src").mkdir()
    (path / "src" / "run.sh").write_text("#!/bin/sh\necho hi\n")
    os.chmod(path / "src" / "run.sh", 0o755)
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "one")
    (path / "flag.txt").write_text("secret v2\n")
    git(path, "commit", "-q", "-am", "two")
    git(path, "tag", "-a", "v1", "-m", "release")
    git(path, "update-server-info")
    return path


@pytest.fixture
def server(repo):
    s = Server(repo)
    yield s
    s.close()

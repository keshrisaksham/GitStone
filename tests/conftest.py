"""Shared fixtures: a small real git repository, an HTTP server that exposes it
the way a misconfigured website would (no directory listings), the same
repository opened for reading, and the Flask app with its test client."""
import os

import pytest

from gitstone.viewer import reader
from gitstone.web import create_app
from tests.helpers import Server, git


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


@pytest.fixture
def opened(repo):
    """``repo`` opened with the read-only viewer."""
    r = reader.open_repo(str(repo))
    yield r
    r.close()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "workspace"))


@pytest.fixture
def client(app):
    return app.test_client()

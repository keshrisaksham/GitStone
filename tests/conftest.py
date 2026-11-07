"""Shared fixtures: a small real git repository used by both the CLI tests
and the web UI's gitview tests."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git(cwd, *args):
    import subprocess
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    """A small repo with two commits, an annotated tag, and server-info
    generated (so gitstone's "no listing" test server works against it)."""
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

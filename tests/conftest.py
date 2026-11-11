"""Shared fixtures: a small real git repository, an HTTP server that exposes it
the way a misconfigured website would (no directory listings), the same
repository opened for reading, and the Flask app with its test client."""
import os
import subprocess

import pytest

from gitstone.viewer import reader
from gitstone.web import create_app
from tests.helpers import ALICE, BOB, SECRET, Server, commit, git


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


@pytest.fixture
def story(tmp_path):
    """Alice and Bob over five months: a CI file, a committed then removed
    secret and .env, a rename, and a README that keeps changing."""
    repo = tmp_path / "story"
    repo.mkdir()
    env = dict(os.environ)
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, env=env, check=True)
    (repo / "app.py").write_text("def main():\n    print('hello')\n    return 0\n\nif __name__ == '__main__':\n    main()\n")
    (repo / "README.md").write_text("# app\n")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    commit(repo, ALICE, "2023-01-10T10:00:00", "initial")

    (repo / "config.py").write_text("DEBUG = False\npassword = '%s'\n" % SECRET)
    (repo / ".env").write_text("DB_PASSWORD=%s\n" % SECRET)
    commit(repo, BOB, "2023-02-15T10:00:00", "add config")

    os.remove(repo / ".env")
    (repo / "config.py").write_text("import os\nDEBUG = False\npassword = os.environ['APP_PASSWORD']\n")
    commit(repo, BOB, "2023-03-20T10:00:00", "stop committing secrets")

    subprocess.run(["git", "mv", "app.py", "main.py"], cwd=repo, env=env, check=True)
    commit(repo, ALICE, "2023-04-01T10:00:00", "rename app to main")

    for i, day in enumerate(("2023-05-05", "2023-05-06", "2023-05-07")):
        (repo / "README.md").write_text("# app\n" + "more\n" * (i + 1))
        commit(repo, ALICE, day + "T10:00:00", "docs %d" % i)
    return repo


@pytest.fixture
def story_repo(story):
    r = reader.open_repo(str(story))
    yield r
    r.close()

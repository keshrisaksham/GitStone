"""Tests for the dulwich-based repo reader behind the web UI."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "GitStone-Packages"))

from gitstone_webui import gitview  # noqa: E402
from conftest import git  # noqa: E402


@pytest.fixture
def opened(repo):
    r = gitview.open_repo(str(repo))
    yield r
    r.close()


def test_open_repo_rejects_non_repo(tmp_path):
    with pytest.raises(gitview.RepoError):
        gitview.open_repo(str(tmp_path))


@pytest.mark.parametrize("ref,safe", [
    ("master", True), ("HEAD", True), ("feature/x", True),
    ("a" * 40, True), ("deadbeef", True),
    ("-rf", False), ("../../etc/passwd", False), ("refs/../x", False),
    ("x\ny", False), ("", False), (".hidden", False), ("x/", False),
])
def test_is_safe_ref(ref, safe):
    assert gitview.is_safe_ref(ref) == safe


def test_list_refs_and_default_ref(opened):
    refs, head_sha = gitview.list_refs(opened)
    names = {(r.kind, r.name) for r in refs}
    assert ("branch", "master") in names
    assert ("tag", "v1") in names
    assert head_sha is not None
    assert gitview.default_ref(opened) == "master"


def test_resolve_commit_by_branch_tag_and_sha(opened):
    commit, sha = gitview.resolve_commit(opened, "master")
    assert len(sha) == 40

    by_sha = gitview.resolve_commit(opened, sha)[0]
    assert by_sha.id == commit.id

    # annotated tag must be peeled to the commit it points at
    tagged, _ = gitview.resolve_commit(opened, "v1")
    assert tagged.id == commit.id


def test_resolve_commit_unknown_ref_raises(opened):
    with pytest.raises(gitview.NotFound):
        gitview.resolve_commit(opened, "no-such-branch")


def test_resolve_commit_rejects_unsafe_ref(opened):
    with pytest.raises(gitview.NotFound):
        gitview.resolve_commit(opened, "../etc/passwd")


def test_list_tree_root_and_subdir(opened):
    commit, _ = gitview.resolve_commit(opened, "master")
    root = gitview.list_tree(opened, commit, "")
    names = {e.name: e.kind for e in root}
    assert names["flag.txt"] == "blob"
    assert names["src"] == "tree"

    sub = gitview.list_tree(opened, commit, "src")
    assert {e.name for e in sub} == {"run.sh"}
    assert sub[0].path == "src/run.sh"


def test_list_tree_rejects_file_as_directory(opened):
    commit, _ = gitview.resolve_commit(opened, "master")
    with pytest.raises(gitview.NotFound):
        gitview.list_tree(opened, commit, "flag.txt")


@pytest.mark.parametrize("path", ["../../etc", "a/../../etc", "a/./b", "a//b", "/etc/passwd"])
def test_list_tree_rejects_path_traversal(opened, path):
    # Exercised directly, not through a URL: the app's <path:subpath> route
    # converter would happily hand a literal ".." straight through, so this
    # (split_path(), used by every gitview path lookup) is what actually has
    # to reject it, not anything at the HTTP layer.
    commit, _ = gitview.resolve_commit(opened, "master")
    with pytest.raises(gitview.NotFound):
        gitview.list_tree(opened, commit, path)


def test_read_blob_returns_latest_content(opened):
    commit, _ = gitview.resolve_commit(opened, "master")
    entry, data = gitview.read_blob(opened, commit, "flag.txt")
    assert data == b"secret v2\n"
    assert entry.kind == "blob"


def test_read_blob_executable_bit_preserved_in_mode(opened):
    commit, _ = gitview.resolve_commit(opened, "master")
    entry, data = gitview.read_blob(opened, commit, "src/run.sh")
    assert entry.mode & 0o111
    assert data.startswith(b"#!/bin/sh")


def test_read_object_by_sha(opened):
    commit, sha = gitview.resolve_commit(opened, "master")
    entry, _ = gitview.read_blob(opened, commit, "flag.txt")
    obj = gitview.read_object(opened, entry.sha)
    assert obj.data == b"secret v2\n"


def test_read_object_rejects_bad_sha(opened):
    with pytest.raises(gitview.NotFound):
        gitview.read_object(opened, "not-a-sha")


def test_log_commits_order_and_paging(opened):
    commit, sha = gitview.resolve_commit(opened, "master")
    commits, has_more = gitview.log_commits(opened, sha, limit=10)
    assert [c.summary for c in commits] == ["two", "one"]
    assert not has_more

    first_page, has_more = gitview.log_commits(opened, sha, limit=1)
    assert [c.summary for c in first_page] == ["two"]
    assert has_more

    second_page, has_more = gitview.log_commits(opened, sha, skip=1, limit=1)
    assert [c.summary for c in second_page] == ["one"]
    assert not has_more


def test_log_commits_filtered_by_path(opened, repo):
    git(repo, "commit", "-q", "--allow-empty", "-m", "unrelated")
    _, sha = gitview.resolve_commit(opened, "master")
    commits, _ = gitview.log_commits(opened, sha, path="flag.txt", limit=10)
    assert [c.summary for c in commits] == ["two", "one"]


def test_commit_diff_shows_the_change(opened):
    commit, _ = gitview.resolve_commit(opened, "master")
    diff = gitview.commit_diff(opened, commit)
    assert "flag.txt" in diff.text
    assert "-secret\n" in diff.text or "-secret" in diff.text
    assert "+secret v2" in diff.text
    assert diff.additions >= 1 and diff.deletions >= 1


def test_commit_diff_of_root_commit_shows_everything_as_added(opened):
    commits, _ = gitview.log_commits(
        opened, gitview.resolve_commit(opened, "master")[1], limit=10
    )
    root = commits[-1]
    commit_obj, _ = gitview.resolve_commit(opened, root.sha)
    diff = gitview.commit_diff(opened, commit_obj)
    assert "+secret\n" in diff.text


def test_secrets_scan_finds_planted_secret(repo, opened):
    (repo / "config.env").write_text("AWS_SECRET_TOKEN=supersecretvalue1234567890\n")
    git(repo, "add", "config.env")
    git(repo, "commit", "-q", "-m", "oops")

    findings, scanned, truncated = gitview.secrets_scan(opened)
    assert scanned > 0
    assert any("supersecretvalue1234567890" in f.line for f in findings)


def test_secrets_scan_finds_secret_even_after_it_is_removed(repo, opened):
    """ The whole point of GitStone: a secret deleted in a later commit is
    still sitting in the object store. """
    (repo / "config.env").write_text("API_KEY=\"leaked-value-0123456789ab\"\n")
    git(repo, "add", "config.env")
    git(repo, "commit", "-q", "-m", "add secret")
    (repo / "config.env").unlink()
    git(repo, "rm", "-q", "config.env")
    git(repo, "commit", "-q", "-m", "remove secret")

    findings, _, _ = gitview.secrets_scan(opened)
    assert any("leaked-value-0123456789ab" in f.line for f in findings)


def test_search_tree_matches_current_files_only(repo, opened):
    git(repo, "commit", "-q", "--allow-empty", "-m", "noop")
    commit, _ = gitview.resolve_commit(opened, "master")
    hits, truncated = gitview.search_tree(opened, commit, "secret v2")
    assert any(h.path == "flag.txt" for h in hits)
    assert not truncated


def test_search_history_matches_commit_messages(opened):
    _, sha = gitview.resolve_commit(opened, "master")
    hits, truncated = gitview.search_history(opened, sha, "two")
    assert any(h.summary == "two" for h in hits)

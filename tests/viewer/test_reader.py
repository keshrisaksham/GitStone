"""Reading a recovered repository: refs, trees, blobs, objects, the log."""
import pytest

from tests.helpers import git
from gitstone.viewer import reader


def test_open_repo_rejects_non_repo(tmp_path):
    with pytest.raises(reader.RepoError):
        reader.open_repo(str(tmp_path))


@pytest.mark.parametrize("ref,safe", [
    ("master", True), ("HEAD", True), ("feature/x", True),
    ("a" * 40, True), ("deadbeef", True),
    ("-rf", False), ("../../etc/passwd", False), ("refs/../x", False),
    ("x\ny", False), ("", False), (".hidden", False), ("x/", False),
])
def test_is_safe_ref(ref, safe):
    assert reader.is_safe_ref(ref) == safe


def test_list_refs_and_default_ref(opened):
    refs, head_sha = reader.list_refs(opened)
    names = {(r.kind, r.name) for r in refs}
    assert ("branch", "master") in names
    assert ("tag", "v1") in names
    assert head_sha is not None
    assert reader.default_ref(opened) == "master"


def test_resolve_commit_by_branch_tag_and_sha(opened):
    commit, sha = reader.resolve_commit(opened, "master")
    assert len(sha) == 40

    by_sha = reader.resolve_commit(opened, sha)[0]
    assert by_sha.id == commit.id

    # annotated tag must be peeled to the commit it points at
    tagged, _ = reader.resolve_commit(opened, "v1")
    assert tagged.id == commit.id


def test_resolve_commit_unknown_ref_raises(opened):
    with pytest.raises(reader.NotFound):
        reader.resolve_commit(opened, "no-such-branch")


def test_resolve_commit_rejects_unsafe_ref(opened):
    with pytest.raises(reader.NotFound):
        reader.resolve_commit(opened, "../etc/passwd")


def test_list_tree_root_and_subdir(opened):
    commit, _ = reader.resolve_commit(opened, "master")
    root = reader.list_tree(opened, commit, "")
    names = {e.name: e.kind for e in root}
    assert names["flag.txt"] == "blob"
    assert names["src"] == "tree"

    sub = reader.list_tree(opened, commit, "src")
    assert {e.name for e in sub} == {"run.sh"}
    assert sub[0].path == "src/run.sh"


def test_list_tree_rejects_file_as_directory(opened):
    commit, _ = reader.resolve_commit(opened, "master")
    with pytest.raises(reader.NotFound):
        reader.list_tree(opened, commit, "flag.txt")


@pytest.mark.parametrize("path", ["../../etc", "a/../../etc", "a/./b", "a//b", "/etc/passwd"])
def test_list_tree_rejects_path_traversal(opened, path):
    # Exercised directly, not through a URL: the app's <path:subpath> route
    # converter would happily hand a literal ".." straight through, so this
    # (split_path(), used by every gitview path lookup) is what actually has
    # to reject it, not anything at the HTTP layer.
    commit, _ = reader.resolve_commit(opened, "master")
    with pytest.raises(reader.NotFound):
        reader.list_tree(opened, commit, path)


def test_read_blob_returns_latest_content(opened):
    commit, _ = reader.resolve_commit(opened, "master")
    entry, data = reader.read_blob(opened, commit, "flag.txt")
    assert data == b"secret v2\n"
    assert entry.kind == "blob"


def test_read_blob_executable_bit_preserved_in_mode(opened):
    commit, _ = reader.resolve_commit(opened, "master")
    entry, data = reader.read_blob(opened, commit, "src/run.sh")
    assert entry.mode & 0o111
    assert data.startswith(b"#!/bin/sh")


def test_read_object_by_sha(opened):
    commit, sha = reader.resolve_commit(opened, "master")
    entry, _ = reader.read_blob(opened, commit, "flag.txt")
    obj = reader.read_object(opened, entry.sha)
    assert obj.data == b"secret v2\n"


def test_read_object_rejects_bad_sha(opened):
    with pytest.raises(reader.NotFound):
        reader.read_object(opened, "not-a-sha")


def test_log_commits_order_and_paging(opened):
    commit, sha = reader.resolve_commit(opened, "master")
    commits, has_more = reader.log_commits(opened, sha, limit=10)
    assert [c.summary for c in commits] == ["two", "one"]
    assert not has_more

    first_page, has_more = reader.log_commits(opened, sha, limit=1)
    assert [c.summary for c in first_page] == ["two"]
    assert has_more

    second_page, has_more = reader.log_commits(opened, sha, skip=1, limit=1)
    assert [c.summary for c in second_page] == ["one"]
    assert not has_more


def test_log_commits_filtered_by_path(opened, repo):
    git(repo, "commit", "-q", "--allow-empty", "-m", "unrelated")
    _, sha = reader.resolve_commit(opened, "master")
    commits, _ = reader.log_commits(opened, sha, path="flag.txt", limit=10)
    assert [c.summary for c in commits] == ["two", "one"]

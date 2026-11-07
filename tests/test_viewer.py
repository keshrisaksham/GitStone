"""Tests for the dulwich-based repository viewer."""
import pytest

from conftest import git
from gitstone.viewer import integrity, reader, search
from gitstone.viewer import secrets as secret_scan


@pytest.fixture
def opened(repo):
    r = reader.open_repo(str(repo))
    yield r
    r.close()


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


def test_commit_diff_shows_the_change(opened):
    commit, _ = reader.resolve_commit(opened, "master")
    diff = reader.commit_diff(opened, commit)
    assert "flag.txt" in diff.text
    assert "-secret\n" in diff.text or "-secret" in diff.text
    assert "+secret v2" in diff.text
    assert diff.additions >= 1 and diff.deletions >= 1


def test_commit_diff_of_root_commit_shows_everything_as_added(opened):
    commits, _ = reader.log_commits(
        opened, reader.resolve_commit(opened, "master")[1], limit=10
    )
    root = commits[-1]
    commit_obj, _ = reader.resolve_commit(opened, root.sha)
    diff = reader.commit_diff(opened, commit_obj)
    assert "+secret\n" in diff.text


def test_secrets_scan_finds_planted_secret(repo, opened):
    (repo / "config.env").write_text("AWS_SECRET_TOKEN=supersecretvalue1234567890\n")
    git(repo, "add", "config.env")
    git(repo, "commit", "-q", "-m", "oops")

    findings, scanned, truncated = secret_scan.secrets_scan(opened)
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

    findings, _, _ = secret_scan.secrets_scan(opened)
    assert any("leaked-value-0123456789ab" in f.line for f in findings)


def test_search_tree_matches_current_files_only(repo, opened):
    git(repo, "commit", "-q", "--allow-empty", "-m", "noop")
    commit, _ = reader.resolve_commit(opened, "master")
    hits, truncated = search.search_tree(opened, commit, "secret v2")
    assert any(h.path == "flag.txt" for h in hits)
    assert not truncated


def test_search_history_matches_commit_messages(opened):
    _, sha = reader.resolve_commit(opened, "master")
    hits, truncated = search.search_history(opened, sha, "two")
    assert any(h.summary == "two" for h in hits)


# ---------------------------------------------------------------- integrity


def test_verify_objects_clean_dump(opened):
    report = integrity.verify_objects(opened)
    assert report.ok
    assert report.commits >= 2 and report.blobs >= 2


def test_verify_objects_lists_what_is_missing(repo, tmp_path):
    import shutil
    clone = tmp_path / "clone"
    shutil.copytree(repo, clone)
    git(clone, "gc", "-q", "--prune=now")
    packs = list((clone / ".git" / "objects" / "pack").glob("*.pack"))
    assert packs
    # keep the refs but lose every object
    for p in (clone / ".git" / "objects" / "pack").iterdir():
        p.unlink()

    r = reader.open_repo(str(clone))
    try:
        report = integrity.verify_objects(r)
    finally:
        r.close()
    assert not report.ok
    assert report.refs_unresolved

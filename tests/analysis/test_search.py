"""Text and regex search over the tree, the history and git metadata."""

from tests.helpers import commit_all, git, git_config_with_credentials, minified
from gitstone.analysis import lines, search
from gitstone.viewer import reader


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


def test_regex_search_in_the_tree(opened):
    commit, _ = reader.resolve_commit(opened, "master")
    hits, _ = search.search_tree(opened, commit, r"secret\s+v\d", regex=True)
    assert [h.path for h in hits] == ["flag.txt"]
    plain, _ = search.search_tree(opened, commit, r"secret\s+v\d")
    assert plain == []  # not interpreted as a regex unless asked


def test_search_every_blob_in_history_finds_deleted_content(repo, opened):
    (repo / "old.cfg").write_text("db_password = 'hunter2-old'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "add")
    git(repo, "rm", "-q", "old.cfg")
    git(repo, "commit", "-q", "-m", "remove")

    commit, _ = reader.resolve_commit(opened, "master")
    assert search.search_tree(opened, commit, "hunter2")[0] == []  # gone from the tree
    hits, scanned, _ = search.search_blobs(opened, r"hunter2-\w+", regex=True)
    assert scanned > 0 and len(hits) == 1
    assert hits[0].locations and hits[0].locations[0][1] == "old.cfg"  # and we know where it was


def test_search_marks_repeated_lines_as_duplicates(repo, opened):
    (repo / "a.txt").write_text("needle one\nunique alpha\n")
    (repo / "b.txt").write_text("needle one\nunique beta\n")
    commit_all(repo, "two files with a repeated line")
    commit, _ = reader.resolve_commit(opened, "master")
    hits, _ = search.search_tree(opened, commit, "needle")
    assert len(hits) == 2
    assert sorted(h.dup_of_first for h in hits) == [False, True]
    assert [h.dup_count for h in hits if not h.dup_of_first] == [2]
    blob_hits, _, _ = search.search_blobs(opened, "needle")
    assert sum(h.dup_of_first for h in blob_hits) == len(blob_hits) - 1
    msgs, _ = search.search_history(opened, commit.id.decode(), "two files")
    assert [m.dup_of_first for m in msgs] == [False]


def test_search_matches_long_lines_for_plain_and_regex_queries(repo, opened):
    (repo / "big.json").write_text(minified('"token_xyz":"needle-value"'))
    commit_all(repo, "big")
    commit, _ = reader.resolve_commit(opened, "master")
    plain, _ = search.search_tree(opened, commit, "needle-value")
    rx, _ = search.search_tree(opened, commit, r'token_xyz":"needle-\w+', regex=True)
    blobs, _, _ = search.search_blobs(opened, "needle-value")
    for hits in (plain, rx, blobs):
        assert len(hits) == 1 and "needle-value" in hits[0].line and len(hits[0].line) <= lines.SNIPPET_WIDTH + 2


def test_search_meta_scope_covers_git_files_and_every_commit_message(repo, opened):
    git_config_with_credentials(repo)
    (repo / "x.txt").write_text("x\n")
    commit_all(repo, "ticket FOO-123 handled")
    hits, scanned, _ = search.search_meta(opened, "deploy:s3cr3t")
    assert [(h.source, h.path) for h in hits] == [("file", ".git/config")]
    hits, _, _ = search.search_meta(opened, "FOO-123")
    # the message is in the commit object, and also in COMMIT_EDITMSG and the reflogs
    assert {h.source for h in hits} == {"commit", "file"}
    commit_hit = next(h for h in hits if h.source == "commit")
    assert commit_hit.ref_id and commit_hit.path.startswith("commit ")
    assert {h.path for h in hits if h.source == "file"} >= {".git/logs/HEAD"}

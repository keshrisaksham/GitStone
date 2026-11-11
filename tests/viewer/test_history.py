"""The staged index against HEAD, and mapping blobs back to commits."""

from tests.helpers import git, stage_password_edit
from gitstone.viewer import history, reader


def test_locate_blobs_finds_deleted_files(repo, opened):
    (repo / "gone.txt").write_text("temp\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "x")
    sha = reader.read_blob(opened, reader.resolve_commit(opened, "master")[0], "gone.txt")[0].sha
    git(repo, "rm", "-q", "gone.txt")
    git(repo, "commit", "-q", "-m", "y")
    assert history.locate_blobs(opened, {sha})[sha][0][1] == "gone.txt"


def test_index_changes_report_staged_but_uncommitted_paths(repo, opened):
    (repo / "new.txt").write_text("new\n")
    (repo / "flag.txt").write_text("changed\n")
    git(repo, "add", "new.txt", "flag.txt")
    changes = {c.path: c for c in history.index_changes(opened)}
    assert changes["new.txt"].status == "added" and changes["new.txt"].old_sha is None
    assert changes["flag.txt"].status == "modified" and changes["flag.txt"].old_sha != changes["flag.txt"].new_sha


def test_staged_diff_is_the_index_against_head(repo, opened):
    stage_password_edit(repo)
    diff = history.staged_diff(opened)
    by_path = {f.path: f for f in diff.files}
    assert set(by_path) == {"auth.js", "added.env"}
    assert by_path["auth.js"].status == "modified" and by_path["added.env"].status == "added"
    assert "+const password = 'OctopiFociPilfer45';" in by_path["auth.js"].text
    assert "-const password = 'thisissupersafe';" in by_path["auth.js"].text


def test_staged_diff_is_empty_when_nothing_is_staged(repo, opened):
    assert history.staged_diff(opened).files == []


def test_a_missing_index_file_means_no_index_not_an_empty_one(repo):
    """dulwich opens a missing index as empty, which would read as everything
    committed having been staged for deletion."""
    import os
    from gitstone.viewer import reader
    os.remove(repo / ".git" / "index")
    r = reader.open_repo(str(repo))
    try:
        assert history.index_entries(r) is None
        assert history.index_changes(r) == []
        diff = history.staged_diff(r)
    finally:
        r.close()
    assert diff.files == [] and any("no readable .git/index" in n for n in diff.notes)

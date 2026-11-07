"""Commit and version diffs."""

from tests.helpers import git
from gitstone.viewer import reader


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


def test_compare_two_versions(opened):
    commits, _ = reader.log_commits(opened, reader.resolve_commit(opened, "master")[1])
    old, _ = reader.resolve_commit(opened, commits[-1].sha)
    new, _ = reader.resolve_commit(opened, commits[0].sha)
    diff = reader.compare_commits(opened, old, new)
    (change,) = diff.files
    assert change.path == "flag.txt" and change.status == "modified"
    assert change.additions == 1 and change.deletions == 1


def test_commit_diff_reports_added_and_deleted_files(repo, opened):
    (repo / "new.txt").write_text("hello\n")
    git(repo, "rm", "-q", "flag.txt")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "churn")
    commit, _ = reader.resolve_commit(opened, "master")
    status = {f.path: f.status for f in reader.commit_diff(opened, commit).files}
    assert status == {"new.txt": "added", "flag.txt": "deleted"}

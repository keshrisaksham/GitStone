"""The change feed across every ref."""

from tests.helpers import commit_all, drop, git, reopen
from gitstone.analysis import walk
from gitstone.viewer import reader, timeline


def test_timeline_finds_a_file_deleted_from_history(repo, opened):
    """flag.txt is modified (not deleted) by the `repo` fixture; add a file
    that gets committed and then removed, the exact "used to be here" case."""
    (repo / "secret.env").write_text("TOKEN=abc\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "add secret.env")
    git(repo, "rm", "-q", "secret.env")
    git(repo, "commit", "-q", "-m", "remove secret.env")

    commit, _ = reader.resolve_commit(opened, "master")
    result = timeline.build_timeline(opened, commit)
    assert not result.truncated

    statuses = {(e.path, e.status) for e in result.events}
    assert ("secret.env", "added") in statuses
    assert ("secret.env", "deleted") in statuses
    assert ("flag.txt", "modified") in statuses  # from the fixture's own 2nd commit

    gone = {e.path: e for e in result.deleted_now}
    assert "secret.env" in gone and "flag.txt" not in gone  # still in the tree today
    assert gone["secret.env"].commit_summary == "remove secret.env"


def test_timeline_events_are_newest_first(repo, opened):
    git(repo, "commit", "-q", "--allow-empty", "-m", "noop")
    commit, _ = reader.resolve_commit(opened, "master")
    result = timeline.build_timeline(opened, commit)
    times = [e.commit_time for e in result.events]
    assert times == sorted(times, reverse=True)


def test_timeline_walks_every_ref_not_just_the_default_branch(repo, opened):
    """A file only ever committed (and removed) on a side branch must still
    turn up -- the whole point is not missing something just because it
    isn't reachable from whichever branch happens to be the default."""
    git(repo, "checkout", "-qb", "side")
    (repo / "side-secret.env").write_text("X=1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "add on a side branch")
    git(repo, "rm", "-q", "side-secret.env")
    git(repo, "commit", "-q", "-m", "remove it too")
    git(repo, "checkout", "-q", "master")

    commit, _ = reader.resolve_commit(opened, "master")  # default_commit stays on master
    result = timeline.build_timeline(opened, commit)
    paths = {e.path for e in result.events}
    assert "side-secret.env" in paths
    assert "side-secret.env" in {e.path for e in result.deleted_now}


def test_timeline_survives_a_missing_parent_commit(repo):
    """A partial dump must not crash the walk. The commit whose parent is
    missing can't honestly be called "added" or "modified" (reader.commit_diff
    marks it "unknown" for the same reason), but it must still show up --
    not silently vanish -- since this is the common case for a partial dump,
    not an edge case."""
    r, commit = reopen(repo)
    drop(repo, commit.parents[0])
    r.close()
    r, commit = reopen(repo)
    try:
        result = timeline.build_timeline(r, commit)
        assert any(e.path == "flag.txt" and e.status == "unknown" for e in result.events)
        assert not any(e.status == "deleted" for e in result.events)  # can't know that either
    finally:
        r.close()


def test_timeline_marks_the_same_change_to_the_same_content_as_a_duplicate(repo, opened):
    (repo / "one.env").write_text("TOKEN=copy\n")
    (repo / "two.env").write_text("TOKEN=copy\n")  # identical bytes, second path
    commit_all(repo, "same content twice")
    commit, _ = reader.resolve_commit(opened, "master")
    result = timeline.build_timeline(opened, commit)
    added = [e for e in result.events if e.status == "added" and e.path in ("one.env", "two.env")]
    assert sorted(e.dup_of_first for e in added) == [False, True]
    assert all(not e.dup_of_first for e in result.events if e.path in ("flag.txt", "src/run.sh"))

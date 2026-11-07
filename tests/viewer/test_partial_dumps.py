"""A recovered repository is usually incomplete: a missing object or parent must never become a crash."""
import pytest

from tests.helpers import drop, reopen
from gitstone.analysis import search
from gitstone.viewer import history, reader


@pytest.mark.parametrize("what", ["parent_tree", "parent_commit", "blob"])
def test_commit_diff_survives_missing_objects(repo, what):
    r, commit = reopen(repo)
    parent = r.object_store[commit.parents[0]]
    if what == "parent_tree":
        victim = parent.tree
    elif what == "parent_commit":
        victim = parent.id
    else:  # the new version of flag.txt
        victim = r.object_store[commit.tree][b"flag.txt"][1]
    r.close()
    drop(repo, victim)

    r, commit = reopen(repo)
    try:
        diff = reader.commit_diff(r, commit)  # used to raise KeyError -> HTTP 500
    finally:
        r.close()
    if what == "blob":
        (flag,) = [f for f in diff.files if f.path == "flag.txt"]
        assert not flag.available and "missing" in flag.note
        assert diff.unavailable == 1
    else:
        assert diff.notes and "missing" in diff.notes[0]
        assert all(f.status == "unknown" for f in diff.files)


def test_log_stops_at_a_missing_parent(repo):
    r, commit = reopen(repo)
    drop(repo, commit.parents[0])
    r.close()
    r, commit = reopen(repo)
    try:
        commits, more = reader.log_commits(r, commit.id.decode())
    finally:
        r.close()
    assert [c.summary for c in commits] == ["two"] and not more


def test_history_features_survive_a_missing_parent(repo):
    """search / secrets-location code paths with a cut-short history"""
    r, commit = reopen(repo)
    drop(repo, commit.parents[0])
    r.close()
    r, commit = reopen(repo)
    try:
        hits, _ = search.search_history(r, commit.id.decode(), "two")
        assert [h.summary for h in hits] == ["two"]
        blob = r.object_store[commit.tree][b"flag.txt"][1].decode()
        assert history.locate_blobs(r, {blob})[blob][0][1] == "flag.txt"
        flag, _ = reader.log_commits(r, commit.id.decode(), path="flag.txt")
        assert [c.summary for c in flag] == ["two"]
    finally:
        r.close()

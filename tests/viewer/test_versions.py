"""The versions a person can compare, and typing them forgivingly."""
import pytest

from tests.helpers import two_commits
from gitstone.viewer import reader, versions


def test_resolve_commit_loose_accepts_how_people_actually_type_versions(repo, opened):
    head_sha, parent_sha = two_commits(repo)
    assert reader.resolve_commit_loose(opened, "master")[1] == head_sha
    assert reader.resolve_commit_loose(opened, "  master ")[1] == head_sha          # stray spaces
    assert reader.resolve_commit_loose(opened, "MASTER")[1] == head_sha             # wrong case
    assert reader.resolve_commit_loose(opened, "V1")[1] == head_sha                 # tag, wrong case
    assert reader.resolve_commit_loose(opened, parent_sha[:7])[1] == parent_sha     # the short sha the Commits page shows
    assert reader.resolve_commit_loose(opened, parent_sha.upper()[:9])[1] == parent_sha
    assert reader.resolve_commit_loose(opened, parent_sha)[1] == parent_sha


def test_resolve_commit_loose_explains_a_failure(repo, opened):
    with pytest.raises(reader.NotFound, match="choose a version"):
        reader.resolve_commit_loose(opened, "   ")
    with pytest.raises(reader.NotFound, match="no branch, tag or commit matches 'nope'"):
        reader.resolve_commit_loose(opened, "nope")
    with pytest.raises(reader.NotFound, match="Did you mean 'master'"):
        reader.resolve_commit_loose(opened, "mastr")
    with pytest.raises(reader.NotFound):
        reader.resolve_commit_loose(opened, "ffff")  # a prefix nothing has


def test_list_versions_names_first_then_commits_without_repeating_a_tip(repo, opened):
    head_sha, parent_sha = two_commits(repo)
    found = versions.list_versions(opened)
    kinds = [v.kind for v in found]
    assert kinds.index("commit") > max(i for i, k in enumerate(kinds) if k in ("branch", "tag"))
    assert {"master", "v1"} <= {v.label for v in found if v.kind in ("branch", "tag")}
    shas = [v.sha for v in found if v.kind == "commit"]
    assert head_sha not in shas and parent_sha in shas  # the tip is listed by its names, once
    one = next(v for v in found if v.sha == parent_sha)
    assert one.value == parent_sha and one.label == parent_sha[:10] and one.summary == "one" and one.date


def test_list_versions_searches_names_shas_messages_and_authors(repo, opened):
    head_sha, parent_sha = two_commits(repo)
    assert [v.label for v in versions.list_versions(opened, "v1")][:1] == ["v1"]
    assert parent_sha in [v.sha for v in versions.list_versions(opened, parent_sha[:6])]
    assert parent_sha in [v.sha for v in versions.list_versions(opened, "ONE")]     # by message, any case
    assert versions.list_versions(opened, "zzz-nothing") == []
    exact = versions.list_versions(opened, "master")
    assert exact[0].label == "master"                                                # exact name sorts first

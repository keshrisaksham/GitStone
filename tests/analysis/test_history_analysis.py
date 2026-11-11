"""The history analysis: scan, risk flags, renames, pickaxe, secret lifecycle."""
import os
import subprocess
import time

import pytest

from gitstone.analysis import changes, lifecycle, pickaxe, risk
from gitstone.viewer import filehistory, reader
from tests.helpers import ALICE, BOB, SECRET, commit

def head_commit(repo):
    return reader.resolve_commit(repo, "master")[0]


# ----------------------------------------------------------------- scan


def test_scan_counts_commits_authors_and_months(story_repo):
    rep = changes.scan_history(story_repo, head_commit(story_repo))
    assert rep.walked == 7 and not rep.truncated
    assert [(a.name, a.commits) for a in rep.authors] == [("Alice", 5), ("Bob", 2)]
    assert [m for m, _ in rep.months] == ["2023-01", "2023-02", "2023-03", "2023-04", "2023-05"]
    assert dict(rep.months)["2023-05"] == 3
    assert rep.merges == 0 and rep.refs.get("branch") == 1


def test_hotspots_rank_the_most_changed_file_first_and_know_what_is_gone(story_repo):
    rep = changes.scan_history(story_repo, head_commit(story_repo))
    assert rep.hotspots[0].path == "README.md" and rep.hotspots[0].changes == 4
    gone = {h.path: h for h in rep.hotspots}[".env"]
    assert gone.last_status == "deleted" and not gone.exists_now


def test_risky_commits_surface_the_env_file_and_ci_change(story_repo):
    rep = changes.scan_history(story_repo, head_commit(story_repo))
    by_summary = {c.summary: c for c in rep.commits}
    codes = {f.code for f in by_summary["add config"].flags}
    assert "sensitive-added" in codes
    assert "sensitive-deleted" in {f.code for f in by_summary["stop committing secrets"].flags}
    assert "ci" in {f.code for f in by_summary["initial"].flags}
    assert {c.summary for c in rep.risky} >= {"add config", "stop committing secrets"}


def test_scan_respects_its_commit_cap(story_repo):
    rep = changes.scan_history(story_repo, head_commit(story_repo), max_commits=3)
    assert rep.walked == 3 and rep.truncated


def test_scan_can_be_cancelled(story_repo):
    with pytest.raises(changes.HistoryCancelled):
        changes.scan_history(story_repo, head_commit(story_repo), should_stop=lambda: True)


def test_count_commits_stops_at_the_limit(story_repo):
    assert changes.count_commits(story_repo, 100) == 7
    assert changes.count_commits(story_repo, 4) == 4


def test_scan_of_an_empty_repository_is_empty(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    r = reader.open_repo(str(tmp_path))
    try:
        rep = changes.scan_history(r)
    finally:
        r.close()
    assert rep.commits == [] and rep.months == [] and rep.first_time == 0.0


def test_identity_oddities_are_reported(tmp_path):
    repo = tmp_path / "ids"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    for i, who in enumerate((("Carol", "c@x.org"), ("C. Jones", "c@x.org"), ("Carol", "other@x.org"))):
        (repo / "f.txt").write_text("v%d\n" % i)
        commit(repo, who, "2023-01-0%dT10:00:00" % (i + 1), "c%d" % i)
    r = reader.open_repo(str(repo))
    try:
        rep = changes.scan_history(r, head_commit(r))
    finally:
        r.close()
    text = " ".join(rep.anomalies)
    assert "c@x.org commits under 2 different names" in text
    assert "Carol commits under 2 different e-mail addresses" in text


def test_commit_dated_before_its_parent_is_flagged(tmp_path):
    repo = tmp_path / "skew"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    (repo / "a").write_text("1\n")
    commit(repo, ALICE, "2023-06-10T10:00:00", "late")
    (repo / "a").write_text("2\n")
    commit(repo, ALICE, "2023-01-01T10:00:00", "early")
    r = reader.open_repo(str(repo))
    try:
        rep = changes.scan_history(r, head_commit(r))
    finally:
        r.close()
    assert "before-parent" in {f.code for c in rep.commits for f in c.flags}
    assert any("dated well before their parent" in a for a in rep.anomalies)


def test_reflog_shows_a_rewritten_history(story):
    (story / "README.md").write_text("# amended\n")
    subprocess.run(["git", "add", "-A"], cwd=story, check=True)
    subprocess.run(["git", "commit", "-q", "--amend", "-m", "amended"], cwd=story, check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@a",
                            GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@a"),
                   stdout=subprocess.DEVNULL)
    r = reader.open_repo(str(story))
    try:
        traces = risk.reflog_traces(r)
    finally:
        r.close()
    assert any("amend" in t.message for t in traces)


# ----------------------------------------------------- one commit, in detail


def test_summary_pairs_a_rename_instead_of_a_delete_and_an_add(story_repo):
    commit_obj = next(c for c in reader.iter_commits(story_repo, [story_repo.head()])
                      if reader.commit_info(c).summary == "rename app to main")
    diff = reader.commit_diff(story_repo, commit_obj)
    summary = changes.summarize_diff(story_repo, diff)
    assert summary["renamed"] == 1 and summary["renames"][0].old == "app.py"
    assert summary["renames"][0].new == "main.py" and summary["renames"][0].similarity == 100
    assert summary["added"] == 0 and summary["deleted"] == 0


def test_a_lightly_edited_file_is_still_a_rename():
    class Blob:
        def __init__(self, data):
            self.data = data

    class Store(dict):
        pass

    old = b"".join(b"line %d\n" % i for i in range(20))
    new = old.replace(b"line 3\n", b"changed\n")
    store = Store({b"a" * 40: Blob(old), b"b" * 40: Blob(new)})

    class Repo:
        object_store = store

    files = [reader.FileChange("old.txt", "deleted", old_sha="a" * 40),
             reader.FileChange("new.txt", "added", new_sha="b" * 40)]
    (rename,) = changes.detect_renames(Repo, files)
    assert (rename.old, rename.new) == ("old.txt", "new.txt") and 90 <= rename.similarity < 100


def test_summary_groups_by_folder_and_kind(story_repo):
    root = next(c for c in reader.iter_commits(story_repo, [story_repo.head()]) if not c.parents)
    summary = changes.summarize_diff(story_repo, reader.commit_diff(story_repo, root))
    assert dict(summary["folders"]) == {"(top level)": 2, ".github/": 1}
    assert dict(summary["kinds"])[".py"] == 1
    assert {f.code for f in summary["flags"]} == {"ci"}


# --------------------------------------------------------------- pickaxe


def test_pickaxe_finds_when_text_came_and_went(story_repo):
    res = pickaxe.pickaxe(story_repo, SECRET)
    assert [h.summary for h in res.hits] == ["stop committing secrets", "add config"]
    removal, intro = res.hits
    assert removal.removed >= 1 and removal.added == 0
    assert intro.added == 2 and {f.path for f in intro.files} == {"config.py", ".env"}


def test_pickaxe_can_ask_for_only_additions_or_removals(story_repo):
    assert [h.summary for h in pickaxe.pickaxe(story_repo, SECRET, change="added").hits] == ["add config"]
    assert [h.summary for h in pickaxe.pickaxe(story_repo, SECRET, change="removed").hits] == ["stop committing secrets"]


def test_pickaxe_filters_by_author_date_and_path(story_repo):
    assert pickaxe.pickaxe(story_repo, "more", author="bob").hits == []
    assert len(pickaxe.pickaxe(story_repo, "more", author="ALICE").hits) == 3
    since = time.mktime(time.strptime("2023-05-06", "%Y-%m-%d"))
    assert len(pickaxe.pickaxe(story_repo, "more", since=since).hits) == 2
    assert pickaxe.pickaxe(story_repo, "more", path_prefix="src/").hits == []
    assert len(pickaxe.pickaxe(story_repo, "more", path_prefix="README").hits) == 3


def test_pickaxe_regex_and_bad_regex(story_repo):
    assert pickaxe.pickaxe(story_repo, r"Sup3r\w+99", regex=True).hits
    with pytest.raises(ValueError):
        pickaxe.pickaxe(story_repo, "(", regex=True)
    assert pickaxe.pickaxe(story_repo, "").hits == []


# -------------------------------------------------------------- lifecycle


def test_a_committed_then_removed_credential_is_traced_from_intro_to_removal(story_repo):
    res = lifecycle.secret_lifecycle(story_repo)
    mine = [s for s in res.secrets if SECRET in s.line]
    assert mine, [s.line for s in res.secrets]
    life = mine[0]
    assert life.status == "removed"
    assert life.introduced.author == "Bob" and life.introduced.short
    assert life.removed.sha != life.introduced.sha and life.exposed_days >= 30
    assert res.removed >= 1


def test_a_credential_that_is_still_there_is_reported_as_present(tmp_path):
    repo = tmp_path / "live"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    (repo / "c.py").write_text("password = '%s'\n" % SECRET)
    commit(repo, BOB, "2023-02-15T10:00:00", "oops")
    r = reader.open_repo(str(repo))
    try:
        res = lifecycle.secret_lifecycle(r)
    finally:
        r.close()
    assert [s.status for s in res.secrets] == ["present"]


# ----------------------------------------------------------- file history


def test_file_history_lists_each_revision_with_its_change(story_repo):
    revs, more = filehistory.file_history(story_repo, story_repo.head().decode(), "README.md")
    assert not more and [r.summary for r in revs] == ["docs 2", "docs 1", "docs 0", "initial"]
    assert [r.status for r in revs] == ["modified", "modified", "modified", "added"]
    assert revs[0].additions == 1 and revs[-1].additions == 1


def test_file_history_shows_the_deletion_and_pages(story_repo):
    head = story_repo.head().decode()
    revs, _ = filehistory.file_history(story_repo, head, ".env")
    assert [r.status for r in revs] == ["deleted", "added"]
    revs, more = filehistory.file_history(story_repo, head, "README.md", limit=2)
    assert len(revs) == 2 and more


def test_blame_attributes_each_line(story_repo):
    lines = filehistory.blame(story_repo, story_repo.head().decode(), "config.py")
    assert [l.text for l in lines] == ["import os", "DEBUG = False", "password = os.environ['APP_PASSWORD']"]
    assert lines[1].author.startswith("Bob") and lines[0].first_of_group


def test_blame_refuses_a_missing_file(story_repo):
    with pytest.raises(reader.NotFound):
        filehistory.blame(story_repo, story_repo.head().decode(), "nope.txt")


# --------------------------------------------------- pickaxe: staged changes


@pytest.fixture
def staged_story(story):
    """The story, plus an uncommitted ``git add``: a new file with a token and
    an edit that replaces one committed line."""
    (story / "staged.txt").write_text("api_token = 'StagedOnlyValue77'\n")
    (story / "config.py").write_text("import os\nDEBUG = True\npassword = os.environ['APP_PASSWORD']\n")
    subprocess.run(["git", "add", "staged.txt", "config.py"], cwd=story, check=True)
    r = reader.open_repo(str(story))
    yield r
    r.close()


def test_pickaxe_can_also_search_what_is_staged_but_not_committed(staged_story):
    res = pickaxe.pickaxe(staged_story, "StagedOnlyValue77", include_staged=True)
    assert res.hits == [] and res.staged_searched
    assert res.staged.staged and res.staged.added == 1 and res.staged.removed == 0
    assert [f.path for f in res.staged.files] == ["staged.txt"]
    assert "StagedOnlyValue77" in res.staged.files[0].lines[0].text


def test_staged_changes_are_left_out_unless_asked_for(staged_story):
    res = pickaxe.pickaxe(staged_story, "StagedOnlyValue77")
    assert res.staged is None and not res.staged_searched


def test_staged_removals_are_found_and_committed_text_is_not_called_staged(staged_story):
    removed = pickaxe.pickaxe(staged_story, "DEBUG = False", change="removed", include_staged=True)
    assert removed.hits == [] and removed.staged.removed == 1 and removed.staged.added == 0
    both = pickaxe.pickaxe(staged_story, SECRET, include_staged=True)
    assert both.staged is None and len(both.hits) == 2  # committed history only


def test_staged_search_honours_change_and_path_filters(staged_story):
    assert pickaxe.pickaxe(staged_story, "StagedOnlyValue77", change="removed", include_staged=True).staged is None
    assert pickaxe.pickaxe(staged_story, "StagedOnlyValue77", path_prefix="src/", include_staged=True).staged is None
    assert pickaxe.pickaxe(staged_story, "StagedOnlyValue77", path_prefix="staged", include_staged=True).staged


def test_staged_changes_have_no_author_or_date_so_those_filters_skip_them(staged_story):
    res = pickaxe.pickaxe(staged_story, "StagedOnlyValue77", author="alice", include_staged=True)
    assert res.staged is None and not res.staged_searched
    res = pickaxe.pickaxe(staged_story, "StagedOnlyValue77", since=1.0, include_staged=True)
    assert not res.staged_searched


def test_a_dump_without_a_readable_index_just_has_no_staged_hits(tmp_path):
    repo = tmp_path / "noindex"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    (repo / "f").write_text("hello needle\n")
    commit(repo, ALICE, "2023-01-01T10:00:00", "c")
    os.remove(repo / ".git" / "index")
    r = reader.open_repo(str(repo))
    try:
        res = pickaxe.pickaxe(r, "needle", include_staged=True)
    finally:
        r.close()
    assert len(res.hits) == 1 and res.staged is None
